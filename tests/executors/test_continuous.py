from io import StringIO
import sys
from types import SimpleNamespace

import pytest
from rich.console import Console

from aworld_cli.executors.continuous import ContinuousExecutor
from aworld_cli.executors.local import LocalAgentExecutor


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
        last_llm_usage={
            "schema_version": "aworld.llm_usage_summary.v1",
            "call_count": 1,
            "usage_call_count": 1,
            "total_tokens": 42,
            "coverage_complete": True,
            "ledger_consistent": True,
        },
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
    assert result["llm_usage"]["total_tokens"] == 42


@pytest.mark.asyncio
async def test_run_iteration_propagates_failed_task_response() -> None:
    async def fake_chat(prompt: str, **kwargs):
        return "Task fail, cause: provider_timeout"

    fake_executor = SimpleNamespace(
        chat=fake_chat,
        session_id="sess-1",
        last_task_response=SimpleNamespace(
            success=False,
            trajectory=[{"id": "failed-step"}],
            llm_calls=[{"model": "test-model"}],
        ),
    )
    continuous = ContinuousExecutor(
        fake_executor,
        console=Console(file=StringIO(), force_terminal=False),
    )

    result = await continuous.run_iteration(1, "hello", agent_name="Aworld")

    assert result["success"] is False
    assert result["completed"] is False
    assert result["immediate_stop"] is False


@pytest.mark.asyncio
async def test_run_iteration_does_not_publish_partial_llm_usage() -> None:
    async def fake_chat(prompt: str, **kwargs):
        return "done"

    fake_executor = SimpleNamespace(
        chat=fake_chat,
        session_id="sess-1",
        last_llm_usage={
            "schema_version": "aworld.llm_usage_summary.v1",
            "call_count": 2,
            "usage_call_count": 1,
            "total_tokens": 42,
            "coverage_complete": False,
            "ledger_consistent": True,
        },
        last_task_response=SimpleNamespace(
            trajectory=[
                {
                    "action": {
                        "content": "done",
                        "is_agent_finished": True,
                        "tool_calls": [],
                    }
                }
            ],
        ),
    )
    continuous = ContinuousExecutor(
        fake_executor,
        console=Console(file=StringIO(), force_terminal=False),
    )

    result = await continuous.run_iteration(1, "hello", agent_name="Aworld")

    assert "llm_usage" not in result


@pytest.mark.asyncio
async def test_local_interruption_signal_cannot_be_reclassified_as_success() -> None:
    executor = object.__new__(LocalAgentExecutor)
    executor.session_id = "sess-1"
    executor.last_task_response = None
    executor.last_task_interrupted = False
    executor._publish_hud_task_finished = lambda *_args, **_kwargs: None

    async def no_hooks(*_args, **_kwargs):
        return []

    executor._run_plugin_task_hook = no_hooks
    task = SimpleNamespace(id="task-1")

    async def interrupted_chat(_prompt: str, **_kwargs):
        return await executor._handle_task_interrupted(
            task,
            answer="partial model output",
        )

    executor.chat = interrupted_chat
    continuous = ContinuousExecutor(
        executor,
        console=Console(file=StringIO(), force_terminal=False),
    )

    result = await continuous.run_iteration(
        1,
        "hello",
        agent_name="Aworld",
        non_interactive=True,
    )

    assert result["response"] == "partial model output"
    assert result["success"] is False
    assert result["completed"] is False
    assert result["termination_status"] == "cancelled"


@pytest.mark.asyncio
async def test_run_iteration_preserves_control_plane_when_inline_trajectory_is_empty() -> None:
    async def fake_chat(prompt: str, **kwargs):
        return "Task fail, cause: provider_timeout"

    build_result = SimpleNamespace(
        to_dict=lambda: {
            "status": "partial",
            "fidelity": "partial",
            "llm_call_count": 4,
            "tool_call_count": 1,
            "completed_updates": 2,
            "persisted_items": 0,
            "source_high_watermark": "event-8",
        }
    )
    fake_executor = SimpleNamespace(
        chat=fake_chat,
        session_id="sess-1",
        last_task_response=SimpleNamespace(
            success=False,
            status="failed",
            trajectory=[],
            llm_calls=[{"request_id": f"request-{index}"} for index in range(4)],
            trajectory_build_result=build_result,
            trajectory_delivery_receipt=None,
        ),
    )
    continuous = ContinuousExecutor(
        fake_executor,
        console=Console(file=StringIO(), force_terminal=False),
    )

    result = await continuous.run_iteration(1, "hello", agent_name="Aworld")

    assert result["trajectory_capture_mode"] == "task_response"
    assert result["trajectory"] == []
    assert len(result["llm_calls"]) == 4
    assert result["trajectory_build_result"]["source_high_watermark"] == "event-8"


