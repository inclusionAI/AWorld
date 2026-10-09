from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

import aworld.agents.llm_agent as llm_agent_module
from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig
from aworld.core.common import ActionModel, Observation
from aworld.core.context.base import Context
from aworld.core.context.execution_state import get_execution_state
from aworld.core.context.generation_budget import (
    GenerationBudgetController,
    GenerationBudgetExceeded,
    GenerationBudgetPolicy,
    GenerationStopReason,
)
from aworld.core.context.session import Session
from aworld.core.event.base import Constants, Message
from aworld.core.exceptions import AWorldTransientModelError
from aworld.core.execution_protocol import ExecutionProtocolStore
from aworld.core.task import Task
from aworld.models.model_response import Function, ModelResponse, ToolCall
from aworld.models.reasoning_policy import OPENAI_REASONING_CAPABILITY
from aworld.runners.execution_protocol import (
    build_execution_protocol_telemetry,
    configure_execution_protocol,
    record_model_execution_profile,
    record_tool_protocol_event,
)


class _ToolAgent(Agent):
    async def _filter_tools(self, context=None):
        return [
            {
                "type": "function",
                "function": {
                    "name": "workspace__write",
                    "description": "write a workspace artifact",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]


class _NoToolAgent(Agent):
    async def _filter_tools(self, context=None):
        return None


def _agent(
    *,
    policy: GenerationBudgetPolicy,
    with_tools: bool = True,
    attempts: int = 1,
) -> Agent:
    cls = _ToolAgent if with_tools else _NoToolAgent
    agent = cls(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
        ),
        generation_budget_policy=policy,
        llm_max_attempts=attempts,
        llm_retry_delay=0,
    )
    # Provider helpers are monkeypatched below. Avoid constructing an SDK client.
    agent._llm = object()
    return agent


def _message(task_id: str = "generation-budget") -> Message:
    context = Context(task_id=task_id, session=Session(session_id=f"{task_id}-s"))
    context.set_task(Task(id=task_id, name=task_id, input="test request"))
    return Message(
        category=Constants.AGENT,
        sender="user",
        receiver="Aworld",
        headers={"context": context},
    )


@pytest.mark.asyncio
async def test_explicit_tool_free_request_reaches_provider_without_filtering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _ToolAgent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
        ),
        llm_max_attempts=1,
    )
    filter_calls = 0
    provider_tools = object()

    async def count_filter(context=None):
        nonlocal filter_calls
        filter_calls += 1
        return await _ToolAgent._filter_tools(agent, context)

    async def capture_provider(*args, **kwargs):
        nonlocal provider_tools
        provider_tools = kwargs.get("tools")
        return ModelResponse(
            id="tool-free",
            model="fake-model",
            content="final",
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        )

    monkeypatch.setattr(agent, "_filter_tools", count_filter)
    monkeypatch.setattr(llm_agent_module, "acall_llm_model", capture_provider)
    message = _message("explicit-tool-free")

    response = await agent.invoke_model(
        messages=[{"role": "user", "content": "finalize"}],
        message=message,
        prepared_tools=None,
        stream=False,
    )

    assert response.content == "final"
    assert filter_calls == 0
    assert provider_tools is None


@pytest.mark.asyncio
async def test_missing_prepared_tools_loads_agent_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _ToolAgent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
        ),
        llm_max_attempts=1,
    )
    filter_calls = 0
    provider_tools = None

    async def count_filter(context=None):
        nonlocal filter_calls
        filter_calls += 1
        return await _ToolAgent._filter_tools(agent, context)

    async def capture_provider(*args, **kwargs):
        nonlocal provider_tools
        provider_tools = kwargs.get("tools")
        return ModelResponse(
            id="catalog-loaded",
            model="fake-model",
            content="continue",
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        )

    monkeypatch.setattr(agent, "_filter_tools", count_filter)
    monkeypatch.setattr(llm_agent_module, "acall_llm_model", capture_provider)

    response = await agent.invoke_model(
        messages=[{"role": "user", "content": "continue"}],
        message=_message("missing-prepared-tools"),
        stream=False,
    )

    assert response.content == "continue"
    assert filter_calls == 1
    assert provider_tools[0]["function"]["name"] == "workspace__write"


def test_default_agent_uses_max_steps_without_generation_deadlines() -> None:
    agent = _ToolAgent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
        ),
    )

    policy = agent._resolve_generation_budget_policy()

    assert policy.total_timeout_seconds is None
    assert policy.stream_idle_timeout_seconds is None
    assert policy.active_tool_free_timeout_seconds is None
    assert policy.action_repair_timeout_seconds is None
    assert policy.action_repair_enabled is False


def _long_running_generation_agent(
    *,
    armed: bool,
    generation_policy: GenerationBudgetPolicy | None = None,
    context_compiler: dict | None = None,
) -> Agent:
    config_kwargs = {
        "llm_provider": "openai",
        "llm_model_name": "fake-model",
        "llm_api_key": "fake-key",
    }
    if context_compiler is not None:
        config_kwargs["context_compiler"] = context_compiler
    agent = _ToolAgent(
        name="Aworld",
        conf=AgentConfig(**config_kwargs),
        generation_budget_policy=generation_policy,
    )
    agent.skill_configs = {"long-running-agent": {"active": True}}
    context = Context(task_id="long-running-generation-budget")
    context.set_task(
        Task(
            id="long-running-generation-budget",
            name="long-running-generation-budget",
            input="test request",
        )
    )
    agent.context = context
    if armed:
        protocol_policy = agent._resolve_execution_protocol_policy()
        store = ExecutionProtocolStore(context, agent.id(), protocol_policy)
        store.save(replace(store.load(), long_horizon_armed=True))
    return agent


def test_active_long_running_skill_keeps_generation_watchdog_off_before_arming() -> (
    None
):
    policy = _long_running_generation_agent(
        armed=False
    )._resolve_generation_budget_policy()

    assert policy.total_timeout_seconds is None
    assert policy.stream_idle_timeout_seconds is None
    assert policy.active_tool_free_timeout_seconds is None
    assert policy.action_repair_timeout_seconds is None
    assert policy.action_repair_enabled is False


def test_armed_protocol_keeps_watchdog_off_when_skill_is_inactive() -> None:
    agent = _long_running_generation_agent(armed=True)
    agent.skill_configs["long-running-agent"]["active"] = False

    policy = agent._resolve_generation_budget_policy()

    assert policy.total_timeout_seconds is None
    assert policy.stream_idle_timeout_seconds is None
    assert policy.active_tool_free_timeout_seconds is None
    assert policy.action_repair_timeout_seconds is None
    assert policy.action_repair_enabled is False


def test_armed_long_running_skill_adds_no_framework_idle_ceiling() -> None:
    policy = _long_running_generation_agent(
        armed=True
    )._resolve_generation_budget_policy()

    assert policy.total_timeout_seconds is None
    assert policy.stream_idle_timeout_seconds is None
    assert policy.active_tool_free_timeout_seconds is None
    assert policy.action_repair_timeout_seconds is None
    assert policy.action_repair_enabled is False


def test_armed_long_running_skill_uses_remaining_task_budget_without_fixed_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _long_running_generation_agent(armed=True)
    context = agent.context
    protocol = agent._resolve_execution_protocol_policy(context)
    monkeypatch.setattr(context.get_task(), "remaining_seconds", lambda: 1200.0)

    policy = agent._resolve_generation_budget_policy(context)

    assert policy.total_timeout_seconds == pytest.approx(
        1200.0 - protocol.finalization_reserve_seconds
    )
    assert policy.total_timeout_seconds > 360.0
    assert policy.stream_idle_timeout_seconds is None
    assert policy.active_tool_free_timeout_seconds is None
    assert policy.action_repair_enabled is False


def test_pre_generation_check_enters_existing_reserve_before_long_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _long_running_generation_agent(armed=True)
    context = agent.context
    policy = agent._resolve_execution_protocol_policy(context)
    monkeypatch.setattr(
        context.get_task(),
        "remaining_seconds",
        lambda: policy.finalization_reserve_seconds - 0.01,
    )

    assert agent._pre_generation_caller_reserve_reached(context) is True
    assert context.context_info["pre_generation_reserve_metrics"]["entry_count"] == 1


def test_pre_generation_check_keeps_solve_window_open_before_reserve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _long_running_generation_agent(armed=True)
    context = agent.context
    policy = agent._resolve_execution_protocol_policy(context)
    monkeypatch.setattr(
        context.get_task(),
        "remaining_seconds",
        lambda: policy.finalization_reserve_seconds + 1,
    )

    assert agent._pre_generation_caller_reserve_reached(context) is False
    assert "pre_generation_reserve_metrics" not in context.context_info


