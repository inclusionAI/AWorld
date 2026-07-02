import os
import tempfile

import pytest

from aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.backends.peekaboo_cli import (
    execute_peekaboo_action,
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


def test_execute_peekaboo_action_cleans_failed_artifact(monkeypatch):
    artifact_path = os.path.join(tempfile.gettempdir(), "aworld-mac-ui-test-artifact.png")
    if os.path.exists(artifact_path):
        os.remove(artifact_path)

    monkeypatch.setattr(
        "aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.backends.peekaboo_cli._build_artifact_path",
        lambda: artifact_path,
    )

    class FakeCompletedProcess:
        def __init__(self):
            self.returncode = 2
            self.stderr = "peekaboo failed"
            self.stdout = ""

    def fake_run(*args, **kwargs):
        with open(artifact_path, "w", encoding="utf-8") as handle:
            handle.write("artifact")
        return FakeCompletedProcess()

    monkeypatch.setattr(
        "aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.backends.peekaboo_cli.subprocess.run",
        fake_run,
    )

    with pytest.raises(MacUIError):
        execute_peekaboo_action("see", {"include_artifact": True})

    assert os.path.exists(artifact_path) is False
