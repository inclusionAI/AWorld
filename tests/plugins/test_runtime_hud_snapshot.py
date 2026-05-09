import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld_cli.executors.local import LocalAgentExecutor
from aworld_cli.executors.stats import StreamTokenStats, build_llm_usage_observability
from aworld_cli.executors.base_executor import BaseAgentExecutor
from aworld_cli.runtime.base import BaseCliRuntime


class DummyRuntime(BaseCliRuntime):
    def __init__(self):
        super().__init__(agent_name="Aworld")
        self.plugin_dirs = []

    async def _load_agents(self):
        return []

    async def _create_executor(self, agent):
        return None

    def _get_source_type(self):
        return "TEST"

    def _get_source_location(self):
        return "test://runtime"


def test_build_hud_context_merges_live_snapshot():
    runtime = DummyRuntime()

    runtime.update_hud_snapshot(
        session={"session_id": "session-1", "model": "gpt-5"},
        task={"current_task_id": "task_001", "status": "running"},
        activity={"recent_tools": ["bash"], "tool_calls_count": 1},
        usage={"input_tokens": 1200, "output_tokens": 80, "context_percent": 34},
    )

    context = runtime.build_hud_context(
        agent_name="Aworld",
        mode="Chat",
        workspace_name="aworld",
        git_branch="feat/hud",
    )

    assert context["session"]["agent"] == "Aworld"
    assert context["session"]["session_id"] == "session-1"
    assert context["session"]["model"] == "gpt-5"
    assert context["task"]["current_task_id"] == "task_001"
    assert context["activity"]["recent_tools"] == ["bash"]
    assert context["usage"]["context_percent"] == 34


def test_settle_hud_snapshot_keeps_last_useful_state():
    runtime = DummyRuntime()

    runtime.update_hud_snapshot(
        session={"elapsed_seconds": 16.8},
        task={"current_task_id": "task_001", "status": "running"},
        activity={"current_tool": "bash", "recent_tools": ["bash"], "tool_calls_count": 1},
        usage={"input_tokens": 1200, "output_tokens": 300, "context_used": 60000, "context_max": 200000, "context_percent": 30},
    )

    runtime.settle_hud_snapshot(task_status="idle")
    context = runtime.build_hud_context(
        agent_name="Aworld",
        mode="Chat",
        workspace_name="aworld",
        git_branch="main",
    )

    assert context["task"]["status"] == "idle"
    assert context["task"]["current_task_id"] == "task_001"
    assert context["activity"]["current_tool"] is None
    assert context["activity"]["recent_tools"] == ["bash"]
    assert context["session"]["elapsed_seconds"] == 16.8
    assert context["usage"]["context_percent"] == 30


def test_stream_token_stats_exports_hud_usage_snapshot():
    stats = StreamTokenStats()
    stats.update(
        agent_id="agent-1",
        agent_name="Aworld",
        output_tokens=300,
        input_tokens=1200,
        tool_calls_count=2,
        model_name="gpt-4o",
    )

    usage = stats.to_hud_usage()

    assert usage["input_tokens"] == 1200
    assert usage["output_tokens"] == 300
    assert usage["total_tokens"] == 1500
    assert usage["context_used"] == 1500


def test_local_executor_publishes_stream_updates_to_runtime():
    runtime = DummyRuntime()
    executor = object.__new__(LocalAgentExecutor)
    executor._base_runtime = runtime
    executor.session_id = "session-1"

    stats = StreamTokenStats()
    stats.update(
        agent_id="agent-1",
        agent_name="Aworld",
        output_tokens=300,
        input_tokens=1200,
        tool_calls_count=2,
        model_name="gpt-4o",
    )

    executor._publish_hud_stream_update(
        task_id="task_001",
        stream_token_stats=stats,
        current_tool="bash",
        elapsed_seconds=12.5,
    )

    context = runtime.build_hud_context(
        agent_name="Aworld",
        mode="Chat",
        workspace_name="aworld",
        git_branch="main",
    )

    assert context["task"]["current_task_id"] == "task_001"
    assert context["task"]["status"] == "running"
    assert context["activity"]["current_tool"] == "bash"
    assert context["activity"]["tool_calls_count"] == 2
    assert context["usage"]["total_tokens"] == 1500
    assert context["session"]["elapsed_seconds"] == 12.5


