from types import SimpleNamespace

import pytest

from aworld.sandbox.run import mcp_servers as mcp_servers_module
from aworld.sandbox.run.mcp_servers import McpServers


@pytest.mark.asyncio
async def test_empty_tool_list_reconnects_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed_server = object()
    healthy_server = object()
    pending_servers = [failed_server, healthy_server]
    cleaned_servers: list[object] = []

    async def fake_get_server_instance(*args, **kwargs):
        return pending_servers.pop(0), None

    async def fake_mcp_run(*, mcp_servers, **kwargs):
        if mcp_servers == [failed_server]:
            return []
        return [{"type": "function", "function": {"name": "bash"}}]

    async def fake_cleanup_server(server):
        cleaned_servers.append(server)

    monkeypatch.setattr(
        mcp_servers_module,
        "get_server_instance",
        fake_get_server_instance,
    )
    monkeypatch.setattr(mcp_servers_module, "mcp_run", fake_mcp_run)
    monkeypatch.setattr(
        mcp_servers_module,
        "cleanup_server",
        fake_cleanup_server,
    )

    servers = McpServers(
        mcp_servers=["terminal"],
        mcp_config={"mcpServers": {"terminal": {"command": "unused"}}},
    )
    servers.sandbox = SimpleNamespace(sandbox_id="sandbox")

    tools = await servers._connect_and_get_tools_one_server("terminal")

    assert tools == [{"type": "function", "function": {"name": "bash"}}]
    assert cleaned_servers == [failed_server]
    assert servers.server_instances["terminal"] is healthy_server
