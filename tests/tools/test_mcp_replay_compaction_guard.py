import pytest

from aworld.core.common import ActionModel
from aworld.tools.mcp_tool.executor import MCPToolExecutor


class _UnexpectedCallServer:
    def __init__(self):
        self.called = False

    async def call_tool(self, action_name, params):
        self.called = True
        raise AssertionError(f"unexpected MCP call: {action_name} {params}")


@pytest.mark.asyncio
async def test_mcp_executor_blocks_compacted_replay_arguments_before_server_call():
    executor = MCPToolExecutor()
    executor.initialized = True
    server = _UnexpectedCallServer()
    executor.mcp_servers = {"terminal": {"instance": server}}

    results, _ = await executor.async_execute_action(
        [
            ActionModel(
                tool_name="terminal",
                action_name="mcp_execute_command",
                params={
                    "_aworld_replay": "compacted_tool_call_arguments",
                    "argument_schema": "object{command:string}",
                    "sanitized_reason": "oversized_replay_compaction",
                    "tool_name": "bash",
                },
            )
        ]
    )

    assert server.called is False
    assert len(results) == 1
    assert "compacted for replay" in results[0].content
    assert "regenerate the full tool call arguments" in results[0].content
