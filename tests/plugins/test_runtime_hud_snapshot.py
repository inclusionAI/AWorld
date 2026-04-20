import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld_cli.executors.local import LocalAgentExecutor
from aworld_cli.executors.stats import StreamTokenStats
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
