from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

import aworld.agents.llm_agent as llm_agent_module
import aworld.runners.execution_protocol as execution_protocol_module
from aworld.agents.llm_agent import Agent, _LongHorizonReviewContinuation
from aworld.config.conf import AgentConfig
from aworld.core.agent.base import AgentResult
from aworld.core.common import ActionModel, ActionResult, Observation
from aworld.core.context.base import Context
from aworld.core.context.compiler import (
    CompletionContract,
    CompletionMode,
    ValidationCommand,
)
from aworld.core.context.execution_state import (
    get_execution_state,
    record_execution_state,
)
from aworld.core.event.base import Constants, Message
from aworld.core.execution_protocol import (
    ControllerDecision,
    ControllerAction,
    DecisionReason,
    EventKind,
    ExecutionProtocolPolicy,
    ExecutionProtocolStore,
    ProtocolMode,
    ProtocolPhase,
    ProtocolTransition,
)
from aworld.core.task import Task
from aworld.models.model_response import Function, ModelResponse, ToolCall
from aworld.models.reasoning_policy import (
    OPENAI_REASONING_CAPABILITY,
    ReasoningPhasePolicy,
)
from aworld.runners.execution_protocol import (
    build_execution_protocol_telemetry,
    configure_execution_protocol,
    consume_execution_protocol_guidance,
    execution_protocol_model_decision_boundary,
    execution_protocol_policy,
    load_acceptance_critic_state,
    load_candidate_fallback,
    load_execution_protocol_state,
    record_acceptance_probe_observation,
    record_acceptance_probe_plan,
    record_candidate_final,
    load_model_plan_update,
    load_public_probe_receipts,
    mutation_gate_interception,
    record_model_execution_profile,
    record_model_decision_attempt_failure,
    record_model_decision_unavailable,
    record_public_probe_observations,
    record_public_probe_plan,
    record_tool_protocol_event,
    store_candidate_fallback,
)


@pytest.fixture(autouse=True)
def _legacy_protocol_features(monkeypatch):
    monkeypatch.setenv("AWORLD_INDEPENDENT_ACCEPTANCE_CRITIC", "false")
    monkeypatch.setenv("AWORLD_SEMANTIC_PROGRESS_LEDGER", "false")


def _agent(context: Context, policy: ExecutionProtocolPolicy) -> Agent:
    agent = Agent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="offline",
            llm_api_key="offline",
        ),
        execution_protocol_policy=policy,
        max_loop_steps=0,
    )
    agent._llm = object()
    agent.context = context
    return agent


def _declare_long_horizon(context: Context, agent_id: str) -> None:
    transition = record_model_execution_profile(
        context,
        agent_id,
        {
            "horizon": "long",
            "confidence": 0.9,
            "milestone_count": 3,
            "expected_tool_actions": 8,
            "verification_required": True,
        },
    )
    assert transition is not None


def test_phase_reasoning_is_per_call_and_static_caller_pin_wins() -> None:
    context = Context(task_id="reasoning-phase")
    policy = ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE)
    agent = _agent(context, policy)
    agent._llm = SimpleNamespace(
        provider_name="openai",
        provider=SimpleNamespace(
            reasoning_transport_capability=lambda: OPENAI_REASONING_CAPABILITY
        ),
    )
    agent.conf.llm_config.llm_model_name = "aisearch_dsv4flash_cron_job"
    agent.conf.llm_config.reasoning_phase_policy = ReasoningPhasePolicy.balanced()
    original_params = dict(agent.conf.llm_config.params or {})

    execute, execute_receipt = agent._apply_reasoning_phase_policy({}, phase="execute")
    review, review_receipt = agent._apply_reasoning_phase_policy({}, phase="review")

    assert execute["reasoning_effort"] == "high"
    assert "extra_body" not in execute
    assert execute_receipt["source"] == "phase_policy"
    assert review["reasoning_effort"] == "xhigh"
    assert review_receipt["phase"] == "review"
    assert agent.conf.llm_config.params == original_params

    agent.conf.llm_config.params = {"reasoning_effort": "max"}
    pinned, receipt = agent._apply_reasoning_phase_policy({}, phase="execute")
    assert pinned["reasoning_effort"] == "xhigh"
    assert receipt["source"] == "caller"


def test_phase_reasoning_does_not_deepcopy_opaque_sdk_kwargs() -> None:
    class OpaqueSDKValue:
        def __deepcopy__(self, memo):
            raise AssertionError("opaque SDK value must not be deep-copied")

    agent = _agent(Context(task_id="reasoning-opaque"), ExecutionProtocolPolicy())
    agent._llm = SimpleNamespace(
        provider_name="openai",
        provider=SimpleNamespace(
            reasoning_transport_capability=lambda: OPENAI_REASONING_CAPABILITY
        ),
    )
    agent.conf.llm_config.llm_model_name = "gpt-4.1"
    opaque = OpaqueSDKValue()

    unchanged, _ = agent._apply_reasoning_phase_policy(
        {"response_format": opaque}, phase="execute"
    )
    agent.conf.llm_config.reasoning_phase_policy = ReasoningPhasePolicy.balanced()
    selected, _ = agent._apply_reasoning_phase_policy(
        {"response_format": opaque}, phase="execute"
    )

    assert unchanged["response_format"] is opaque
    assert selected["response_format"] is opaque
    assert selected["reasoning_effort"] == "high"
    assert "extra_body" not in selected


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"decision_boundary": "initial"}, "plan"),
        ({"independent_acceptance_review": True}, "review"),
        ({"model_owned_review": True}, "review"),
        ({"tool_free_finalization": True}, "finalize"),
        ({"role_reasoning_phase": "review"}, "review"),
        ({}, "execute"),
    ],
)
def test_reasoning_phase_covers_every_execution_boundary(kwargs, expected):
    values = {
        "decision_boundary": None,
        "independent_acceptance_review": False,
        "model_owned_review": False,
        "tool_free_finalization": False,
        "role_reasoning_phase": None,
        **kwargs,
    }

    assert Agent._reasoning_phase_for_turn(**values) == expected


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (
            {
                "tool_free_finalization": False,
                "independent_acceptance_review": False,
                "model_owned_review": False,
                "decision_boundary": None,
            },
            "ordinary",
        ),
        (
            {
                "tool_free_finalization": False,
                "independent_acceptance_review": False,
                "model_owned_review": True,
                "decision_boundary": None,
            },
            "control",
        ),
    ],
)
def test_model_owned_review_is_not_solver_resolution_authority(values, expected):
    assert Agent._execution_state_resolution_mode_for_turn(**values) == expected


@pytest.mark.asyncio
async def test_review_model_error_returns_original_candidate_as_incomplete() -> (
    None
):
    context = Context(task_id="review-error")
    context.set_task(Task(id="review-error", input="finish the task"))
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        activation_event_threshold=1,
        independent_acceptance_enabled=False,
    )
    agent = _agent(context, policy)
    configure_execution_protocol(context, agent.id(), policy)
    _declare_long_horizon(context, agent.id())
    record_tool_protocol_event(
        context,
        agent.id(),
        {
            "goal_progress_observable": False,
            "goal_progress": False,
            "validation_evidence_advanced": True,
        },
    )
    record_candidate_final(context, agent.id())
    fallback = ActionModel(agent_name=agent.id(), policy_info="best current result")
    calls = 0

    async def attempt(observation, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _LongHorizonReviewContinuation(
                observation=Observation(content="review current evidence"),
                kwargs={},
                fallback_actions=(fallback,),
            )
        raise RuntimeError("review provider unavailable")

    agent._async_policy_once = attempt
    message = Message(category=Constants.AGENT, headers={"context": context})

    result = await agent.async_policy(Observation(content="candidate"), message=message)

    assert result == [fallback]
    assert agent.finished is True
    assert get_execution_state(context)["status"] == "incomplete"
    assert get_execution_state(context)["reason"] == (
        "model_owned_review_error_unverified"
    )


def _independent_review_failure_fixture(
    task_id: str,
) -> tuple[Context, Agent, ExecutionProtocolPolicy, ActionModel]:
    context = Context(task_id=task_id)
    context.set_task(Task(id=task_id, input="finish the task", timeout=600))
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(),
            immutable_inputs=(),
            validation_commands=(
                ValidationCommand(command_id="registered-check", argv=("true",)),
            ),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
        ),
        mode=CompletionMode.ENFORCE,
    )
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        review_unarmed_candidates=True,
        independent_acceptance_enabled=True,
        max_repairs=0,
    )
    agent = _agent(context, policy)
    configure_execution_protocol(context, agent.id(), policy)
    fallback = ActionModel(agent_name=agent.id(), policy_info="preserved candidate")
    record_candidate_final(context, agent.id(), actions=(fallback,))
    store_candidate_fallback(context, agent.id(), (fallback,))
    return context, agent, policy, fallback


@pytest.mark.asyncio
async def test_independent_review_provider_error_preserves_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AWORLD_INDEPENDENT_ACCEPTANCE_CRITIC", raising=False)
    context, agent, _policy, fallback = _independent_review_failure_fixture(
        "independent-review-provider-error"
    )

    async def fail_review(_observation, **_kwargs):
        raise RuntimeError("critic provider unavailable")

    agent._async_policy_once = fail_review
    message = Message(category=Constants.AGENT, headers={"context": context})

    result = await agent.async_policy(Observation(content="candidate"), message=message)

    assert result == [fallback]
    state = get_execution_state(context, agent.id())
    assert state["status"] == "incomplete"
    assert state["reason"] == "independent_acceptance_review_error"
    assert state["recoverable"] is True


@pytest.mark.asyncio
async def test_independent_review_budget_stop_preserves_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AWORLD_INDEPENDENT_ACCEPTANCE_CRITIC", raising=False)
    context, agent, _policy, fallback = _independent_review_failure_fixture(
        "independent-review-budget-stop"
    )

    async def continue_review(_observation, **_kwargs):
        return _LongHorizonReviewContinuation(
            observation=Observation(content="review current evidence"),
            kwargs={},
            fallback_actions=(fallback,),
        )

    async def terminate(_message):
        return True

    agent._async_policy_once = continue_review
    agent.should_terminate_loop = terminate
    message = Message(category=Constants.AGENT, headers={"context": context})

    result = await agent.async_policy(Observation(content="candidate"), message=message)

    assert result == [fallback]
    state = get_execution_state(context, agent.id())
    assert state["status"] == "incomplete"
    assert state["reason"] == "independent_acceptance_review_budget_stop"
    assert state["recoverable"] is True


@pytest.mark.asyncio
async def test_independent_critic_persistence_error_preserves_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AWORLD_INDEPENDENT_ACCEPTANCE_CRITIC", raising=False)
    context, agent, policy, fallback = _independent_review_failure_fixture(
        "independent-critic-persistence-error"
    )

    async def no_memory(*_args, **_kwargs):
        return None

    async def build_input(_observation, info=None, message=None, **_kwargs):
        return [{"role": "user", "content": "review candidate"}]

    async def no_tools(context=None):
        return None

    critic_response = ModelResponse(
        id="critic-persistence-error",
        model="offline",
        content=json.dumps(
            {
                "decision": "uncertain",
                "highest_risk_counterexample": "persistence unavailable",
                "hypothesis_id": "persistence",
                "reason": "the protocol state could not be saved",
            }
        ),
        message={"role": "assistant", "content": "critic decision"},
        finish_reason="stop",
        usage={"prompt_tokens": 1, "completion_tokens": 1},
    )

    async def invoke_model(messages=None, message=None, **_kwargs):
        return critic_response

    def fail_protocol_persistence(_context, _agent_id, _value):
        state = ExecutionProtocolStore(context, agent.id(), policy).load()
        return (
            ProtocolTransition(
                state=state,
                decision=ControllerDecision(
                    action=ControllerAction.STOP_INCOMPLETE,
                    reason=DecisionReason.PERSISTENCE_ERROR,
                ),
            ),
            None,
            False,
        )

    agent._add_message_to_memory = no_memory
    agent.build_llm_input = build_input
    agent._filter_tools = no_tools
    agent.invoke_model = invoke_model
    monkeypatch.setattr(
        execution_protocol_module,
        "record_acceptance_critic_decision",
        fail_protocol_persistence,
    )
    message = Message(category=Constants.AGENT, headers={"context": context})

    result = await agent.async_policy(Observation(content="candidate"), message=message)

    assert result == [fallback]
    assert critic_response.message["content"] == "preserved candidate"
    assert critic_response.message["aworld_incomplete_reason"] == (
        "independent_acceptance_protocol_persistence_error"
    )
    assert critic_response.message["aworld_recoverable"] is True
    state = get_execution_state(context, agent.id())
    assert state["status"] == "incomplete"
    assert state["reason"] == (
        "independent_acceptance_protocol_persistence_error"
    )
    assert state["recoverable"] is True
    assert load_candidate_fallback(context, agent.id()) == (fallback,)


