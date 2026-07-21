import json

import pytest

import aworld.sandbox
from aworld.sandbox.run import mcp_servers as mcp_servers_module
from aworld.sandbox.run.mcp_servers import McpServers


@pytest.mark.asyncio
async def test_sandbox_mcp_servers_terminate_compacted_replay_tool_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    servers = McpServers(
        mcp_servers=["terminal"],
        mcp_config={"mcpServers": {"terminal": {"command": "unused"}}},
    )
    servers.tool_list = [{"type": "function", "function": {"name": "terminal__mcp_execute_command"}}]

    async def unexpected_call(**kwargs):
        raise AssertionError("compacted arguments must not reach the MCP server")

    monkeypatch.setattr(
        mcp_servers_module,
        "call_mcp_tool_with_exit_stack",
        unexpected_call,
    )

    results = await servers._call_tool_impl(
        action_list=[
            {
                "tool_name": "terminal",
                "action_name": "mcp_execute_command",
                "params": {
                    "command": json.dumps(
                        {
                            "_aworld_replay": "compacted_string_field",
                            "field_hint": "command",
                            "sanitized_reason": "oversized_string_field_compaction",
                        }
                    ),
                    "timeout": 15,
                },
            }
        ]
    )

    assert len(results) == 1
    assert results[0].success is False
    assert results[0].is_done is True
    assert results[0].error == "replay_compacted_argument_unavailable"
    assert "cannot be executed directly" in results[0].content
    assert results[0].metadata["failure_type"] == "replay_compacted_argument_unavailable"
