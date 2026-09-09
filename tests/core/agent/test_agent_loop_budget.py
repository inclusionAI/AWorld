import asyncio

import pytest

from aworld.config.conf import AgentConfig
from aworld.core.agent.base import BaseAgent
from aworld.core.context.base import Context
from aworld.core.event.base import Constants, Message, TopicType


class LoopBudgetAgent(BaseAgent):
    async def async_policy(self, observation, message=None, **kwargs):
        return observation


def test_default_adaptive_context_installs_elastic_budget_policy():
    agent = LoopBudgetAgent(
        name="adaptive-default",
        conf=AgentConfig(llm_provider="mock", llm_model_name="mock-model"),
        max_loop_steps=20,
    )

    policy = agent._elastic_step_budget_policy
    assert policy is not None
    assert policy.soft_limit == 20
    assert policy.extension_steps == 40
    assert policy.hard_limit == 240
    assert policy.recent_progress_window_steps == 20


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
    assert exhaustion["elastic_budget"]["decision"] == "no_new_goal_progress"


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