@pytest.mark.asyncio
async def test_review_timeout_returns_original_candidate() -> None:
    context = Context(task_id="review-timeout")
    context.set_task(Task(id="review-timeout", input="finish the task", timeout=60))
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        activation_event_threshold=1,
        final_review_timeout_seconds=0.01,
        independent_acceptance_enabled=False,
    )
    agent = _agent(context, policy)
    configure_execution_protocol(context, agent.id(), policy)
    fallback = ActionModel(agent_name=agent.id(), policy_info="safe candidate")
    calls = 0

    async def attempt(observation, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _LongHorizonReviewContinuation(
                observation=Observation(content="review current evidence"),
                kwargs={},
                fallback_actions=(fallback,),
            )
        await asyncio.sleep(1)
        return [ActionModel(agent_name=agent.id(), policy_info="too late")]

    agent._async_policy_once = attempt
    message = Message(category=Constants.AGENT, headers={"context": context})

    result = await agent.async_policy(Observation(content="candidate"), message=message)

    assert result == [fallback]
    assert calls == 2
    assert get_execution_state(context)["reason"] == (
        "model_owned_review_error_unverified"
    )


@pytest.mark.asyncio
async def test_review_deadline_is_shared_across_continuation_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = Context(task_id="review-shared-deadline")
    context.set_task(
        Task(id="review-shared-deadline", input="finish the task", timeout=600)
    )
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        activation_event_threshold=1,
        final_review_timeout_seconds=10,
        independent_acceptance_enabled=False,
    )
    agent = _agent(context, policy)
    configure_execution_protocol(context, agent.id(), policy)
    fallback = ActionModel(agent_name=agent.id(), policy_info="bounded candidate")
    calls = 0
    clock = 0.0

    def monotonic_now() -> float:
        return clock

    async def attempt(observation, **kwargs):
        nonlocal calls, clock
        calls += 1
        if calls > 1:
            clock += 6.0
        return _LongHorizonReviewContinuation(
            observation=Observation(content=f"review pass {calls}"),
            kwargs={},
            fallback_actions=(fallback,),
        )

    monkeypatch.setattr(llm_agent_module, "_monotonic_now", monotonic_now)
    agent._async_policy_once = attempt
    message = Message(category=Constants.AGENT, headers={"context": context})

    result = await agent.async_policy(Observation(content="candidate"), message=message)

    assert result == [fallback]
    assert calls == 3
    assert agent.finished is True
    assert get_execution_state(context)["reason"] == (
        "model_owned_review_error_unverified"
    )


@pytest.mark.asyncio
async def test_effectively_disabled_independent_review_budget_stop_keeps_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AWORLD_INDEPENDENT_ACCEPTANCE_CRITIC", raising=False)
    context = Context(task_id="review-budget-fail-open")
    context.set_task(
        Task(id="review-budget-fail-open", input="finish the task", timeout=600)
    )
    requested_policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        review_unarmed_candidates=True,
        independent_acceptance_enabled=True,
    )
    agent = _agent(context, requested_policy)
    configure_execution_protocol(context, agent.id(), requested_policy)
    effective_policy = execution_protocol_policy(context, agent.id())
    assert effective_policy.independent_acceptance_enabled is False
    record_candidate_final(context, agent.id())
    fallback = ActionModel(agent_name=agent.id(), policy_info="bounded candidate")

    async def attempt(observation, **kwargs):
        return _LongHorizonReviewContinuation(
            observation=Observation(content="review current evidence"),
            kwargs={},
            fallback_actions=(fallback,),
        )

    async def terminate(message):
        return True

    agent._async_policy_once = attempt
    agent.should_terminate_loop = terminate
    message = Message(category=Constants.AGENT, headers={"context": context})

    result = await agent.async_policy(Observation(content="candidate"), message=message)

    assert result == [fallback]
    assert agent.finished is True
    assert get_execution_state(context)["reason"] == (
        "model_owned_review_budget_stop_unverified"
    )
    assert agent._load_long_horizon_review_deadline(context) is None


@pytest.mark.asyncio
async def test_review_deadline_survives_probe_tool_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AWORLD_INDEPENDENT_ACCEPTANCE_CRITIC", raising=False)
    clock = 0.0
    calls = 0
    prepared_tools = []
    registered_command = "pytest -q tests/test_contract.py"

    def monotonic_now() -> float:
        return clock

    class ProbeRoundTripAgent(Agent):
        async def _add_message_to_memory(self, *args, **kwargs):
            return None

        async def build_llm_input(self, observation, info=None, message=None, **kwargs):
            return [{"role": "user", "content": str(observation.content or "")}]

        async def _filter_tools(self, context=None):
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "terminal__execute",
                        "parameters": {
                            "type": "object",
                            "properties": {"command": {"type": "string"}},
                            "required": ["command"],
                        },
                    },
                }
            ]

        async def invoke_model(self, messages=None, message=None, **kwargs):
            nonlocal calls
            calls += 1
            prepared_tools.append(kwargs.get("prepared_tools"))
            if calls == 1:
                return ModelResponse(
                    id="candidate",
                    model="offline",
                    content="candidate requiring an independent check",
                    message={
                        "role": "assistant",
                        "content": "candidate requiring an independent check",
                    },
                    finish_reason="stop",
                    usage={"prompt_tokens": 1, "completion_tokens": 1},
                )
            if calls == 2:
                arguments = {
                    "command": registered_command,
                    "__aworld_acceptance_probe": {
                        "hypothesis_id": "registered-check",
                        "highest_risk_counterexample": "the registered check fails",
                        "probe_kind": "independent_cross_check",
                    },
                }
                tool_calls = [
                    ToolCall(
                        id="probe-1",
                        function=Function(
                            name="terminal__execute",
                            arguments=json.dumps(arguments),
                        ),
                    )
                ]
                return ModelResponse(
                    id="probe",
                    model="offline",
                    content="",
                    message={
                        "role": "assistant",
                        "content": "",
                        "tool_calls": tool_calls,
                    },
                    tool_calls=tool_calls,
                    finish_reason="tool_calls",
                    usage={"prompt_tokens": 1, "completion_tokens": 1},
                )
            tool_calls = [
                ToolCall(
                    id="repair-1",
                    function=Function(
                        name="terminal__execute",
                        arguments='{"command":"inspect and repair"}',
                    ),
                )
            ]
            return ModelResponse(
                id="repair",
                model="offline",
                content="",
                message={
                    "role": "assistant",
                    "content": "",
                    "tool_calls": tool_calls,
                },
                tool_calls=tool_calls,
                finish_reason="tool_calls",
                usage={"prompt_tokens": 1, "completion_tokens": 1},
            )

    context = Context(task_id="review-probe-round-trip")
    context.set_task(
        Task(id="review-probe-round-trip", input="finish the task", timeout=600)
    )
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(),
            immutable_inputs=(),
            validation_commands=(
                ValidationCommand(
                    command_id="registered-check",
                    argv=("pytest", "-q", "tests/test_contract.py"),
                ),
            ),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
        ),
        mode=CompletionMode.ENFORCE,
    )
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        review_unarmed_candidates=True,
        independent_acceptance_enabled=True,
        final_review_timeout_seconds=10,
        max_repairs=1,
        max_final_reviews=2,
    )
    agent = ProbeRoundTripAgent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="offline",
            llm_api_key="offline",
        ),
        execution_protocol_policy=policy,
        max_loop_steps=0,
    )
    monkeypatch.setattr(llm_agent_module, "_monotonic_now", monotonic_now)
    message = Message(category=Constants.AGENT, headers={"context": context})

    probe = await agent.async_policy(Observation(content="start"), message=message)

    assert calls == 2
    assert probe[0].tool_call_id == "probe-1"
    assert record_acceptance_probe_observation(
        context,
        agent.id(),
        actions=probe,
        result_projection={
            "tool_call_id": "probe-1",
            "success": True,
            "return_code": 0,
            "stdout_tail": "1 passed",
            "stderr_tail": "",
            "content_tail": "",
            "failure_code": None,
            "observed_content_present": False,
            "observed_content_hash": "sha256:empty",
        },
        success=True,
        failure_code=None,
        artifact_after="sha256:after",
    )
    clock = 11.0

    repair = await agent.async_policy(Observation(content="1 passed"), message=message)

    assert calls == 3
    assert repair[0].tool_call_id == "repair-1"
    assert repair[0].params == {"command": "inspect and repair"}
    assert prepared_tools[2] is not None
    properties = prepared_tools[2][0]["function"]["parameters"]["properties"]
    assert "__aworld_acceptance_probe" not in properties
    assert load_acceptance_critic_state(context, agent.id()) == {}
    assert agent._load_long_horizon_review_deadline(context) is None
    state = ExecutionProtocolStore(context, agent.id(), policy).load()
    assert state.phase.value == "repair"
    assert state.review_pending is False