@pytest.mark.parametrize("armed", [False, True])
def test_public_delivery_reserve_requests_typed_model_decision_only_when_armed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    armed: bool,
) -> None:
    agent = _long_running_generation_agent(armed=armed)
    context = agent.context
    context.get_task().timeout = 1800
    output = tmp_path / "result.json"
    context.context_info["public_deliverable_contract"] = {
        "schema_version": "aworld.public-deliverables/v1",
        "authority": "public_task_advisory",
        "source": "public_task_text",
        "artifacts": [
            {
                "deliverable_id": "public-output-1",
                "path": str(output),
                "display_path": "result.json",
                "kind": "file",
                "authority": "public_task_advisory",
            }
        ],
    }
    policy = agent._resolve_execution_protocol_policy(context)
    configure_execution_protocol(context, agent.id(), policy)
    if armed:
        assert record_model_execution_profile(
            context,
            agent.id(),
            {
                "horizon": "long",
                "confidence": 0.9,
                "milestone_count": 3,
                "expected_tool_actions": 8,
                "verification_required": True,
            },
        ) is not None
    monkeypatch.setattr(
        context.get_task(),
        "remaining_seconds",
        lambda: policy.finalization_reserve_seconds + 179,
    )

    from aworld.runners.execution_protocol import (
        execution_protocol_model_decision_boundary,
        record_pre_generation_delivery_decision,
    )

    transition = record_pre_generation_delivery_decision(
        context, agent.id(), policy=policy
    )
    if armed:
        assert transition is not None
        assert transition.decision.reason.value == "candidate_decision_reserve"
        assert execution_protocol_model_decision_boundary(context, agent.id()) == "replan"
    else:
        assert transition is None
        assert execution_protocol_model_decision_boundary(context, agent.id()) == "initial"

    output.write_text("{}")
    if not armed:
        assert record_pre_generation_delivery_decision(
            context, agent.id(), policy=policy
        ) is None


def test_public_deliverable_feedback_rejects_whole_file_placeholder(
    tmp_path,
) -> None:
    agent = _long_running_generation_agent(armed=False)
    context = agent.context
    output = tmp_path / "out.txt"
    context.context_info["public_deliverable_contract"] = {
        "schema_version": "aworld.public-deliverables/v1",
        "authority": "public_task_advisory",
        "source": "public_task_text",
        "artifacts": [
            {
                "deliverable_id": "public-output-1",
                "path": str(output),
                "display_path": "out.txt",
                "kind": "file",
                "authority": "public_task_advisory",
            }
        ],
    }

    output.write_text("TBD\n", encoding="utf-8")
    feedback = agent._public_deliverable_feedback_if_unsatisfied(context)

    assert feedback is not None
    assert "missing, blank, or still placeholders" in feedback
    observation = context.context_info["public_deliverable_observations"][
        "artifacts"
    ][0]
    assert observation["exists"] is True
    assert observation["candidate_eligible"] is False
    assert observation["rejection_reason"] == "placeholder"

    output.write_text("flag{resolved}", encoding="utf-8")
    assert agent._public_deliverable_feedback_if_unsatisfied(context) is None


def test_explicit_generation_compiler_configuration_wins_after_arming() -> None:
    policy = _long_running_generation_agent(
        armed=True,
        context_compiler={"generation_total_timeout_seconds": 17},
    )._resolve_generation_budget_policy()

    assert policy.total_timeout_seconds == 17
    assert policy.stream_idle_timeout_seconds is None
    assert policy.active_tool_free_timeout_seconds is None
    assert policy.action_repair_timeout_seconds is None
    assert policy.action_repair_enabled is False


def test_explicit_generation_policy_wins_after_long_running_protocol_arms() -> None:
    explicit = GenerationBudgetPolicy(
        total_timeout_seconds=31,
        stream_idle_timeout_seconds=7,
        active_tool_free_timeout_seconds=11,
        action_repair_timeout_seconds=None,
        action_repair_enabled=False,
    )
    resolved = _long_running_generation_agent(
        armed=True,
        generation_policy=explicit,
    )._resolve_generation_budget_policy()

    assert resolved is explicit


def test_explicit_disabled_generation_mode_suppresses_automatic_watchdog() -> None:
    agent = _long_running_generation_agent(armed=True)
    agent._generation_budget_explicit_fields = frozenset({"generation_budget_mode"})

    policy = agent._resolve_generation_budget_policy()
    finalization_policy = agent._resolve_generation_budget_policy(
        agent.context,
        finalization_turn=True,
    )

    assert policy.total_timeout_seconds is None
    assert policy.stream_idle_timeout_seconds is None
    assert finalization_policy.total_timeout_seconds is None
    assert finalization_policy.stream_idle_timeout_seconds is None
    metrics = agent.context.context_info[f"generation_budget_policy:{agent.id()}"]
    assert metrics["policy_source"] == "explicit_config"


def test_automatic_watchdog_reserves_task_time_for_finalization() -> None:
    agent = _long_running_generation_agent(armed=True)
    agent.context.set_task(
        Task(
            id="long-running-generation-budget",
            input="test request",
            timeout=65,
        )
    )

    policy = agent._resolve_generation_budget_policy(agent.context)

    # The protocol derives a 15% finalization window from the bounded Task
    # instead of applying the fixed 60-second reserve to short tasks.
    assert 55.0 < policy.total_timeout_seconds <= 55.25
    assert policy.stream_idle_timeout_seconds is None
    assert policy.active_tool_free_timeout_seconds is None
    assert policy.action_repair_enabled is False
    metrics = agent.context.context_info[f"generation_budget_policy:{agent.id()}"]
    assert metrics["policy_source"] == "long_horizon_auto"


def test_finalization_turn_uses_agent_window_before_external_reserve() -> None:
    agent = _long_running_generation_agent(armed=True)
    task = Task(
        id="long-running-finalization-budget",
        input="test request",
        timeout=360,
        completion_reserve_seconds=36,
    )
    # Model the instant at which P=81 seconds remains: the ordinary turn must
    # stop, while finalization retains F=45 seconds before external reserve E.
    task.remaining_seconds = lambda: 81.0
    agent.context.set_task(task)

    normal = agent._resolve_generation_budget_policy(agent.context)
    finalization = agent._resolve_generation_budget_policy(
        agent.context,
        finalization_turn=True,
    )
    clock = SimpleNamespace(now=0.0)
    normal_controller = GenerationBudgetController(
        normal,
        clock=lambda: clock.now,
    )
    finalization_controller = GenerationBudgetController(
        finalization,
        clock=lambda: clock.now,
    )

    assert normal.total_timeout_seconds == pytest.approx(0.1)
    assert finalization.total_timeout_seconds == pytest.approx(45.0)
    clock.now = 1.0
    assert normal_controller.remaining_seconds(streaming=False) == 0.0
    assert finalization_controller.remaining_seconds(streaming=False) == 44.0


def _generation_timeout(
    reason: GenerationStopReason = GenerationStopReason.CALL_DEADLINE_EXCEEDED,
) -> GenerationBudgetExceeded:
    controller = GenerationBudgetController(
        GenerationBudgetPolicy(
            total_timeout_seconds=1,
            stream_idle_timeout_seconds=None,
            active_tool_free_timeout_seconds=None,
            action_repair_timeout_seconds=None,
            action_repair_enabled=False,
        )
    )
    return GenerationBudgetExceeded(controller.receipt(reason))