def test_build_llm_usage_observability_preserves_request_linked_cache_usage():
    usage = build_llm_usage_observability(
        [
            {
                "task_id": "task_001",
                "request_id": "llm_req_123",
                "provider_request_id": "req_provider_123",
                "model": "gpt-4.1",
                "usage_normalized": {
                    "prompt_tokens": 100,
                    "completion_tokens": 25,
                    "total_tokens": 125,
                },
                "usage_raw": {
                    "prompt_tokens": 100,
                    "completion_tokens": 25,
                    "total_tokens": 125,
                    "cache_hit_tokens": 80,
                    "cache_write_tokens": 20,
                    "prompt_tokens_details": {"cached_tokens": 80},
                },
            }
        ],
        task_id="task_001",
    )

    assert usage["input_tokens"] == 100
    assert usage["output_tokens"] == 25
    assert usage["total_tokens"] == 125
    assert usage["request_id"] == "llm_req_123"
    assert usage["provider_request_id"] == "req_provider_123"
    assert usage["raw_usage"]["cache_hit_tokens"] == 80
    assert usage["cache_usage"] == {
        "cache_hit_tokens": 80,
        "cache_write_tokens": 20,
        "prompt_tokens_details": {"cached_tokens": 80},
    }


def test_build_llm_usage_observability_aggregates_matching_task_calls():
    usage = build_llm_usage_observability(
        [
            {
                "task_id": "task_001",
                "request_id": "llm_req_older",
                "provider_request_id": "req_provider_older",
                "model": "gpt-4.1",
                "usage_normalized": {
                    "prompt_tokens": 100,
                    "completion_tokens": 25,
                    "total_tokens": 125,
                },
                "usage_raw": {
                    "prompt_tokens": 100,
                    "completion_tokens": 25,
                    "total_tokens": 125,
                    "cache_hit_tokens": 80,
                    "prompt_tokens_details": {"cached_tokens": 80},
                },
            },
            {
                "task_id": "task_001",
                "request_id": "llm_req_latest",
                "provider_request_id": "req_provider_latest",
                "model": "gpt-4.1",
                "usage_normalized": {
                    "prompt_tokens": 40,
                    "completion_tokens": 10,
                    "total_tokens": 50,
                },
                "usage_raw": {
                    "prompt_tokens": 40,
                    "completion_tokens": 10,
                    "total_tokens": 50,
                    "cache_write_tokens": 20,
                    "input_tokens_details": {"cache_read_input_tokens": 5},
                },
            },
            {
                "task_id": "child-task",
                "request_id": "llm_req_child",
                "provider_request_id": "req_provider_child",
                "model": "gpt-4.1-mini",
                "usage_normalized": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
                "usage_raw": {"cache_hit_tokens": 1},
            },
        ],
        task_id="task_001",
    )

    assert usage["input_tokens"] == 140
    assert usage["output_tokens"] == 35
    assert usage["total_tokens"] == 175
    assert usage["request_id"] == "llm_req_latest"
    assert usage["provider_request_id"] == "req_provider_latest"
    assert usage["raw_usage"]["cache_hit_tokens"] == 80
    assert usage["raw_usage"]["cache_write_tokens"] == 20
    assert usage["raw_usage"]["prompt_tokens"] == 140
    assert usage["raw_usage"]["completion_tokens"] == 35
    assert usage["cache_usage"] == {
        "cache_hit_tokens": 80,
        "cache_write_tokens": 20,
        "prompt_tokens_details": {"cached_tokens": 80},
        "input_tokens_details": {"cache_read_input_tokens": 5},
    }


def test_local_executor_publishes_final_llm_usage_snapshot_to_runtime():
    runtime = DummyRuntime()
    executor = object.__new__(LocalAgentExecutor)
    executor._base_runtime = runtime
    executor.session_id = "session-1"

    executor._publish_hud_llm_observability(
        task_id="task_001",
        llm_calls=[
            {
                "task_id": "task_001",
                "request_id": "llm_req_123",
                "provider_request_id": "req_provider_123",
                "model": "gpt-4.1",
                "usage_normalized": {
                    "prompt_tokens": 100,
                    "completion_tokens": 25,
                    "total_tokens": 125,
                },
                "usage_raw": {
                    "prompt_tokens": 100,
                    "completion_tokens": 25,
                    "total_tokens": 125,
                    "cache_hit_tokens": 80,
                },
            }
        ],
    )

    context = runtime.build_hud_context(
        agent_name="Aworld",
        mode="Chat",
        workspace_name="aworld",
        git_branch="main",
    )

    assert context["task"]["current_task_id"] == "task_001"
    assert context["usage"]["request_id"] == "llm_req_123"
    assert context["usage"]["provider_request_id"] == "req_provider_123"
    assert context["usage"]["cache_usage"]["cache_hit_tokens"] == 80
    assert context["session"]["model"] == "gpt-4.1"


