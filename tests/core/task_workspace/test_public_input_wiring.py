"""Public inputs stay active through the real default CLI/session completion path."""
import json
import sys
from types import SimpleNamespace
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock

import pytest

from aworld.core.context.base import Context
from aworld.core.context.compiler import ArtifactEvidence, ArtifactRequirement, CompletionContract, CompletionMode, CompletionStatus, ValidationCommand
from aworld.core.context.session import Session
from aworld.core.task_workspace.session import get_task_workspace, prepare_task_workspace
from aworld.core.task_workspace.contracts import derive_delivery_contract
from aworld_cli.core.runtime_completion import configure_goal_completion, configure_runtime_completion, resolve_runtime_completion_evidence
from aworld_cli.executors.local import LocalAgentExecutor
from aworld_cli.builtin_agents.smllc.agents.aworld_agent import build_aworld_agent

REQUEST = 'Read source.csv. Do not modify the input file source.csv. Write result.json of at most 32 bytes.'


@pytest.fixture
def native(tmp_path, monkeypatch):
    workspace = tmp_path / 'task'
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    for name in ('AWORLD_COMPLETION_MODE', 'AWORLD_REQUIRED_ARTIFACTS_JSON', 'AWORLD_VALIDATION_COMMANDS_JSON', 'AWORLD_INFER_REQUIRED_ARTIFACTS'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('AWORLD_TASK_WORKSPACE_ROOT', str(tmp_path / 'stores'))
    monkeypatch.setenv('AWORLD_BUILTIN_SUBAGENTS', 'none')
    monkeypatch.setenv('LLM_MODEL_NAME', 'offline')
    monkeypatch.setenv('LLM_API_KEY', 'offline')
    settings = SimpleNamespace(workspace=workspace, explicit=None, install=None, goal={})
    swarm = build_aworld_agent()
    executor = object.__new__(LocalAgentExecutor)
    executor._base_runtime = None
    executor.session_id = 'native-public-inputs'
    executor.swarm = swarm
    executor.context_config = SimpleNamespace()
    executor._execute_hooks = AsyncMock(return_value=None)
    executor._create_workspace = AsyncMock(return_value=None)
    executor._resolve_swarm_skills = Mock()
    executor._goal_session_state = lambda: settings.goal
    async def from_input(task_input, **kwargs):
        context = Context(task_id=task_input.task_id, session=Session(session_id=executor.session_id))
        context.user_id = 'local-test'
        context.get_config = lambda: SimpleNamespace(debug_mode=False)
        context.init_swarm_state = AsyncMock(side_effect=lambda swarm: swarm.agents)
        if settings.explicit is not None:
            context.context_info['task_workspace_contract'] = settings.explicit
        if settings.install is not None:
            settings.install(context)
        return context
    monkeypatch.setattr('aworld_cli.executors.local.ApplicationContext.from_input', from_input)
    async def build(request=REQUEST, task_id='first'):
        task = await executor._build_task(request, task_id=task_id)
        task.context.set_task(task)
        root = swarm.communicate_agent
        root = root[0] if isinstance(root, list) else root
        root.context = task.context
        settings.agent = root
        return task.context
    settings.build = build
    return settings


async def finish(native, context):
    feedback = await native.agent._completion_feedback_if_unsatisfied(context=context, final_response_text='Done.')
    assessment = context.assess_completion_contract(agent_claimed_finished=True)
    assert bool(feedback) is (assessment.status is not CompletionStatus.SATISFIED)
    return assessment.status


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['default', 'outputs', 'commands', 'both'])
async def test_env_shortcuts_keep_public_inputs_and_same_path_checks(native, monkeypatch, mode):
    if mode in ('outputs', 'both'):
        monkeypatch.setenv('AWORLD_REQUIRED_ARTIFACTS_JSON', '["result.json"]')
    if mode in ('commands', 'both'):
        monkeypatch.setenv('AWORLD_VALIDATION_COMMANDS_JSON', json.dumps([{'command_id':'caller', 'argv':[sys.executable,'-c','pass']}]))
    source = native.workspace / 'source.csv'
    original = 'id,value\n1,3\n'
    source.write_text(original)
    context = await native.build()
    contract = context.completion_contract
    session = get_task_workspace(context)
    assert context.completion_mode is CompletionMode.ENFORCE
    assert contract.immutable_inputs == tuple(i['id'] for i in session.delivery['inputs'] if i['immutable'])
    assert len(contract.immutable_inputs) == 1
    assert {c['kind'] for item in session.delivery['outputs'] for c in item['checks']} >= {'json', 'file_size'}
    assert {c['id'] for item in session.delivery['outputs'] for c in item['checks']} <= set(contract.required_self_check_ids)
    assert session.store.current_input_snapshot_id
    assert await finish(native, context) is not CompletionStatus.SATISFIED
    output = native.workspace / 'result.json'
    output.write_text('not JSON')
    assert await finish(native, context) is not CompletionStatus.SATISFIED
    output.write_text(json.dumps({'value':'x'*40}))
    assert await finish(native, context) is not CompletionStatus.SATISFIED
    output.write_text('{"value":3}')
    assert await finish(native, context) is CompletionStatus.SATISFIED
    source.write_text('changed\n')
    assert await finish(native, context) is not CompletionStatus.SATISFIED
    assert next(iter(session.store.protected_input_files().values())).read_text() == original


