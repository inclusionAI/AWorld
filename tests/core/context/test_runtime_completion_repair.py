"""Generic explicit completion and long-running repair regressions."""

from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from aworld.agents.llm_agent import Agent, LlmOutputParser, _ValidationRepairContinuation
from aworld.config.conf import AgentConfig
from aworld.core.common import ActionModel, Observation
from aworld.core.context.base import Context
from aworld.core.context.compiler import (
    CompletionContract,
    CompletionMode,
    CompletionStatus,
    SelfCheckEvidence,
    ValidationCommand,
)
from aworld.core.context.execution_state import get_execution_state
from aworld.core.context.session import Session
from aworld.core.event.base import Constants, Message
from aworld.core.exceptions import AWorldRuntimeException
from aworld.core.task import Task
from aworld.models.model_response import ModelResponse
from aworld_cli.core.runtime_completion import (
    build_runtime_completion_contract,
    configure_goal_completion,
    configure_runtime_completion,
    resolve_runtime_completion_evidence,
)
from aworld_cli.executors.local import LocalAgentExecutor


def _agent(context, **kwargs):
    agent = Agent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="offline",
            llm_api_key="offline",
        ),
        **kwargs,
    )
    agent._llm = object()
    agent.context = context
    return agent


@pytest.mark.asyncio
async def test_explicit_enforce_mode_blocks_missing_artifact(
    monkeypatch, tmp_path: Path
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AWORLD_COMPLETION_MODE", "enforce")
    monkeypatch.setenv("AWORLD_REQUIRED_ARTIFACTS_JSON", '["result.json"]')
    executor = object.__new__(LocalAgentExecutor)
    executor._base_runtime = None
    executor.session_id = "completion"
    executor.swarm = SimpleNamespace(agents={})
    executor.context_config = SimpleNamespace()
    executor._execute_hooks = AsyncMock(return_value=None)
    executor._create_workspace = AsyncMock(return_value=None)
    executor._resolve_swarm_skills = Mock(return_value=())
    monkeypatch.setattr(executor, "_goal_session_state", lambda: {"active": False})

    async def from_input(task_input, **kwargs):
        context = Context(
            task_id=task_input.task_id,
            session=Session(session_id="completion"),
        )
        context.user_id = "user"
        context.get_config = lambda: SimpleNamespace(debug_mode=False)
        context.init_swarm_state = AsyncMock()
        return context

    monkeypatch.setattr(
        "aworld_cli.executors.local.ApplicationContext.from_input", from_input
    )
    task = await executor._build_task("Write result.json.")
    task.context.set_task(task)
    assert task.context.completion_mode is CompletionMode.ENFORCE
    assert task.context.completion_contract.max_repairs is None
    assert task.context.completion_contract.required_artifacts

    agent = _agent(task.context)
    response = ModelResponse(
        id="final", model="offline", content="All done.", finish_reason="stop"
    )
    parsed = await LlmOutputParser().parse(response, agent_id=agent.id())
    assert await agent._completion_feedback_if_unsatisfied(
        context=task.context, final_response_text="All done."
    )
    assert not agent.is_agent_finished(response, parsed)

    (tmp_path / "result.json").write_text('{"delivered":true}')
    assert (
        await agent._completion_feedback_if_unsatisfied(
            context=task.context, final_response_text="All done."
        )
        is None
    )
    assert agent.is_agent_finished(response, parsed)


@pytest.mark.asyncio
async def test_existing_caller_contract_and_evidence_are_not_replaced(tmp_path: Path):
    context = Context(task_id="caller")
    check = ValidationCommand(
        "caller-check", (sys.executable, "-c", "raise SystemExit(4)")
    )
    contract = CompletionContract((), (), (check,), None, (), max_repairs=0)
    resolver = AsyncMock()
    context.configure_completion_contract(
        contract,
        mode=CompletionMode.ENFORCE,
        evidence_resolver=resolver,
    )
    evidence = SelfCheckEvidence(
        "caller-check", 4, None, __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        )
    )
    context.record_completion_self_check(evidence)

    configured = configure_runtime_completion(
        context,
        request="Write an unrelated file.",
        workspace_path=tmp_path,
    )

    assert configured is contract
    assert context.completion_contract is contract
    assert evidence in context._completion_self_checks
    await context.resolve_completion_evidence()
    resolver.assert_awaited_once_with(context, contract)


def test_runtime_defaults_preserve_unbounded_and_caller_freshness(tmp_path: Path):
    runtime_contract = build_runtime_completion_contract(
        "", workspace_path=tmp_path, explicit_paths=["out.json"]
    )
    assert runtime_contract is not None
    assert runtime_contract.max_evidence_age_seconds is None

    goal_context = Context(task_id="goal-freshness")
    goal_contract = configure_goal_completion(
        goal_context,
        verification_commands=["true"],
        workspace_path=tmp_path,
    )
    assert goal_contract.max_evidence_age_seconds is None

    caller_context = Context(task_id="caller-freshness")
    caller_contract = CompletionContract((), (), (), 120, (), max_repairs=3)
    caller_context.configure_completion_contract(
        caller_contract, mode=CompletionMode.ENFORCE
    )
    extended = configure_goal_completion(
        caller_context,
        verification_commands=["true"],
        workspace_path=tmp_path,
    )
    assert extended.max_evidence_age_seconds == 120
    assert extended.max_repairs == 3


