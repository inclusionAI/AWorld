import asyncio

import pytest

from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig
from aworld.core.agent.base import BaseAgent
from aworld.core.common import ActionModel, ActionResult, Observation
from aworld.core.context.base import Context
from aworld.core.event.base import AgentMessage, Constants, Message, TopicType
from aworld.models.model_response import Function, ModelResponse, ToolCall


class LoopBudgetAgent(BaseAgent):
    async def async_policy(self, observation, message=None, **kwargs):
        return observation


def _tool_observation(
    *,
    success: bool = False,
    category: str | None = "infrastructure",
    code: str = "docker_checkpoint_create_failed",
) -> Observation:
    metadata = {}
    if category is not None:
        metadata = {"failure_category": category, "failure_code": code}
    return Observation(
        action_result=[
            ActionResult(
                success=success,
                error=None if success else "backend unavailable",
                tool_name="docker",
                metadata=metadata,
            )
        ]
    )


def _agent_message(agent: BaseAgent, context: Context, payload: Observation) -> Message:
    return Message(
        category=Constants.AGENT,
        payload=payload,
        sender="docker",
        caller=agent.id(),
        session_id=context.session_id,
        headers={"context": context},
    )


@pytest.mark.asyncio
async def test_repeated_typed_infrastructure_failure_opens_circuit_at_threshold():
    agent = LoopBudgetAgent(
        name="infra-circuit",
        conf=AgentConfig(
            llm_provider="mock",
            llm_model_name="mock-model",
            infrastructure_error_circuit_breaker_threshold=3,
        ),
        max_loop_steps=100,
    )
    context = Context(task_id="infra-task", session_id="infra-session")

    for _ in range(2):
        result = await agent.async_run(
            _agent_message(agent, context, _tool_observation())
        )
        assert result.category == Constants.AGENT

    result = await agent.async_run(
        _agent_message(agent, context, _tool_observation())
    )

    assert result.category == Constants.TASK
    assert result.topic == TopicType.FINISHED
    assert result.payload.stop is True
    assert result.payload.success is False
    assert result.payload.msg == "agent_infrastructure_error_circuit_open"
    assert result.payload.data["consecutive_count"] == 3
    assert result.payload.data["failure_codes"] == [
        "docker_checkpoint_create_failed"
    ]


def test_circuit_ignores_untyped_tool_failures_and_resets_after_success():
    agent = LoopBudgetAgent(
        name="infra-reset",
        conf=AgentConfig(
            llm_provider="mock",
            llm_model_name="mock-model",
            infrastructure_error_circuit_breaker_threshold=3,
        ),
    )
    context = Context(task_id="infra-reset-task", session_id="infra-session")

    assert agent._observe_infrastructure_failure(
        _agent_message(agent, context, _tool_observation())
    ) is None
    assert agent._observe_infrastructure_failure(
        _agent_message(agent, context, _tool_observation(success=True))
    ) is None
    assert agent._observe_infrastructure_failure(
        _agent_message(agent, context, _tool_observation(category=None))
    ) is None
    assert agent._observe_infrastructure_failure(
        _agent_message(agent, context, _tool_observation())
    ) is None

    state = context.context_info[
        f"agent_infrastructure_error_circuit:{agent.id()}"
    ]
    assert state["consecutive_count"] == 1


def test_explicit_adaptive_context_installs_elastic_budget_policy():
    agent = LoopBudgetAgent(
        name="adaptive-default",
        conf=AgentConfig(llm_provider="mock", llm_model_name="mock-model",
                         context_compiler={"elastic_step_budget": True}),
        max_loop_steps=20,
    )

    policy = agent._elastic_step_budget_policy
    assert policy is not None
    assert policy.soft_limit == 20
    assert policy.extension_steps == 40
    assert policy.hard_limit == 240
    assert policy.recent_progress_window_steps == 20


