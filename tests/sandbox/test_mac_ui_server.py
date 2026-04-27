import json
import subprocess
import sys
from pathlib import Path

from aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.main import (
    permissions,
    run_action,
    normalize_see_result,
    resolve_click_target,
    validate_action_params,
)
from aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.errors import MacUIError


def test_resolve_click_target_prefers_target_id_over_coordinates():
    target = resolve_click_target({"target_id": "B1", "x": 5, "y": 6})
    assert target == {"target_id": "B1"}


def test_resolve_click_target_falls_back_to_coordinates():
    target = resolve_click_target({"x": 5, "y": 6})
    assert target == {"x": 5, "y": 6}


def test_see_result_includes_required_target_fields():
    result = normalize_see_result(
        {
            "targets": [{"target_id": "B1", "role": "button", "text": "Play"}],
            "visible_text": "Episode summary",
        }
    )
    assert "targets" in result
    assert isinstance(result["targets"], list)
    assert result["targets"][0]["target_id"] == "B1"
    assert result["targets"][0]["role"] == "button"
    assert result["targets"][0]["text"] == "Play"


def test_interaction_actions_accept_timeout_override():
    validate_action_params("click", {"target_id": "B1", "timeout_seconds": 10.0})
    validate_action_params("type", {"text": "hello", "timeout_seconds": 10.0})
    validate_action_params("press", {"keys": "enter", "timeout_seconds": 10.0})
    validate_action_params("scroll", {"direction": "down", "amount": 2, "timeout_seconds": 10.0})


def test_timeout_seconds_has_upper_bound():
    try:
        validate_action_params("click", {"target_id": "B1", "timeout_seconds": 301.0})
    except ValueError as exc:
        assert "300" in str(exc)
    else:
        raise AssertionError("timeout_seconds should be capped")


def test_focus_window_requires_window_selector():
    try:
        validate_action_params("focus_window", {"app": "Xiaoyuzhou"})
    except ValueError as exc:
        assert "window" in str(exc)
    else:
        raise AssertionError("focus_window should require window_id or window_title")


def test_non_interaction_actions_accept_minimal_phase1_inputs():
    validate_action_params("permissions", {})
    validate_action_params("list_apps", {})
    validate_action_params("launch_app", {"app": "Xiaoyuzhou"})
    validate_action_params("see", {"app": "Xiaoyuzhou", "include_artifact": True})


def test_list_windows_requires_app_selector():
    try:
        validate_action_params("list_windows", {})
    except ValueError as exc:
        assert "app" in str(exc)
    else:
        raise AssertionError("list_windows should require app for the peekaboo backend")


def test_focus_window_window_title_requires_app_scope():
    try:
        validate_action_params("focus_window", {"window_title": "Inbox"})
    except ValueError as exc:
        assert "app" in str(exc)
    else:
        raise AssertionError("focus_window with window_title should require app scope")


def test_focus_window_accepts_window_id_without_app():
    validate_action_params("focus_window", {"window_id": "12345"})


def test_run_action_reports_capability_disabled(monkeypatch):
    monkeypatch.setattr(
        "aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.main.gate_enabled",
        lambda: False,
    )
    monkeypatch.setattr(
        "aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.main.is_macos_host",
        lambda: True,
    )
    monkeypatch.setattr(
        "aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.main.detect_backend_availability",
        lambda: True,
    )

    try:
        run_action("permissions", {})
    except MacUIError as exc:
        assert exc.code == "CAPABILITY_DISABLED"
    else:
        raise AssertionError("run_action should fail when capability is disabled")


def test_run_action_reports_unsupported_platform(monkeypatch):
    monkeypatch.setattr(
        "aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.main.gate_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.main.is_macos_host",
        lambda: False,
    )
    monkeypatch.setattr(
        "aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.main.detect_backend_availability",
        lambda: True,
    )

    try:
        run_action("permissions", {})
    except MacUIError as exc:
        assert exc.code == "UNSUPPORTED_PLATFORM"
    else:
        raise AssertionError("run_action should fail when host is not macOS")


def test_run_action_reports_missing_backend(monkeypatch):
    monkeypatch.setattr(
        "aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.main.gate_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.main.is_macos_host",
        lambda: True,
    )
    monkeypatch.setattr(
        "aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.main.detect_backend_availability",
        lambda: False,
    )

    try:
        run_action("permissions", {})
    except MacUIError as exc:
        assert exc.code == "BACKEND_NOT_AVAILABLE"
    else:
        raise AssertionError("run_action should fail when peekaboo backend is unavailable")