@pytest.mark.asyncio
async def test_independent_review_error_resumes_tool_enabled_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AWORLD_INDEPENDENT_ACCEPTANCE_CRITIC", raising=False)
    context = Context(task_id="review-error-repair")
    context.set_task(
        Task(id="review-error-repair", input="finish the task", timeout=600)
    )
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(),
            immutable_inputs=(),
            validation_commands=(
                ValidationCommand(
                    command_id="registered-check",
                    argv=("true",),
                ),
            ),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
        ),
        mode=CompletionMode.ENFORCE,
    )
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        review_unarmed_candidates=True,
        independent_acceptance_enabled=True,
        max_repairs=1,
        max_final_reviews=2,
    )
    agent = _agent(context, policy)
    configure_execution_protocol(context, agent.id(), policy)
    fallback = ActionModel(agent_name=agent.id(), policy_info="unverified candidate")
    record_candidate_final(context, agent.id(), actions=(fallback,))
    store_candidate_fallback(context, agent.id(), (fallback,))
    registered_command = "true"
    assert record_acceptance_probe_plan(
        context,
        agent.id(),
        tool_call_id="stale-probe",
        hypothesis_id="stale-hypothesis",
        highest_risk_counterexample="the old candidate fails validation",
        tool_identity="terminal:execute",
        arguments_projection={"command": registered_command},
        probe_kind="independent_cross_check",
    )
    calls = 0

    async def attempt(observation, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("critic provider unavailable")
        return [
            ActionModel(
                agent_name=agent.id(),
                tool_name="terminal",
                action_name="run_code",
                params={"code": "inspect-and-repair"},
            )
        ]

    agent._async_policy_once = attempt
    message = Message(category=Constants.AGENT, headers={"context": context})

    result = await agent.async_policy(Observation(content="candidate"), message=message)

    assert calls == 2
    assert result[0].action_name == "run_code"
    assert agent.finished is False
    state = ExecutionProtocolStore(context, agent.id(), policy).load()
    assert state.phase.value == "repair"
    assert state.repair_count == 1
    assert state.review_pending is False
    execution_state = get_execution_state(context)
    assert execution_state["status"] == "running"
    assert execution_state["reason"] == "independent_acceptance_review_repair_scheduled"
    assert execution_state["recoverable"] is False
    assert load_acceptance_critic_state(context, agent.id()) == {}

    # The next candidate owns a fresh critic episode. A stale planned probe
    # from the failed review must not reject its new probe plan.
    transition = record_candidate_final(
        context,
        agent.id(),
        actions=(
            ActionModel(agent_name=agent.id(), policy_info="repaired candidate"),
        ),
    )
    assert transition is not None
    assert transition.state.review_pending is True
    assert record_acceptance_probe_plan(
        context,
        agent.id(),
        tool_call_id="fresh-probe",
        hypothesis_id="fresh-hypothesis",
        highest_risk_counterexample="the repaired candidate fails validation",
        tool_identity="terminal:execute",
        arguments_projection={"command": registered_command},
        probe_kind="independent_cross_check",
    )


@pytest.mark.asyncio
async def test_typed_critic_repair_survives_post_llm_hook_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AWORLD_INDEPENDENT_ACCEPTANCE_CRITIC", raising=False)
    calls = 0
    prepared_tools = []

    async def failing_post_critic_hook(context, hook_point, **kwargs):
        payload = kwargs.get("payload")
        if (
            hook_point is llm_agent_module.HookPoint.POST_LLM_CALL
            and getattr(payload, "id", None) == "critic-repair"
        ):
            raise RuntimeError("post-critic hook failed")
        if False:
            yield None

    class HookFailureAfterDecisionAgent(Agent):
        async def _add_message_to_memory(self, *args, **kwargs):
            return None

        async def build_llm_input(self, observation, info=None, message=None, **kwargs):
            return [{"role": "user", "content": str(observation.content or "")}]

        async def _filter_tools(self, context=None):
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "terminal__execute",
                        "parameters": {
                            "type": "object",
                            "properties": {"command": {"type": "string"}},
                            "required": ["command"],
                        },
                    },
                }
            ]

        async def invoke_model(self, messages=None, message=None, **kwargs):
            nonlocal calls
            calls += 1
            prepared_tools.append(kwargs.get("prepared_tools"))
            if calls == 1:
                return ModelResponse(
                    id="candidate",
                    model="offline",
                    content="candidate before critic repair",
                    message={
                        "role": "assistant",
                        "content": "candidate before critic repair",
                    },
                    finish_reason="stop",
                    usage={"prompt_tokens": 1, "completion_tokens": 1},
                )
            if calls == 2:
                content = json.dumps(
                    {
                        "decision": "repair",
                        "highest_risk_counterexample": "the registered check fails",
                        "hypothesis_id": "registered-check",
                        "reason": "repair the independently identified gap",
                    }
                )
                return ModelResponse(
                    id="critic-repair",
                    model="offline",
                    content=content,
                    message={"role": "assistant", "content": content},
                    finish_reason="stop",
                    usage={"prompt_tokens": 1, "completion_tokens": 1},
                )
            tool_calls = [
                ToolCall(
                    id="repair-after-hook",
                    function=Function(
                        name="terminal__execute",
                        arguments='{"command":"repair the gap"}',
                    ),
                )
            ]
            return ModelResponse(
                id="repair-tool",
                model="offline",
                content="",
                message={
                    "role": "assistant",
                    "content": "",
                    "tool_calls": tool_calls,
                },
                tool_calls=tool_calls,
                finish_reason="tool_calls",
                usage={"prompt_tokens": 1, "completion_tokens": 1},
            )

    context = Context(task_id="critic-repair-hook-error")
    context.set_task(
        Task(id="critic-repair-hook-error", input="finish the task", timeout=600)
    )
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(),
            immutable_inputs=(),
            validation_commands=(
                ValidationCommand(command_id="registered-check", argv=("true",)),
            ),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
        ),
        mode=CompletionMode.ENFORCE,
    )
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        review_unarmed_candidates=True,
        independent_acceptance_enabled=True,
        max_repairs=1,
        max_final_reviews=1,
    )
    agent = HookFailureAfterDecisionAgent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="offline",
            llm_api_key="offline",
        ),
        execution_protocol_policy=policy,
        max_loop_steps=0,
    )
    monkeypatch.setattr(llm_agent_module, "run_hooks", failing_post_critic_hook)
    message = Message(category=Constants.AGENT, headers={"context": context})

    result = await agent.async_policy(Observation(content="start"), message=message)

    assert calls == 3
    assert result[0].tool_call_id == "repair-after-hook"
    assert result[0].params == {"command": "repair the gap"}
    assert prepared_tools[2] is not None
    properties = prepared_tools[2][0]["function"]["parameters"]["properties"]
    assert "__aworld_acceptance_probe" not in properties
    state = ExecutionProtocolStore(context, agent.id(), policy).load()
    assert state.phase.value == "repair"
    assert state.review_pending is False
    execution_state = get_execution_state(context)
    assert execution_state["status"] == "running"
    assert agent._load_long_horizon_review_deadline(context) is None


def test_agent_rejects_untyped_execution_protocol_policy() -> None:
    context = Context(task_id="bad-policy")

    with pytest.raises(TypeError, match="ExecutionProtocolPolicy"):
        Agent(
            name="Aworld",
            conf=AgentConfig(
                llm_provider="openai",
                llm_model_name="offline",
                llm_api_key="offline",
            ),
            execution_protocol_policy={"mode": "guide"},
            context=context,
        )


def test_agent_uses_default_convergence_with_skill_specific_review() -> None:
    agent = Agent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="offline",
            llm_api_key="offline",
        ),
    )
    agent.skill_configs = {"long-running-agent": {"active": True}}

    assert agent._resolve_execution_protocol_policy().mode is ProtocolMode.GUIDE
    assert agent._resolve_execution_protocol_policy().review_unarmed_candidates is True
    agent.skill_configs["long-running-agent"]["active"] = False
    assert agent._resolve_execution_protocol_policy().mode is ProtocolMode.GUIDE
    assert agent._resolve_execution_protocol_policy().review_unarmed_candidates is False


def test_explicit_off_policy_remains_a_developer_rollback() -> None:
    agent = Agent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="offline",
            llm_api_key="offline",
        ),
        execution_protocol_policy=ExecutionProtocolPolicy(mode=ProtocolMode.OFF),
    )
    agent.skill_configs = {"long-running-agent": {"active": True}}

    assert agent._resolve_execution_protocol_policy().mode is ProtocolMode.OFF


def test_long_running_skill_can_disable_review_for_unarmed_candidates(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "AWORLD_EXECUTION_PROTOCOL_REVIEW_UNARMED_CANDIDATES", "false"
    )
    agent = Agent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="offline",
            llm_api_key="offline",
        ),
    )
    agent.skill_configs = {"long-running-agent": {"active": True}}

    assert agent._resolve_execution_protocol_policy().review_unarmed_candidates is False


def test_runtime_can_enable_model_review_for_every_candidate(monkeypatch) -> None:
    monkeypatch.delenv("AWORLD_INDEPENDENT_ACCEPTANCE_CRITIC", raising=False)
    monkeypatch.delenv("AWORLD_SEMANTIC_PROGRESS_LEDGER", raising=False)
    monkeypatch.setenv("AWORLD_EXECUTION_PROTOCOL_REVIEW_UNARMED_CANDIDATES", "true")
    agent = Agent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="offline",
            llm_api_key="offline",
        ),
    )
    agent.skill_configs = {"long-running-agent": {"active": True}}

    policy = agent._resolve_execution_protocol_policy()

    assert policy.mode is ProtocolMode.GUIDE
    assert policy.review_unarmed_candidates is True
    assert policy.independent_acceptance_enabled is True
    assert policy.semantic_progress_enabled is True


def test_runtime_canary_flags_can_disable_new_protocol_features(monkeypatch) -> None:
    monkeypatch.setenv("AWORLD_INDEPENDENT_ACCEPTANCE_CRITIC", "false")
    monkeypatch.setenv("AWORLD_SEMANTIC_PROGRESS_LEDGER", "0")
    agent = Agent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="offline",
            llm_api_key="offline",
        ),
    )
    agent.skill_configs = {"long-running-agent": {"active": True}}

    policy = agent._resolve_execution_protocol_policy()

    assert policy.independent_acceptance_enabled is False
    assert policy.semantic_progress_enabled is False


def test_review_every_candidate_env_does_not_expand_disabled_skill_review(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AWORLD_EXECUTION_PROTOCOL_REVIEW_UNARMED_CANDIDATES", "true")
    agent = Agent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="offline",
            llm_api_key="offline",
        ),
    )
    agent.skill_configs = {"long-running-agent": {"active": False}}

    policy = agent._resolve_execution_protocol_policy()

    assert policy.mode is ProtocolMode.GUIDE
    assert policy.review_unarmed_candidates is False


def test_agent_offers_required_initial_model_decision_before_real_tools() -> None:
    context = Context(task_id="profile-schema")
    context.set_task(Task(id="profile-schema", timeout=600))
    policy = ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE)
    agent = _agent(context, policy)
    agent.skill_configs = {"long-running-agent": {"active": True}}
    configure_execution_protocol(context, agent.id(), policy)
    tools = [
        {
            "type": "function",
            "function": {
                "name": "terminal__execute",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            },
        }
    ]

    augmented, offer = agent._with_long_horizon_execution_profile(tools, context)

    assert offer.profile_schema_offered is True
    assert offer.decision_boundary == "initial"
    assert offer.carrier_function_name == "aworld__execution_decision"
    assert len(augmented) == 1
    assert augmented[0]["function"]["name"] == "aworld__execution_decision"
    assert (
        "__aworld_execution_profile"
        not in (tools[0]["function"]["parameters"]["properties"])
    )
    profile = augmented[0]["function"]["parameters"]["properties"][
        "__aworld_execution_profile"
    ]
    assert profile["type"] == "object"
    assert profile["additionalProperties"] is False
    assert set(profile["required"]) == {
        "horizon",
        "confidence",
        "milestone_count",
        "expected_tool_actions",
        "verification_required",
        "workspace_mutation_required",
    }
    plan = augmented[0]["function"]["parameters"]["properties"]["__aworld_plan_update"]
    assert plan["type"] == "object"
    assert "submit_current" in plan["properties"]["delivery_intent"]["enum"]
    assert set(plan["required"]) == {
        "decision",
        "horizon",
        "milestone",
        "next_action",
        "next_action_tool",
        "next_action_arguments",
        "verification_plan",
        "completion_assessment",
        "delivery_intent",
        "delivery_rationale",
        "assumptions",
        "retired_approaches",
        "evidence_refs",
        "selected_candidate_id",
    }
    assert set(augmented[0]["function"]["parameters"]["required"]) == {
        "__aworld_execution_profile",
        "__aworld_plan_update",
    }
    contract_table = json.loads(
        plan["properties"]["next_action_arguments"]["description"].rsplit(": ", 1)[1]
    )
    assert contract_table["terminal__execute"] == '{"command":string!}'


def test_initial_decision_schema_does_not_duplicate_across_large_tool_catalog():
    context = Context(task_id="profile-schema-large")
    context.set_task(Task(id="profile-schema-large", timeout=600))
    policy = ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE)
    agent = _agent(context, policy)
    agent.skill_configs = {"long-running-agent": {"active": True}}
    configure_execution_protocol(context, agent.id(), policy)
    tools = [
        {
            "type": "function",
            "function": {
                "name": (
                    "terminal__execute" if index == 37 else f"service_{index}__read"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                },
            },
        }
        for index in range(50)
    ]

    augmented, offer = agent._with_long_horizon_execution_profile(tools, context)

    assert offer.carrier_function_name == "aworld__execution_decision"
    carriers = [
        schema
        for schema in augmented
        if "__aworld_plan_update" in schema["function"]["parameters"]["properties"]
    ]
    assert len(carriers) == 1
    assert len(json.dumps(augmented)) < 8_000


def test_decision_tool_contract_is_globally_bounded_for_oversized_catalog():
    tools = [
        {
            "type": "function",
            "function": {
                "name": f"service_{index:03d}__read",
                "parameters": {
                    "type": "object",
                    "properties": {
                        f"parameter_{field:02d}": {"type": "string"}
                        for field in range(48)
                    },
                    "required": ["parameter_00"],
                },
            },
        }
        for index in range(200)
    ]
    tools.append(
        {
            "type": "function",
            "function": {
                "name": "terminal__execute",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            },
        }
    )
    context = Context(task_id="oversized-decision-catalog")
    context.set_task(Task(id="oversized-decision-catalog", timeout=600))
    policy = ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE)
    agent = _agent(context, policy)
    agent.skill_configs = {"long-running-agent": {"active": True}}
    configure_execution_protocol(context, agent.id(), policy)

    offered, offer = agent._with_long_horizon_execution_profile(tools, context)
    schema = offered[0]["function"]["parameters"]["properties"]["__aworld_plan_update"]
    serialized = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    tool_schema = schema["properties"]["next_action_tool"]
    contracts = json.loads(
        schema["properties"]["next_action_arguments"]["description"].rsplit(": ", 1)[1]
    )

    assert len(serialized) < 8_000
    assert set(tool_schema["anyOf"][0]["enum"]) == {
        tool["function"]["name"] for tool in tools
    }
    assert len(offer.decision_tool_names) == len(tools)
    assert "terminal__execute" in offer.decision_tool_names
    assert len(contracts) < len(tools)