def test_agent_config_can_disable_infrastructure_error_circuit_breaker():
    agent = LoopBudgetAgent(
        name="circuit-disabled",
        conf=AgentConfig(
            llm_provider="mock",
            llm_model_name="mock-model",
            infrastructure_error_circuit_breaker_threshold=0,
        ),
    )

    assert agent.infrastructure_error_circuit_breaker_threshold == 0


def test_infrastructure_error_circuit_breaker_is_opt_in():
    agent = LoopBudgetAgent(
        name="circuit-default",
        conf=AgentConfig(llm_provider="mock", llm_model_name="mock-model"),
    )

    assert agent.infrastructure_error_circuit_breaker_threshold == 0


@pytest.mark.parametrize("mode", ["off", "observe", "shadow"])
def test_non_enforcing_context_modes_do_not_install_default_elastic_budget(mode):
    agent = LoopBudgetAgent(
        name=f"adaptive-{mode}",
        conf=AgentConfig(
            llm_provider="mock",
            llm_model_name="mock-model",
            context_compiler={"mode": mode},
        ),
        max_loop_steps=20,
    )

    assert agent._elastic_step_budget_policy is None


def test_explicit_elastic_budget_overrides_context_mode_defaults():
    agent = LoopBudgetAgent(
        name="explicit-elastic",
        conf=AgentConfig(
            llm_provider="mock",
            llm_model_name="mock-model",
            context_compiler={"mode": "off"},
        ),
        max_loop_steps=20,
        loop_step_extension_steps=5,
        max_extended_loop_steps=30,
        loop_step_progress_window=3,
    )

    policy = agent._elastic_step_budget_policy
    assert policy is not None
    assert policy.extension_steps == 5
    assert policy.hard_limit == 30
    assert policy.recent_progress_window_steps == 3


@pytest.mark.asyncio
async def test_agent_terminates_when_configured_loop_budget_is_reached():
    agent = LoopBudgetAgent(
        name="bounded",
        conf=AgentConfig(llm_provider="mock", llm_model_name="mock-model"),
        max_loop_steps=3,
    )

    agent.loop_step = 2
    assert await agent.should_terminate_loop(message=None) is False

    agent.loop_step = 3
    assert await agent.should_terminate_loop(message=None) is True


@pytest.mark.asyncio
async def test_non_positive_loop_budget_remains_unbounded():
    agent = LoopBudgetAgent(
        name="unbounded",
        conf=AgentConfig(llm_provider="mock", llm_model_name="mock-model"),
        max_loop_steps=0,
    )
    agent.loop_step = 10_000

    assert await agent.should_terminate_loop(message=None) is False


@pytest.mark.asyncio
async def test_large_fixed_budget_only_postpones_the_same_hard_stop():
    agent = LoopBudgetAgent(
        name="large-fixed-budget",
        conf=AgentConfig(
            llm_provider="mock",
            llm_model_name="mock-model",
            context_compiler={"mode": "off"},
        ),
        max_loop_steps=1000,
    )
    context = Context(task_id="large-fixed-budget-task")
    message = Message(
        category=Constants.AGENT,
        payload="observation",
        sender="tool",
        caller=agent.id(),
        session_id="session",
        headers={"context": context},
    )

    for _ in range(20):
        context.update_agent_step(agent.id())
    assert await agent.should_terminate_loop(message) is False

    for _ in range(980):
        context.update_agent_step(agent.id())
    assert await agent.should_terminate_loop(message) is True


@pytest.mark.asyncio
async def test_async_run_emits_task_completion_and_resolves_contract_at_budget():
    agent = LoopBudgetAgent(
        name="bounded",
        conf=AgentConfig(llm_provider="mock", llm_model_name="mock-model"),
        max_loop_steps=1,
    )
    context = Context(task_id="bounded-task")
    resolved = []

    async def resolve():
        resolved.append(True)

    context.resolve_completion_evidence = resolve
    message = Message(
        category=Constants.AGENT,
        payload="last observation",
        sender="tool",
        caller=agent.id(),
        session_id="session",
        headers={"context": context},
    )

    result = await agent.async_run(message)

    assert result.category == Constants.TASK
    assert result.topic == TopicType.FINISHED
    assert result.payload.stop is True
    assert resolved == [True]
    exhaustion = context.context_info[f"agent_loop_budget_exhausted:{agent.id()}"]
    assert exhaustion["loop_step"] == 1
    assert exhaustion["context_agent_step"] == 1
    assert exhaustion["max_loop_steps"] == 1
    assert "elastic_budget" not in exhaustion


