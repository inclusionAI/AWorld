import pytest

from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.dataset.trajectory_strategy import DefaultTrajectoryStrategy


def _build_message(*, timestamp: float = 20.0, receiver: str = "agent-1") -> Message:
    context = Context(task_id="task-1")
    message = Message(
        payload={"question": "hello"},
        receiver=receiver,
        timestamp=timestamp,
    )
    message.context = context
    return message


@pytest.mark.asyncio
async def test_build_trajectory_state_prefers_matching_llm_call_messages(monkeypatch):
    strategy = DefaultTrajectoryStrategy()
    message = _build_message(timestamp=25.0)
    message.context.context_info["llm_calls"] = [
        {
            "task_id": "task-1",
            "agent_id": "agent-1",
            "started_at": 10.0,
            "finished_at": 12.0,
            "request": {"messages": [{"role": "user", "content": "older"}]},
        },
        {
            "task_id": "task-1",
            "agent_id": "agent-1",
            "started_at": 21.0,
            "finished_at": 24.0,
            "request": {
                "messages": [
                    {"role": "system", "content": "truth-source"},
                    {"role": "user", "content": "latest"},
                ]
            },
        },
    ]

    def _unexpected_fallback(*args, **kwargs):
        raise AssertionError("memory fallback should not be used when llm_calls exist")

    monkeypatch.setattr(strategy, "_get_llm_messages_from_memory", _unexpected_fallback)

    state = await strategy.build_trajectory_state(message)

    assert state.messages == [
        {"role": "system", "content": "truth-source"},
        {"role": "user", "content": "latest"},
    ]


@pytest.mark.asyncio
async def test_build_trajectory_state_falls_back_to_memory_without_llm_calls(monkeypatch):
    strategy = DefaultTrajectoryStrategy()
    message = _build_message()
    expected_messages = [{"role": "user", "content": "from-memory"}]

    monkeypatch.setattr(
        strategy,
        "_get_llm_messages_from_memory",
        lambda source, use_tools_in_prompt: expected_messages,
    )

    state = await strategy.build_trajectory_state(message)

    assert state.messages == expected_messages


@pytest.mark.asyncio
async def test_build_trajectory_state_strips_cache_usage_fields_from_llm_messages(monkeypatch):
    strategy = DefaultTrajectoryStrategy()
    message = _build_message(timestamp=30.0)
    message.context.context_info["llm_calls"] = [
        {
            "task_id": "task-1",
            "agent_id": "agent-1",
            "started_at": 20.0,
            "finished_at": 29.0,
            "request": {
                "messages": [
                    {
                        "role": "system",
                        "content": "cache metadata should stay out of trajectory",
                        "cache_hit_tokens": 80,
                        "cache_write_tokens": 20,
                        "prompt_tokens_details": {"cached_tokens": 80},
                    }
                ]
            },
        }
    ]

    def _unexpected_fallback(*args, **kwargs):
        raise AssertionError("memory fallback should not be used when llm_calls exist")

    monkeypatch.setattr(strategy, "_get_llm_messages_from_memory", _unexpected_fallback)

    state = await strategy.build_trajectory_state(message)

    assert state.messages == [
        {
            "role": "system",
            "content": "cache metadata should stay out of trajectory",
        }
    ]
    assert "cache_hit_tokens" not in state.messages[0]
    assert "cache_write_tokens" not in state.messages[0]
    assert "prompt_tokens_details" not in state.messages[0]


@pytest.mark.asyncio
async def test_build_trajectory_state_falls_back_when_llm_call_agent_does_not_match(monkeypatch):
    strategy = DefaultTrajectoryStrategy()
    message = _build_message(timestamp=25.0, receiver="agent-1")
    expected_messages = [{"role": "user", "content": "from-memory"}]
    message.context.context_info["llm_calls"] = [
        {
            "task_id": "task-1",
            "agent_id": "agent-2",
            "started_at": 20.0,
            "finished_at": 24.0,
            "request": {"messages": [{"role": "user", "content": "wrong-agent"}]},
        }
    ]

    monkeypatch.setattr(
        strategy,
        "_get_llm_messages_from_memory",
        lambda source, use_tools_in_prompt: expected_messages,
    )

    state = await strategy.build_trajectory_state(message)

    assert state.messages == expected_messages


@pytest.mark.asyncio
async def test_build_trajectory_state_falls_back_when_only_future_llm_calls_exist(monkeypatch):
    strategy = DefaultTrajectoryStrategy()
    message = _build_message(timestamp=25.0, receiver="agent-1")
    expected_messages = [{"role": "user", "content": "from-memory"}]
    message.context.context_info["llm_calls"] = [
        {
            "task_id": "task-1",
            "agent_id": "agent-1",
            "started_at": 26.0,
            "finished_at": 28.0,
            "request": {"messages": [{"role": "user", "content": "future-call"}]},
        }
    ]

    monkeypatch.setattr(
        strategy,
        "_get_llm_messages_from_memory",
        lambda source, use_tools_in_prompt: expected_messages,
    )

    state = await strategy.build_trajectory_state(message)

    assert state.messages == expected_messages


@pytest.mark.asyncio
async def test_build_trajectory_state_falls_back_when_only_other_task_llm_calls_exist(monkeypatch):
    strategy = DefaultTrajectoryStrategy()
    message = _build_message(timestamp=25.0, receiver="agent-1")
    expected_messages = [{"role": "user", "content": "from-memory"}]
    message.context.context_info["llm_calls"] = [
        {
            "task_id": "child-task",
            "agent_id": "agent-1",
            "started_at": 20.0,
            "finished_at": 24.0,
            "request": {"messages": [{"role": "user", "content": "child-task-call"}]},
        }
    ]

    monkeypatch.setattr(
        strategy,
        "_get_llm_messages_from_memory",
        lambda source, use_tools_in_prompt: expected_messages,
    )

    state = await strategy.build_trajectory_state(message)

    assert state.messages == expected_messages