def test_extreme_tool_name_catalog_fails_open_to_ordinary_tools():
    context = Context(task_id="decision-schema-overflow")
    context.set_task(Task(id="decision-schema-overflow", timeout=600))
    policy = ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE)
    agent = _agent(context, policy)
    agent.skill_configs = {"long-running-agent": {"active": True}}
    configure_execution_protocol(context, agent.id(), policy)
    tools = [
        {
            "type": "function",
            "function": {
                "name": f"tool_{index:02d}_" + ("x" * 240),
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for index in range(40)
    ]

    offered, offer = agent._with_long_horizon_execution_profile(tools, context)

    assert offered == tools
    assert offer.decision_boundary is None
    assert execution_protocol_model_decision_boundary(context, agent.id()) is None
    telemetry = build_execution_protocol_telemetry(context, agent.id())
    assert telemetry["initial_decision_status"] == "fail_open_unknown"
    assert telemetry["initial_decision_fail_open_reason"] == (
        "decision_schema_overflow"
    )


def test_reserved_real_tool_parameter_is_never_consumed_as_aworld_control():
    context = Context(task_id="profile-reserved-collision")
    context.set_task(Task(id="profile-reserved-collision", timeout=600))
    policy = ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE)
    agent = _agent(context, policy)
    agent.skill_configs = {"long-running-agent": {"active": True}}
    configure_execution_protocol(context, agent.id(), policy)
    tools = [
        {
            "type": "function",
            "function": {
                "name": "terminal__execute",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "__aworld_plan_update": {"type": "string"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "filesystem__write",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
            },
        },
    ]
    # Resolve the initial decision first so ordinary per-Tool probe controls
    # are offered without taking ownership of colliding real parameters.
    record_model_execution_profile(
        context,
        agent.id(),
        {
            "horizon": "short",
            "confidence": 0.9,
            "milestone_count": 1,
            "expected_tool_actions": 1,
            "verification_required": False,
        },
    )
    _, offer = agent._with_long_horizon_execution_profile(tools, context)
    action = ActionModel(
        tool_name="terminal",
        action_name="execute",
        params={"command": "true", "__aworld_plan_update": "real-tool-value"},
    )
    result = AgentResult(current_state=None, actions=[action], is_call_tool=True)

    agent._consume_long_horizon_execution_profile(result, context, offer=offer)

    assert offer.carrier_function_name == "filesystem__write"
    assert action.params["__aworld_plan_update"] == "real-tool-value"


def test_agent_strips_and_records_optional_public_probe_control() -> None:
    context = Context(task_id="public-probe-control")
    context.set_task(Task(id="public-probe-control", input="validate output"))
    policy = ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE)
    agent = _agent(context, policy)
    agent.skill_configs = {"long-running-agent": {"active": True}}
    configure_execution_protocol(context, agent.id(), policy)
    record_model_execution_profile(
        context,
        agent.id(),
        {
            "horizon": "short",
            "confidence": 0.9,
            "milestone_count": 1,
            "expected_tool_actions": 1,
            "verification_required": True,
        },
    )
    action = ActionModel(
        tool_name="terminal",
        action_name="execute",
        tool_call_id="probe-call",
        params={
            "command": "pytest -q",
            "__aworld_public_probe": {
                "hypothesis_id": "regression-suite",
                "highest_risk_counterexample": "the changed path still fails",
                "probe_kind": "regression",
            },
        },
    )
    result = AgentResult(current_state=None, actions=[action], is_call_tool=True)
    tools = [
        {
            "type": "function",
            "function": {
                "name": "terminal__execute",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                },
            },
        }
    ]
    _, offer = agent._with_long_horizon_execution_profile(tools, context)

    assert agent._consume_public_probe_controls(result, context, offer=offer) == 1
    assert action.params == {"command": "pytest -q"}
    assert load_public_probe_receipts(context, agent.id()) == []


def test_agent_consumes_explicit_decision_without_forwarding_internal_tool() -> None:
    context = Context(task_id="profile-consume")
    context.set_task(Task(id="profile-consume", timeout=600))
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        activation_event_threshold=20,
    )
    agent = _agent(context, policy)
    agent.skill_configs = {"long-running-agent": {"active": True}}
    agent.tool_mapping = {"run_code": "docker__run_code"}
    configure_execution_protocol(context, agent.id(), policy)
    action = ActionModel(
        tool_name="aworld",
        action_name="execution_decision",
        tool_call_id="call-control-decision",
        params={
            "__aworld_execution_profile": {
                "horizon": "long",
                "confidence": 0.9,
                "milestone_count": 4,
                "expected_tool_actions": 12,
                "verification_required": True,
            },
            "__aworld_plan_update": {
                "decision": "replan",
                "horizon": "long",
                "milestone": "runnable candidate",
                "next_action": "run the smoke test",
                "next_action_tool": "run_code",
                "next_action_arguments": '{"code":"cat /app/input.txt"}',
                "verification_plan": "inspect the smoke-test exit code",
                "completion_assessment": "in_progress",
                "delivery_intent": "validate_candidate",
                "delivery_rationale": "the candidate needs a fresh smoke test",
                "assumptions": [],
                "retired_approaches": ["static inspection only"],
                "evidence_refs": ["artifact:sha256:abc"],
                "selected_candidate_id": "candidate-1",
            },
        },
    )
    result = AgentResult(current_state=None, actions=[action], is_call_tool=True)
    tools = [
        {
            "type": "function",
            "function": {
                "name": "run_code",
                "parameters": {
                    "type": "object",
                    "properties": {"code": {"type": "string"}},
                },
            },
        }
    ]
    _, offer = agent._with_long_horizon_execution_profile(tools, context)

    agent._consume_long_horizon_execution_profile(result, context, offer=offer)

    assert action.params == {}
    state = ExecutionProtocolStore(context, agent.id(), policy).load()
    assert state.long_horizon_armed is True
    assert state.attempt_epoch == 1
    assert state.replan_requested_count == 0
    assert state.replan_applied_count == 0
    assert state.model_plan_update.decision_call_id == "call-control-decision"
    assert "workspace.execute" in (
        state.model_plan_update.next_action_semantics.capability_aliases
    )
    assert (
        load_model_plan_update(context, agent.id())["selected_candidate_id"]
        == "candidate-1"
    )


def test_malformed_initial_decision_retries_once_then_fails_open_unknown():
    context = Context(task_id="profile-invalid-bounded")
    context.set_task(Task(id="profile-invalid-bounded", timeout=600))
    policy = ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE)
    agent = _agent(context, policy)
    agent.skill_configs = {"long-running-agent": {"active": True}}
    configure_execution_protocol(context, agent.id(), policy)

    assert execution_protocol_model_decision_boundary(context, agent.id()) == "initial"
    assert (
        record_model_decision_attempt_failure(context, agent.id(), boundary="initial")
        is True
    )
    assert execution_protocol_model_decision_boundary(context, agent.id()) == "initial"
    assert (
        record_model_decision_attempt_failure(context, agent.id(), boundary="initial")
        is False
    )
    assert execution_protocol_model_decision_boundary(context, agent.id()) is None

    telemetry = build_execution_protocol_telemetry(context, agent.id())
    assert telemetry["initial_decision_status"] == "fail_open_unknown"
    assert telemetry["initial_decision_attempt_count"] == 2
    assert telemetry["initial_decision_unavailable_count"] == 0
    assert telemetry.get("model_horizon") is None


def test_unacknowledged_replan_checkpoint_fails_open_without_claiming_applied():
    context = Context(task_id="replan-invalid-bounded")
    context.set_task(Task(id="replan-invalid-bounded", timeout=600))
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        repetition_threshold=1,
    )
    agent = _agent(context, policy)
    agent.skill_configs = {"long-running-agent": {"active": True}}
    configure_execution_protocol(context, agent.id(), policy)
    record_model_execution_profile(
        context,
        agent.id(),
        {
            "horizon": "unknown",
            "confidence": 0.0,
            "milestone_count": 1,
            "expected_tool_actions": 0,
            "verification_required": True,
        },
    )
    transition = record_tool_protocol_event(
        context,
        agent.id(),
        {"repetition_count": 1, "current_agent_step": 2},
    )
    assert transition is not None
    assert execution_protocol_model_decision_boundary(context, agent.id()) == "replan"

    assert (
        record_model_decision_attempt_failure(context, agent.id(), boundary="replan")
        is True
    )
    assert (
        record_model_decision_attempt_failure(context, agent.id(), boundary="replan")
        is False
    )
    assert execution_protocol_model_decision_boundary(context, agent.id()) is None

    state = ExecutionProtocolStore(context, agent.id(), policy).load()
    assert state.decision_checkpoint_pending is False
    assert state.replan_requested_count == 1
    assert state.replan_applied_count == 0
    telemetry = build_execution_protocol_telemetry(context, agent.id())
    assert telemetry["replan_decision_status"] == "fail_open_unacknowledged"
    assert telemetry["replan_decision_attempt_count"] == 2
    assert telemetry["replan_decision_unavailable_count"] == 0

    record_tool_protocol_event(
        context,
        agent.id(),
        {"completion_advanced": True, "current_agent_step": 3},
    )
    assert execution_protocol_model_decision_boundary(context, agent.id()) is None
    state = ExecutionProtocolStore(context, agent.id(), policy).load()
    assert state.replan_requested_count == 1

    record_tool_protocol_event(
        context,
        agent.id(),
        {"repetition_count": 1, "current_agent_step": 4},
    )
    state = ExecutionProtocolStore(context, agent.id(), policy).load()
    assert state.replan_requested_count == 2
    assert execution_protocol_model_decision_boundary(context, agent.id()) == ("replan")


def test_unavailable_replan_fails_open_without_consuming_malformed_retry():
    context = Context(task_id="replan-provider-unavailable")
    context.set_task(Task(id="replan-provider-unavailable", timeout=600))
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        repetition_threshold=1,
    )
    agent = _agent(context, policy)
    agent.skill_configs = {"long-running-agent": {"active": True}}
    configure_execution_protocol(context, agent.id(), policy)
    record_model_execution_profile(
        context,
        agent.id(),
        {
            "horizon": "unknown",
            "confidence": 0.0,
            "milestone_count": 1,
            "expected_tool_actions": 0,
            "verification_required": True,
        },
    )
    transition = record_tool_protocol_event(
        context,
        agent.id(),
        {"repetition_count": 1, "current_agent_step": 2},
    )

    assert transition is not None
    assert execution_protocol_model_decision_boundary(context, agent.id()) == "replan"
    assert record_model_decision_unavailable(
        context,
        agent.id(),
        boundary="replan",
        reason="provider_unavailable",
    )
    assert execution_protocol_model_decision_boundary(context, agent.id()) is None

    state = ExecutionProtocolStore(context, agent.id(), policy).load()
    assert state.decision_checkpoint_pending is False
    assert state.replan_requested_count == 1
    assert state.replan_applied_count == 0
    telemetry = build_execution_protocol_telemetry(context, agent.id())
    assert telemetry["replan_decision_status"] == "fail_open_unacknowledged"
    assert telemetry["replan_decision_attempt_count"] == 0
    assert telemetry["replan_decision_unavailable_count"] == 1
    assert telemetry["replan_decision_fail_open_reason"] == "provider_unavailable"


def test_pending_stagnation_checkpoint_exposes_only_required_model_decision():
    context = Context(task_id="replan-required")
    context.set_task(Task(id="replan-required", timeout=600))
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        repetition_threshold=1,
    )
    agent = _agent(context, policy)
    agent.skill_configs = {"long-running-agent": {"active": True}}
    configure_execution_protocol(context, agent.id(), policy)
    record_model_execution_profile(
        context,
        agent.id(),
        {
            "horizon": "unknown",
            "confidence": 0.0,
            "milestone_count": 1,
            "expected_tool_actions": 0,
            "verification_required": True,
        },
    )
    record_tool_protocol_event(
        context,
        agent.id(),
        {"repetition_count": 1, "current_agent_step": 2},
    )
    real_tools = [
        {
            "type": "function",
            "function": {
                "name": "terminal__execute",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                },
            },
        }
    ]

    offered, offer = agent._with_long_horizon_execution_profile(real_tools, context)

    assert offer.decision_boundary == "replan"
    assert [item["function"]["name"] for item in offered] == [
        "aworld__execution_decision"
    ]
    parameters = offered[0]["function"]["parameters"]
    assert parameters["required"] == ["__aworld_plan_update"]
    assert "__aworld_execution_profile" not in parameters["properties"]