@pytest.mark.asyncio
async def test_budget_resolution_does_not_repeat_evidence_resolved_in_final_turn():
    agent = LoopBudgetAgent(
        name="resolved-once",
        conf=AgentConfig(llm_provider="mock", llm_model_name="mock-model"),
        max_loop_steps=1,
    )
    context = Context(task_id="resolved-once-task")
    resolved = []

    async def resolve():
        resolved.append(True)

    context.resolve_completion_evidence = resolve
    context.context_info[
        f"completion_evidence_resolved_this_turn:{agent.id()}"
    ] = context.get_agent_step(agent.id())
    message = Message(
        category=Constants.AGENT,
        payload="last observation",
        sender="tool",
        caller=agent.id(),
        session_id="session",
        headers={"context": context},
    )

    await agent._resolve_completion_at_loop_budget(message)

    assert resolved == []


@pytest.mark.asyncio
async def test_llm_agent_uses_budget_boundary_for_one_finalization_turn():
    class FinalizingAgent(Agent):
        async def async_policy(self, observation, message=None, **kwargs):
            self.finalization_requested = kwargs.get("_loop_budget_finalization")
            self._finished = True
            return [
                ActionModel(
                    agent_name=self.id(),
                    policy_info="verified final summary",
                )
            ]

    agent = FinalizingAgent(
        name="finalizing",
        conf=AgentConfig(llm_provider="mock", llm_model_name="mock-model"),
        max_loop_steps=1,
    )
    context = Context(task_id="finalizing-task")
    message = Message(
        category=Constants.AGENT,
        payload=Observation(content="last tool observation"),
        sender="tool",
        caller=agent.id(),
        session_id="session",
        headers={"context": context},
    )

    result = await agent.async_run(message)

    assert isinstance(result, AgentMessage)
    assert result.payload[0].policy_info == "verified final summary"
    assert agent.finalization_requested is True
    exhaustion = context.context_info[f"agent_loop_budget_exhausted:{agent.id()}"]
    assert exhaustion["finalization_performed"] is True
    assert exhaustion["final_answer_preserved"] is True


def test_sync_llm_agent_uses_budget_boundary_for_finalization():
    class SyncFinalizingAgent(Agent):
        async def async_policy(self, observation, message=None, **kwargs):
            self.finalization_requested = kwargs.get("_loop_budget_finalization")
            self._finished = True
            return [ActionModel(agent_name=self.id(), policy_info="sync summary")]

        async def async_post_run(self, policy_result, policy_input, message=None):
            return AgentMessage(payload=policy_result, headers=message.headers)

    agent = SyncFinalizingAgent(
        name="sync-finalizing",
        conf=AgentConfig(llm_provider="mock", llm_model_name="mock-model"),
        max_loop_steps=1,
    )
    context = Context(task_id="sync-finalizing-task")
    message = Message(
        category=Constants.AGENT,
        payload=Observation(content="last tool observation"),
        sender="tool",
        caller=agent.id(),
        session_id="session",
        headers={"context": context},
    )

    result = agent.run(message)

    assert isinstance(result, AgentMessage)
    assert result.payload[0].policy_info == "sync summary"
    assert agent.finalization_requested is True
    exhaustion = context.context_info[f"agent_loop_budget_exhausted:{agent.id()}"]
    assert exhaustion["finalization_performed"] is True
    assert exhaustion["final_answer_preserved"] is True


