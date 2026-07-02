from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_cli.acp.plugin_runtime import AcpPluginRuntime


def test_plugin_runtime_without_plugin_roots_is_empty(tmp_path: Path) -> None:
    runtime = AcpPluginRuntime(
        workspace_path=str(tmp_path),
        plugin_roots=[],
    )

    assert runtime.active_plugin_capabilities() == ()
    assert runtime.get_plugin_hooks("task_started") == []
    assert runtime.get_hud_snapshot() == {}


def test_plugin_runtime_hud_snapshot_round_trip(tmp_path: Path) -> None:
    runtime = AcpPluginRuntime(
        workspace_path=str(tmp_path),
        plugin_roots=[],
    )

    updated = runtime.update_hud_snapshot(
        session={"session_id": "session-1"},
        task={"status": "running"},
    )
    settled = runtime.settle_hud_snapshot(task_status="idle")

    assert updated["session"]["session_id"] == "session-1"
    assert updated["task"]["status"] == "running"
    assert settled["task"]["status"] == "idle"
    assert runtime.get_hud_snapshot()["task"]["status"] == "idle"


def test_plugin_runtime_build_plugin_hook_state_prefers_executor_context(tmp_path: Path) -> None:
    runtime = AcpPluginRuntime(
        workspace_path=str(tmp_path / "runtime"),
        plugin_roots=[],
    )

    context = type(
        "Context",
        (),
        {
            "workspace_path": str(tmp_path / "context"),
            "session_id": "context-session",
            "task_id": "task-1",
        },
    )()
    executor = type(
        "Executor",
        (),
        {
            "context": context,
            "session_id": "executor-session",
        },
    )()

    state = runtime.build_plugin_hook_state(
        "plugin.demo",
        "workspace",
        executor,
    )

    assert state["workspace_path"] == str(tmp_path / "context")
    assert state["session_id"] == "executor-session"
    assert state["task_id"] == "task-1"


async def test_plugin_runtime_run_plugin_hooks_is_fail_soft(tmp_path: Path) -> None:
    runtime = AcpPluginRuntime(
        workspace_path=str(tmp_path),
        plugin_roots=[],
    )

    class FailingHook:
        plugin_id = "plugin.fail"
        scope = "workspace"
        priority = 10
        entrypoint_id = "fail-hook"

        async def run(self, event, state):
            raise RuntimeError("boom")

    class PassingHook:
        plugin_id = "plugin.ok"
        scope = "workspace"
        priority = 20
        entrypoint_id = "ok-hook"

        async def run(self, event, state):
            return {"event": event, "state": state}

    runtime._plugin_hooks = {"task_started": (FailingHook(), PassingHook())}

    results = await runtime.run_plugin_hooks(
        "TASK_STARTED",
        event={"task_id": "task-1"},
        executor_instance=None,
    )

    assert len(results) == 1
    hook, payload = results[0]
    assert hook.entrypoint_id == "ok-hook"
    assert payload["event"]["task_id"] == "task-1"
    assert payload["state"]["workspace_path"] == str(tmp_path)
