from aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.backends.peekaboo_cli import (
    build_peekaboo_command,
    normalize_backend_failure,
    parse_peekaboo_output,
)
from aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.errors import MacUIError


def test_build_peekaboo_command_for_launch_app():
    cmd = build_peekaboo_command("launch_app", {"app": "Xiaoyuzhou"})
    assert cmd[:3] == ["peekaboo", "app", "launch"]
    assert cmd[-1] == "Xiaoyuzhou"


def test_parse_peekaboo_see_output_normalizes_targets():
    stdout = '{"targets":[{"id":"B1","role":"button","text":"Play"}],"text":"visible"}'
    result = parse_peekaboo_output("see", stdout)
    assert result["targets"][0]["target_id"] == "B1"
    assert result["targets"][0]["role"] == "button"
    assert result["targets"][0]["text"] == "Play"
    assert result["visible_text"] == "visible"


def test_backend_execution_failure_is_normalized():
    error = normalize_backend_failure(
        action="launch_app",
        returncode=2,
        stderr="peekaboo failed",
    )
    assert isinstance(error, MacUIError)
    assert error.code == "BACKEND_EXECUTION_FAILED"