@pytest.mark.asyncio
async def test_exhausted_transient_provider_retries_resume_with_normal_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for the DNA canary: transport failure is not finalization."""

    class ProviderStatusError(Exception):
        status_code = 502

    # A real initial decision boundary precedes protocol arming. Transport
    # fail-open must still permit one ordinary recovery turn within the caller
    # deadline instead of requiring an impossible pre-armed state.
    agent = _long_running_generation_agent(armed=False)
    agent._llm = object()
    agent.llm_max_attempts = 3
    agent.llm_retry_delay = 0
    agent.context._session = Session(session_id="transient-recovery-s")
    task = Task(
        id="long-running-generation-budget",
        name="long-running-generation-budget",
        input="create /app/primers.fasta",
        timeout=600,
    )
    agent.context.set_task(task)
    provider_calls: list[dict] = []

    async def provider(*args, **kwargs):
        provider_calls.append(kwargs)
        if len(provider_calls) <= agent.llm_max_attempts:
            raise ProviderStatusError("provider response body was interrupted")
        return ModelResponse(
            id="recovered-tool-turn",
            model="fake-model",
            content="continue material work",
            finish_reason="tool_calls",
            tool_calls=[
                ToolCall(
                    id="write-primers",
                    function=Function(
                        name="workspace__write",
                        arguments=(
                            '{"path":"/app/primers.fasta",'
                            '"content":">input_fwd\\nACGT"}'
                        ),
                    ),
                )
            ],
        )

    async def no_memory(*args, **kwargs):
        return None

    async def no_output(*args, **kwargs):
        return None

    async def simple_input(observation, info=None, message=None, **kwargs):
        return [{"role": "user", "content": str(observation.content or "")}]

    monkeypatch.setattr(llm_agent_module, "acall_llm_model", provider)
    monkeypatch.setattr(agent, "build_llm_input", simple_input)
    monkeypatch.setattr(agent, "_add_message_to_memory", no_memory)
    monkeypatch.setattr(agent, "send_agent_response_output", no_output)
    message = Message(
        category=Constants.AGENT,
        payload=Observation(
            content="prior tool work inspected all input sequences",
            observer="terminal",
            from_agent_name="terminal",
            to_agent_name=agent.id(),
        ),
        receiver=agent.id(),
        headers={"context": agent.context},
    )

    actions = await agent.async_policy(message.payload, message=message, stream=False)

    assert len(provider_calls) == 4
    assert all(call.get("tools") for call in provider_calls)
    assert all(
        call["tools"][0]["function"]["name"] == "aworld__execution_decision"
        for call in provider_calls[:3]
    )
    assert provider_calls[-1]["tools"][0]["function"]["name"] == "workspace__write"
    assert any(
        "transient model-provider interruption" in str(item.get("content", ""))
        for item in provider_calls[-1]["messages"]
    )
    assert len(actions) == 1
    assert actions[0].tool_name == "workspace"
    assert actions[0].action_name == "write"
    assert actions[0].params["path"] == "/app/primers.fasta"
    metrics = agent.context.context_info["transient_model_recovery_metrics"]
    assert metrics["attempt_count"] == 1
    assert metrics["outcome:recovered"] == 1
    assert metrics["consecutive_failure_count"] == 0
    protocol = build_execution_protocol_telemetry(agent.context, agent.id())
    assert protocol["initial_decision_status"] == "fail_open_unknown"
    assert protocol["initial_decision_attempt_count"] == 0
    assert protocol["initial_decision_unavailable_count"] == 1
    assert protocol["initial_decision_fail_open_reason"] == "provider_unavailable"


@pytest.mark.asyncio
async def test_unarmed_replan_transport_failure_resumes_with_normal_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ProviderStatusError(Exception):
        status_code = 429

    agent = _long_running_generation_agent(armed=False)
    agent._llm = object()
    agent.llm_max_attempts = 3
    agent.llm_retry_delay = 0
    agent.context._session = Session(session_id="replan-transient-recovery-s")
    task = Task(
        id="long-running-generation-budget",
        name="long-running-generation-budget",
        input="create /app/primers.fasta",
        timeout=600,
    )
    agent.context.set_task(task)
    policy = agent._resolve_execution_protocol_policy(agent.context)
    configure_execution_protocol(agent.context, agent.id(), policy)
    record_model_execution_profile(
        agent.context,
        agent.id(),
        {
            "horizon": "short",
            "confidence": 1.0,
            "milestone_count": 1,
            "expected_tool_actions": 1,
            "verification_required": True,
        },
    )
    transition = record_tool_protocol_event(
        agent.context,
        agent.id(),
        {
            "repetition_count": policy.repetition_threshold,
            "current_agent_step": 2,
        },
    )
    assert transition is not None
    state = ExecutionProtocolStore(agent.context, agent.id(), policy).load()
    assert state.long_horizon_armed is False
    assert state.decision_checkpoint_pending is True

    provider_calls: list[dict] = []

    async def provider(*args, **kwargs):
        provider_calls.append(kwargs)
        if len(provider_calls) <= agent.llm_max_attempts:
            raise ProviderStatusError("rate limited")
        return ModelResponse(
            id="recovered-replan-tool-turn",
            model="fake-model",
            content="resume material work",
            finish_reason="tool_calls",
            tool_calls=[
                ToolCall(
                    id="write-after-replan",
                    function=Function(
                        name="workspace__write",
                        arguments=(
                            '{"path":"/app/primers.fasta",'
                            '"content":">input_fwd\\nACGT"}'
                        ),
                    ),
                )
            ],
        )

    async def no_memory(*args, **kwargs):
        return None

    async def no_output(*args, **kwargs):
        return None

    async def simple_input(observation, info=None, message=None, **kwargs):
        return [{"role": "user", "content": str(observation.content or "")}]

    monkeypatch.setattr(llm_agent_module, "acall_llm_model", provider)
    monkeypatch.setattr(agent, "build_llm_input", simple_input)
    monkeypatch.setattr(agent, "_add_message_to_memory", no_memory)
    monkeypatch.setattr(agent, "send_agent_response_output", no_output)
    message = Message(
        category=Constants.AGENT,
        payload=Observation(content="the current approach repeated"),
        receiver=agent.id(),
        headers={"context": agent.context},
    )

    actions = await agent.async_policy(
        message.payload,
        message=message,
        stream=False,
    )

    assert len(provider_calls) == 4
    assert all(
        call["tools"][0]["function"]["name"] == "aworld__execution_decision"
        for call in provider_calls[:3]
    )
    assert provider_calls[-1]["tools"][0]["function"]["name"] == "workspace__write"
    assert actions[0].tool_name == "workspace"
    assert actions[0].action_name == "write"
    state = ExecutionProtocolStore(agent.context, agent.id(), policy).load()
    assert state.decision_checkpoint_pending is False
    assert state.replan_requested_count == 1
    assert state.replan_applied_count == 0
    protocol = build_execution_protocol_telemetry(agent.context, agent.id())
    assert protocol["replan_decision_status"] == "fail_open_unacknowledged"
    assert protocol["replan_decision_attempt_count"] == 0
    assert protocol["replan_decision_unavailable_count"] == 1
    assert protocol["replan_decision_fail_open_reason"] == "provider_unavailable"


@pytest.mark.asyncio
async def test_truncated_model_actions_continue_with_tools_until_task_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean length stop is task progress, not a terminal retry budget."""

    agent = _long_running_generation_agent(armed=False)
    agent._llm = object()
    agent.llm_max_attempts = 2
    agent.llm_retry_delay = 0
    agent.context._session = Session(session_id="model-response-recovery-s")
    agent.context.set_task(
        Task(
            id="long-running-generation-budget",
            name="long-running-generation-budget",
            input="create /app/primers.fasta",
            timeout=600,
            completion_reserve_seconds=60,
        )
    )
    provider_calls: list[dict] = []

    async def provider(*args, **kwargs):
        provider_calls.append(kwargs)
        if len(provider_calls) <= 6:
            return ModelResponse(
                id=f"truncated-{len(provider_calls)}",
                model="fake-model",
                content="unfinished model action",
                finish_reason="length",
            )
        return ModelResponse(
            id="recovered-tool-turn",
            model="fake-model",
            content="continue material work",
            finish_reason="tool_calls",
            tool_calls=[
                ToolCall(
                    id="write-primers",
                    function=Function(
                        name="workspace__write",
                        arguments=(
                            '{"path":"/app/primers.fasta",'
                            '"content":">input_fwd\\nACGT"}'
                        ),
                    ),
                )
            ],
        )

    async def no_memory(*args, **kwargs):
        return None

    async def no_output(*args, **kwargs):
        return None

    async def simple_input(observation, info=None, message=None, **kwargs):
        return [{"role": "user", "content": str(observation.content or "")}]

    monkeypatch.setattr(llm_agent_module, "acall_llm_model", provider)
    monkeypatch.setattr(agent, "build_llm_input", simple_input)
    monkeypatch.setattr(agent, "_add_message_to_memory", no_memory)
    monkeypatch.setattr(agent, "send_agent_response_output", no_output)
    message = Message(
        category=Constants.AGENT,
        payload=Observation(content="continue retained work"),
        receiver=agent.id(),
        headers={"context": agent.context},
    )

    actions = await agent.async_policy(message.payload, message=message, stream=False)

    # Three bounded continuation batches may recover on the final allowed
    # turn, and every normal turn still receives the full Tool catalog.
    assert len(provider_calls) == 7
    assert all(call.get("tools") for call in provider_calls)
    assert all(
        provider_calls[index].get("tool_choice") == "required"
        for index in range(7)
    )
    assert all(
        not {"max_tokens", "max_completion_tokens"}.intersection(
            provider_calls[index]
        )
        for index in range(1, 7)
    )
    assert all(
        provider_calls[index]["tools"][0]["function"]["name"]
        == "aworld__execution_decision"
        for index in (0, 1)
    )
    assert all(
        provider_calls[index]["tools"][0]["function"]["name"]
        == "workspace__write"
        for index in (2, 3, 4, 5, 6)
    )
    assert any(
        "Runtime response recovery" in str(item.get("content", ""))
        for item in provider_calls[-1]["messages"]
    )
    assert any(
        "prior recovery was also truncated" in str(item.get("content", "")).lower()
        for item in provider_calls[-1]["messages"]
    )
    assert any(
        "retained-incomplete-context" in str(item.get("content", ""))
        and "unfinished model action" in str(item.get("content", ""))
        for item in provider_calls[-1]["messages"]
    )
    assert actions[0].tool_name == "workspace"
    assert actions[0].action_name == "write"
    assert actions[0].params["path"] == "/app/primers.fasta"
    policy = agent._resolve_execution_protocol_policy(agent.context)
    assert not ExecutionProtocolStore(
        agent.context, agent.id(), policy
    ).load().long_horizon_armed
    metrics = agent.context.context_info["model_response_recovery_metrics"]
    assert metrics["continuation_count"] == 3
    assert metrics["consecutive_continuation_count"] == 0
    assert metrics["outcome:recovered"] == 1
    protocol = build_execution_protocol_telemetry(agent.context, agent.id())
    assert protocol["initial_decision_status"] == "fail_open_unknown"
    assert protocol["initial_decision_attempt_count"] == 0
    assert protocol["initial_decision_unavailable_count"] == 1
    assert (
        protocol["initial_decision_fail_open_reason"]
        == "model_response_incomplete"
    )
    assert (
        agent._model_response_recovery_context_key()
        not in agent.context.context_info
    )


def test_incomplete_action_recovery_downgrades_only_declared_reasoning() -> None:
    updated = Agent._incomplete_action_recovery_kwargs(
        {
            "reasoning_effort": "max",
            "extra_body": {
                "chat_template_kwargs": {
                    "thinking": True,
                    "reasoning_effort": "max",
                    "preserved": "value",
                }
            },
            "tool_choice": "required",
            "_aworld_reasoning_selection": {"reasoning_effort": "max"},
        },
        max_output_tokens=1024,
    )

    assert updated["reasoning_effort"] == "low"
    assert updated["tool_choice"] == "required"
    assert updated["max_completion_tokens"] == 1024
    assert "_aworld_reasoning_selection" not in updated
    assert updated["extra_body"]["chat_template_kwargs"] == {
        "thinking": True,
        "reasoning_effort": "low",
        "preserved": "value",
    }
    assert Agent._incomplete_action_recovery_kwargs(
        {"stream": False}, max_output_tokens=1024
    ) == {"stream": False, "max_completion_tokens": 1024}


