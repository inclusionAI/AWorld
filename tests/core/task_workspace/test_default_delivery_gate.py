"""A1 integration: real CLI construction/Agent finish, isolated facade boundary."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from aworld.agents.llm_agent import Agent, LlmOutputParser, _ValidationRepairContinuation
from aworld.config.conf import AgentConfig
from aworld.core.common import ActionModel, Observation
from aworld.core.context.base import Context
from aworld.core.context.compiler import CompletionContract, CompletionMode, CompletionStatus, SelfCheckEvidence, ValidationCommand
from aworld.core.context.execution_state import get_execution_state
from aworld.core.context.session import Session
from aworld.core.event.base import Constants, Message
from aworld.core.task import Task
from aworld.core.task_workspace.contracts import derive_delivery_contract
from aworld.models.model_response import ModelResponse
from aworld.core.exceptions import AWorldRuntimeException
from aworld_cli.core.runtime_completion import build_runtime_completion_contract, configure_goal_completion, configure_runtime_completion, resolve_runtime_completion_evidence
from aworld_cli.executors.local import LocalAgentExecutor


@pytest.fixture
def local_facade(monkeypatch):
    # The session/store/validator implementation is owned by the other workers.
    # This boundary executes real JSON parsing, never accepts model pass flags.
    module = ModuleType('aworld.core.task_workspace.session')
    def prepare(context, request, workspace_path):
        existing = context.completion_contract
        explicit = {'outputs': [item.path for item in existing.required_artifacts]} if existing else None
        delivery = derive_delivery_contract(request, workspace_path=workspace_path, explicit=explicit)
        context.context_info['delivery_contract'] = delivery
        return delivery
    async def evaluate(context, contract):
        okay = True
        for output in context.context_info['delivery_contract']['outputs']:
            path = Path(output['path'])
            okay = okay and path.is_file()
            if path.suffix == '.json':
                try:
                    json.loads(path.read_text())
                except (OSError, ValueError):
                    okay = False
        for check_id in contract.required_self_check_ids:
            context.record_completion_self_check(SelfCheckEvidence(check_id, 0 if okay else 1, None, datetime.now(timezone.utc)))
    module.prepare_task_workspace = prepare
    module.evaluate_delivery = evaluate
    monkeypatch.setitem(sys.modules, module.__name__, module)
    for key in ('AWORLD_COMPLETION_MODE', 'AWORLD_REQUIRED_ARTIFACTS_JSON', 'AWORLD_VALIDATION_COMMANDS_JSON', 'AWORLD_INFER_REQUIRED_ARTIFACTS'):
        monkeypatch.delenv(key, raising=False)
    return module


def _bind(context, tmp_path):
    context.context_info['task_workspace_binding'] = {'workspace': str(tmp_path), 'scope': {'task_id': context.task_id}, 'authority': 'local'}


def _agent(context, **kwargs):
    agent = Agent(name='Aworld', conf=AgentConfig(llm_provider='openai', llm_model_name='offline', llm_api_key='offline'), **kwargs)
    agent._llm = object()
    agent.context = context
    return agent


@pytest.mark.asyncio
async def test_default_cli_build_enforces_literal_output_and_agent_cannot_finish_early(local_facade, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('AWORLD_REQUIRED_ARTIFACTS_JSON', '["result.json"]')
    executor = object.__new__(LocalAgentExecutor)
    executor._base_runtime = None
    executor.session_id = 'delivery'
    executor.swarm = SimpleNamespace(agents={})
    executor.context_config = SimpleNamespace()
    executor._execute_hooks = AsyncMock(return_value=None)
    executor._create_workspace = AsyncMock(return_value=None)
    executor._resolve_swarm_skills = Mock()
    async def from_input(task_input, **kwargs):
        context = Context(task_id=task_input.task_id, session=Session(session_id='delivery'))
        context.user_id = 'user'
        context.get_config = lambda: SimpleNamespace(debug_mode=False)
        context.init_swarm_state = AsyncMock()
        _bind(context, tmp_path)
        return context
    monkeypatch.setattr('aworld_cli.executors.local.ApplicationContext.from_input', from_input)
    task = await executor._build_task('Write result.json.')
    task.context.set_task(task)
    assert task.context.completion_mode is CompletionMode.ENFORCE
    assert task.context.completion_contract.max_repairs is None
    assert task.context.completion_contract.max_evidence_age_seconds is None
    assert task.context.completion_contract.required_artifacts
    agent = _agent(task.context)
    response = ModelResponse(id='final', model='offline', content='All done.', finish_reason='stop')
    parsed = await LlmOutputParser().parse(response, agent_id=agent.id())
    for _ in range(3):
        assert await agent._completion_feedback_if_unsatisfied(context=task.context, final_response_text='All done.')
        assert not agent.is_agent_finished(response, parsed)
        assert get_execution_state(task.context)['status'] == 'incomplete'
    (tmp_path / 'result.json').write_text('{"delivered":true}')
    assert await agent._completion_feedback_if_unsatisfied(context=task.context, final_response_text='All done.') is None
    assert agent.is_agent_finished(response, parsed)
    assert get_execution_state(task.context)['status'] == 'succeeded'


@pytest.mark.asyncio
async def test_caller_check_survives_derived_path_and_aggregate_attachment(local_facade, tmp_path):
    context = Context(task_id='caller')
    _bind(context, tmp_path)
    check = ValidationCommand('caller-check', (sys.executable, '-c', 'raise SystemExit(4)'))
    contract = CompletionContract((), (), (check,), None, (), max_repairs=0)
    original_resolver = AsyncMock()
    context.configure_completion_contract(contract, mode=CompletionMode.ENFORCE, evidence_resolver=original_resolver)
    evidence = SelfCheckEvidence('caller-check', 4, None, datetime.now(timezone.utc))
    context.record_completion_self_check(evidence)
    extended = configure_runtime_completion(context, request='Write invented.json.', workspace_path=tmp_path)
    assert extended.validation_commands == (check,)
    assert extended.required_artifacts == ()
    assert evidence in context._completion_self_checks
    await context.resolve_completion_evidence()
    original_resolver.assert_awaited_once_with(context, contract)
    assert 'self_check_failed' in context.assess_completion_contract(agent_claimed_finished=True).reason_codes


@pytest.mark.asyncio
async def test_old_pass_is_invalidated_when_delivery_evaluation_fails(local_facade, tmp_path):
    context = Context(task_id='freshness')
    _bind(context, tmp_path)
    (tmp_path / 'result.json').write_text('{}')
    configure_runtime_completion(context, request='Write result.json.', workspace_path=tmp_path)
    context.record_completion_final_evidence('agent_final_response')
    await context.resolve_completion_evidence()
    assert context.assess_completion_contract(agent_claimed_finished=True).status is CompletionStatus.SATISFIED
    local_facade.evaluate_delivery = AsyncMock(side_effect=OSError('unavailable'))
    agent = _agent(context)
    assert await agent._completion_feedback_if_unsatisfied(context=context, final_response_text='done')
    assert context.assess_completion_contract(agent_claimed_finished=True).status is not CompletionStatus.SATISFIED


def test_custom_unbound_context_never_runs_host_derived_checks(local_facade, tmp_path):
    context = Context(task_id='remote')
    assert configure_runtime_completion(context, request='Write result.json.', workspace_path=tmp_path) is None
    assert context.context_info['delivery_evaluation_unavailable'] == 'local_workspace_not_bound'
    assert context.context_info['delivery_contract']['outputs']


def test_runtime_defaults_allow_long_self_checks_but_preserve_caller_freshness(local_facade, tmp_path):
    runtime_contract = build_runtime_completion_contract('', workspace_path=tmp_path, explicit_paths=['out.json'])
    assert runtime_contract.max_evidence_age_seconds is None
    goal_context = Context(task_id='goal-freshness')
    goal_contract = configure_goal_completion(goal_context, verification_commands=['true'], workspace_path=tmp_path)
    assert goal_contract.max_evidence_age_seconds is None
    caller_context = Context(task_id='caller-freshness')
    caller_contract = CompletionContract((), (), (), 120, (), max_repairs=3)
    caller_context.configure_completion_contract(caller_contract, mode=CompletionMode.ENFORCE)
    extended = configure_goal_completion(caller_context, verification_commands=['true'], workspace_path=tmp_path)
    assert extended.max_evidence_age_seconds == 120
    assert extended.max_repairs == 3


@pytest.mark.asyncio
async def test_repair_trampoline_can_continue_beyond_one_without_recursion(tmp_path):
    context = Context(task_id='repair')
    context.set_task(Task(id='repair', input='finish'))
    agent = _agent(context, max_loop_steps=0)
    count = 0
    async def attempt(observation, **kwargs):
        nonlocal count
        count += 1
        return _ValidationRepairContinuation(Observation(content='fix next issue'), {}) if count < 1200 else [ActionModel(policy_info='done')]
    agent._async_policy_once = attempt
    message = Message(category=Constants.AGENT, headers={'context': context})
    result = await agent.async_policy(Observation(content='start'), message=message)
    assert result[0].policy_info == 'done' and count == 1200


@pytest.mark.asyncio
async def test_repair_trampoline_respects_existing_attempt_limit_and_preserves_incomplete(tmp_path):
    context = Context(task_id='attempt-limit')
    context.set_task(Task(id='attempt-limit', input='finish'))
    agent = _agent(context, max_loop_steps=2)
    context.update_agent_step(agent.id())
    async def attempt(observation, **kwargs):
        return _ValidationRepairContinuation(Observation(content='still missing output'), {})
    agent._async_policy_once = attempt
    message = Message(category=Constants.AGENT, headers={'context': context})
    await agent.async_policy(Observation(content='start'), message=message)
    assert get_execution_state(context)['status'] == 'budget_exhausted'


@pytest.mark.asyncio
async def test_configured_three_repairs_terminate_after_the_third_retry(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv('AWORLD_COMPLETION_MAX_REPAIRS', '3')
    contract = build_runtime_completion_contract(
        '', workspace_path=tmp_path, explicit_paths=['out.json']
    )
    assert contract is not None and contract.max_repairs == 3
    context = Context(task_id='three-repairs')
    context.set_task(Task(id='three-repairs', input='finish'))
    context.configure_completion_contract(
        contract, mode=CompletionMode.ENFORCE
    )
    agent = _agent(context, max_loop_steps=0)
    message = Message(category=Constants.AGENT, headers={'context': context})
    observation = Observation(content='still missing output')

    for expected_count in (1, 2, 3):
        result = await agent._retry_for_result_validation(
            validation_feedback='required artifact missing',
            observation=observation,
            info={},
            message=message,
            kwargs={},
            iterative=True,
        )
        assert isinstance(result, _ValidationRepairContinuation)
        assert context.context_info[
            agent._result_validation_retry_key(agent.id())
        ] == expected_count

    result = await agent._retry_for_result_validation(
        validation_feedback='required artifact missing',
        observation=observation,
        info={},
        message=message,
        kwargs={},
        iterative=True,
    )

    assert result[0].policy_info.endswith('not claiming success.')
    assert agent.finished is True
    assert get_execution_state(context)['status'] == 'incomplete'
    assert get_execution_state(context)['reason'] == 'validation_repair_exhausted'


@pytest.mark.asyncio
async def test_repair_trampoline_records_empty_followup_as_incomplete(tmp_path):
    context = Context(task_id='empty-repair')
    context.set_task(Task(id='empty-repair', input='finish'))
    agent = _agent(context, max_loop_steps=0)
    calls = 0
    async def attempt(observation, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _ValidationRepairContinuation(Observation(content='fix output'), {})
        raise AWorldRuntimeException('LLM returned empty or invalid response: {}')
    agent._async_policy_once = attempt
    message = Message(category=Constants.AGENT, headers={'context': context})
    result = await agent.async_policy(Observation(content='start'), message=message)
    assert get_execution_state(context)['status'] == 'incomplete'
    assert get_execution_state(context)['reason'] == 'validation_repair_unavailable'
    assert 'not claiming success' in result[0].policy_info


@pytest.mark.asyncio
async def test_completion_feedback_identifies_bounded_failed_checks_without_raw_output():
    context = Context(task_id='check-feedback')
    contract = CompletionContract((), (), (), None, (), max_repairs=None,
                                  required_self_check_ids=('workbench.delivery',))
    context.configure_completion_contract(contract, mode=CompletionMode.ENFORCE)
    context.context_info['delivery_validation'] = {
        'receipt': {'success': False, 'unchanged': False, 'checks': [
            {'id': f'check-{index}', 'kind': 'json', 'path': f'/outputs/file-{index}.json',
             'status': 'error', 'success': False, 'error_type': 'JSONDecodeError',
             'error': 'RAW_CONTENT_MUST_NOT_APPEAR', 'stdout': 'RAW_CONTENT_MUST_NOT_APPEAR'}
            for index in range(8)]},
        'readback': {'valid': False},
    }
    feedback = await _agent(context)._completion_feedback_if_unsatisfied(context=context, final_response_text='done')
    assert 'check-0' in feedback and '/outputs/file-0.json' in feedback
    assert 'JSONDecodeError' in feedback and '3 more failed checks' in feedback
    assert 'check-7' not in feedback and 'RAW_CONTENT_MUST_NOT_APPEAR' not in feedback
    assert 'readback is invalid' in feedback and 'bytes changed' in feedback
    assert 'WORKBENCH inspect' in feedback

    recovery = _agent(context)._build_result_validation_recovery_brief(
        authoritative_request='Write /outputs/file-0.json.',
        validation_feedback=feedback,
    )
    assert 'execute at least one concrete tool action' in recovery
    assert 'changes the result or verifies it against the failed requirement' in recovery
    assert 'Do not repeat a completion claim without new tool evidence' in recovery


@pytest.mark.asyncio
async def test_agent_caller_contract_does_not_replace_workspace_or_goal_extensions(local_facade, tmp_path):
    context = Context(task_id='agent-caller')
    _bind(context, tmp_path)
    contract = CompletionContract((), (), (ValidationCommand('caller', (sys.executable, '-c', 'pass')),),
                                  90, (), max_repairs=4)
    resolver = AsyncMock()
    agent = _agent(context)
    agent.configure_completion_contract(contract, mode=CompletionMode.ENFORCE, evidence_resolver=resolver)
    agent._install_runtime_completion_contract(context)
    extended = configure_runtime_completion(context, request='Write unrelated.json.', workspace_path=tmp_path)
    agent._install_runtime_completion_contract(context)
    assert context.completion_contract is extended
    assert extended.required_self_check_ids == ()
    goal_extended = configure_goal_completion(context, verification_commands=['true'], workspace_path=tmp_path)
    agent._install_runtime_completion_contract(context)
    assert context.completion_contract is goal_extended
    assert contract.validation_commands[0] in goal_extended.validation_commands
    await context.resolve_completion_evidence()
    resolver.assert_awaited_once_with(context, contract)
    # Old ownership markers must not authorize an unrelated replacement.
    context.configure_completion_contract(CompletionContract((), (), (), None, ()), mode=CompletionMode.ENFORCE)
    with pytest.raises(ValueError, match='conflicting completion contracts'):
        agent._install_runtime_completion_contract(context)


@pytest.mark.asyncio
async def test_goal_extension_executes_custom_caller_checks_once(tmp_path):
    counter = tmp_path / 'caller-check-count.txt'
    command = ValidationCommand('caller-once', (sys.executable, '-c',
        'from pathlib import Path; import sys; p=Path(sys.argv[1]); p.write_text(p.read_text()+"x" if p.exists() else "x")',
        str(counter)))
    original = CompletionContract((), (), (command,), None, ())
    context = Context(task_id='caller-execution-count')
    async def caller_resolver(target, contract):
        await resolve_runtime_completion_evidence(target, contract)
    context.configure_completion_contract(original, mode=CompletionMode.ENFORCE, evidence_resolver=caller_resolver)
    configure_goal_completion(context, verification_commands=['true'], workspace_path=tmp_path)
    await context.resolve_completion_evidence()
    assert counter.read_text() == 'x'


@pytest.mark.asyncio
async def test_default_question_does_not_gain_a_later_agent_authored_gate(local_facade, tmp_path):
    context = Context(task_id='initially-no-delivery')
    _bind(context, tmp_path)
    contract = configure_runtime_completion(context, request='Explain how CSV headers work.', workspace_path=tmp_path)
    assert contract is None
    assert context.completion_mode is CompletionMode.OFF
    agent = _agent(context)
    assert await agent._completion_feedback_if_unsatisfied(context=context, final_response_text='A header names columns.') is None
    path = tmp_path / 'later.json'
    path.write_text('invalid JSON')
    async def later_check(target, configured):
        try:
            json.loads(path.read_text())
            exit_code = 0
        except (ValueError, OSError):
            exit_code = 1
        target.record_completion_self_check(SelfCheckEvidence('workbench.delivery', exit_code, None, datetime.now(timezone.utc)))
    local_facade.evaluate_delivery = later_check
    assert await agent._completion_feedback_if_unsatisfied(context=context, final_response_text='The added file is done.') is None
    response = ModelResponse(id='later-final', model='offline', content='Done.', finish_reason='stop')
    parsed = await LlmOutputParser().parse(response, agent_id=agent.id())
    assert agent.is_agent_finished(response, parsed)
