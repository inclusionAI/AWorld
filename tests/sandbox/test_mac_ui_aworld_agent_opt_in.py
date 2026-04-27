from aworld_cli.builtin_agents.smllc.agents.mac_ui_automation import (
    MAC_UI_AUTOMATION_SERVER_NAME,
    augment_aworld_agent_builtin_tools,
    augment_aworld_agent_mcp_servers,
    resolve_mac_ui_automation_backend,
    should_enable_mac_ui_automation,
)
from aworld.sandbox.config.manager import ToolConfigManager


def test_mac_ui_automation_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("AWORLD_ENABLE_MAC_UI_AUTOMATION", raising=False)
    assert should_enable_mac_ui_automation() is False


def test_mac_ui_automation_defaults_to_peekaboo_cli(monkeypatch):
    monkeypatch.delenv("AWORLD_MAC_UI_AUTOMATION_BACKEND", raising=False)
    assert resolve_mac_ui_automation_backend() == "peekaboo_cli"


def test_mac_ui_automation_opt_in_augments_aworld_agent(monkeypatch):
    monkeypatch.setenv("AWORLD_ENABLE_MAC_UI_AUTOMATION", "1")
    builtin_tools = augment_aworld_agent_builtin_tools(["filesystem", "terminal"])
    mcp_servers = augment_aworld_agent_mcp_servers(["terminal"])

    assert builtin_tools == ["filesystem", "terminal", MAC_UI_AUTOMATION_SERVER_NAME]
    assert mcp_servers == ["terminal", MAC_UI_AUTOMATION_SERVER_NAME]


def test_tool_config_manager_exposes_mac_ui_automation_builtin():
    config = ToolConfigManager(mode="local").get_mcp_config([MAC_UI_AUTOMATION_SERVER_NAME])
    server = config["mcpServers"][MAC_UI_AUTOMATION_SERVER_NAME]

    assert server["type"] == "stdio"
    assert server["args"][-1] == "--stdio"
    assert "platforms/mac/ui_automation/src/main.py" in server["args"][0]


def test_tool_config_manager_forwards_mac_ui_gate_env(monkeypatch):
    monkeypatch.setenv("AWORLD_ENABLE_MAC_UI_AUTOMATION", "1")
    monkeypatch.setenv("AWORLD_MAC_UI_AUTOMATION_BACKEND", "peekaboo_cli")
    config = ToolConfigManager(mode="local").get_mcp_config([MAC_UI_AUTOMATION_SERVER_NAME])
    server_env = config["mcpServers"][MAC_UI_AUTOMATION_SERVER_NAME]["env"]

    assert server_env["AWORLD_ENABLE_MAC_UI_AUTOMATION"] == "1"
    assert server_env["AWORLD_MAC_UI_AUTOMATION_BACKEND"] == "peekaboo_cli"