@pytest.mark.asyncio
async def test_non_llm_agent_keeps_hard_budget_termination_without_finalizer():
    agent = LoopBudgetAgent(
        name="generic-bounded",
        conf=AgentConfig(llm_provider="mock", llm_model_name="mock-model"),
        max_loop_steps=1,
    )
    context = Context(task_id="generic-bounded-task")
    message = Message(
        category=Constants.AGENT,
        payload="last observation",
        sender="tool",
        caller=agent.id(),
        session_id="session",
        headers={"context": context},
    )

    result = await agent.async_run(message)

    assert result.category == Constants.TASK
    assert result.payload.msg == "agent_loop_budget_exhausted"
    exhaustion = context.context_info[f"agent_loop_budget_exhausted:{agent.id()}"]
    assert exhaustion["finalization_performed"] is False


@pytest.mark.asyncio
async def test_failed_llm_budget_finalization_degrades_to_hard_stop():
    class FailingFinalizer(Agent):
        async def async_policy(self, observation, message=None, **kwargs):
            raise RuntimeError("model unavailable")

    agent = FailingFinalizer(
        name="failing-finalizer",
        conf=AgentConfig(llm_provider="mock", llm_model_name="mock-model"),
        max_loop_steps=1,
    )
    context = Context(task_id="failing-finalizer-task")
    message = Message(
        category=Constants.AGENT,
        payload=Observation(content="last tool observation"),
        sender="tool",
        caller=agent.id(),
        session_id="session",
        headers={"context": context},
    )

    result = await agent.async_run(message)

    assert result.category == Constants.TASK
    assert result.payload.msg == "agent_loop_budget_exhausted"
    assert context.context_info[
        f"agent_loop_budget_finalization_error:{agent.id()}"
    ] == {"error_type": "RuntimeError"}
    exhaustion = context.context_info[f"agent_loop_budget_exhausted:{agent.id()}"]
    assert exhaustion["finalization_performed"] is False


@pytest.mark.asyncio
async def test_cancelled_llm_budget_finalization_propagates():
    class CancelledFinalizer(Agent):
        async def async_policy(self, observation, message=None, **kwargs):
            raise asyncio.CancelledError()

    agent = CancelledFinalizer(
        name="cancelled-finalizer",
        conf=AgentConfig(llm_provider="mock", llm_model_name="mock-model"),
        max_loop_steps=1,
    )
    context = Context(task_id="cancelled-finalizer-task")
    message = Message(
        category=Constants.AGENT,
        payload=Observation(content="last tool observation"),
        sender="tool",
        caller=agent.id(),
        session_id="session",
        headers={"context": context},
    )

    with pytest.raises(asyncio.CancelledError):
        await agent.async_run(message)

    assert (
        f"agent_loop_budget_finalization_error:{agent.id()}"
        not in context.context_info
    )


def test_budget_finalization_sanitizes_a_copy_and_preserves_raw_response():
    raw = ModelResponse(
        id="raw-response",
        model="test-model",
        content="",
        reasoning_content="internal reasoning must remain private",
        tool_calls=[
            ToolCall(
                id="call-1",
                function=Function(name="terminal", arguments="{}"),
            )
        ],
    )

    sanitized = Agent._coerce_loop_budget_final_response(raw)

    assert sanitized is not raw
    assert len(raw.tool_calls) == 1
    assert "tool_calls" in raw.message
    assert sanitized.tool_calls == []
    assert "tool_calls" not in sanitized.message
    assert sanitized.content == ""


@pytest.mark.asyncio
async def test_empty_llm_budget_finalization_degrades_to_hard_stop():
    class EmptyFinalizer(Agent):
        async def async_policy(self, observation, message=None, **kwargs):
            self._finished = True
            return [ActionModel(agent_name=self.id(), policy_info="")]

    agent = EmptyFinalizer(
        name="empty-finalizer",
        conf=AgentConfig(llm_provider="mock", llm_model_name="mock-model"),
        max_loop_steps=1,
    )
    context = Context(task_id="empty-finalizer-task")
    message = Message(
        category=Constants.AGENT,
        payload=Observation(content="last tool observation"),
        sender="tool",
        caller=agent.id(),
        session_id="session",
        headers={"context": context},
    )

    result = await agent.async_run(message)

    assert result.category == Constants.TASK
    assert result.payload.msg == "agent_loop_budget_exhausted"
    assert context.context_info[
        f"agent_loop_budget_finalization_error:{agent.id()}"
    ] == {"error_type": "AWorldRuntimeException"}