def test_local_executor_publishes_aggregated_final_llm_usage_to_runtime():
    runtime = DummyRuntime()
    executor = object.__new__(LocalAgentExecutor)
    executor._base_runtime = runtime
    executor.session_id = "session-1"

    executor._publish_hud_llm_observability(
        task_id="task_001",
        llm_calls=[
            {
                "task_id": "task_001",
                "request_id": "llm_req_older",
                "provider_request_id": "req_provider_older",
                "model": "gpt-4.1",
                "usage_normalized": {
                    "prompt_tokens": 100,
                    "completion_tokens": 25,
                    "total_tokens": 125,
                },
                "usage_raw": {
                    "prompt_tokens": 100,
                    "completion_tokens": 25,
                    "total_tokens": 125,
                    "cache_hit_tokens": 80,
                },
            },
            {
                "task_id": "task_001",
                "request_id": "llm_req_latest",
                "provider_request_id": "req_provider_latest",
                "model": "gpt-4.1",
                "usage_normalized": {
                    "prompt_tokens": 40,
                    "completion_tokens": 10,
                    "total_tokens": 50,
                },
                "usage_raw": {
                    "prompt_tokens": 40,
                    "completion_tokens": 10,
                    "total_tokens": 50,
                    "cache_write_tokens": 20,
                },
            },
            {
                "task_id": "child-task",
                "request_id": "llm_req_child",
                "provider_request_id": "req_provider_child",
                "model": "gpt-4.1-mini",
                "usage_normalized": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
                "usage_raw": {"cache_hit_tokens": 1},
            },
        ],
    )

    context = runtime.build_hud_context(
        agent_name="Aworld",
        mode="Chat",
        workspace_name="aworld",
        git_branch="main",
    )

    assert context["usage"]["request_id"] == "llm_req_latest"
    assert context["usage"]["provider_request_id"] == "req_provider_latest"
    assert context["usage"]["input_tokens"] == 140
    assert context["usage"]["output_tokens"] == 35
    assert context["usage"]["total_tokens"] == 175
    assert context["usage"]["cache_usage"]["cache_hit_tokens"] == 80
    assert context["usage"]["cache_usage"]["cache_write_tokens"] == 20
    assert context["session"]["model"] == "gpt-4.1"


def test_build_llm_usage_observability_ignores_other_task_calls():
    usage = build_llm_usage_observability(
        [
            {
                "task_id": "task_001",
                "request_id": "llm_req_parent",
                "provider_request_id": "req_parent",
                "model": "gpt-4.1",
                "usage_normalized": {
                    "prompt_tokens": 100,
                    "completion_tokens": 25,
                    "total_tokens": 125,
                },
                "usage_raw": {"cache_hit_tokens": 80},
            },
            {
                "task_id": "child-task",
                "request_id": "llm_req_child",
                "provider_request_id": "req_child",
                "model": "gpt-4.1-mini",
                "usage_normalized": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
                "usage_raw": {"cache_hit_tokens": 1},
            },
        ],
        task_id="task_001",
    )

    assert usage["request_id"] == "llm_req_parent"
    assert usage["provider_request_id"] == "req_parent"
    assert usage["model"] == "gpt-4.1"


def test_build_llm_usage_observability_does_not_collapse_mixed_models_into_one_label():
    usage = build_llm_usage_observability(
        [
            {
                "task_id": "task_001",
                "request_id": "llm_req_parent",
                "provider_request_id": "req_parent",
                "provider_name": "openai",
                "model": "gpt-4.1",
                "usage_normalized": {
                    "prompt_tokens": 100,
                    "completion_tokens": 25,
                    "total_tokens": 125,
                },
                "usage_raw": {"cache_hit_tokens": 80},
            },
            {
                "task_id": "task_001",
                "request_id": "llm_req_child",
                "provider_request_id": "req_child",
                "provider_name": "anthropic",
                "model": "claude-3-5-sonnet",
                "usage_normalized": {
                    "prompt_tokens": 40,
                    "completion_tokens": 10,
                    "total_tokens": 50,
                },
                "usage_raw": {"cache_write_tokens": 20},
            },
        ],
        task_id="task_001",
    )

    assert usage["input_tokens"] == 140
    assert usage["output_tokens"] == 35
    assert usage["total_tokens"] == 175
    assert "model" not in usage
    assert "request_id" not in usage
    assert "provider_request_id" not in usage
    assert usage["model_breakdown"] == {
        "anthropic:claude-3-5-sonnet": {
            "calls": 1,
            "input_tokens": 40,
            "output_tokens": 10,
            "total_tokens": 50,
            "cache_hit_tokens": 0,
            "cache_write_tokens": 20,
        },
        "openai:gpt-4.1": {
            "calls": 1,
            "input_tokens": 100,
            "output_tokens": 25,
            "total_tokens": 125,
            "cache_hit_tokens": 80,
            "cache_write_tokens": 0,
        },
    }