@pytest.mark.asyncio
async def test_observe_mode_never_mutates_tools_or_tool_choice_after_stagnation():
    requests = []
    original_tools = [
        {
            "type": "function",
            "function": {
                "name": "terminal__execute",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            },
        }
    ]

    class ObserveAgent(Agent):
        async def _add_message_to_memory(self, *args, **kwargs):
            return None

        async def build_llm_input(self, observation, info=None, message=None, **kwargs):
            return [{"role": "user", "content": str(observation.content or "")}]

        async def _filter_tools(self, context=None):
            return original_tools

        async def invoke_model(self, messages=None, message=None, **kwargs):
            requests.append(kwargs)
            return ModelResponse(
                id=f"observe-{len(requests)}",
                model="offline",
                content="",
                tool_calls=[
                    ToolCall(
                        id=f"observe-call-{len(requests)}",
                        function=Function(
                            name="terminal__execute",
                            arguments=json.dumps({"command": "pwd"}),
                        ),
                    )
                ],
                finish_reason="tool_calls",
                usage={"prompt_tokens": 1, "completion_tokens": 1},
            )

    context = Context(task_id="observe-inert")
    context.set_task(Task(id="observe-inert", timeout=600))
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.OBSERVE,
        repetition_threshold=1,
    )
    agent = ObserveAgent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="offline",
            llm_api_key="offline",
        ),
        execution_protocol_policy=policy,
        max_loop_steps=0,
    )
    agent.skill_configs = {"long-running-agent": {"active": True}}
    message = Message(category=Constants.AGENT, headers={"context": context})

    await agent.async_policy(Observation(content="first"), message=message)
    transition = record_tool_protocol_event(
        context,
        agent.id(),
        {"repetition_count": 1, "current_agent_step": 2},
    )
    assert transition is not None
    assert transition.decision.action is ControllerAction.WOULD_REQUEST_REPLAN
    assert transition.state.replan_count == 1
    assert transition.state.replan_requested_count == 0
    assert transition.state.decision_checkpoint_pending is False
    await agent.async_policy(Observation(content="second"), message=message)

    assert len(requests) == 2
    assert all(request["prepared_tools"] == original_tools for request in requests)
    assert all("tool_choice" not in request for request in requests)
    assert all(
        "__aworld_execution_profile"
        not in request["prepared_tools"][0]["function"]["parameters"]["properties"]
        for request in requests
    )


def test_agent_strips_stale_profile_schema_value_without_recording_again() -> None:
    context = Context(task_id="profile-stale-catalog")
    context.set_task(Task(id="profile-stale-catalog", timeout=600))
    policy = ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE)
    agent = _agent(context, policy)
    agent.skill_configs = {"long-running-agent": {"active": True}}
    configure_execution_protocol(context, agent.id(), policy)
    record_model_execution_profile(
        context,
        agent.id(),
        {
            "horizon": "short",
            "confidence": 0.9,
            "milestone_count": 1,
            "expected_tool_actions": 1,
            "verification_required": False,
        },
    )
    action = ActionModel(
        tool_name="terminal",
        action_name="execute",
        params={
            "command": "pwd",
            "__aworld_execution_profile": {
                "horizon": "long",
                "confidence": 0.99,
                "milestone_count": 10,
                "expected_tool_actions": 50,
                "verification_required": True,
            },
        },
    )
    result = AgentResult(current_state=None, actions=[action], is_call_tool=True)
    tools = [
        {
            "type": "function",
            "function": {
                "name": "terminal__execute",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                },
            },
        }
    ]
    _, offer = agent._with_long_horizon_execution_profile(tools, context)

    agent._consume_long_horizon_execution_profile(result, context, offer=offer)

    assert action.params == {"command": "pwd"}
    state = ExecutionProtocolStore(context, agent.id(), policy).load()
    assert state.model_execution_profile is not None
    assert state.long_horizon_armed is False


def test_default_convergence_avoids_extra_profile_turn_without_skill() -> None:
    context = Context(task_id="profile-disabled")
    context.set_task(Task(id="profile-disabled", timeout=600))
    policy = ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE)
    agent = _agent(context, policy)
    agent.skill_configs = {"long-running-agent": {"active": False}}
    configure_execution_protocol(context, agent.id(), policy)
    tools = [
        {
            "type": "function",
            "function": {
                "name": "terminal__execute",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    augmented, offer = agent._with_long_horizon_execution_profile(tools, context)

    assert offer.carrier_function_name is None
    assert augmented == tools


def test_default_guide_exposes_profile_after_observed_long_work_without_skill() -> None:
    context = Context(task_id="profile-observed-long")
    context.set_task(Task(id="profile-observed-long", timeout=600))
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        activation_event_threshold=6,
        model_activation_min_tool_actions=6,
        repetition_threshold=99,
    )
    agent = _agent(context, policy)
    agent.skill_configs = {"long-running-agent": {"active": False}}
    configure_execution_protocol(context, agent.id(), policy)
    for step in range(1, 7):
        record_tool_protocol_event(
            context,
            agent.id(),
            {"current_agent_step": step, "completion_advanced": True},
        )
    tools = [
        {
            "type": "function",
            "function": {
                "name": "terminal__execute",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    augmented, offer = agent._with_long_horizon_execution_profile(tools, context)

    assert offer.decision_boundary == "initial"
    assert offer.profile_schema_offered is True
    assert [item["function"]["name"] for item in augmented] == [
        "aworld__execution_decision"
    ]


def test_default_guide_constrains_after_two_unapplied_replans_without_skill() -> None:
    context = Context(task_id="replan-default-guide")
    context.set_task(Task(id="replan-default-guide", timeout=600))
    policy = ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE, repetition_threshold=1)
    agent = _agent(context, policy)
    agent.skill_configs = {"long-running-agent": {"active": False}}
    configure_execution_protocol(context, agent.id(), policy)
    _declare_long_horizon(context, agent.id())
    tools = [
        {
            "type": "function",
            "function": {
                "name": "terminal__execute",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    for step in (1, 2):
        transition = record_tool_protocol_event(
            context,
            agent.id(),
            {"current_agent_step": step, "repetition_count": 1},
        )
        assert transition.decision.action is ControllerAction.REQUEST_REPLAN
        _, first_offer = agent._with_long_horizon_execution_profile(tools, context)
        assert first_offer.decision_boundary == "replan"
        malformed = AgentResult(
            current_state=None,
            actions=[],
            is_call_tool=False,
        )
        assert agent._consume_long_horizon_execution_profile(
            malformed, context, offer=first_offer
        ) == "retry"
        _, retry_offer = agent._with_long_horizon_execution_profile(tools, context)
        assert agent._consume_long_horizon_execution_profile(
            malformed, context, offer=retry_offer
        ) == "fail_open"

    state = load_execution_protocol_state(context, agent.id())
    assert state.convergence_constraint_active is True
    augmented, offer = agent._with_long_horizon_execution_profile(tools, context)
    assert offer.decision_boundary is None
    assert all(
        item["function"]["name"] != "aworld__execution_decision"
        for item in augmented
    )
    first_guidance = consume_execution_protocol_guidance(context, agent.id())
    assert first_guidance is not None
    assert "convergence constraint" in first_guidance.lower()
    assert consume_execution_protocol_guidance(context, agent.id()) is None


def test_no_user_tools_records_structurally_unavailable_review_boundary() -> None:
    context = Context(task_id="profile-no-tools")
    context.set_task(Task(id="profile-no-tools", timeout=600))
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        independent_acceptance_enabled=False,
    )
    agent = _agent(context, policy)
    agent.skill_configs = {"long-running-agent": {"active": True}}
    configure_execution_protocol(context, agent.id(), policy)

    augmented, offer = agent._with_long_horizon_execution_profile(None, context)
    transition = record_candidate_final(
        context,
        agent.id(),
        review_boundary_available=False,
    )

    assert augmented is None
    assert offer.decision_boundary is None
    assert offer.carrier_function_name is None
    assert transition is not None
    assert transition.decision.action is ControllerAction.SUBMIT_CURRENT_RESULT
    assert transition.decision.reason is DecisionReason.REVIEW_BOUNDARY_UNAVAILABLE
    assert transition.state.phase is ProtocolPhase.COMPLETE
    assert transition.state.final_review_count == 0


def test_no_user_tools_never_downgrade_strict_acceptance_to_submit(
    monkeypatch,
) -> None:
    monkeypatch.delenv("AWORLD_INDEPENDENT_ACCEPTANCE_CRITIC", raising=False)
    context = Context(task_id="strict-profile-no-tools")
    context.set_task(Task(id="strict-profile-no-tools", timeout=600))
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(),
            immutable_inputs=(),
            validation_commands=(
                ValidationCommand(
                    command_id="registered-check",
                    argv=("pytest", "-q", "tests/test_contract.py"),
                ),
            ),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
        ),
        mode=CompletionMode.ENFORCE,
    )
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        independent_acceptance_enabled=True,
    )
    agent = _agent(context, policy)
    configure_execution_protocol(context, agent.id(), policy)

    review_boundary_available = agent._candidate_review_boundary_available(
        context,
        tools=None,
    )
    transition = record_candidate_final(
        context,
        agent.id(),
        actions=[ActionModel(agent_name=agent.id(), policy_info="candidate")],
        review_boundary_available=review_boundary_available,
    )

    assert execution_protocol_policy(
        context, agent.id()
    ).independent_acceptance_enabled is True
    assert review_boundary_available is True
    assert transition is not None
    assert transition.decision.action is ControllerAction.REQUEST_FINAL_REVIEW
    assert transition.state.review_pending is True


@pytest.mark.asyncio
async def test_no_user_tools_do_not_force_unreachable_profile_or_review() -> None:
    requests = []

    class NoToolAgent(Agent):
        async def _add_message_to_memory(self, *args, **kwargs):
            return None

        async def build_llm_input(self, observation, info=None, message=None, **kwargs):
            return [{"role": "user", "content": str(observation.content or "")}]

        async def _filter_tools(self, context=None):
            return None

        async def invoke_model(self, messages=None, message=None, **kwargs):
            requests.append(kwargs)
            content = "candidate"
            return ModelResponse(
                id=f"no-tool-{len(requests)}",
                model="offline",
                content=content,
                message={"role": "assistant", "content": content},
                finish_reason="stop",
                usage={"prompt_tokens": 1, "completion_tokens": 1},
            )

    context = Context(task_id="profile-no-tools-production")
    context.set_task(Task(id="profile-no-tools-production", timeout=600))
    agent = NoToolAgent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="offline",
            llm_api_key="offline",
        ),
        execution_protocol_policy=ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            independent_acceptance_enabled=False,
        ),
        max_loop_steps=0,
    )
    agent.skill_configs = {"long-running-agent": {"active": True}}
    message = Message(category=Constants.AGENT, headers={"context": context})

    result = await agent.async_policy(Observation(content="answer"), message=message)

    assert result[0].policy_info == "candidate"
    assert len(requests) == 1
    assert all(request.get("prepared_tools") is None for request in requests)
    assert all("tool_choice" not in request for request in requests)
    policy = execution_protocol_policy(context, agent.id())
    state = ExecutionProtocolStore(context, agent.id(), policy).load()
    assert state.phase is ProtocolPhase.COMPLETE
    assert state.final_review_count == 0
    assert state.history[-1].kind is EventKind.CANDIDATE_FINAL
    assert state.history[-1].review_boundary_available is False


