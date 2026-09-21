from pathlib import Path

from aworld_cli.builtin_agents.smllc.agents.sandbox_factory import (
    create_agent_sandbox,
)


def test_create_agent_sandbox_uses_packaged_terminal_provider(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AWORLD_SANDBOX_REUSE", "false")

    sandbox = create_agent_sandbox(["terminal"])
    terminal_config = sandbox.mcp_config["mcpServers"]["terminal"]

    assert sandbox.reuse is False
    assert sandbox.mcp_servers == ["terminal"]
    assert terminal_config["command"] == "${PYTHON_CMD}"
    assert Path(terminal_config["args"][0]).name == "terminal.py"
    assert terminal_config["args"][-1] == "--stdio"
    assert terminal_config["cwd"] == str(tmp_path)
    assert "examples.gaia" not in repr(terminal_config)
    assert terminal_config["env"]["AWORLD_WORKSPACE"] == str(tmp_path)