@pytest.mark.asyncio
async def test_ordinary_inputs_allow_in_place_changes_but_keep_original_snapshot(native):
    source = native.workspace / 'source.csv'
    source.write_text('original\n')
    context = await native.build('Read source.csv. Write result.json.')
    assert context.completion_contract.immutable_inputs == ()
    source.write_text('updated in place\n')
    (native.workspace / 'result.json').write_text('{}')
    assert await finish(native, context) is CompletionStatus.SATISFIED
    assert next(iter(get_task_workspace(context).store.protected_input_files().values())).read_text() == 'original\n'


@pytest.mark.asyncio
async def test_structured_caller_overrides_only_explicit_fields_with_provenance(native):
    native.explicit = {'inputs':[{'path':'source.csv','immutable':False}],
                      'outputs':[{'path':'result.json','checks':[{'id':'caller-only','kind':'regular_file'}]}]}
    (native.workspace / 'source.csv').write_text('original\n')
    context = await native.build()
    (native.workspace / 'source.csv').write_text('caller permits modification\n')
    (native.workspace / 'result.json').write_text('caller explicitly replaced JSON and size checks')
    delivery = get_task_workspace(context).delivery
    assert context.completion_contract.immutable_inputs == ()
    assert 'caller-only' in context.completion_contract.required_self_check_ids
    assert {x['field'] for x in delivery['resolved_conflicts']} == {'immutable', 'checks'}
    conflict = next(x for x in delivery['resolved_conflicts'] if x['field'] == 'immutable')
    assert conflict['public_source']['quote'] == 'Do not modify the input file source.csv'
    assert conflict['caller_value'] is False
    assert await finish(native, context) is CompletionStatus.SATISFIED


@pytest.mark.asyncio
@pytest.mark.parametrize('empty_mode', ['env_outputs', 'caller_contract'])
async def test_empty_output_authority_keeps_public_input_protection(native, monkeypatch, empty_mode):
    if empty_mode == 'env_outputs':
        monkeypatch.setenv('AWORLD_REQUIRED_ARTIFACTS_JSON', '[]')
    else:
        native.install = lambda c: c.configure_completion_contract(CompletionContract((),(),(),None,('agent_final_response',)), mode=CompletionMode.ENFORCE)
    (native.workspace / 'source.csv').write_text('original\n')
    context = await native.build()
    assert context.completion_contract.required_artifacts == ()
    assert len(context.completion_contract.immutable_inputs) == 1
    assert await finish(native, context) is CompletionStatus.SATISFIED
    (native.workspace / 'source.csv').write_text('changed\n')
    assert await finish(native, context) is not CompletionStatus.SATISFIED