@pytest.mark.asyncio
async def test_late_catalog_latch_finalizes_only_at_next_generation_boundary() -> None:
    requests = []

    class LateLatchAgent(Agent):
        filter_calls = 0

        async def _add_message_to_memory(self, *args, **kwargs):
            return None

        async def build_llm_input(self, observation, info=None, message=None, **kwargs):
            return [{"role": "user", "content": str(observation.content or "")}]

        def _with_long_horizon_execution_profile(self, tools, context):
            return tools, llm_agent_module._LongHorizonControlOffer()

        async def _filter_tools(self, context=None):
            self.filter_calls += 1
            if self.filter_calls == 1:
                blocked = mutation_gate_interception(
                    context,
                    [
                        ActionModel(
                            tool_name="terminal",
                            action_name="run_code",
                            params={"code": "unmodeled_late_catalog"},
                            tool_call_id="late-catalog-second-rejection",
                            agent_name=self.id(),
                        )
                    ],
                )
                assert blocked is not None
                assert blocked["candidate_tool_free_latched"] is True
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "run_code",
                        "description": "run one command",
                        "parameters": {
                            "type": "object",
                            "properties": {"code": {"type": "string"}},
                            "required": ["code"],
                        },
                    },
                }
            ]

        async def invoke_model(self, messages=None, message=None, **kwargs):
            prepared_tools = kwargs.get("prepared_tools")
            requests.append((messages, prepared_tools))
            if prepared_tools is not None:
                tool_calls = [
                    ToolCall(
                        id="late-catalog-tool",
                        function=Function(
                            name="run_code",
                            arguments='{"code":"cat still-blocked.log"}',
                        ),
                    )
                ]
                return ModelResponse(
                    id="late-catalog-tool-response",
                    model="offline",
                    content="",
                    message={
                        "role": "assistant",
                        "content": "",
                        "tool_calls": tool_calls,
                    },
                    tool_calls=tool_calls,
                    finish_reason="tool_calls",
                    usage={"prompt_tokens": 1, "completion_tokens": 1},
                )
            return ModelResponse(
                id="late-catalog-final",
                model="offline",
                content="bounded final",
                message={"role": "assistant", "content": "bounded final"},
                finish_reason="stop",
                usage={"prompt_tokens": 1, "completion_tokens": 1},
            )

    context = Context(task_id="late-catalog-agent-race")
    context.set_task(
        Task(id="late-catalog-agent-race", input="finish the task", timeout=600)
    )
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        post_candidate_read_only_threshold=1,
        repetition_threshold=99,
        low_information_gain_threshold=99,
        no_goal_progress_threshold=99,
        stagnation_event_threshold=99,
        independent_acceptance_enabled=False,
    )
    agent = LateLatchAgent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="offline",
            llm_api_key="offline",
        ),
        execution_protocol_policy=policy,
        max_loop_steps=0,
    )
    configure_execution_protocol(context, agent.id(), policy)
    _declare_long_horizon(context, agent.id())

    from aworld.core.context.compiler import semantic_fingerprint

    candidate_fingerprint = semantic_fingerprint("late-catalog-candidate")

    def semantic(step: int, **overrides):
        value = {
            "repetition_count": 0,
            "low_information_gain_count": 0,
            "no_goal_progress_count": 0,
            "goal_progress_observable": False,
            "goal_progress": False,
            "validation_evidence_advanced": False,
            "completion_advanced": False,
            "current_agent_step": step,
            "operation_hash": f"sha256:late-operation-{step}",
            "result_hash": f"sha256:late-result-{step}",
        }
        value.update(overrides)
        return value

    record_tool_protocol_event(
        context,
        agent.id(),
        semantic(
            1,
            candidate_present=True,
            candidate_advanced=True,
            delivery_progress_advanced=True,
            public_candidate_mutated=True,
            public_delivery_fingerprint=candidate_fingerprint,
        ),
    )
    record_tool_protocol_event(
        context,
        agent.id(),
        semantic(
            2,
            candidate_present=True,
            public_candidate_mutated=True,
            public_delivery_fingerprint=candidate_fingerprint,
        ),
    )
    for index in range(3):
        assert mutation_gate_interception(
            context,
            [
                ActionModel(
                    tool_name="terminal",
                    action_name="run_code",
                    params={"code": f"cat diagnostic-{index}.log"},
                    tool_call_id=f"diagnostic-{index}",
                    agent_name=agent.id(),
                )
            ],
        ) is None
    assert mutation_gate_interception(
        context,
        [
            ActionModel(
                tool_name="terminal",
                action_name="run_code",
                params={"code": "unmodeled_first_rejection"},
                tool_call_id="late-catalog-first-rejection",
                agent_name=agent.id(),
            )
        ],
    ) is not None

    message = Message(category=Constants.AGENT, headers={"context": context})
    first = await agent.async_policy(Observation(content="continue"), message=message)

    assert first[0].tool_name == "run_code"
    assert requests[0][1] is not None
    assert load_execution_protocol_state(context, agent.id()).phase is not (
        ProtocolPhase.FINALIZE
    )

    second = await agent.async_policy(
        Observation(content="blocked tool result"), message=message
    )

    assert second[0].policy_info == "bounded final"
    assert requests[1][1] is None
    assert any(
        "reserved this bounded final turn" in str(item.get("content", ""))
        for item in requests[1][0]
    )
    final_state = load_execution_protocol_state(context, agent.id())
    assert final_state.phase is ProtocolPhase.REVIEW
    assert final_state.review_pending is False
    assert final_state.terminal_incomplete is True


def test_strict_critic_uses_required_probe_control_not_review_marker(
    monkeypatch,
) -> None:
    monkeypatch.delenv("AWORLD_INDEPENDENT_ACCEPTANCE_CRITIC", raising=False)
    context = Context(task_id="strict-review-schema")
    context.set_task(Task(id="strict-review-schema", input="finish the task"))
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(),
            immutable_inputs=(),
            validation_commands=(
                ValidationCommand(
                    command_id="registered-check",
                    argv=("pytest", "-q", "tests/test_contract.py"),
                ),
            ),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
        ),
        mode=CompletionMode.ENFORCE,
    )
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        review_unarmed_candidates=True,
        independent_acceptance_enabled=True,
    )
    agent = _agent(context, policy)
    configure_execution_protocol(context, agent.id(), policy)
    record_candidate_final(context, agent.id())
    tools = [
        {
            "type": "function",
            "function": {
                "name": "terminal__execute",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            },
        }
    ]

    solver_controls, offer = agent._with_long_horizon_execution_profile(tools, context)
    assert offer.carrier_function_name is None
    solver_properties = solver_controls[0]["function"]["parameters"]["properties"]
    assert "__aworld_execution_profile" not in solver_properties
    assert "__aworld_plan_update" not in solver_properties
    assert "__aworld_public_probe" not in solver_properties

    reflected = agent._with_model_review_control(solver_controls, context)
    augmented = agent._with_acceptance_probe_control(reflected, context)
    parameters = augmented[0]["function"]["parameters"]

    assert "__aworld_review_decision" not in parameters["properties"]
    assert "__aworld_acceptance_probe" in parameters["properties"]
    assert "__aworld_acceptance_probe" in parameters["required"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("caller_reasoning", "expected_reasoning"),
    [
        (None, ["xhigh", "high"]),
        ("max", ["xhigh", "xhigh"]),
    ],
)
async def test_production_policy_path_records_one_decision_then_exposes_real_tools(
    caller_reasoning,
    expected_reasoning,
) -> None:
    captured_tools = []
    captured_reasoning = []

    class ProfileAgent(Agent):
        async def _add_message_to_memory(self, *args, **kwargs):
            return None

        async def build_llm_input(self, observation, info=None, message=None, **kwargs):
            return [{"role": "user", "content": str(observation.content or "")}]

        async def _filter_tools(self, context=None):
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "terminal__execute",
                        "parameters": {
                            "type": "object",
                            "properties": {"command": {"type": "string"}},
                            "required": ["command"],
                        },
                    },
                }
            ]

        async def invoke_model(self, messages=None, message=None, **kwargs):
            captured_tools.append(kwargs["prepared_tools"])
            captured_reasoning.append(kwargs.get("reasoning_effort"))
            if len(captured_tools) == 1:
                function_name = "aworld__execution_decision"
                arguments = {
                    "__aworld_execution_profile": {
                        "horizon": "long",
                        "confidence": 0.95,
                        "milestone_count": 4,
                        "expected_tool_actions": 12,
                        "verification_required": True,
                    },
                    "__aworld_plan_update": {
                        "decision": "continue",
                        "horizon": "long",
                        "milestone": "create a runnable candidate",
                        "next_action": "run make test",
                        "next_action_tool": "terminal__execute",
                        "next_action_arguments": '{"command":"make test"}',
                        "verification_plan": "inspect the observed test result",
                        "completion_assessment": "in_progress",
                        "delivery_intent": "validate_candidate",
                        "delivery_rationale": "the candidate needs a fresh test",
                        "assumptions": [],
                        "retired_approaches": [],
                        "evidence_refs": [],
                        "selected_candidate_id": None,
                    },
                }
            else:
                function_name = "terminal__execute"
                arguments = {"command": "make test"}
            return ModelResponse(
                id=f"profile-response-{len(captured_tools)}",
                model="offline",
                content="",
                tool_calls=[
                    ToolCall(
                        id=f"call-{len(captured_tools)}",
                        function=Function(
                            name=function_name,
                            arguments=json.dumps(arguments),
                        ),
                    )
                ],
                finish_reason="tool_calls",
                usage={"prompt_tokens": 1, "completion_tokens": 1},
            )

    context = Context(task_id="profile-production-path")
    context.set_task(Task(id="profile-production-path", timeout=600))
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        activation_event_threshold=20,
    )
    agent = ProfileAgent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="offline",
            llm_api_key="offline",
        ),
        execution_protocol_policy=policy,
        max_loop_steps=0,
    )
    agent.skill_configs = {"long-running-agent": {"active": True}}
    agent.conf.llm_config.reasoning_phase_policy = ReasoningPhasePolicy.balanced()
    agent.conf.llm_config.reasoning_transport = "openai"
    agent._llm = SimpleNamespace(
        context_compiler_mode="enforce",
        _context_progressive_skills=False,
        _context_progressive_tools=True,
        _context_progressive_tool_base_tools=("terminal__execute",),
        _context_progressive_tool_unmanaged_policy="preserve",
        _context_task_catalog_policy="sticky",
        _context_artifact_offload=True,
        enforced_tool_output_policy=None,
        provider=SimpleNamespace(
            reasoning_transport_capability=lambda: OPENAI_REASONING_CAPABILITY
        ),
    )
    message = Message(category=Constants.AGENT, headers={"context": context})

    request_kwargs = (
        {"reasoning_effort": caller_reasoning}
        if caller_reasoning is not None
        else {}
    )
    result = await agent.async_policy(
        Observation(content="complete the task"),
        message=message,
        **request_kwargs,
    )

    assert len(captured_tools) == 2
    assert captured_reasoning == expected_reasoning
    assert captured_tools[0][0]["function"]["name"] == ("aworld__execution_decision")
    assert (
        "__aworld_execution_profile"
        in captured_tools[0][0]["function"]["parameters"]["properties"]
    )
    assert captured_tools[1][0]["function"]["name"] == "terminal__execute"
    assert result[0].params == {"command": "make test"}
    assert result[0].tool_name == "terminal"
    state = ExecutionProtocolStore(context, agent.id(), policy).load()
    assert state.long_horizon_armed is True


@pytest.mark.asyncio
async def test_initial_short_submit_current_completes_tool_free() -> None:
    requests = []
    memory_writes = []

    class SubmitCurrentAgent(Agent):
        async def _add_message_to_memory(self, *args, **kwargs):
            memory_writes.append(kwargs)

        async def build_llm_input(self, observation, info=None, message=None, **kwargs):
            return [{"role": "user", "content": str(observation.content or "")}]

        async def _filter_tools(self, context=None):
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "terminal__execute",
                        "parameters": {
                            "type": "object",
                            "properties": {"command": {"type": "string"}},
                            "required": ["command"],
                        },
                    },
                }
            ]

        async def invoke_model(self, messages=None, message=None, **kwargs):
            requests.append(kwargs)
            if len(requests) == 1:
                arguments = {
                    "__aworld_execution_profile": {
                        "horizon": "short",
                        "confidence": 0.95,
                        "milestone_count": 1,
                        "expected_tool_actions": 0,
                        "verification_required": False,
                    },
                    "__aworld_plan_update": {
                        "decision": "continue",
                        "horizon": "short",
                        "milestone": "answer is ready",
                        "next_action": "submit the current result",
                        "next_action_tool": None,
                        "next_action_arguments": None,
                        "verification_plan": "return the current result",
                        "completion_assessment": "candidate_ready",
                        "delivery_intent": "submit_current",
                        "delivery_rationale": "no Tool call is needed",
                        "assumptions": ["private-plan-payload-must-not-enter-memory"],
                        "retired_approaches": [],
                        "evidence_refs": [],
                        "selected_candidate_id": None,
                    },
                }
                return ModelResponse(
                    id="submit-current-decision",
                    model="offline",
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="call-submit-current",
                            function=Function(
                                name="aworld__execution_decision",
                                arguments=json.dumps(arguments),
                            ),
                        )
                    ],
                    finish_reason="tool_calls",
                    usage={"prompt_tokens": 1, "completion_tokens": 1},
                )
            feedback = "\n".join(str(item.get("content", "")) for item in messages)
            assert "Tool-free finalization" in feedback
            assert "Resume with ordinary Tools" not in feedback
            return ModelResponse(
                id="submit-current-final",
                model="offline",
                content="ready answer",
                message={"role": "assistant", "content": "ready answer"},
                finish_reason="stop",
                usage={"prompt_tokens": 1, "completion_tokens": 1},
            )

    context = Context(task_id="initial-submit-current")
    context.set_task(Task(id="initial-submit-current", timeout=600))
    policy = ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE)
    agent = SubmitCurrentAgent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="offline",
            llm_api_key="offline",
        ),
        execution_protocol_policy=policy,
        max_loop_steps=0,
    )
    agent.skill_configs = {"long-running-agent": {"active": True}}
    message = Message(category=Constants.AGENT, headers={"context": context})

    result = await agent.async_policy(
        Observation(content="answer the short request"),
        message=message,
    )

    assert len(requests) == 2
    assert requests[0]["tool_choice"] == "required"
    assert requests[1]["prepared_tools"] is None
    assert result[0].policy_info == "ready answer"
    assert result[0].tool_name is None
    assert all(
        getattr(write["payload"], "id", None) != "submit-current-decision"
        for write in memory_writes
    )
    assert not any(isinstance(write["payload"], ActionResult) for write in memory_writes)
    assert "private-plan-payload-must-not-enter-memory" not in repr(memory_writes)
    state = ExecutionProtocolStore(context, agent.id(), policy).load()
    assert state.finalization_entered is True
    assert state.model_plan_update.delivery_intent.value == "submit_current"


