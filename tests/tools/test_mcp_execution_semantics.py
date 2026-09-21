import pytest
from mcp.types import CallToolResult, TextContent

from aworld.core.common import ActionModel
from aworld.tools.mcp_tool.executor import MCPToolExecutor


class _ProtocolErrorServer:
    async def call_tool(self, action_name, params):
        return CallToolResult(
            content=[TextContent(type="text", text="permission denied")],
            structuredContent={"permission": "write"},
            isError=True,
        )


@pytest.mark.asyncio
async def test_executor_preserves_mcp_protocol_error_semantics():
    executor = MCPToolExecutor.__new__(MCPToolExecutor)
    executor.initialized = True
    executor.mcp_servers = {"files": {"instance": _ProtocolErrorServer()}}

    results, _ = await executor.async_execute_action(
        [
            ActionModel(
                tool_name="files",
                action_name="write",
                params={"path": "/tmp/out"},
            )
        ]
    )

    assert len(results) == 1
    assert results[0].success is False
    assert results[0].error == "permission denied"
    assert results[0].metadata == {
        "structured_content": {"permission": "write"}
    }
    assert results[0].tool_name == "files"
    assert results[0].action_name == "write"