@pytest.mark.asyncio
async def test_context_agent_step_is_monotonic_when_caller_identity_changes():
    agent = LoopBudgetAgent(
        name="bounded",
        conf=AgentConfig(llm_provider="mock", llm_model_name="mock-model"),
        max_loop_steps=3,
    )
    context = Context(task_id="bounded-task")
    message = Message(
        category=Constants.AGENT,
        payload="observation",
        sender="tool",
        caller="different-caller",
        session_id="session",
        headers={"context": context},
    )

    context.update_agent_step(agent.id())
    context.update_agent_step(agent.id())
    assert await agent.should_terminate_loop(message) is False
    context.update_agent_step(agent.id())
    assert await agent.should_terminate_loop(message) is True


@pytest.mark.asyncio
async def test_agent_loop_budget_is_monotonic_across_context_transport_copies():
    agent = LoopBudgetAgent(
        name="bounded",
        conf=AgentConfig(llm_provider="mock", llm_model_name="mock-model"),
        max_loop_steps=3,
    )
    root = Context(task_id="bounded-task")

    first = root.deep_copy()
    first.update_agent_step(agent.id())
    assert first.get_agent_step(agent.id()) == 1
    assert root.get_agent_step(agent.id()) == 1

    second = root.deep_copy()
    second.update_agent_step(agent.id())
    assert second.get_agent_step(agent.id()) == 2
    assert first.get_agent_step(agent.id()) == 2

    third = root.deep_copy()
    third.update_agent_step(agent.id())
    message = Message(
        category=Constants.AGENT,
        payload="observation",
        sender="tool",
        caller="different-caller",
        session_id="session",
        headers={"context": third},
    )

    assert third.get_agent_step(agent.id()) == 3
    assert await agent.should_terminate_loop(message) is True


@pytest.mark.asyncio
async def test_agent_loop_budget_registry_is_partitioned_by_task():
    root = Context(task_id="parent-task")
    agent_id = "shared-agent"
    root.update_agent_step(agent_id)

    child = await root.build_sub_context("child input", sub_task_id="child-task")
    child.update_agent_step(agent_id)

    assert root.get_agent_step(agent_id) == 1
    assert child.get_agent_step(agent_id) == 1


@pytest.mark.asyncio
async def test_duplicate_post_tool_continuation_is_consumed_exactly_once():
    agent = LoopBudgetAgent(
        name="deduplicated",
        conf=AgentConfig(llm_provider="mock", llm_model_name="mock-model"),
        max_loop_steps=0,
    )
    context = Context(task_id="deduplicated-task")

    def continuation() -> Message:
        return Message(
            category=Constants.AGENT,
            payload="same tool observation",
            sender="tool",
            caller=agent.id(),
            session_id="session",
            headers={
                "context": context.deep_copy(),
                "post_tool_continuation_token": "sha256:one-observation",
            },
        )

    first = await agent.async_run(continuation())
    duplicate = await agent.async_run(continuation())

    assert first is not None
    assert duplicate is None
    assert context.get_agent_step(agent.id()) == 1