@pytest.mark.asyncio
async def test_decision_contract_pins_a_progressively_filtered_custom_tool() -> None:
    requests = []
    selected_arguments = None

    class StatelessProfileAgent(Agent):
        async def _add_message_to_memory(self, *args, **kwargs):
            return None

        async def build_llm_input(self, observation, info=None, message=None, **kwargs):
            return [{"role": "user", "content": str(observation.content or "")}]

        async def _filter_tools(self, context=None):
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "terminal__execute",
                        "parameters": {
                            "type": "object",
                            "properties": {"command": {"type": "string"}},
                            "required": ["command"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "artifact__publish",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "artifact_path": {"type": "string"},
                            },
                            "required": ["artifact_path"],
                        },
                    },
                },
            ]

        async def invoke_model(self, messages=None, message=None, **kwargs):
            nonlocal selected_arguments
            requests.append(kwargs)
            if len(requests) == 1:
                decision = kwargs["prepared_tools"][0]
                plan_schema = decision["function"]["parameters"]["properties"][
                    "__aworld_plan_update"
                ]
                contracts = json.loads(
                    plan_schema["properties"]["next_action_arguments"][
                        "description"
                    ].rsplit(": ", 1)[1]
                )
                # This model is stateless: it learns the exact parameter name
                # solely from the current internal decision Tool schema.
                parameter_name = contracts["artifact__publish"].split('"')[1]
                selected_arguments = {parameter_name: "result.json"}
                function_name = "aworld__execution_decision"
                arguments = {
                    "__aworld_execution_profile": {
                        "horizon": "long",
                        "confidence": 0.9,
                        "milestone_count": 2,
                        "expected_tool_actions": 2,
                        "verification_required": True,
                    },
                    "__aworld_plan_update": {
                        "decision": "continue",
                        "horizon": "long",
                        "milestone": "publish the candidate",
                        "next_action": "publish result.json",
                        "next_action_tool": "artifact__publish",
                        "next_action_arguments": json.dumps(selected_arguments),
                        "verification_plan": "inspect the publish receipt",
                        "completion_assessment": "in_progress",
                        "delivery_intent": "produce_candidate",
                        "delivery_rationale": "the result is ready to publish",
                        "assumptions": [],
                        "retired_approaches": [],
                        "evidence_refs": [],
                        "selected_candidate_id": "candidate-1",
                    },
                }
            else:
                function_name = "artifact__publish"
                arguments = selected_arguments
            return ModelResponse(
                id=f"stateless-contract-{len(requests)}",
                model="offline",
                content="",
                tool_calls=[
                    ToolCall(
                        id=f"call-{len(requests)}",
                        function=Function(
                            name=function_name,
                            arguments=json.dumps(arguments),
                        ),
                    )
                ],
                finish_reason="tool_calls",
                usage={"prompt_tokens": 1, "completion_tokens": 1},
            )

    context = Context(task_id="progressive-decision-contract")
    context.set_task(Task(id="progressive-decision-contract", timeout=600))
    policy = ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE)
    agent = StatelessProfileAgent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="offline",
            llm_api_key="offline",
        ),
        execution_protocol_policy=policy,
        max_loop_steps=0,
    )
    agent.skill_configs = {"long-running-agent": {"active": True}}
    agent._llm = SimpleNamespace(
        context_compiler_mode="enforce",
        _context_progressive_skills=False,
        _context_progressive_tools=True,
        _context_progressive_tool_base_tools=("terminal__execute",),
        _context_progressive_tool_unmanaged_policy="drop",
        _context_task_catalog_policy="sticky",
        _context_artifact_offload=True,
        enforced_tool_output_policy=None,
        provider=None,
    )
    message = Message(category=Constants.AGENT, headers={"context": context})

    result = await agent.async_policy(
        Observation(content="publish the requested artifact"),
        message=message,
    )

    assert len(requests) == 2
    assert requests[0]["tool_choice"] == "required"
    assert [tool["function"]["name"] for tool in requests[1]["prepared_tools"]] == [
        "terminal__execute",
        "artifact__publish",
    ]
    assert result[0].tool_name == "artifact"
    assert result[0].action_name == "publish"
    assert result[0].params == {"artifact_path": "result.json"}


@pytest.mark.asyncio
async def test_malformed_decision_cannot_loop_past_one_retry() -> None:
    calls = 0
    tool_catalogs = []
    memory_writes = []

    class InvalidDecisionAgent(Agent):
        async def _add_message_to_memory(self, *args, **kwargs):
            memory_writes.append(kwargs)

        async def build_llm_input(self, observation, info=None, message=None, **kwargs):
            return [{"role": "user", "content": str(observation.content or "")}]

        async def _filter_tools(self, context=None):
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "terminal__execute",
                        "parameters": {
                            "type": "object",
                            "properties": {"command": {"type": "string"}},
                            "required": ["command"],
                        },
                    },
                }
            ]

        async def invoke_model(self, messages=None, message=None, **kwargs):
            nonlocal calls
            calls += 1
            tool_catalogs.append(kwargs["prepared_tools"])
            if calls == 1:
                name = "terminal__execute"
                arguments = {"command": "must-not-run"}
            elif calls == 2:
                name = "aworld__execution_decision"
                arguments = {"__aworld_plan_update": {"decision": "continue"}}
            else:
                name = "terminal__execute"
                arguments = {"command": "pwd"}
            return ModelResponse(
                id=f"invalid-decision-{calls}",
                model="offline",
                content="",
                tool_calls=[
                    ToolCall(
                        id=f"call-{calls}",
                        function=Function(name=name, arguments=json.dumps(arguments)),
                    )
                ],
                finish_reason="tool_calls",
                usage={"prompt_tokens": 1, "completion_tokens": 1},
            )

    context = Context(task_id="bounded-invalid-decision")
    context.set_task(Task(id="bounded-invalid-decision", timeout=600))
    policy = ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE)
    agent = InvalidDecisionAgent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="offline",
            llm_api_key="offline",
        ),
        execution_protocol_policy=policy,
        max_loop_steps=0,
    )
    agent.skill_configs = {"long-running-agent": {"active": True}}
    message = Message(category=Constants.AGENT, headers={"context": context})

    result = await agent.async_policy(Observation(content="start"), message=message)

    assert calls == 3
    assert [catalog[0]["function"]["name"] for catalog in tool_catalogs[:2]] == [
        "aworld__execution_decision",
        "aworld__execution_decision",
    ]
    assert result[0].params == {"command": "pwd"}
    assert result[0].tool_name == "terminal"
    telemetry = build_execution_protocol_telemetry(context, agent.id())
    assert telemetry["initial_decision_status"] == "fail_open_unknown"
    assert telemetry["initial_decision_attempt_count"] == 2
    assert telemetry.get("model_horizon") is None
    assert all(
        getattr(write["payload"], "id", None)
        not in {"invalid-decision-1", "invalid-decision-2"}
        for write in memory_writes
    )
    assert not any(isinstance(write["payload"], ActionResult) for write in memory_writes)


@pytest.mark.parametrize(
    ("total,external,expected"),
    [
        (30.0, 3.0, 7.05),
        (60.0, 6.0, 14.1),
        (360.0, 36.0, 81.0),
        (3753.0, 60.0, 105.0),
    ],
)
def test_default_protocol_derives_three_part_finalization_budget(
    total: float,
    external: float,
    expected: float,
) -> None:
    context = Context(task_id="adaptive-reserve")
    context.set_task(
        Task(
            id="adaptive-reserve",
            input="complete the task",
            timeout=total,
            completion_reserve_seconds=external,
        )
    )
    agent = Agent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="offline",
            llm_api_key="offline",
        ),
    )
    agent.skill_configs = {"long-running-agent": {"active": True}}

    policy = agent._resolve_execution_protocol_policy(context)

    assert policy.mode is ProtocolMode.GUIDE
    assert policy.finalization_reserve_seconds == pytest.approx(expected)
    assert policy.finalization_reserve_seconds > external
    assert policy.finalization_reserve_seconds < total


def test_explicit_protocol_policy_is_not_rewritten_by_task_budget() -> None:
    context = Context(task_id="explicit-reserve")
    context.set_task(
        Task(
            id="explicit-reserve",
            timeout=30,
            completion_reserve_seconds=3,
        )
    )
    explicit = ExecutionProtocolPolicy(
        mode=ProtocolMode.OBSERVE,
        finalization_reserve_seconds=12,
    )
    agent = _agent(context, explicit)

    assert agent._resolve_execution_protocol_policy(context) is explicit


def test_explicit_execution_protocol_policy_survives_agent_round_trip() -> None:
    context = Context(task_id="round-trip")
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.OBSERVE,
        activation_event_threshold=7,
        final_review_timeout_seconds=12,
    )
    agent = _agent(context, policy)

    restored = Agent.from_dict(agent.to_dict())

    assert restored._resolve_execution_protocol_policy() == policy


@pytest.mark.asyncio
async def test_final_review_guidance_reaches_the_second_model_request() -> None:
    captured_messages = []

    class CapturingAgent(Agent):
        async def _add_message_to_memory(self, *args, **kwargs):
            return None

        async def build_llm_input(self, observation, info=None, message=None, **kwargs):
            return [
                {"role": "system", "content": "rules"},
                {"role": "user", "content": str(observation.content or "")},
            ]

        async def _filter_tools(self, context=None):
            return None

        async def invoke_model(self, messages=None, message=None, **kwargs):
            captured_messages.append(messages)
            ordinal = len(captured_messages)
            content = "candidate" if ordinal == 1 else "reviewed final"
            return ModelResponse(
                id=f"response-{ordinal}",
                model="offline",
                content=content,
                message={"role": "assistant", "content": content},
                finish_reason="stop",
                usage={"prompt_tokens": 1, "completion_tokens": 1},
            )

    context = Context(task_id="review-guidance")
    context.set_task(Task(id="review-guidance", input="finish the task"))
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        activation_event_threshold=1,
        max_final_reviews=1,
        max_repairs=1,
    )
    agent = CapturingAgent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="offline",
            llm_api_key="offline",
        ),
        execution_protocol_policy=policy,
        max_loop_steps=0,
    )
    configure_execution_protocol(context, agent.id(), policy)
    _declare_long_horizon(context, agent.id())
    record_execution_state(
        context,
        agent.id(),
        "incomplete",
        "model_owned_review_error_unverified",
        recoverable=True,
    )
    probe_action = ActionModel(
        tool_name="terminal",
        action_name="execute",
        tool_call_id="pre-final-probe",
        params={"command": "pytest -q"},
    )
    assert record_public_probe_plan(
        context,
        agent.id(),
        tool_call_id="pre-final-probe",
        tool_identity="terminal:execute",
        arguments_projection=probe_action.params,
        value={
            "hypothesis_id": "candidate-regression",
            "highest_risk_counterexample": "the candidate still fails",
            "probe_kind": "regression",
        },
    )
    assert (
        record_public_probe_observations(
            context,
            agent.id(),
            actions=[probe_action],
            result_projections=[{"tool_call_id": "pre-final-probe", "success": True}],
        )
        == 1
    )
    record_tool_protocol_event(
        context,
        agent.id(),
        {
            "goal_progress_observable": False,
            "goal_progress": False,
            "validation_evidence_advanced": True,
        },
    )
    message = Message(category=Constants.AGENT, headers={"context": context})

    result = await agent.async_policy(
        Observation(content="initial request"), message=message
    )

    assert result[0].policy_info == "reviewed final"
    assert len(captured_messages) == 2
    guidance = captured_messages[1][-1]["content"]
    assert "model-owned completion reflection" in guidance
    assert "solver self-review" in guidance
    assert "No trusted independent validation contract is active" in guidance
    assert "framework probe receipt" not in guidance
    assert "accept requires" not in guidance
    assert "public self-check receipts" in guidance
    assert '"probe_assessment":"unassessed"' in guidance
    assert '"stale":true' in guidance
    execution_state = get_execution_state(context, agent.id())
    assert execution_state["status"] == "succeeded"
    assert execution_state["unresolved_blockers"] == []
    assert any(
        item["evidence_kind"] == "accepted_review"
        for item in execution_state["resolution_evidence"]
    )