@pytest.mark.asyncio
async def test_run_iteration_executor_exception_keeps_fresh_infrastructure_origin() -> None:
    stale_task_response = SimpleNamespace(
        success=False,
        status="failed",
        failure_origin="task",
        failure_code="completion_contract_unsatisfied",
        error_type="CompletionContractError",
        trajectory=[{"role": "assistant", "content": "partial"}],
        llm_calls=[{"request_id": "call-1"}],
        trajectory_build_result={"status": "partial"},
        trajectory_delivery_receipt={"status": "persisted"},
    )

    class RaisingExecutor:
        last_task_response = stale_task_response
        last_task_interrupted = False

        async def chat(self, *_args, **_kwargs):
            raise RuntimeError("framework cleanup failed")

    continuous = ContinuousExecutor(
        RaisingExecutor(),
        console=Console(file=StringIO(), force_terminal=False),
    )
    result = await continuous.run_iteration(1, "hello", agent_name="Aworld")

    assert result["failure_origin"] == "infrastructure"
    assert result["failure_code"] == "executor_exception"
    assert result["error_type"] == "RuntimeError"
    assert result["trajectory"] == [{"role": "assistant", "content": "partial"}]
    assert result["llm_calls"] == [{"request_id": "call-1"}]
    assert result["trajectory_build_result"] == {"status": "partial"}
    assert result["trajectory_delivery_receipt"] == {"status": "persisted"}
    assert "task_status" not in result


@pytest.mark.asyncio
async def test_run_continuous_stops_on_terminal_task_response_at_any_iteration() -> None:
    class FakeExecutor:
        session_id = "sess-terminal"
        last_task_response = None

        def __init__(self) -> None:
            self.calls = 0

        async def chat(self, prompt: str, **kwargs):
            self.calls += 1
            terminal = self.calls == 2
            self.last_task_response = SimpleNamespace(
                trajectory=[
                    {
                        "action": {
                            "content": "final answer" if terminal else "working",
                            "is_agent_finished": terminal,
                            "tool_calls": [] if terminal else [{"id": "call-1"}],
                        }
                    },
                    {
                        "action": {
                            "content": "None",
                            "is_agent_finished": False,
                            "tool_calls": [],
                        }
                    },
                ]
            )
            return "final answer" if terminal else "working"

    fake_executor = FakeExecutor()
    continuous = ContinuousExecutor(
        fake_executor,
        console=Console(file=StringIO(), force_terminal=False),
    )

    summary = await continuous.run_continuous(
        prompt="do the task",
        agent_name="Aworld",
        max_runs=10,
        show_start_banner=False,
        show_iteration_header=False,
    )

    assert fake_executor.calls == 2
    assert summary["total_runs"] == 2
    assert summary["results"][-1]["completed"] is True
    assert summary["results"][-1]["immediate_stop"] is True


@pytest.mark.asyncio
async def test_repeated_runtime_failures_never_count_as_completion() -> None:
    class FakeExecutor:
        session_id = "sess-failure"
        last_task_response = None

        def __init__(self) -> None:
            self.calls = 0

        async def chat(self, prompt: str, **kwargs):
            self.calls += 1
            self.last_task_response = SimpleNamespace(
                trajectory=[
                    {
                        "action": {
                            "content": "None",
                            "is_agent_finished": False,
                            "tool_calls": [],
                        }
                    }
                ]
            )
            return (
                "Task fail, cause: Failed to call LLM model: "
                "rate limit exceeded. Response: None"
            )

    fake_executor = FakeExecutor()
    continuous = ContinuousExecutor(
        fake_executor,
        console=Console(file=StringIO(), force_terminal=False),
    )

    summary = await continuous.run_continuous(
        prompt="do the task",
        agent_name="Aworld",
        max_runs=4,
        show_start_banner=False,
        show_iteration_header=False,
    )

    assert fake_executor.calls == 4
    assert summary["total_runs"] == 4
    assert all(result["completed"] is False for result in summary["results"])