def test_model_response_action_projection_uses_capability_backed_low_reasoning() -> (
    None
):
    agent = _agent(policy=GenerationBudgetPolicy())
    agent._llm = SimpleNamespace(
        provider=SimpleNamespace(
            reasoning_transport_capability=lambda: OPENAI_REASONING_CAPABILITY
        )
    )
    context = Context(task_id="reasoning-action-projection")

    updated = agent._model_response_action_projection_kwargs(
        {"stream": False, "max_completion_tokens": 16_384},
        max_output_tokens=1024,
        context=context,
    )

    assert updated["reasoning_effort"] == "low"
    assert updated["max_completion_tokens"] == 1024
    metrics = context.context_info["model_response_recovery_metrics"]
    assert metrics["last_action_projection"]["applied"] is True
    assert metrics["last_action_projection"]["policy_id"] == (
        "model-response-action-projection/v1"
    )
    assert metrics["last_action_projection_max_output_tokens"] == 1024


def test_model_response_action_projection_still_caps_unknown_transport() -> None:
    agent = _agent(policy=GenerationBudgetPolicy())
    agent._llm = SimpleNamespace(provider=object())
    context = Context(task_id="unknown-reasoning-action-projection")

    updated = agent._model_response_action_projection_kwargs(
        {"stream": False, "max_completion_tokens": 16_384},
        max_output_tokens=1024,
        context=context,
    )

    assert "reasoning_effort" not in updated
    assert updated["max_completion_tokens"] == 1024
    receipt = context.context_info["model_response_recovery_metrics"][
        "last_action_projection"
    ]
    assert receipt["applied"] is False
    assert receipt["reason_code"] == "unsupported_reasoning_transport"