@pytest.mark.asyncio
async def test_final_review_keeps_ordinary_multi_tool_work_in_review() -> None:
    calls = 0

    class MultiToolReviewAgent(Agent):
        async def _add_message_to_memory(self, *args, **kwargs):
            return None

        async def build_llm_input(self, observation, info=None, message=None, **kwargs):
            return [{"role": "user", "content": str(observation.content or "")}]

        async def _filter_tools(self, context=None):
            return None

        async def invoke_model(self, messages=None, message=None, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return ModelResponse(
                    id="candidate",
                    model="offline",
                    content="best candidate",
                    message={"role": "assistant", "content": "best candidate"},
                    finish_reason="stop",
                    usage={"prompt_tokens": 1, "completion_tokens": 1},
                )
            tool_calls = [
                ToolCall(
                    id=f"call-{index}",
                    function=Function(name="run_code", arguments='{"code":"true"}'),
                )
                for index in (1, 2)
            ]
            return ModelResponse(
                id="over-broad-repair",
                model="offline",
                content="",
                message={"role": "assistant", "content": "", "tool_calls": tool_calls},
                tool_calls=tool_calls,
                finish_reason="tool_calls",
                usage={"prompt_tokens": 1, "completion_tokens": 1},
            )

    context = Context(task_id="multi-tool-review")
    context.set_task(Task(id="multi-tool-review", input="finish the task"))
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        activation_event_threshold=1,
    )
    agent = MultiToolReviewAgent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="offline",
            llm_api_key="offline",
        ),
        execution_protocol_policy=policy,
        max_loop_steps=0,
    )
    configure_execution_protocol(context, agent.id(), policy)
    _declare_long_horizon(context, agent.id())
    record_tool_protocol_event(
        context,
        agent.id(),
        {"validation_evidence_advanced": True},
    )
    message = Message(category=Constants.AGENT, headers={"context": context})

    result = await agent.async_policy(Observation(content="start"), message=message)

    assert calls == 2
    assert len(result) == 2
    assert [action.tool_name for action in result] == ["run_code", "run_code"]
    assert agent.finished is False
    state = ExecutionProtocolStore(context, agent.id(), policy).load()
    assert state.phase.value == "review"
    assert state.review_pending is True
    assert state.repair_count == 0


@pytest.mark.asyncio
async def test_single_review_repair_returns_to_normal_tool_execution() -> None:
    calls = 0
    prepared_tools = []

    class SingleRepairAgent(Agent):
        async def _add_message_to_memory(self, *args, **kwargs):
            return None

        async def build_llm_input(self, observation, info=None, message=None, **kwargs):
            return [{"role": "user", "content": str(observation.content or "")}]

        async def _filter_tools(self, context=None):
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "run_code",
                        "description": "run one command",
                        "parameters": {"type": "object"},
                    },
                }
            ]

        async def invoke_model(self, messages=None, message=None, **kwargs):
            nonlocal calls
            calls += 1
            prepared_tools.append(kwargs.get("prepared_tools"))
            if calls == 1:
                content = "candidate before repair"
                return ModelResponse(
                    id="candidate",
                    model="offline",
                    content=content,
                    message={"role": "assistant", "content": content},
                    finish_reason="stop",
                    usage={"prompt_tokens": 1, "completion_tokens": 1},
                )
            if calls == 2:
                tool_calls = [
                    ToolCall(
                        id="repair-call",
                        function=Function(
                            name="run_code",
                            arguments=json.dumps(
                                {
                                    "code": "true",
                                    "__aworld_review_decision": {
                                        "decision": "repair",
                                        "reason": "the observed output is stale",
                                    },
                                }
                            ),
                        ),
                    )
                ]
                return ModelResponse(
                    id="repair",
                    model="offline",
                    content="",
                    message={
                        "role": "assistant",
                        "content": "",
                        "tool_calls": tool_calls,
                    },
                    tool_calls=tool_calls,
                    finish_reason="tool_calls",
                    usage={"prompt_tokens": 1, "completion_tokens": 1},
                )
            content = "final after bounded repair"
            return ModelResponse(
                id="final",
                model="offline",
                content=content,
                message={"role": "assistant", "content": content},
                finish_reason="stop",
                usage={"prompt_tokens": 1, "completion_tokens": 1},
            )

    context = Context(task_id="single-repair")
    context.set_task(Task(id="single-repair", input="finish the task"))
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        activation_event_threshold=1,
        max_final_reviews=1,
        max_repairs=1,
    )
    agent = SingleRepairAgent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="offline",
            llm_api_key="offline",
        ),
        execution_protocol_policy=policy,
        max_loop_steps=0,
    )
    configure_execution_protocol(context, agent.id(), policy)
    _declare_long_horizon(context, agent.id())
    record_tool_protocol_event(
        context,
        agent.id(),
        {"validation_evidence_advanced": True},
    )
    message = Message(category=Constants.AGENT, headers={"context": context})

    repair = await agent.async_policy(Observation(content="start"), message=message)
    final = await agent.async_policy(
        Observation(content="repair succeeded"), message=message
    )

    assert len(repair) == 1
    assert repair[0].tool_name == "run_code"
    assert repair[0].params == {"code": "true"}
    repair_state = ExecutionProtocolStore(context, agent.id(), policy).load()
    assert repair_state.repair_count == 1
    assert final[0].policy_info == "final after bounded repair"
    assert calls == 3
    assert prepared_tools[-1] is not None
    review_control = "__aworld_review_decision"
    assert (
        review_control in prepared_tools[1][0]["function"]["parameters"]["properties"]
    )
    assert review_control not in prepared_tools[1][0]["function"]["parameters"].get(
        "required", []
    )
    assert review_control not in prepared_tools[2][0]["function"]["parameters"].get(
        "properties", {}
    )


@pytest.mark.asyncio
async def test_model_can_continue_ordinary_tool_work_while_review_is_pending() -> None:
    calls = 0

    class ToolOnlyFinalizationAgent(Agent):
        async def _add_message_to_memory(self, *args, **kwargs):
            return None

        async def build_llm_input(self, observation, info=None, message=None, **kwargs):
            return [{"role": "user", "content": str(observation.content or "")}]

        async def _filter_tools(self, context=None):
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "run_code",
                        "description": "run one command",
                        "parameters": {"type": "object"},
                    },
                }
            ]

        async def invoke_model(self, messages=None, message=None, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return ModelResponse(
                    id="candidate",
                    model="offline",
                    content="safe original candidate",
                    message={
                        "role": "assistant",
                        "content": "safe original candidate",
                    },
                    finish_reason="stop",
                    usage={"prompt_tokens": 1, "completion_tokens": 1},
                )
            tool_calls = [
                ToolCall(
                    id=f"repair-{calls}",
                    function=Function(name="run_code", arguments='{"code":"true"}'),
                )
            ]
            return ModelResponse(
                id=f"tool-only-{calls}",
                model="offline",
                content="",
                message={
                    "role": "assistant",
                    "content": "",
                    "tool_calls": tool_calls,
                },
                tool_calls=tool_calls,
                finish_reason="tool_calls",
                usage={"prompt_tokens": 1, "completion_tokens": 1},
            )

    context = Context(task_id="tool-only-finalization")
    context.set_task(Task(id="tool-only-finalization", input="finish the task"))
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        activation_event_threshold=1,
    )
    agent = ToolOnlyFinalizationAgent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="offline",
            llm_api_key="offline",
        ),
        execution_protocol_policy=policy,
        max_loop_steps=0,
    )
    configure_execution_protocol(context, agent.id(), policy)
    _declare_long_horizon(context, agent.id())
    record_tool_protocol_event(
        context,
        agent.id(),
        {"validation_evidence_advanced": True},
    )
    message = Message(category=Constants.AGENT, headers={"context": context})

    repair = await agent.async_policy(Observation(content="start"), message=message)
    final = await agent.async_policy(
        Observation(content="repair completed"), message=message
    )

    assert repair[0].tool_name == "run_code"
    assert final[0].tool_name == "run_code"
    assert agent.finished is False
    assert calls == 3
    state = ExecutionProtocolStore(context, agent.id(), policy).load()
    assert state.phase.value == "review"
    assert state.review_pending is True
    assert state.repair_count == 0


@pytest.mark.asyncio
async def test_independent_uncertain_review_returns_typed_incomplete_outcome(
    monkeypatch,
) -> None:
    monkeypatch.delenv("AWORLD_INDEPENDENT_ACCEPTANCE_CRITIC", raising=False)
    calls = 0
    clock = 0.0
    requests = []
    captured_reasoning = []

    def monotonic_now() -> float:
        return clock

    class UncertainCriticAgent(Agent):
        async def _add_message_to_memory(self, *args, **kwargs):
            return None

        async def build_llm_input(self, observation, info=None, message=None, **kwargs):
            return [
                {"role": "system", "content": "solver rules"},
                {"role": "assistant", "content": "private solver reasoning"},
                {"role": "user", "content": str(observation.content or "")},
            ]

        async def _filter_tools(self, context=None):
            return None

        async def invoke_model(self, messages=None, message=None, **kwargs):
            nonlocal calls, clock
            calls += 1
            requests.append(messages)
            captured_reasoning.append(kwargs.get("reasoning_effort"))
            if calls in {1, 3}:
                content = f"candidate-{calls}"
            else:
                if calls == 2:
                    # The first review episode has expired by the time its
                    # typed uncertain decision requests repair. That completed
                    # episode must not bound the normal repair turn or the next
                    # candidate's independent review.
                    clock = 11.0
                content = json.dumps(
                    {
                        "decision": "uncertain",
                        "highest_risk_counterexample": "unverified edge case",
                        "hypothesis_id": "edge-case",
                        "reason": "no independent probe tool was available",
                    }
                )
            return ModelResponse(
                id=f"response-{calls}",
                model="offline",
                content=content,
                message={"role": "assistant", "content": content},
                finish_reason="stop",
                usage={"prompt_tokens": 1, "completion_tokens": 1},
            )

    context = Context(task_id="independent-uncertain")
    context.origin_user_input = "complete the public task"
    context.set_task(
        Task(id="independent-uncertain", input="complete the public task", timeout=600)
    )
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(),
            immutable_inputs=(),
            validation_commands=(
                ValidationCommand(
                    command_id="registered-check",
                    argv=("pytest", "-q", "tests/test_contract.py"),
                ),
            ),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
        ),
        mode=CompletionMode.ENFORCE,
    )
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        review_unarmed_candidates=True,
        independent_acceptance_enabled=True,
        final_review_timeout_seconds=10,
        max_repairs=1,
        max_final_reviews=2,
    )
    agent = UncertainCriticAgent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="offline",
            llm_api_key="offline",
        ),
        execution_protocol_policy=policy,
        max_loop_steps=0,
    )
    agent.conf.llm_config.reasoning_phase_policy = ReasoningPhasePolicy.balanced()
    agent.conf.llm_config.reasoning_transport = "openai"
    monkeypatch.setattr(llm_agent_module, "_monotonic_now", monotonic_now)
    message = Message(category=Constants.AGENT, headers={"context": context})

    result = await agent.async_policy(Observation(content="start"), message=message)

    assert calls == 4
    assert captured_reasoning == ["high", "xhigh", "high", "xhigh"]
    assert "completion is unverified" in result[0].policy_info.lower()
    state = get_execution_state(context)
    assert state["status"] == "incomplete"
    assert state["reason"] == "acceptance_evidence_missing"
    assert state["recoverable"] is False
    assert agent._load_long_horizon_review_deadline(context) is None
    for critic_request in (requests[1], requests[3]):
        serialized = json.dumps(critic_request)
        assert "private solver reasoning" not in serialized
        assert [item["role"] for item in critic_request] == ["system", "user"]
