import os

MAC_UI_AUTOMATION_SERVER_NAME = "mac_ui_automation"
DEFAULT_MAC_UI_AUTOMATION_BACKEND = "peekaboo_cli"
SUPPORTED_MAC_UI_AUTOMATION_BACKENDS = {DEFAULT_MAC_UI_AUTOMATION_BACKEND}


def should_enable_mac_ui_automation() -> bool:
    value = os.getenv("AWORLD_ENABLE_MAC_UI_AUTOMATION", "")
    return value.strip().lower() in {"1", "true", "yes"}


def resolve_mac_ui_automation_backend() -> str:
    backend = os.getenv("AWORLD_MAC_UI_AUTOMATION_BACKEND", DEFAULT_MAC_UI_AUTOMATION_BACKEND).strip()
    if not backend:
        backend = DEFAULT_MAC_UI_AUTOMATION_BACKEND
    if backend not in SUPPORTED_MAC_UI_AUTOMATION_BACKENDS:
        raise ValueError(f"Unsupported mac UI automation backend: {backend}")
    return backend


def augment_aworld_agent_builtin_tools(builtin_tools: list[str]) -> list[str]:
    tools = list(builtin_tools)
    if not should_enable_mac_ui_automation():
        return tools
    resolve_mac_ui_automation_backend()
    if MAC_UI_AUTOMATION_SERVER_NAME not in tools:
        tools.append(MAC_UI_AUTOMATION_SERVER_NAME)
    return tools


def augment_aworld_agent_mcp_servers(mcp_servers: list[str]) -> list[str]:
    servers = list(mcp_servers)
    if not should_enable_mac_ui_automation():
        return servers
    resolve_mac_ui_automation_backend()
    if MAC_UI_AUTOMATION_SERVER_NAME not in servers:
        servers.append(MAC_UI_AUTOMATION_SERVER_NAME)
    return servers