@pytest.mark.asyncio
async def test_disabled_default_recovery_preserves_long_tool_output_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A filter-js-sized Tool call must not be squeezed into 1024 tokens."""

    agent = _ToolAgent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
        ),
        llm_max_attempts=2,
        llm_retry_delay=0,
    )
    agent._llm = object()
    message = _message("long-tool-response-recovery")
    provider_calls: list[dict] = []
    long_source = "const safe = true;\n" + ("x" * 4_096)

    async def provider(*_args, **kwargs):
        provider_calls.append(kwargs)
        if len(provider_calls) == 1:
            yield ModelResponse(
                id="truncated-long-write",
                model="fake-model",
                tool_calls=[
                    ToolCall(
                        id="partial-write",
                        function=Function(
                            name="workspace__write",
                            arguments=(
                                '{"path":"/app/filter.py","content":"'
                                + ("x" * 3_500)
                            ),
                        ),
                    )
                ],
            )
            yield ModelResponse(
                id="truncated-long-write",
                model="fake-model",
                finish_reason="length",
            )
            return
        yield ModelResponse(
            id="complete-long-write",
            model="fake-model",
            tool_calls=[
                ToolCall(
                    id="complete-write",
                    function=Function(
                        name="workspace__write",
                        arguments=json.dumps(
                            {"path": "/app/filter.py", "content": long_source}
                        ),
                    ),
                )
            ],
        )
        yield ModelResponse(
            id="complete-long-write",
            model="fake-model",
            finish_reason="tool_calls",
        )

    monkeypatch.setattr(llm_agent_module, "acall_llm_model_stream", provider)

    response = await agent.invoke_model(
        messages=[{"role": "user", "content": "write /app/filter.py"}],
        message=message,
        max_completion_tokens=8_192,
        stream=True,
    )

    assert len(provider_calls) == 2
    assert provider_calls[1]["tool_choice"] == "required"
    assert provider_calls[1]["max_completion_tokens"] == 8_192
    assert len(
        json.loads(response.tool_calls[0].function.arguments)["content"]
    ) > 3_000
    assert any(
        "smaller staged Tool action" in str(item.get("content", ""))
        for item in provider_calls[1]["messages"]
    )
    policy_metrics = message.context.context_info[
        f"generation_budget_policy:{agent.id()}"
    ]
    assert policy_metrics["policy_source"] == "disabled_default"
    recovery_metrics = message.context.context_info[
        "model_response_recovery_metrics"
    ]
    assert recovery_metrics["last_action_projection_max_output_tokens"] is None
    assert recovery_metrics["last_action_projection_output_cap_applied"] is False


@pytest.mark.asyncio
async def test_explicit_action_repair_keeps_configured_recovery_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _agent(
        policy=GenerationBudgetPolicy(
            total_timeout_seconds=5,
            action_repair_enabled=True,
            action_repair_max_output_tokens=1_536,
        ),
        attempts=2,
    )
    provider_calls: list[dict] = []

    async def provider(*_args, **kwargs):
        provider_calls.append(kwargs)
        if len(provider_calls) == 1:
            return ModelResponse(
                id="truncated-explicit-repair",
                model="fake-model",
                content="unfinished",
                finish_reason="length",
            )
        return ModelResponse(
            id="recovered-explicit-repair",
            model="fake-model",
            finish_reason="tool_calls",
            tool_calls=[
                ToolCall(
                    id="write-small",
                    function=Function(
                        name="workspace__write",
                        arguments='{"path":"/app/out.txt","content":"done"}',
                    ),
                )
            ],
        )

    monkeypatch.setattr(llm_agent_module, "acall_llm_model", provider)

    await agent.invoke_model(
        messages=[{"role": "user", "content": "write"}],
        message=_message("explicit-action-repair-cap"),
        max_completion_tokens=8_192,
        stream=False,
    )

    assert provider_calls[1]["max_completion_tokens"] == 1_536


@pytest.mark.asyncio
async def test_tool_free_incomplete_response_recovery_preserves_final_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _agent(
        policy=GenerationBudgetPolicy(total_timeout_seconds=5),
        with_tools=False,
        attempts=2,
    )
    provider_calls: list[dict] = []

    async def provider(*_args, **kwargs):
        provider_calls.append(kwargs)
        if len(provider_calls) == 1:
            return ModelResponse(
                id="reasoning-only-final",
                model="fake-model",
                reasoning_content="compose the accurate final response",
                finish_reason="length",
            )
        return ModelResponse(
            id="recovered-final",
            model="fake-model",
            content="accurate final response",
            finish_reason="stop",
        )

    monkeypatch.setattr(llm_agent_module, "acall_llm_model", provider)
    response = await agent.invoke_model(
        messages=[{"role": "user", "content": "finalize accurately"}],
        message=_message("tool-free-response-recovery"),
        prepared_tools=None,
        max_completion_tokens=8192,
        stream=False,
    )

    assert response.content == "accurate final response"
    assert len(provider_calls) == 2
    retry = provider_calls[1]
    assert retry["tools"] is None
    assert "tool_choice" not in retry
    assert retry["max_completion_tokens"] == 8192
    recovery_prompt = retry["messages"][-1]["content"]
    assert "No Tools are available" in recovery_prompt
    assert "do not request a Tool call" in recovery_prompt


@pytest.mark.asyncio
async def test_truncated_model_action_has_bounded_outer_recovery() -> None:
    agent = _long_running_generation_agent(armed=False)
    agent.context.set_task(
        Task(
            id="bounded-model-response-recovery",
            name="bounded-model-response-recovery",
            input="create /app/result.json",
            timeout=600,
            completion_reserve_seconds=60,
        )
    )
    calls = 0

    async def attempt(observation, **kwargs):
        nonlocal calls
        calls += 1
        from aworld.core.context.execution_state import record_execution_state

        record_execution_state(
            agent.context,
            agent.id(),
            "incomplete",
            "model_output_truncated",
            recoverable=True,
        )
        return [ActionModel(agent_name=agent.id(), policy_info="Incomplete.")]

    agent._async_policy_once = attempt
    message = Message(category=Constants.AGENT, headers={"context": agent.context})

    result = await agent.async_policy(Observation(content="continue"), message=message)

    assert calls == 4
    assert result[0].policy_info == "Incomplete."
    assert agent.finished is True
    metrics = agent.context.context_info["model_response_recovery_metrics"]
    assert metrics["continuation_count"] == 3
    assert metrics["consecutive_continuation_count"] == 3
    assert metrics["last_outcome"] == "continuation_exhausted"


@pytest.mark.asyncio
async def test_incomplete_tool_arguments_are_retained_as_non_executable_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _agent(
        policy=GenerationBudgetPolicy(
            total_timeout_seconds=None,
            stream_idle_timeout_seconds=None,
            active_tool_free_timeout_seconds=None,
            action_repair_timeout_seconds=None,
            action_repair_enabled=False,
            partial_response_context_chars=512,
        ),
        attempts=2,
    )
    message = _message("incomplete-tool-reference")
    provider_calls: list[dict] = []

    async def provider(*args, **kwargs):
        provider_calls.append(kwargs)
        if len(provider_calls) == 1:
            return ModelResponse(
                id="partial-tool",
                model="fake-model",
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCall(
                        id="write-partial",
                        function=Function(
                            name="workspace__write",
                            arguments=(
                                '{"path":"/app/result.json",'
                                '"content":"retained fragment'
                            ),
                        ),
                    )
                ],
            )
        return ModelResponse(
            id="repaired-tool",
            model="fake-model",
            content="",
            finish_reason="tool_calls",
            tool_calls=[
                ToolCall(
                    id="write-complete",
                    function=Function(
                        name="workspace__write",
                        arguments='{"path":"/app/result.json","content":"done"}',
                    ),
                )
            ],
        )

    monkeypatch.setattr(llm_agent_module, "acall_llm_model", provider)

    response = await agent.invoke_model(
        messages=[{"role": "user", "content": "write the artifact"}],
        message=message,
        stream=False,
    )

    assert len(provider_calls) == 2
    retained = provider_calls[1]["messages"][-2]
    assert retained["role"] == "assistant"
    assert "tool_calls" not in retained
    assert "Non-executable partial tool-call reference" in retained["content"]
    assert "workspace__write" in retained["content"]
    assert "/app/result.json" in retained["content"]
    assert '"executable":false' in retained["content"]
    assert len(retained["content"]) <= 512
    assert provider_calls[1]["tool_choice"] == "required"
    assert response.tool_calls[0].id == "write-complete"


@pytest.mark.asyncio
async def test_truncated_model_action_stops_honestly_at_caller_reserve() -> None:
    agent = _long_running_generation_agent(armed=False)
    task = Task(
        id="long-running-generation-budget",
        name="long-running-generation-budget",
        input="continue",
        timeout=600,
        completion_reserve_seconds=60,
    )
    task.remaining_seconds = lambda: 60.0
    agent.context.set_task(task)
    calls = 0

    async def attempt(observation, **kwargs):
        nonlocal calls
        calls += 1
        from aworld.core.context.execution_state import record_execution_state

        record_execution_state(
            agent.context,
            agent.id(),
            "incomplete",
            "model_output_truncated",
            recoverable=True,
        )
        return [
            ActionModel(
                agent_name=agent.id(),
                policy_info="Work remains incomplete.",
            )
        ]

    agent._async_policy_once = attempt
    message = Message(category=Constants.AGENT, headers={"context": agent.context})

    result = await agent.async_policy(Observation(content="continue"), message=message)

    assert calls == 1
    assert result[0].policy_info == "Work remains incomplete."
    assert agent.finished is True
    state = get_execution_state(agent.context)
    assert state["status"] == "incomplete"
    assert state["reason"] == "model_output_truncated"
    assert state["recoverable"] is True
    metrics = agent.context.context_info["model_response_recovery_metrics"]
    assert metrics["last_outcome"] == "deadline_exhausted"


@pytest.mark.asyncio
async def test_truncated_model_action_respects_agent_step_budget() -> None:
    agent = _long_running_generation_agent(armed=False)
    agent.max_loop_steps = 1
    agent.context.update_agent_step(agent.id())
    calls = 0

    async def attempt(observation, **kwargs):
        nonlocal calls
        calls += 1
        from aworld.core.context.execution_state import record_execution_state

        record_execution_state(
            agent.context,
            agent.id(),
            "incomplete",
            "model_output_truncated",
            recoverable=True,
        )
        return [ActionModel(agent_name=agent.id(), policy_info="Incomplete.")]

    agent._async_policy_once = attempt
    message = Message(category=Constants.AGENT, headers={"context": agent.context})

    result = await agent.async_policy(Observation(content="continue"), message=message)

    assert calls == 1
    assert result[0].policy_info == "Incomplete."
    assert agent.finished is True
    assert get_execution_state(agent.context)["status"] == "incomplete"
    metrics = agent.context.context_info["model_response_recovery_metrics"]
    assert metrics["last_outcome"] == "step_budget_exhausted"


@pytest.mark.asyncio
async def test_transient_provider_recovery_stops_at_finalization_reserve() -> None:
    agent = _long_running_generation_agent(armed=True)
    task = Task(
        id="long-running-generation-budget",
        name="long-running-generation-budget",
        input="continue",
        timeout=600,
    )
    task.remaining_seconds = lambda: 45.0
    agent.context.set_task(task)
    calls = 0

    async def attempt(observation, **kwargs):
        nonlocal calls
        calls += 1
        raise AWorldTransientModelError(
            status_code=502,
            source_error_type="InternalServerError",
        )

    agent._async_policy_once = attempt
    message = Message(category=Constants.AGENT, headers={"context": agent.context})

    result = await agent.async_policy(Observation(content="continue"), message=message)

    assert calls == 1
    assert "does not claim successful completion" in result[0].policy_info
    state = get_execution_state(agent.context)
    assert state["status"] == "incomplete"
    assert state["reason"] == "transient_model_recovery_deadline_exhausted"


@pytest.mark.asyncio
async def test_deterministic_provider_error_does_not_enter_recovery() -> None:
    class BadRequestError(Exception):
        status_code = 400

    agent = _long_running_generation_agent(armed=True)
    agent.context.set_task(
        Task(
            id="long-running-generation-budget",
            name="long-running-generation-budget",
            input="continue",
            timeout=600,
        )
    )
    calls = 0

    async def attempt(observation, **kwargs):
        nonlocal calls
        calls += 1
        raise BadRequestError("invalid request")

    agent._async_policy_once = attempt
    message = Message(category=Constants.AGENT, headers={"context": agent.context})

    with pytest.raises(BadRequestError):
        await agent.async_policy(Observation(content="continue"), message=message)

    assert calls == 1
    assert "transient_model_recovery_metrics" not in agent.context.context_info


@pytest.mark.asyncio
async def test_automatic_timeout_nonempty_handoff_remains_budget_exhausted() -> None:
    agent = _long_running_generation_agent(armed=True)
    calls: list[dict] = []

    async def attempt(observation, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise _generation_timeout()
        assert kwargs["_loop_budget_finalization"] is True
        return [
            ActionModel(
                agent_name=agent.id(),
                policy_info=(
                    "Work remains incomplete; the requested deliverable was not "
                    "produced."
                ),
            )
        ]

    agent._async_policy_once = attempt
    message = Message(category=Constants.AGENT, headers={"context": agent.context})

    result = await agent.async_policy(Observation(content="continue"), message=message)

    assert result[0].policy_info == (
        "Work remains incomplete; the requested deliverable was not produced."
    )
    assert len(calls) == 2
    state = get_execution_state(agent.context)
    assert state["status"] == "budget_exhausted"
    assert state["reason"] == "long_horizon_generation_budget_exhausted"
    assert state["recoverable"] is True
    metrics = agent.context.context_info["long_horizon_generation_budget_metrics"]
    assert metrics["policy_source"] == "long_horizon_auto"
    assert metrics["last_outcome"] == "finalized"


@pytest.mark.asyncio
async def test_automatic_timeout_finalization_failure_returns_honest_result() -> None:
    agent = _long_running_generation_agent(armed=True)
    calls = 0

    async def attempt(observation, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _generation_timeout(GenerationStopReason.IDLE_TIMEOUT)
        raise RuntimeError("finalization provider unavailable")

    agent._async_policy_once = attempt
    message = Message(category=Constants.AGENT, headers={"context": agent.context})

    result = await agent.async_policy(Observation(content="continue"), message=message)

    assert calls == 2
    assert "does not claim successful completion" in result[0].policy_info
    state = get_execution_state(agent.context)
    assert state["status"] == "budget_exhausted"
    assert state["reason"] == "long_horizon_generation_budget_exhausted"
    assert state["recoverable"] is True


@pytest.mark.asyncio
async def test_automatic_timeout_empty_finalization_is_budget_exhausted() -> None:
    agent = _long_running_generation_agent(armed=True)
    calls = 0

    async def attempt(observation, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _generation_timeout(GenerationStopReason.PROVIDER_TIMEOUT)
        assert kwargs["_loop_budget_finalization"] is True
        return []

    agent._async_policy_once = attempt
    message = Message(category=Constants.AGENT, headers={"context": agent.context})

    result = await agent.async_policy(Observation(content="continue"), message=message)

    assert calls == 2
    assert "does not claim successful completion" in result[0].policy_info
    state = get_execution_state(agent.context)
    assert state["status"] == "budget_exhausted"
    assert state["reason"] == "long_horizon_generation_budget_exhausted"
    assert state["recoverable"] is True


@pytest.mark.asyncio
async def test_automatic_timeout_finalization_is_claimed_only_once() -> None:
    agent = _long_running_generation_agent(armed=True)
    context = agent.context
    assert agent._claim_long_horizon_generation_finalization(context) is True

    calls = 0

    async def attempt(observation, **kwargs):
        nonlocal calls
        calls += 1
        raise _generation_timeout()

    agent._async_policy_once = attempt
    message = Message(category=Constants.AGENT, headers={"context": context})

    result = await agent.async_policy(Observation(content="continue"), message=message)

    assert calls == 1
    assert "does not claim successful completion" in result[0].policy_info


@pytest.mark.asyncio
async def test_explicit_watchdog_timeout_remains_fail_closed() -> None:
    explicit = GenerationBudgetPolicy(
        total_timeout_seconds=1,
        stream_idle_timeout_seconds=None,
        active_tool_free_timeout_seconds=None,
        action_repair_timeout_seconds=None,
        action_repair_enabled=False,
    )
    agent = _long_running_generation_agent(
        armed=True,
        generation_policy=explicit,
    )

    async def attempt(observation, **kwargs):
        raise _generation_timeout()

    agent._async_policy_once = attempt
    message = Message(category=Constants.AGENT, headers={"context": agent.context})

    with pytest.raises(GenerationBudgetExceeded):
        await agent.async_policy(Observation(content="continue"), message=message)


class _ProductionBoundaryTimeoutAgent(Agent):
    def __init__(self, *args, fail_once: bool, **kwargs):
        super().__init__(*args, **kwargs)
        self.fail_once = fail_once
        self.invoke_count = 0

    async def _add_message_to_memory(self, *args, **kwargs):
        return None

    async def build_llm_input(self, observation, info=None, message=None, **kwargs):
        return [{"role": "user", "content": str(observation.content or "")}]

    async def _filter_tools(self, context=None):
        return None

    async def invoke_model(self, messages=None, message=None, **kwargs):
        self.invoke_count += 1
        if self.fail_once and self.invoke_count > 1:
            content = "bounded production-path summary"
            return ModelResponse(
                id="finalized",
                model="fake-model",
                content=content,
                message={"role": "assistant", "content": content},
                finish_reason="stop",
                usage={"prompt_tokens": 1, "completion_tokens": 1},
            )
        raise _generation_timeout()


def _production_boundary_timeout_agent(
    *,
    generation_policy: GenerationBudgetPolicy | None = None,
    fail_once: bool,
) -> tuple[Agent, Context]:
    agent = _ProductionBoundaryTimeoutAgent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
        ),
        generation_budget_policy=generation_policy,
        fail_once=fail_once,
    )
    agent._llm = object()
    agent.skill_configs = {"long-running-agent": {"active": True}}
    context = Context(task_id="production-boundary-timeout")
    context.set_task(
        Task(
            id="production-boundary-timeout",
            input="test request",
        )
    )
    agent.context = context
    policy = agent._resolve_execution_protocol_policy()
    store = ExecutionProtocolStore(context, agent.id(), policy)
    store.save(replace(store.load(), long_horizon_armed=True))
    return agent, context


@pytest.mark.asyncio
async def test_production_invoke_timeout_reaches_auto_fail_open_boundary() -> None:
    agent, context = _production_boundary_timeout_agent(fail_once=True)
    message = Message(category=Constants.AGENT, headers={"context": context})

    result = await agent.async_policy(Observation(content="continue"), message=message)

    assert agent.invoke_count == 2
    assert result[0].policy_info == "bounded production-path summary"
    state = get_execution_state(context)
    assert state["status"] == "budget_exhausted"
    assert state["reason"] == "long_horizon_generation_budget_exhausted"
    assert state["recoverable"] is True


@pytest.mark.asyncio
async def test_production_invoke_timeout_with_explicit_policy_stays_typed() -> None:
    explicit = GenerationBudgetPolicy(
        total_timeout_seconds=1,
        stream_idle_timeout_seconds=None,
        active_tool_free_timeout_seconds=None,
        action_repair_timeout_seconds=None,
        action_repair_enabled=False,
    )
    agent, context = _production_boundary_timeout_agent(
        generation_policy=explicit,
        fail_once=False,
    )
    message = Message(category=Constants.AGENT, headers={"context": context})

    with pytest.raises(GenerationBudgetExceeded):
        await agent.async_policy(Observation(content="continue"), message=message)

    assert agent.invoke_count == 1


@pytest.mark.asyncio
async def test_generation_finalization_claim_is_atomic_across_context_copies() -> None:
    agent = _long_running_generation_agent(armed=True)
    context = agent.context
    transported = context.deep_copy()

    claims = await asyncio.gather(
        asyncio.to_thread(
            agent._claim_long_horizon_generation_finalization,
            context,
        ),
        asyncio.to_thread(
            agent._claim_long_horizon_generation_finalization,
            transported,
        ),
    )

    assert sorted(claims) == [False, True]

    context.task_id = "next-task"
    assert agent._claim_long_horizon_generation_finalization(context) is True


@pytest.fixture(autouse=True)
def _silence_event_delivery(monkeypatch: pytest.MonkeyPatch):
    async def noop_send_message(*args, **kwargs):
        return None

    monkeypatch.setattr(llm_agent_module, "send_message", noop_send_message)


@pytest.mark.asyncio
async def test_active_tool_free_stream_is_cut_off_and_repaired_once(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[list[dict]] = []
    primary_closed = asyncio.Event()

    async def fake_stream(*args, **kwargs):
        call_messages = kwargs["messages"]
        calls.append(call_messages)
        if len(calls) == 1:
            try:
                while True:
                    await asyncio.sleep(0.002)
                    yield ModelResponse(
                        id="primary",
                        model="fake-model",
                        content="planning ",
                    )
            finally:
                primary_closed.set()
            return
        yield ModelResponse(
            id="repair",
            model="fake-model",
            content="",
            tool_calls=[
                ToolCall(
                    id="call-1",
                    function=Function(name="workspace__write", arguments="{}"),
                )
            ],
        )

    monkeypatch.setattr(llm_agent_module, "acall_llm_model_stream", fake_stream)
    agent = _agent(
        policy=GenerationBudgetPolicy(
            total_timeout_seconds=0.5,
            stream_idle_timeout_seconds=0.1,
            active_tool_free_timeout_seconds=0.02,
            action_repair_timeout_seconds=0.1,
            action_repair_max_output_tokens=128,
            partial_response_context_chars=64,
        )
    )
    message = _message("active-stream")

    response = await agent.invoke_model(
        messages=[{"role": "user", "content": "create the artifact"}],
        message=message,
        stream=True,
    )

    assert primary_closed.is_set()
    assert len(calls) == 2
    assert response.tool_calls[0].function.name == "workspace__write"
    assert calls[1][-2]["role"] == "assistant"
    assert "planning" in calls[1][-2]["content"]
    assert len(calls[1][-2]["content"]) <= 64
    assert calls[1][-1]["role"] == "user"
    assert "next concrete action" in calls[1][-1]["content"]
    events = message.context.context_info["generation_budget_events"]
    assert [event["reason"] for event in events] == [
        GenerationStopReason.ACTIVE_STREAM_OVER_BUDGET.value
    ]
    assert events[0]["repair_scheduled"] is True
    assert events[0]["repair_attempted"] is True
    assert events[0]["partial_response_available"] is True
    assert (
        message.context.context_info["post_tool_progress_metrics"][
            "generation_action_repair_scheduled_count"
        ]
        == 1
    )


@pytest.mark.asyncio
async def test_stream_idle_timeout_is_typed_and_keeps_partial_response(
    monkeypatch: pytest.MonkeyPatch,
):
    async def idle_stream(*args, **kwargs):
        yield ModelResponse(id="idle", model="fake-model", content="partial")
        await asyncio.Event().wait()

    monkeypatch.setattr(llm_agent_module, "acall_llm_model_stream", idle_stream)
    agent = _agent(
        policy=GenerationBudgetPolicy(
            total_timeout_seconds=0.5,
            stream_idle_timeout_seconds=0.01,
            active_tool_free_timeout_seconds=0.2,
            action_repair_timeout_seconds=0.1,
        ),
        attempts=1,
    )
    message = _message("idle-stream")

    with pytest.raises(GenerationBudgetExceeded) as raised:
        await agent.invoke_model(
            messages=[{"role": "user", "content": "work"}],
            message=message,
            stream=True,
        )

    assert raised.value.reason is GenerationStopReason.IDLE_TIMEOUT
    assert raised.value.partial_response.content == "partial"
    event = message.context.context_info["generation_budget_events"][-1]
    assert event["reason"] == GenerationStopReason.IDLE_TIMEOUT.value
    assert event["partial_response_chars"] == len("partial")
    assert event["partial_response_available"] is True


@pytest.mark.asyncio
async def test_no_tool_stream_is_not_subject_to_active_action_deadline(
    monkeypatch: pytest.MonkeyPatch,
):
    async def text_only_stream(*args, **kwargs):
        for _ in range(8):
            await asyncio.sleep(0.003)
            yield ModelResponse(id="text", model="fake-model", content="answer ")

    monkeypatch.setattr(llm_agent_module, "acall_llm_model_stream", text_only_stream)
    agent = _agent(
        policy=GenerationBudgetPolicy(
            total_timeout_seconds=0.5,
            stream_idle_timeout_seconds=0.1,
            active_tool_free_timeout_seconds=0.005,
            action_repair_timeout_seconds=0.1,
        ),
        with_tools=False,
    )
    message = _message("no-tool-stream")

    response = await agent.invoke_model(
        messages=[{"role": "user", "content": "write a long answer"}],
        message=message,
        stream=True,
    )

    assert response.content == "answer " * 8
    assert message.context.context_info.get("generation_budget_events") is None


@pytest.mark.asyncio
async def test_started_tool_call_disarms_tool_free_deadline(
    monkeypatch: pytest.MonkeyPatch,
):
    async def tool_stream(*args, **kwargs):
        yield ModelResponse(
            id="tool",
            model="fake-model",
            tool_calls=[
                ToolCall(
                    id="call-1",
                    function=Function(
                        name="workspace__write", arguments='{"content":"'
                    ),
                )
            ],
        )
        for _ in range(6):
            await asyncio.sleep(0.003)
            yield ModelResponse(
                id="tool",
                model="fake-model",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        function=Function(name="unknown", arguments="x"),
                    )
                ],
            )

        yield ModelResponse(
            id="tool",
            model="fake-model",
            finish_reason="tool_calls",
            tool_calls=[
                ToolCall(id="call-1", function=Function(name="unknown", arguments='"}'))
            ],
        )

    monkeypatch.setattr(llm_agent_module, "acall_llm_model_stream", tool_stream)
    agent = _agent(
        policy=GenerationBudgetPolicy(
            total_timeout_seconds=0.5,
            stream_idle_timeout_seconds=0.1,
            active_tool_free_timeout_seconds=0.005,
            action_repair_timeout_seconds=0.1,
        )
    )
    message = _message("tool-started")

    response = await agent.invoke_model(
        messages=[{"role": "user", "content": "write"}],
        message=message,
        stream=True,
    )

    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].function.arguments == '{"content":"xxxxxx"}'
    assert message.context.context_info.get("generation_budget_events") is None


@pytest.mark.asyncio
async def test_all_optional_deadlines_can_be_disabled(
    monkeypatch: pytest.MonkeyPatch,
):
    async def immediate_provider(*args, **kwargs):
        return ModelResponse(id="ok", model="fake-model", content="ok")

    monkeypatch.setattr(llm_agent_module, "acall_llm_model", immediate_provider)
    agent = _agent(
        policy=GenerationBudgetPolicy(
            total_timeout_seconds=None,
            stream_idle_timeout_seconds=None,
            active_tool_free_timeout_seconds=None,
            action_repair_timeout_seconds=None,
            action_repair_enabled=False,
        ),
        with_tools=False,
    )
    message = _message("deadlines-disabled")

    response = await agent.invoke_model(
        messages=[{"role": "user", "content": "hello"}],
        message=message,
        stream=False,
    )

    assert response.content == "ok"


@pytest.mark.asyncio
async def test_provider_timeout_is_not_mislabeled_as_framework_deadline(
    monkeypatch: pytest.MonkeyPatch,
):
    class APITimeoutError(Exception):
        pass

    async def provider_timeout(*args, **kwargs):
        raise APITimeoutError("provider read timeout")

    monkeypatch.setattr(llm_agent_module, "acall_llm_model", provider_timeout)
    agent = _agent(
        policy=GenerationBudgetPolicy(
            total_timeout_seconds=0.5,
            stream_idle_timeout_seconds=None,
            active_tool_free_timeout_seconds=None,
            action_repair_timeout_seconds=0.1,
        ),
        with_tools=False,
        attempts=1,
    )
    message = _message("provider-timeout")

    with pytest.raises(GenerationBudgetExceeded) as raised:
        await agent.invoke_model(
            messages=[{"role": "user", "content": "hello"}],
            message=message,
            stream=False,
        )

    assert raised.value.reason is GenerationStopReason.PROVIDER_TIMEOUT
    assert (
        message.context.context_info["generation_budget_events"][-1]["reason"]
        == GenerationStopReason.PROVIDER_TIMEOUT.value
    )


@pytest.mark.asyncio
async def test_caller_cancellation_propagates_and_is_recorded_separately(
    monkeypatch: pytest.MonkeyPatch,
):
    provider_cancelled = asyncio.Event()
    provider_started = asyncio.Event()

    async def blocked_provider(*args, **kwargs):
        provider_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            provider_cancelled.set()
            raise

    monkeypatch.setattr(llm_agent_module, "acall_llm_model", blocked_provider)
    agent = _agent(
        policy=GenerationBudgetPolicy(
            total_timeout_seconds=1,
            stream_idle_timeout_seconds=None,
            active_tool_free_timeout_seconds=None,
            action_repair_timeout_seconds=0.1,
        ),
        with_tools=False,
    )
    message = _message("caller-cancel")
    task = asyncio.create_task(
        agent.invoke_model(
            messages=[{"role": "user", "content": "hello"}],
            message=message,
            stream=False,
        )
    )
    await asyncio.wait_for(provider_started.wait(), timeout=1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert provider_cancelled.is_set()
    events = message.context.context_info["generation_budget_events"]
    assert events[-1]["reason"] == GenerationStopReason.CALLER_CANCELLED.value


@pytest.mark.asyncio
async def test_generation_deadline_does_not_wait_forever_for_provider_cleanup(
    monkeypatch: pytest.MonkeyPatch,
):
    provider_started = asyncio.Event()
    provider_release = asyncio.Event()
    provider_tasks: list[asyncio.Task] = []

    async def stubborn_provider(*args, **kwargs):
        provider_tasks.append(asyncio.current_task())
        provider_started.set()
        while not provider_release.is_set():
            try:
                await provider_release.wait()
            except asyncio.CancelledError:
                continue

    monkeypatch.setattr(llm_agent_module, "acall_llm_model", stubborn_provider)
    monkeypatch.setattr(llm_agent_module, "_GENERATION_CLEANUP_GRACE_SECONDS", 0.01)
    agent = _agent(
        policy=GenerationBudgetPolicy(
            total_timeout_seconds=0.01,
            stream_idle_timeout_seconds=None,
            active_tool_free_timeout_seconds=None,
            action_repair_timeout_seconds=None,
            action_repair_enabled=False,
        ),
        with_tools=False,
        attempts=1,
    )

    try:
        with pytest.raises(GenerationBudgetExceeded) as raised:
            await asyncio.wait_for(
                agent.invoke_model(
                    messages=[{"role": "user", "content": "hello"}],
                    message=_message("stubborn-provider"),
                    stream=False,
                ),
                timeout=0.15,
            )
        assert raised.value.reason is GenerationStopReason.CALL_DEADLINE_EXCEEDED
        assert provider_started.is_set()
    finally:
        provider_release.set()
        if provider_tasks:
            await asyncio.wait_for(provider_tasks[0], timeout=1)


@pytest.mark.asyncio
async def test_caller_cancellation_is_not_blocked_by_provider_cleanup(
    monkeypatch: pytest.MonkeyPatch,
):
    provider_started = asyncio.Event()
    provider_release = asyncio.Event()
    provider_tasks: list[asyncio.Task] = []

    async def stubborn_provider(*args, **kwargs):
        provider_tasks.append(asyncio.current_task())
        provider_started.set()
        while not provider_release.is_set():
            try:
                await provider_release.wait()
            except asyncio.CancelledError:
                continue

    monkeypatch.setattr(llm_agent_module, "acall_llm_model", stubborn_provider)
    monkeypatch.setattr(llm_agent_module, "_GENERATION_CLEANUP_GRACE_SECONDS", 0.01)
    agent = _agent(
        policy=GenerationBudgetPolicy(
            total_timeout_seconds=1,
            stream_idle_timeout_seconds=None,
            active_tool_free_timeout_seconds=None,
            action_repair_timeout_seconds=None,
            action_repair_enabled=False,
        ),
        with_tools=False,
    )
    task = asyncio.create_task(
        agent.invoke_model(
            messages=[{"role": "user", "content": "hello"}],
            message=_message("stubborn-provider-caller-cancel"),
            stream=False,
        )
    )
    await asyncio.wait_for(provider_started.wait(), timeout=1)

    try:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=0.15)
    finally:
        provider_release.set()
        if provider_tasks:
            await asyncio.wait_for(provider_tasks[0], timeout=1)


@pytest.mark.asyncio
async def test_stream_close_is_bounded_when_provider_cleanup_stalls(
    monkeypatch: pytest.MonkeyPatch,
):
    close_started = asyncio.Event()
    close_release = asyncio.Event()
    close_tasks: list[asyncio.Task] = []

    class StubbornStream:
        emitted = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self.emitted:
                self.emitted = True
                return ModelResponse(
                    id="complete", model="fake-model", content="complete"
                )
            raise StopAsyncIteration

        async def aclose(self):
            close_tasks.append(asyncio.current_task())
            close_started.set()
            while not close_release.is_set():
                try:
                    await close_release.wait()
                except asyncio.CancelledError:
                    continue

    monkeypatch.setattr(
        llm_agent_module,
        "acall_llm_model_stream",
        lambda *args, **kwargs: StubbornStream(),
    )
    monkeypatch.setattr(llm_agent_module, "_GENERATION_CLEANUP_GRACE_SECONDS", 0.01)
    agent = _agent(
        policy=GenerationBudgetPolicy(
            total_timeout_seconds=1,
            stream_idle_timeout_seconds=None,
            active_tool_free_timeout_seconds=None,
            action_repair_timeout_seconds=None,
            action_repair_enabled=False,
        ),
        with_tools=False,
    )

    try:
        response = await asyncio.wait_for(
            agent.invoke_model(
                messages=[{"role": "user", "content": "hello"}],
                message=_message("stubborn-stream-close"),
                stream=True,
            ),
            timeout=0.15,
        )
        assert response.content == "complete"
        assert close_started.is_set()
    finally:
        close_release.set()
        if close_tasks:
            await asyncio.wait_for(close_tasks[0], timeout=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("close_behavior", ("raise", "non_awaitable"))
async def test_broken_stream_close_does_not_replace_completed_response(
    monkeypatch: pytest.MonkeyPatch, close_behavior: str
):
    class BrokenCloseStream:
        emitted = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.emitted:
                raise StopAsyncIteration
            self.emitted = True
            return ModelResponse(id="ok", model="fake-model", content="complete")

        def aclose(self):
            if close_behavior == "raise":
                raise RuntimeError("broken stream cleanup")
            return None

    monkeypatch.setattr(
        llm_agent_module,
        "acall_llm_model_stream",
        lambda *args, **kwargs: BrokenCloseStream(),
    )
    agent = _agent(
        policy=GenerationBudgetPolicy(
            total_timeout_seconds=1,
            stream_idle_timeout_seconds=None,
            active_tool_free_timeout_seconds=None,
            action_repair_timeout_seconds=None,
            action_repair_enabled=False,
        ),
        with_tools=False,
    )

    response = await agent.invoke_model(
        messages=[{"role": "user", "content": "hello"}],
        message=_message(f"broken-close-{close_behavior}"),
        stream=True,
    )

    assert response.content == "complete"


@pytest.mark.asyncio
async def test_broken_stream_close_does_not_replace_primary_error(
    monkeypatch: pytest.MonkeyPatch,
):
    class BrokenCloseStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise RuntimeError("primary provider failure")

        def aclose(self):
            raise RuntimeError("secondary cleanup failure")

    monkeypatch.setattr(
        llm_agent_module,
        "acall_llm_model_stream",
        lambda *args, **kwargs: BrokenCloseStream(),
    )
    agent = _agent(
        policy=GenerationBudgetPolicy(
            total_timeout_seconds=1,
            stream_idle_timeout_seconds=None,
            active_tool_free_timeout_seconds=None,
            action_repair_timeout_seconds=None,
            action_repair_enabled=False,
        ),
        with_tools=False,
        attempts=1,
    )

    with pytest.raises(Exception, match="primary provider failure") as raised:
        await agent.invoke_model(
            messages=[{"role": "user", "content": "hello"}],
            message=_message("broken-close-primary-error"),
            stream=True,
        )

    assert "secondary cleanup failure" not in str(raised.value)


@pytest.mark.asyncio
async def test_detached_provider_cleanup_capacity_is_finite(
    monkeypatch: pytest.MonkeyPatch,
):
    release = asyncio.Event()
    detached: list[asyncio.Task] = []

    async def stubborn_provider():
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue

    monkeypatch.setenv("AWORLD_MAX_PENDING_GENERATION_TASKS", "3")
    monkeypatch.setattr(llm_agent_module, "_GENERATION_CLEANUP_GRACE_SECONDS", 0.001)
    llm_agent_module._DETACHED_GENERATION_TASKS.clear()
    llm_agent_module._ACTIVE_GENERATION_TASKS.clear()
    agent = _agent(
        policy=GenerationBudgetPolicy(
            total_timeout_seconds=None,
            stream_idle_timeout_seconds=None,
            active_tool_free_timeout_seconds=None,
            action_repair_timeout_seconds=None,
            action_repair_enabled=False,
        ),
        with_tools=False,
    )

    try:
        for _ in range(3):
            task = agent._create_generation_task(stubborn_provider())
            assert task is not None
            detached.append(task)
            await asyncio.sleep(0)
            await agent._cancel_generation_task(task)
        assert len(llm_agent_module._DETACHED_GENERATION_TASKS) == 3

        with pytest.raises(Exception, match="cleanup capacity is exhausted"):
            await agent._await_generation_operation(
                stubborn_provider(),
                controller=llm_agent_module.GenerationBudgetController(
                    agent._resolve_generation_budget_policy()
                ),
                streaming=False,
            )
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(*detached), timeout=1)
        await asyncio.sleep(0)
        llm_agent_module._DETACHED_GENERATION_TASKS.clear()
        llm_agent_module._ACTIVE_GENERATION_TASKS.clear()


@pytest.mark.asyncio
async def test_concurrent_provider_timeouts_cannot_exceed_cleanup_capacity(
    monkeypatch: pytest.MonkeyPatch,
):
    release = asyncio.Event()
    provider_tasks: list[asyncio.Task] = []

    async def stubborn_provider():
        provider_tasks.append(asyncio.current_task())
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue

    monkeypatch.setenv("AWORLD_MAX_PENDING_GENERATION_TASKS", "3")
    monkeypatch.setattr(llm_agent_module, "_GENERATION_CLEANUP_GRACE_SECONDS", 0.001)
    llm_agent_module._DETACHED_GENERATION_TASKS.clear()
    llm_agent_module._ACTIVE_GENERATION_TASKS.clear()
    agent = _agent(
        policy=GenerationBudgetPolicy(
            total_timeout_seconds=0.005,
            stream_idle_timeout_seconds=None,
            active_tool_free_timeout_seconds=None,
            action_repair_timeout_seconds=None,
            action_repair_enabled=False,
        ),
        with_tools=False,
    )

    try:
        results = await asyncio.gather(
            *(
                agent._await_generation_operation(
                    stubborn_provider(),
                    controller=llm_agent_module.GenerationBudgetController(
                        agent._resolve_generation_budget_policy()
                    ),
                    streaming=False,
                )
                for _ in range(20)
            ),
            return_exceptions=True,
        )

        assert all(isinstance(result, Exception) for result in results)
        assert len(llm_agent_module._ACTIVE_GENERATION_TASKS) <= 3
        assert len(llm_agent_module._DETACHED_GENERATION_TASKS) <= 3
        assert len(provider_tasks) <= 3
    finally:
        release.set()
        if provider_tasks:
            await asyncio.wait_for(asyncio.gather(*provider_tasks), timeout=1)
        await asyncio.sleep(0)
        llm_agent_module._DETACHED_GENERATION_TASKS.clear()
        llm_agent_module._ACTIVE_GENERATION_TASKS.clear()


@pytest.mark.asyncio
async def test_ordinary_runtime_does_not_cap_healthy_generation_concurrency(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("AWORLD_MAX_PENDING_GENERATION_TASKS", raising=False)
    llm_agent_module._DETACHED_GENERATION_TASKS.clear()
    llm_agent_module._ACTIVE_GENERATION_TASKS.clear()
    agent = _agent(
        policy=GenerationBudgetPolicy(
            total_timeout_seconds=None,
            stream_idle_timeout_seconds=None,
            active_tool_free_timeout_seconds=None,
            action_repair_timeout_seconds=None,
            action_repair_enabled=False,
        ),
        with_tools=False,
    )

    results = await asyncio.gather(
        *(
            agent._await_generation_operation(
                asyncio.sleep(0.01, result=index),
                controller=llm_agent_module.GenerationBudgetController(
                    agent._resolve_generation_budget_policy()
                ),
                streaming=False,
            )
            for index in range(16)
        )
    )

    assert results == list(range(16))


@pytest.mark.asyncio
async def test_action_repair_cannot_repair_itself_in_a_loop(
    monkeypatch: pytest.MonkeyPatch,
):
    calls = 0

    async def endless_active_stream(*args, **kwargs):
        nonlocal calls
        calls += 1
        while True:
            await asyncio.sleep(0.002)
            yield ModelResponse(
                id=f"response-{calls}", model="fake-model", content="more analysis"
            )

    monkeypatch.setattr(
        llm_agent_module, "acall_llm_model_stream", endless_active_stream
    )
    agent = _agent(
        policy=GenerationBudgetPolicy(
            total_timeout_seconds=0.5,
            stream_idle_timeout_seconds=0.1,
            active_tool_free_timeout_seconds=0.01,
            action_repair_timeout_seconds=0.015,
            action_repair_max_output_tokens=64,
        )
    )
    message = _message("repair-once")

    with pytest.raises(GenerationBudgetExceeded) as raised:
        await agent.invoke_model(
            messages=[{"role": "user", "content": "create the artifact"}],
            message=message,
            stream=True,
        )

    assert raised.value.reason is GenerationStopReason.ACTION_REPAIR_TIMEOUT
    assert calls == 2
    events = message.context.context_info["generation_budget_events"]
    assert [event["reason"] for event in events] == [
        GenerationStopReason.ACTIVE_STREAM_OVER_BUDGET.value,
        GenerationStopReason.ACTION_REPAIR_TIMEOUT.value,
    ]


@pytest.mark.asyncio
async def test_truncated_action_repair_is_typed_as_exhausted(
    monkeypatch: pytest.MonkeyPatch,
):
    calls = 0

    async def fake_stream(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            while True:
                await asyncio.sleep(0.002)
                yield ModelResponse(
                    id="primary", model="fake-model", content="planning "
                )
        yield ModelResponse(id="repair", model="fake-model", content="still planning")
        yield ModelResponse(id="repair", model="fake-model", finish_reason="length")

    monkeypatch.setattr(llm_agent_module, "acall_llm_model_stream", fake_stream)
    agent = _agent(
        policy=GenerationBudgetPolicy(
            total_timeout_seconds=0.5,
            stream_idle_timeout_seconds=0.1,
            active_tool_free_timeout_seconds=0.01,
            action_repair_timeout_seconds=0.1,
            action_repair_max_output_tokens=64,
        )
    )
    message = _message("truncated-repair")

    with pytest.raises(GenerationBudgetExceeded) as raised:
        await agent.invoke_model(
            messages=[{"role": "user", "content": "create the artifact"}],
            message=message,
            stream=True,
        )

    assert raised.value.reason is GenerationStopReason.ACTION_REPAIR_EXHAUSTED
    assert raised.value.partial_response.finish_reason == "length"
    assert calls == 2
    events = message.context.context_info["generation_budget_events"]
    assert events[-1]["reason"] == GenerationStopReason.ACTION_REPAIR_EXHAUSTED.value


def test_context_overflow_preserves_latest_answer_without_cancellation() -> None:
    response = Agent._context_overflow_response(
        [
            {"role": "user", "content": "work"},
            {"role": "assistant", "content": "partial useful answer"},
            {"role": "tool", "content": "large output"},
        ]
    )

    assert response.content == "partial useful answer"
    assert response.message["aworld_incomplete_reason"] == "context_window_exceeded"
    assert response.message["aworld_recoverable"] is False