def test_local_executor_final_usage_ignores_merged_child_task_calls():
    runtime = DummyRuntime()
    executor = object.__new__(LocalAgentExecutor)
    executor._base_runtime = runtime
    executor.session_id = "session-1"

    executor._publish_hud_llm_observability(
        task_id="task_001",
        llm_calls=[
            {
                "task_id": "task_001",
                "request_id": "llm_req_parent",
                "provider_request_id": "req_parent",
                "model": "gpt-4.1",
                "usage_normalized": {
                    "prompt_tokens": 100,
                    "completion_tokens": 25,
                    "total_tokens": 125,
                },
                "usage_raw": {"cache_hit_tokens": 80},
            },
            {
                "task_id": "child-task",
                "request_id": "llm_req_child",
                "provider_request_id": "req_child",
                "model": "gpt-4.1-mini",
                "usage_normalized": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
                "usage_raw": {"cache_hit_tokens": 1},
            },
        ],
    )

    context = runtime.build_hud_context(
        agent_name="Aworld",
        mode="Chat",
        workspace_name="aworld",
        git_branch="main",
    )

    assert context["usage"]["request_id"] == "llm_req_parent"
    assert context["usage"]["provider_request_id"] == "req_parent"
    assert context["session"]["model"] == "gpt-4.1"


def test_local_executor_does_not_publish_single_model_for_mixed_model_usage():
    runtime = DummyRuntime()
    executor = object.__new__(LocalAgentExecutor)
    executor._base_runtime = runtime
    executor.session_id = "session-1"

    executor._publish_hud_llm_observability(
        task_id="task_001",
        llm_calls=[
            {
                "task_id": "task_001",
                "request_id": "llm_req_parent",
                "provider_request_id": "req_parent",
                "provider_name": "openai",
                "model": "gpt-4.1",
                "usage_normalized": {
                    "prompt_tokens": 100,
                    "completion_tokens": 25,
                    "total_tokens": 125,
                },
                "usage_raw": {"cache_hit_tokens": 80},
            },
            {
                "task_id": "task_001",
                "request_id": "llm_req_child",
                "provider_request_id": "req_child",
                "provider_name": "anthropic",
                "model": "claude-3-5-sonnet",
                "usage_normalized": {
                    "prompt_tokens": 40,
                    "completion_tokens": 10,
                    "total_tokens": 50,
                },
                "usage_raw": {"cache_write_tokens": 20},
            },
        ],
    )

    context = runtime.build_hud_context(
        agent_name="Aworld",
        mode="Chat",
        workspace_name="aworld",
        git_branch="main",
    )

    assert context["usage"]["input_tokens"] == 140
    assert context["usage"]["output_tokens"] == 35
    assert context["usage"]["total_tokens"] == 175
    assert "model" not in context["usage"]
    assert "request_id" not in context["usage"]
    assert "provider_request_id" not in context["usage"]
    assert context["usage"]["model_breakdown"]["openai:gpt-4.1"]["cache_hit_tokens"] == 80
    assert context["usage"]["model_breakdown"]["anthropic:claude-3-5-sonnet"]["cache_write_tokens"] == 20
    assert "model" not in context["session"]


class BrokenRuntime(BaseCliRuntime):
    def __init__(self, fail_update: bool = True, fail_settle: bool = True):
        super().__init__(agent_name="Aworld")
        self.plugin_dirs = []
        self.fail_update = fail_update
        self.fail_settle = fail_settle

    async def _load_agents(self):
        return []

    async def _create_executor(self, agent):
        return None

    def _get_source_type(self):
        return "TEST"

    def _get_source_location(self):
        return "test://runtime"

    def update_hud_snapshot(self, **sections):
        if self.fail_update:
            raise RuntimeError("boom")
        return super().update_hud_snapshot(**sections)

    def settle_hud_snapshot(self, task_status: str = "idle"):
        if self.fail_settle:
            raise RuntimeError("boom")
        return super().settle_hud_snapshot(task_status=task_status)


