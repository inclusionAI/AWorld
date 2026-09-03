import pytest

from aworld.config.conf import AgentConfig
from aworld.core.agent.base import BaseAgent
from aworld.core.context.base import Context
from aworld.core.event.base import Constants, Message, TopicType


class LoopBudgetAgent(BaseAgent):
    async def async_policy(self, observation, message=None, **kwargs):
        return observation


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
    assert context.context_info[f"agent_loop_budget_exhausted:{agent.id()}"] == {
        "loop_step": 1,
        "context_agent_step": 1,
        "max_loop_steps": 1,
    }


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