def test_run_action_reports_missing_permissions(monkeypatch):
    monkeypatch.setattr(
        "aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.main.gate_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.main.is_macos_host",
        lambda: True,
    )
    monkeypatch.setattr(
        "aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.main.detect_backend_availability",
        lambda: True,
    )

    def fake_execute(action, params):
        if action == "permissions":
            return {
                "permissions": [
                    {"name": "Accessibility", "status": "granted"},
                    {"name": "Screen Recording", "status": "denied"},
                ]
            }
        raise AssertionError("action execution should not continue when permissions are missing")

    monkeypatch.setattr(
        "aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.main.execute_peekaboo_action",
        fake_execute,
    )

    try:
        run_action("click", {"target_id": "B1"})
    except MacUIError as exc:
        assert exc.code == "PERMISSION_MISSING"
    else:
        raise AssertionError("run_action should fail when required permissions are missing")


def test_run_action_ignores_non_required_permissions(monkeypatch):
    monkeypatch.setattr(
        "aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.main._permission_preflight_cache",
        {"checked_at": 0.0, "missing": None},
    )
    monkeypatch.setattr(
        "aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.main.gate_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.main.is_macos_host",
        lambda: True,
    )
    monkeypatch.setattr(
        "aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.main.detect_backend_availability",
        lambda: True,
    )

    def fake_execute(action, params):
        if action == "permissions":
            return {
                "permissions": [
                    {"name": "Accessibility", "status": "granted"},
                    {"name": "Screen Recording", "status": "granted"},
                    {"name": "Full Disk Access", "status": "denied"},
                ]
            }
        if action == "click":
            return {"clicked": True}
        raise AssertionError(f"unexpected action: {action}")

    monkeypatch.setattr(
        "aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.main.execute_peekaboo_action",
        fake_execute,
    )

    result = run_action("click", {"target_id": "B1"})
    assert result["clicked"] is True


def test_run_action_caches_permission_preflight(monkeypatch):
    monkeypatch.setattr(
        "aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.main._permission_preflight_cache",
        {"checked_at": 0.0, "missing": None},
    )
    monkeypatch.setattr(
        "aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.main.gate_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.main.is_macos_host",
        lambda: True,
    )
    monkeypatch.setattr(
        "aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.main.detect_backend_availability",
        lambda: True,
    )
    monkeypatch.setattr(
        "aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.main.time.monotonic",
        lambda: 100.0,
    )

    calls = {"permissions": 0, "click": 0}

    def fake_execute(action, params):
        calls[action] += 1
        if action == "permissions":
            return {
                "permissions": [
                    {"name": "Accessibility", "status": "granted"},
                    {"name": "Screen Recording", "status": "granted"},
                ]
            }
        if action == "click":
            return {"clicked": True}
        raise AssertionError(f"unexpected action: {action}")

    monkeypatch.setattr(
        "aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.main.execute_peekaboo_action",
        fake_execute,
    )

    run_action("click", {"target_id": "B1"})
    run_action("click", {"target_id": "B1"})

    assert calls["permissions"] == 1
    assert calls["click"] == 2


def test_mac_ui_server_module_can_be_executed_by_path_without_relative_import_failure():
    server_main = Path(
        "/Users/wuman/Documents/workspace/aworld/.worktrees/aworld-host-local-mac-ui-automation/"
        "aworld/sandbox/tool_servers/platforms/mac/ui_automation/src/main.py"
    )
    command = [
        sys.executable,
        "-c",
        (
            "import runpy; "
            f"runpy.run_path({server_main.as_posix()!r}, run_name='aworld_mac_ui_server_test')"
        ),
    ]

    result = subprocess.run(command, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr or result.stdout


async def test_permissions_tool_offloads_run_action_with_to_thread(monkeypatch):
    calls = {}

    async def fake_to_thread(func, *args, **kwargs):
        calls["func"] = func
        calls["args"] = args
        return {"permissions": []}

    monkeypatch.setattr(
        "aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.main.asyncio.to_thread",
        fake_to_thread,
    )

    response = await permissions(None)
    payload = json.loads(response.text)

    assert calls["func"].__name__ == "run_action"
    assert calls["args"] == ("permissions", {})
    assert payload["ok"] is True