def test_local_executor_hud_publish_is_best_effort():
    runtime = BrokenRuntime(fail_update=True, fail_settle=True)
    executor = object.__new__(LocalAgentExecutor)
    executor._base_runtime = runtime
    executor.session_id = "session-1"

    stats = StreamTokenStats()
    stats.update(
        agent_id="agent-1",
        agent_name="Aworld",
        output_tokens=10,
        input_tokens=20,
        tool_calls_count=1,
        model_name="gpt-4o",
    )

    executor._publish_hud_task_started(type("Task", (), {"id": "task_1"})())
    executor._publish_hud_stream_update(
        task_id="task_1",
        stream_token_stats=stats,
        current_tool="bash",
        elapsed_seconds=1.0,
    )
    executor._publish_hud_task_finished(task_id="task_1", task_status="idle")


def test_stream_update_preserves_recent_tools_when_no_current_tool():
    runtime = DummyRuntime()
    runtime.update_hud_snapshot(activity={"recent_tools": ["bash"]})
    executor = object.__new__(LocalAgentExecutor)
    executor._base_runtime = runtime
    executor.session_id = "session-1"

    stats = StreamTokenStats()
    stats.update(
        agent_id="agent-1",
        agent_name="Aworld",
        output_tokens=10,
        input_tokens=20,
        tool_calls_count=0,
        model_name="gpt-4o",
    )

    executor._publish_hud_stream_update(
        task_id="task_1",
        stream_token_stats=stats,
        current_tool=None,
        elapsed_seconds=1.0,
    )

    context = runtime.build_hud_context(
        agent_name="Aworld",
        mode="Chat",
        workspace_name="aworld",
        git_branch="main",
    )
    assert context["activity"]["recent_tools"] == ["bash"]


def test_snapshot_is_safe_during_concurrent_updates():
    started = threading.Event()
    result: dict[str, object] = {}

    class SlowValue:
        def __deepcopy__(self, memo):
            started.set()
            threading.Event().wait(0.05)
            return "copied"

    runtime = DummyRuntime()
    runtime._hud_snapshot_store._snapshot = {"session": {"slow": SlowValue()}}

    def reader():
        try:
            result["value"] = runtime._hud_snapshot_store.snapshot()
        except Exception as exc:  # pragma: no cover - exercised in red phase
            result["error"] = exc

    thread = threading.Thread(target=reader)
    thread.start()

    assert started.wait(1), "snapshot deepcopy did not start in time"
    runtime._hud_snapshot_store.update(task={"status": "running"})
    thread.join()

    assert "error" not in result
    assert result["value"] == {"session": {"slow": "copied"}}


def test_task_finish_sets_task_status():
    runtime = DummyRuntime()
    executor = object.__new__(LocalAgentExecutor)
    executor._base_runtime = runtime
    executor.session_id = "session-1"

    executor._publish_hud_task_finished(task_id="task_1", task_status="idle")
    context = runtime.build_hud_context(
        agent_name="Aworld",
        mode="Chat",
        workspace_name="aworld",
        git_branch="main",
    )
    assert context["task"]["status"] == "idle"

    executor._publish_hud_task_finished(task_id="task_1", task_status="error")
    context = runtime.build_hud_context(
        agent_name="Aworld",
        mode="Chat",
        workspace_name="aworld",
        git_branch="main",
    )
    assert context["task"]["status"] == "error"


def test_new_session_resets_hud_snapshot_and_switches_session_id(monkeypatch):
    class DummyExecutor(BaseAgentExecutor):
        async def chat(self, message):
            return ""

    runtime = DummyRuntime()
    runtime.update_hud_snapshot(
        session={"session_id": "session-old", "model": "gpt-5", "elapsed_seconds": 13.1},
        task={"current_task_id": "task-old", "status": "idle"},
        activity={"current_tool": None, "recent_tools": ["bash"], "tool_calls_count": 2},
        usage={"input_tokens": 6500, "output_tokens": 125, "context_percent": 3},
    )

    monkeypatch.setattr(DummyExecutor, "_generate_session_id", lambda self: "session-new")
    executor = DummyExecutor()
    executor._base_runtime = runtime
    executor.session_id = "session-old"

    executor.new_session()

    context = runtime.build_hud_context(
        agent_name="Aworld",
        mode="Chat",
        workspace_name="aworld",
        git_branch="main",
    )

    assert context["session"]["session_id"] == "session-new"
    assert "model" not in context["session"]
    assert "elapsed_seconds" not in context["session"]
    assert context["task"]["status"] == "idle"
    assert "current_task_id" not in context["task"]
    assert context["activity"]["current_tool"] is None
    assert context["activity"]["recent_tools"] == []
    assert context["activity"]["tool_calls_count"] == 0
    assert context["usage"] == {}