@pytest.mark.asyncio
async def test_elastic_budget_grants_only_recent_new_goal_progress():
    agent = LoopBudgetAgent(
        name="elastic",
        conf=AgentConfig(llm_provider="mock", llm_model_name="mock-model"),
        max_loop_steps=3,
        loop_step_extension_steps=2,
        max_extended_loop_steps=7,
        loop_step_progress_window=2,
    )
    context = Context(task_id="elastic-task")
    message = Message(
        category=Constants.AGENT,
        payload="observation",
        sender="tool",
        caller=agent.id(),
        session_id="session",
        headers={"context": context},
    )

    for _ in range(3):
        context.update_agent_step(agent.id())
    context.context_info["post_tool_progress_metrics"] = {"goal_progress_count": 1}
    context.context_info["context_semantic_progress"] = {
        agent.id(): {
            "goal_progress_count": 1,
            "last_goal_progress_agent_step": 2,
        }
    }

    assert await agent.should_terminate_loop(message) is False
    receipt = context.context_info[f"agent_step_budget:{agent.id()}"]
    assert receipt["decision"] == "progress_extension_granted"
    assert receipt["effective_limit"] == 5
    assert receipt["extension_count"] == 1

    context.update_agent_step(agent.id())
    context.update_agent_step(agent.id())
    assert await agent.should_terminate_loop(message) is True
    receipt = context.context_info[f"agent_step_budget:{agent.id()}"]
    assert receipt["decision"] == "no_new_goal_progress"

    context.context_info["post_tool_progress_metrics"]["goal_progress_count"] = 2
    context.context_info["context_semantic_progress"][agent.id()][
        "goal_progress_count"
    ] = 2
    context.context_info["context_semantic_progress"][agent.id()][
        "last_goal_progress_agent_step"
    ] = 5
    assert await agent.should_terminate_loop(message) is False
    assert (
        context.context_info[f"agent_step_budget:{agent.id()}"]["effective_limit"] == 7
    )

    context.update_agent_step(agent.id())
    context.update_agent_step(agent.id())
    context.context_info["post_tool_progress_metrics"]["goal_progress_count"] = 3
    context.context_info["context_semantic_progress"][agent.id()][
        "goal_progress_count"
    ] = 3
    context.context_info["context_semantic_progress"][agent.id()][
        "last_goal_progress_agent_step"
    ] = 7
    assert await agent.should_terminate_loop(message) is True
    assert (
        context.context_info[f"agent_step_budget:{agent.id()}"]["decision"]
        == "hard_limit_reached"
    )


@pytest.mark.asyncio
async def test_elastic_budget_extends_when_goal_progress_is_unobservable():
    agent = LoopBudgetAgent(
        name="elastic-unobservable",
        conf=AgentConfig(llm_provider="mock", llm_model_name="mock-model"),
        max_loop_steps=3,
        loop_step_extension_steps=2,
        max_extended_loop_steps=7,
        loop_step_progress_window=2,
    )
    context = Context(task_id="elastic-unobservable-task")
    context.context_info["context_semantic_progress"] = {
        agent.id(): {
            "goal_progress_count": 0,
            "goal_progress_observable": False,
            "last_goal_progress_agent_step": None,
        }
    }
    message = Message(
        category=Constants.AGENT,
        payload="observation",
        sender="tool",
        caller=agent.id(),
        session_id="session",
        headers={"context": context},
    )

    for _ in range(3):
        context.update_agent_step(agent.id())
    assert await agent.should_terminate_loop(message) is False
    receipt = context.context_info[f"agent_step_budget:{agent.id()}"]
    assert receipt["decision"] == "unobservable_progress_extension_granted"
    assert receipt["effective_limit"] == 5

    context.update_agent_step(agent.id())
    context.update_agent_step(agent.id())
    assert await agent.should_terminate_loop(message) is False
    assert (
        context.context_info[f"agent_step_budget:{agent.id()}"]["effective_limit"]
        == 7
    )

    context.update_agent_step(agent.id())
    context.update_agent_step(agent.id())
    assert await agent.should_terminate_loop(message) is True
    assert (
        context.context_info[f"agent_step_budget:{agent.id()}"]["decision"]
        == "hard_limit_reached"
    )