@pytest.mark.asyncio
async def test_repair_trampoline_can_continue_beyond_one_without_recursion():
    context = Context(task_id="repair")
    context.set_task(Task(id="repair", input="finish"))
    agent = _agent(context, max_loop_steps=0)
    count = 0

    async def attempt(observation, **kwargs):
        nonlocal count
        count += 1
        if count < 1200:
            return _ValidationRepairContinuation(
                Observation(content="fix next issue"), {}
            )
        return [ActionModel(policy_info="done")]

    agent._async_policy_once = attempt
    message = Message(category=Constants.AGENT, headers={"context": context})
    result = await agent.async_policy(Observation(content="start"), message=message)
    assert result[0].policy_info == "done"
    assert count == 1200


@pytest.mark.asyncio
async def test_repair_trampoline_respects_existing_attempt_limit():
    context = Context(task_id="attempt-limit")
    context.set_task(Task(id="attempt-limit", input="finish"))
    agent = _agent(context, max_loop_steps=2)
    context.update_agent_step(agent.id())

    async def attempt(observation, **kwargs):
        return _ValidationRepairContinuation(
            Observation(content="still missing output"), {}
        )

    agent._async_policy_once = attempt
    message = Message(category=Constants.AGENT, headers={"context": context})
    await agent.async_policy(Observation(content="start"), message=message)
    assert get_execution_state(context)["status"] == "budget_exhausted"


@pytest.mark.asyncio
async def test_configured_three_repairs_stop_after_third_retry(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("AWORLD_COMPLETION_MAX_REPAIRS", "3")
    contract = build_runtime_completion_contract(
        "", workspace_path=tmp_path, explicit_paths=["out.json"]
    )
    assert contract is not None and contract.max_repairs == 3
    context = Context(task_id="three-repairs")
    context.set_task(Task(id="three-repairs", input="finish"))
    context.configure_completion_contract(contract, mode=CompletionMode.ENFORCE)
    agent = _agent(context, max_loop_steps=0)
    message = Message(category=Constants.AGENT, headers={"context": context})
    observation = Observation(content="still missing output")

    for expected_count in (1, 2, 3):
        result = await agent._retry_for_result_validation(
            validation_feedback="required artifact missing",
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
        validation_feedback="required artifact missing",
        observation=observation,
        info={},
        message=message,
        kwargs={},
        iterative=True,
    )
    assert result[0].policy_info.endswith("not claiming success.")
    assert get_execution_state(context)["reason"] == "validation_repair_exhausted"


@pytest.mark.asyncio
async def test_repair_trampoline_records_empty_followup_as_incomplete():
    context = Context(task_id="empty-repair")
    context.set_task(Task(id="empty-repair", input="finish"))
    agent = _agent(context, max_loop_steps=0)
    calls = 0

    async def attempt(observation, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _ValidationRepairContinuation(Observation(content="fix output"), {})
        raise AWorldRuntimeException("LLM returned empty or invalid response: {}")

    agent._async_policy_once = attempt
    message = Message(category=Constants.AGENT, headers={"context": context})
    result = await agent.async_policy(Observation(content="start"), message=message)
    assert get_execution_state(context)["reason"] == "validation_repair_unavailable"
    assert "not claiming success" in result[0].policy_info


@pytest.mark.asyncio
async def test_agent_caller_contract_survives_goal_extension(tmp_path: Path):
    context = Context(task_id="agent-caller")
    contract = CompletionContract(
        (),
        (),
        (ValidationCommand("caller", (sys.executable, "-c", "pass")),),
        90,
        (),
        max_repairs=4,
    )
    resolver = AsyncMock()
    agent = _agent(context)
    agent.configure_completion_contract(
        contract,
        mode=CompletionMode.ENFORCE,
        evidence_resolver=resolver,
    )
    agent._install_runtime_completion_contract(context)
    assert configure_runtime_completion(
        context, request="Write unrelated.json.", workspace_path=tmp_path
    ) is contract
    goal_extended = configure_goal_completion(
        context,
        verification_commands=["true"],
        workspace_path=tmp_path,
    )
    agent._install_runtime_completion_contract(context)
    assert context.completion_contract is goal_extended
    assert contract.validation_commands[0] in goal_extended.validation_commands
    await context.resolve_completion_evidence()
    resolver.assert_awaited_once_with(context, contract)


@pytest.mark.asyncio
async def test_goal_extension_executes_custom_caller_checks_once(tmp_path: Path):
    counter = tmp_path / "caller-check-count.txt"
    command = ValidationCommand(
        "caller-once",
        (
            sys.executable,
            "-c",
            "from pathlib import Path; import sys; p=Path(sys.argv[1]); "
            'p.write_text(p.read_text()+"x" if p.exists() else "x")',
            str(counter),
        ),
    )
    original = CompletionContract((), (), (command,), None, ())
    context = Context(task_id="caller-execution-count")

    async def caller_resolver(target, contract):
        await resolve_runtime_completion_evidence(target, contract)

    context.configure_completion_contract(
        original,
        mode=CompletionMode.ENFORCE,
        evidence_resolver=caller_resolver,
    )
    configure_goal_completion(
        context,
        verification_commands=["true"],
        workspace_path=tmp_path,
    )
    await context.resolve_completion_evidence()
    assert counter.read_text() == "x"


@pytest.mark.asyncio
async def test_default_question_does_not_gain_an_implicit_gate(tmp_path: Path):
    context = Context(task_id="model-owned")
    assert (
        configure_runtime_completion(
            context,
            request="Explain how CSV headers work.",
            workspace_path=tmp_path,
        )
        is None
    )
    assert context.completion_mode is CompletionMode.OFF
    assert (
        await _agent(context)._completion_feedback_if_unsatisfied(
            context=context,
            final_response_text="A header names columns.",
        )
        is None
    )
