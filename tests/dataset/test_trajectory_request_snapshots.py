import pytest

from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig
from aworld.core.context.base import Context
from aworld.core.context.session import Session
from aworld.core.event.base import Constants, Message
from aworld.core.task import Task
from aworld.dataset.trajectory_strategy import DefaultTrajectoryStrategy


def _build_agent(name: str = "Aworld") -> Agent:
    return Agent(
        name=name,
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
        ),
    )


def _build_message(agent: Agent, task_id: str = "task-1") -> Message:
    context = Context(task_id=task_id)
    context.session = Session(session_id="session-1")
    context.set_task(Task(id=task_id, name="test-task"))
    return Message(
        category=Constants.AGENT,
        sender="user",
        receiver=agent.name(),
        payload="hello",
        headers={"context": context},
    )


@pytest.mark.asyncio
async def test_build_trajectory_state_prefers_latest_llm_call_snapshot(monkeypatch):
    agent = _build_agent()
    strategy = DefaultTrajectoryStrategy()
    message = _build_message(agent)
    message.context.context_info["llm_calls"] = [
        {"call_id": "call-1", "request": {"messages": [{"role": "user", "content": "first"}]}},
        {"call_id": "call-2", "request": {"messages": [{"role": "user", "content": "second"}]}},
    ]

    def fail_if_memory_fallback(*args, **kwargs):
        raise AssertionError("memory reconstruction should not run when llm_calls snapshots exist")

    monkeypatch.setattr(strategy, "_get_llm_messages_from_memory", fail_if_memory_fallback)

    state = await strategy.build_trajectory_state(message, use_tools_in_prompt=False)

    assert state.messages == [{"role": "user", "content": "second"}]


@pytest.mark.asyncio
async def test_message_to_trajectory_item_uses_call_id_to_avoid_snapshot_overwrite():
    agent = _build_agent()
    strategy = DefaultTrajectoryStrategy()
    message = _build_message(agent)
    message.context.context_info["llm_calls"] = [
        {
            "call_id": "call-2",
            "request_id": "request-2",
            "request": {"messages": [{"role": "user", "content": "second"}]},
        }
    ]
    message.headers["aworld_trajectory_llm_call_id"] = "call-2"

    item = await strategy.message_to_trajectory_item(message)

    assert item.id == f"{message.id}:call-2"
    assert item.meta.llm_request_id == "request-2"
    assert item.state.messages == [{"role": "user", "content": "second"}]


@pytest.mark.asyncio
async def test_native_trajectory_drops_unbounded_llm_request_id() -> None:
    agent = _build_agent()
    strategy = DefaultTrajectoryStrategy()
    message = _build_message(agent)
    message.context.context_info["llm_calls"] = [
        {
            "call_id": "call-oversized",
            "request_id": "r" * 257,
            "request": {"messages": [{"role": "user", "content": "second"}]},
        }
    ]
    message.headers["aworld_trajectory_llm_call_id"] = "call-oversized"

    item = await strategy.message_to_trajectory_item(message)

    assert item.meta.llm_request_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("include_prior", [False, True])
async def test_native_trajectory_uses_explicit_current_call_after_source_message(
    include_prior: bool,
) -> None:
    agent = _build_agent()
    strategy = DefaultTrajectoryStrategy()
    message = _build_message(agent)
    message.timestamp = 100.0
    current = {
        "call_id": "call-current",
        "request_id": "request-current",
        "task_id": "task-1",
        "agent_id": agent.id(),
        "started_at": 101.0,
        "finished_at": 102.0,
        "request": {"messages": [{"role": "user", "content": "current"}]},
    }
    prior = {
        "call_id": "call-prior",
        "request_id": "request-prior",
        "task_id": "task-1",
        "agent_id": agent.id(),
        "started_at": 90.0,
        "finished_at": 99.0,
        "request": {"messages": [{"role": "user", "content": "prior"}]},
    }
    message.context.context_info["llm_calls"] = (
        [prior, current] if include_prior else [current]
    )
    message.headers["aworld_trajectory_llm_call_id"] = "call-current"

    item = await strategy.message_to_trajectory_item(message)

    assert item.meta.llm_request_id == "request-current"
    assert item.id == f"{message.id}:call-current"


def test_agent_request_reservation_is_carried_on_source_message() -> None:
    agent = _build_agent()
    message = _build_message(agent)

    call_id = agent._record_llm_call_request(
        message,
        [{"role": "user", "content": "hello"}],
    )

    assert message.headers["aworld_trajectory_llm_call_id"] == call_id