@pytest.mark.asyncio
async def test_goal_reentry_is_idempotent_and_never_rebaselines_originals(native, monkeypatch):
    monkeypatch.setenv('AWORLD_VALIDATION_COMMANDS_JSON', json.dumps([{'command_id':'caller','argv':[sys.executable,'-c','pass']}]))
    native.goal = {'active':True,'workspace_id':'goal-public-inputs','objective':REQUEST,'verification_commands':['true']}
    (native.workspace / 'source.csv').write_text('original\n')
    first = await native.build()
    delivery = get_task_workspace(first).delivery
    snapshot = get_task_workspace(first).store.current_input_snapshot_id
    configured = first.completion_contract
    assert prepare_task_workspace(first, REQUEST, native.workspace) == delivery
    assert configure_runtime_completion(first, request=REQUEST, workspace_path=native.workspace) is configured
    (native.workspace / 'source.csv').write_text('changed\n')
    second = await native.build('Continue.', task_id='second')
    assert get_task_workspace(second).delivery == delivery
    assert get_task_workspace(second).store.current_input_snapshot_id == snapshot
    (native.workspace / 'result.json').write_text('{}')
    assert await finish(native, second) is not CompletionStatus.SATISFIED


@pytest.mark.asyncio
async def test_real_caller_command_keeps_output_authority_and_executes_once_per_resolution(native):
    counter = native.workspace / 'counter.txt'
    command = ValidationCommand('custom-caller', (sys.executable, '-c',
        'import sys; from pathlib import Path; p=Path(sys.argv[1]); p.write_text((p.read_text() if p.exists() else "")+"x")',
        str(counter)))
    caller = CompletionContract((), (), (command,), None, ('agent_final_response',))
    async def resolver(context, contract):
        assert contract is caller
        await resolve_runtime_completion_evidence(context, contract)
    native.install = lambda c: c.configure_completion_contract(caller, mode=CompletionMode.ENFORCE, evidence_resolver=resolver)
    (native.workspace / 'source.csv').write_text('original\n')
    context = await native.build()
    assert context.completion_contract.required_artifacts == ()
    assert context.completion_contract.immutable_inputs
    assert await finish(native, context) is CompletionStatus.SATISFIED
    assert counter.read_text() == 'x'
    configure_goal_completion(context, verification_commands=['true'], workspace_path=native.workspace)
    configure_runtime_completion(context, request=REQUEST, workspace_path=native.workspace)
    assert await finish(native, context) is CompletionStatus.SATISFIED
    assert counter.read_text() == 'xx'


@pytest.mark.asyncio
async def test_native_and_goal_extensions_cannot_overwrite_caller_artifact_rejection(native):
    output = native.workspace / 'result.json'
    output.write_text('{}')
    caller = CompletionContract((ArtifactRequirement('caller-output', str(output)),), (), (), None, ('agent_final_response',))
    calls = 0
    async def resolver(context, configured):
        nonlocal calls
        assert configured is caller
        calls += 1
        context.record_completion_artifact(ArtifactEvidence('caller-output', False, None, datetime.now(timezone.utc)))
    native.install = lambda c: c.configure_completion_contract(caller, mode=CompletionMode.ENFORCE, evidence_resolver=resolver)
    context = await native.build('Write result.json.')
    assert await finish(native, context) is not CompletionStatus.SATISFIED
    assert calls == 1
    configure_goal_completion(context, verification_commands=['true'], workspace_path=native.workspace)
    assert await finish(native, context) is not CompletionStatus.SATISFIED
    assert calls == 2


