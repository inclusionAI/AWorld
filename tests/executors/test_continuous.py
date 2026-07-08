from io import StringIO
import sys
from types import SimpleNamespace

import pytest
from rich.console import Console

from aworld_cli.executors.continuous import ContinuousExecutor


@pytest.mark.asyncio
async def test_run_iteration_uses_active_steering_in_terminal_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_chat(prompt: str, **kwargs):
        captured["chat_prompt"] = prompt
        captured["chat_kwargs"] = kwargs
        return "chat-result"

    async def fake_run_executor_with_active_steering(**kwargs):
        captured["active_steering_kwargs"] = kwargs
        executor = kwargs["executor"]
        return await executor(kwargs["prompt"])

    fake_cli = SimpleNamespace(
        _build_session_completer=lambda **kwargs: "completer",
        _run_executor_with_active_steering=fake_run_executor_with_active_steering,
    )
    fake_runtime = SimpleNamespace(cli=fake_cli)
    fake_executor = SimpleNamespace(
        chat=fake_chat,
        session_id="sess-1",
        _base_runtime=fake_runtime,
    )
    continuous = ContinuousExecutor(
        fake_executor,
        console=Console(file=StringIO(), force_terminal=False),
    )

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    result = await continuous.run_iteration(
        1,
        "hello",
        agent_name="Aworld",
        requested_skill_names=["browser-use"],
    )

    assert result["success"] is True
    assert captured["active_steering_kwargs"]["prompt"] == "hello"
    assert captured["active_steering_kwargs"]["agent_name"] == "Aworld"
    assert captured["active_steering_kwargs"]["executor_instance"] is fake_executor
    assert captured["chat_kwargs"]["requested_skill_names"] == ["browser-use"]


@pytest.mark.asyncio
async def test_run_iteration_carries_task_response_trajectory() -> None:
    full_trajectory = [
        {
            "id": "step-1",
            "state": {"messages": [{"role": "assistant", "content": "evidence"}]},
            "action": {"content": "done", "tool_calls": [{"name": "browser"}]},
            "reward": {"status": "ok"},
        }
    ]

    async def fake_chat(prompt: str, **kwargs):
        return "done"

    fake_executor = SimpleNamespace(
        chat=fake_chat,
        session_id="sess-1",
        last_task_response=SimpleNamespace(
            trajectory=full_trajectory,
            llm_calls=[{"model": "test-model"}],
        ),
    )
    continuous = ContinuousExecutor(
        fake_executor,
        console=Console(file=StringIO(), force_terminal=False),
    )

    result = await continuous.run_iteration(1, "hello", agent_name="Aworld")

    assert result["trajectory_capture_mode"] == "task_response"
    assert result["trajectory"] == full_trajectory
    assert result["llm_calls"] == [{"model": "test-model"}]