def test_runtime_build_plugin_hook_state_includes_state_handle():
    runtime = DummyRuntime()
    runtime._plugin_state_store = runtime._plugin_state_store or None
    runtime._plugin_state_store = __import__("aworld_cli.plugin_capabilities.state", fromlist=["PluginStateStore"]).PluginStateStore(Path.cwd() / ".tmp-plugin-state-test")

    executor_instance = SimpleNamespace(
        session_id="session-1",
        context=SimpleNamespace(workspace_path="/tmp/workspace", task_id="task-1"),
    )

    state = runtime.build_plugin_hook_state("plugin-a", "session", executor_instance=executor_instance)

    assert "__plugin_state__" in state


@pytest.mark.asyncio
async def test_local_executor_task_hook_delegates_to_runtime():
    runtime = SimpleNamespace(run_plugin_hooks=AsyncMock(return_value=[]))
    executor = object.__new__(LocalAgentExecutor)
    executor._base_runtime = runtime
    executor.session_id = "session-1"

    await executor._run_plugin_task_hook(
        "task_started",
        {"task_id": "task-1", "session_id": "session-1"},
    )

    runtime.run_plugin_hooks.assert_awaited_once()


@pytest.mark.asyncio
async def test_local_executor_chat_sets_context_before_task_hooks():
    class StopAfterHook(Exception):
        pass

    task_context = SimpleNamespace(
        workspace_path="/tmp/workspace",
        task_id="task-1",
        session_id="session-1",
    )
    task = SimpleNamespace(id="task-1", session_id="session-1", context=task_context)

    executor = object.__new__(LocalAgentExecutor)
    executor.console = SimpleNamespace(print=lambda *args, **kwargs: None)
    executor.session_id = "session-1"
    executor._update_session_last_used = MagicMock()
    executor._build_task = AsyncMock(return_value=task)
    executor._publish_hud_task_started = MagicMock()
    executor._execute_hooks = AsyncMock(return_value=None)

    async def fail_after_assert(hook_point, event):
        assert hook_point == "task_started"
        assert executor.context is task_context
        raise StopAfterHook()

    executor._run_plugin_task_hook = fail_after_assert

    with pytest.raises(StopAfterHook):
        await executor.chat("hello")


@pytest.mark.asyncio
async def test_local_executor_task_interrupted_hook_reports_interrupted_status():
    executor = object.__new__(LocalAgentExecutor)
    executor.session_id = "session-1"
    executor._run_plugin_task_hook = AsyncMock(return_value=[])
    executor._publish_hud_task_finished = MagicMock()

    task = SimpleNamespace(id="task-1")

    await executor._handle_task_interrupted(task, answer="partial answer")

    executor._run_plugin_task_hook.assert_awaited_once_with(
        "task_interrupted",
        {
            "task_id": "task-1",
            "session_id": "session-1",
            "task_status": "interrupted",
            "partial_answer": "partial answer",
        },
    )
    executor._publish_hud_task_finished.assert_called_once_with("task-1", task_status="idle")


@pytest.mark.asyncio
async def test_local_executor_task_progress_hook_is_throttled(monkeypatch):
    executor = object.__new__(LocalAgentExecutor)
    executor.session_id = "session-1"
    executor._run_plugin_task_hook = AsyncMock(return_value=[])

    timestamps = iter((100.0, 100.5, 102.2))
    monkeypatch.setattr("aworld_cli.executors.local.monotonic", lambda: next(timestamps))

    event = {
        "task_id": "task-1",
        "session_id": "session-1",
        "current_tool": "bash",
        "elapsed_seconds": 1.0,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }

    await executor._emit_task_progress_hook(event)
    await executor._emit_task_progress_hook(event)
    await executor._emit_task_progress_hook(event)

    assert executor._run_plugin_task_hook.await_count == 2
    executor._run_plugin_task_hook.assert_any_await("task_progress", event)