@pytest.mark.asyncio
async def test_elastic_budget_rejects_stale_progress_at_soft_limit():
    agent = LoopBudgetAgent(
        name="elastic-stale",
        conf=AgentConfig(llm_provider="mock", llm_model_name="mock-model"),
        max_loop_steps=3,
        loop_step_extension_steps=2,
        max_extended_loop_steps=7,
        loop_step_progress_window=1,
    )
    context = Context(task_id="elastic-stale-task")
    for _ in range(3):
        context.update_agent_step(agent.id())
    context.context_info["post_tool_progress_metrics"] = {"goal_progress_count": 1}
    context.context_info["context_semantic_progress"] = {
        agent.id(): {
            "goal_progress_count": 1,
            "last_goal_progress_agent_step": 1,
        }
    }
    message = Message(
        category=Constants.AGENT,
        payload="observation",
        sender="tool",
        caller=agent.id(),
        session_id="session",
        headers={"context": context},
    )

    assert await agent.should_terminate_loop(message) is True
    assert (
        context.context_info[f"agent_step_budget:{agent.id()}"]["decision"]
        == "goal_progress_stale"
    )


@pytest.mark.asyncio
async def test_elastic_budget_does_not_consume_another_agents_progress():
    agent = LoopBudgetAgent(
        name="elastic-isolated",
        conf=AgentConfig(llm_provider="mock", llm_model_name="mock-model"),
        max_loop_steps=2,
        loop_step_extension_steps=2,
        max_extended_loop_steps=4,
        loop_step_progress_window=2,
    )
    context = Context(task_id="multi-agent-task")
    for _ in range(2):
        context.update_agent_step(agent.id())
    context.context_info["post_tool_progress_metrics"] = {"goal_progress_count": 9}
    context.context_info["context_semantic_progress"] = {
        "different-agent": {
            "goal_progress_count": 9,
            "last_goal_progress_agent_step": 2,
        }
    }
    message = Message(
        category=Constants.AGENT,
        payload="observation",
        sender="tool",
        caller=agent.id(),
        session_id="session",
        headers={"context": context},
    )

    assert await agent.should_terminate_loop(message) is True
    assert (
        context.context_info[f"agent_step_budget:{agent.id()}"]["decision"]
        == "no_new_goal_progress"
    )


@pytest.mark.asyncio
async def test_async_agent_runs_are_serialized_per_task_across_context_copies():
    entered = asyncio.Event()
    release = asyncio.Event()

    class SerializedAgent(LoopBudgetAgent):
        async def async_policy(self, observation, message=None, **kwargs):
            entered.set()
            await release.wait()
            return observation

    agent = SerializedAgent(
        name="serialized",
        conf=AgentConfig(llm_provider="mock", llm_model_name="mock-model"),
        max_loop_steps=2,
    )
    root = Context(task_id="serialized-task")

    def message(context):
        return Message(
            category=Constants.AGENT,
            payload="observation",
            sender="tool",
            caller=agent.id(),
            session_id="session",
            headers={"context": context},
        )

    first = asyncio.create_task(agent.async_run(message(root.deep_copy())))
    await entered.wait()
    second = asyncio.create_task(agent.async_run(message(root.deep_copy())))
    await asyncio.sleep(0)

    assert second.done() is False
    assert root.get_agent_step(agent.id()) == 1

    release.set()
    await first
    second_result = await second
    assert second_result.topic == TopicType.FINISHED
    assert second_result.payload.msg == "agent_loop_budget_exhausted"
    assert root.get_agent_step(agent.id()) == 2


@pytest.mark.asyncio
async def test_cancelled_agent_run_releases_shared_execution_lock():
    entered = asyncio.Event()

    class CancelledAgent(LoopBudgetAgent):
        async def async_policy(self, observation, message=None, **kwargs):
            entered.set()
            await asyncio.Event().wait()

    agent = CancelledAgent(
        name="cancelled",
        conf=AgentConfig(llm_provider="mock", llm_model_name="mock-model"),
        max_loop_steps=3,
    )
    root = Context(task_id="cancelled-task")

    def message():
        return Message(
            category=Constants.AGENT,
            payload="observation",
            sender="tool",
            caller=agent.id(),
            session_id="session",
            headers={"context": root.deep_copy()},
        )

    first = asyncio.create_task(agent.async_run(message()))
    await entered.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    lock = root.get_agent_execution_lock(agent.id())
    await asyncio.wait_for(lock.acquire(), timeout=1)
    lock.release()