@pytest.mark.asyncio
async def test_caller_command_id_cannot_be_overwritten_by_native_pass(native, monkeypatch):
    delivery = derive_delivery_contract('Write result.json.', workspace_path=native.workspace)
    identifier = next(c['id'] for c in delivery['outputs'][0]['checks'] if c['kind'] == 'json')
    monkeypatch.setenv('AWORLD_VALIDATION_COMMANDS_JSON', json.dumps([{
        'command_id':identifier, 'argv':[sys.executable,'-c','raise SystemExit(9)'],
    }]))
    (native.workspace / 'result.json').write_text('{}')
    with pytest.raises(ValueError, match='check IDs conflict'):
        await native.build('Write result.json.')


@pytest.mark.asyncio
@pytest.mark.parametrize('reserved', [False, True])
async def test_caller_self_check_identity_cannot_alias_framework_checks(native, reserved):
    from aworld.core.task_workspace.session import _digest
    output = native.workspace / 'result.json'
    output.write_text('{}')
    identifier = 'workbench.delivery' if reserved else 'caller-' + _digest('out')[:32]
    caller = CompletionContract((ArtifactRequirement('out', str(output)),), (), (), None, (),
                                required_self_check_ids=(identifier,))
    native.install = lambda c: c.configure_completion_contract(caller, mode=CompletionMode.ENFORCE)
    with pytest.raises(ValueError, match='check IDs conflict'):
        await native.build('Write result.json.')


@pytest.mark.asyncio
async def test_goal_checks_avoid_public_native_check_ids(native):
    native.explicit = {'checks':[{'id':'goal-verify-1','kind':'json','path':'result.json'}]}
    (native.workspace / 'result.json').write_text('{}')
    context = await native.build('Write result.json.')
    contract = configure_goal_completion(context, verification_commands=['exit 9'], workspace_path=native.workspace)
    assert contract.validation_commands[0].command_id != 'goal-verify-1'
    assert await finish(native, context) is not CompletionStatus.SATISFIED


@pytest.mark.asyncio
@pytest.mark.parametrize('caller_mode', ['env', 'env_goal', 'custom_goal'])
async def test_later_native_checks_cannot_overwrite_a_caller_command_failure(native, monkeypatch, caller_mode):
    if caller_mode == 'custom_goal':
        caller = CompletionContract((), (), (ValidationCommand('caller-fail', (sys.executable, '-c', 'raise SystemExit(9)')),), None, ())
        async def resolver(context, configured):
            assert configured is caller
            await resolve_runtime_completion_evidence(context, configured)
        native.install = lambda c: c.configure_completion_contract(caller, mode=CompletionMode.ENFORCE, evidence_resolver=resolver)
    else:
        monkeypatch.setenv('AWORLD_VALIDATION_COMMANDS_JSON', json.dumps([{
            'command_id':'caller-fail','argv':[sys.executable,'-c','raise SystemExit(9)'],
        }]))
    (native.workspace / 'result.json').write_text('{}')
    context = await native.build('Write result.json.')
    if caller_mode != 'env':
        configure_goal_completion(context, verification_commands=['true'], workspace_path=native.workspace)
    await get_task_workspace(context).execute('revise_checks', {
        'checks':[{'id':'caller-fail','kind':'json','path':'result.json'}], 'reason':'Add a JSON self-check',
    })
    assert await finish(native, context) is not CompletionStatus.SATISFIED
    assert 'check IDs conflict' in context.context_info['delivery_validation']['error']
    assert next(c for c in context._completion_self_checks if c.command_id == 'caller-fail').exit_code == 9


@pytest.mark.asyncio
async def test_explicit_empty_outputs_do_not_activate_later_agent_self_checks(native, monkeypatch):
    monkeypatch.setenv('AWORLD_REQUIRED_ARTIFACTS_JSON', '[]')
    context = await native.build('Explain CSV headers.')
    assert context.completion_contract.required_artifacts == ()
    assert await finish(native, context) is CompletionStatus.SATISFIED
    (native.workspace / 'later.json').write_text('invalid JSON')
    await get_task_workspace(context).execute('revise_checks', {
        'checks':[{'id':'later-json','kind':'json','path':'later.json'}], 'reason':'Validate the added output',
    })
    assert await finish(native, context) is CompletionStatus.SATISFIED
