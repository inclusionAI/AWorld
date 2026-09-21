import pytest
from mcp.types import CallToolResult, TextContent

from aworld.sandbox.run.mcp_servers import (
    McpServers,
    _build_tool_call_failure_result,
    _coalesce_tool_result_content,
)
from aworld.sandbox.errors import SandboxInfrastructureError


def test_coalesce_tool_result_content_returns_plain_string_for_single_item():
    assert _coalesce_tool_result_content(["only line"]) == "only line"


def test_coalesce_tool_result_content_preserves_multiple_items():
    assert _coalesce_tool_result_content(["line one", "line two"]) == ["line one", "line two"]


def test_coalesce_tool_result_content_returns_empty_string_for_no_items():
    assert _coalesce_tool_result_content([]) == ""


def test_build_tool_call_failure_result_includes_error_context_and_parameter_summary():
    result = _build_tool_call_failure_result(
        server_name="terminal",
        tool_name="mcp_execute_command",
        parameter={"command": "python script.py", "timeout": 30},
        error=RuntimeError("boom"),
    )

    assert result.tool_name == "terminal"
    assert result.action_name == "mcp_execute_command"
    assert "terminal__mcp_execute_command" in result.content
    assert "RuntimeError: boom" in result.content
    assert "command=python script.py" in result.content
    assert "timeout=30" in result.content


def test_build_tool_call_failure_result_preserves_typed_infrastructure_error():
    result = _build_tool_call_failure_result(
        server_name="docker",
        tool_name="run_code",
        parameter={},
        error=SandboxInfrastructureError(
            "docker_checkpoint_create_failed", "backend unavailable"
        ),
    )

    assert result.metadata == {
        "failure_category": "infrastructure",
        "failure_code": "docker_checkpoint_create_failed",
    }


def _terminal_tool(tool_name: str, param_name: str) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": f"terminal__{tool_name}",
            "parameters": {
                "type": "object",
                "properties": {
                    param_name: {
                        "type": "string",
                    }
                },
            },
        },
    }


class _FailingSyncSandbox:
    def __init__(self) -> None:
        self.mode = "remote"
        self.sandbox_id = None
        self.reuse = False
        self.env_content_name = None

    async def ensure_skill_execution_assets_ready(
        self,
        skill_name: str,
        skill_config: dict[str, object],
    ) -> str:
        raise RuntimeError(f"sync failed for {skill_name}")


@pytest.mark.asyncio
async def test_call_tool_surfaces_remote_sync_failure_before_terminal_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed = {"called": False}

    async def _unexpected_call(**kwargs):
        executed["called"] = True
        return None

    monkeypatch.setattr(
        "aworld.sandbox.run.mcp_servers.call_mcp_tool_with_exit_stack",
        _unexpected_call,
    )

    servers = McpServers(
        mcp_servers=["terminal"],
        mcp_config={"mcpServers": {"terminal": {}}},
        sandbox=_FailingSyncSandbox(),
        skill_configs={
            "browser-use": {
                "asset_root": "/host/skills/browser-use",
                "execution_assets": {
                    "enabled": True,
                    "relative_paths": ["scripts/run.py"],
                    "digest": "feed1234feed1234",
                },
            }
        },
    )
    servers.tool_list = [_terminal_tool("run_code", "code")]

    results = await servers.call_tool(
        action_list=[
            {
                "tool_name": "terminal",
                "action_name": "run_code",
                "params": {"code": "python /skills/browser-use/scripts/run.py"},
            }
        ],
        context=None,
    )

    assert executed["called"] is False
    assert results is not None
    assert len(results) == 1
    assert "sync failed for browser-use" in results[0].content


@pytest.mark.asyncio
async def test_call_tool_returns_one_typed_result_per_action(monkeypatch):
    async def _fake_call(**kwargs):
        return CallToolResult(
            content=[TextContent(type="text", text="tool rejected input")],
            structuredContent={"reason": "invalid"},
            isError=True,
        )

    monkeypatch.setattr(
        "aworld.sandbox.run.mcp_servers.call_mcp_tool_with_exit_stack",
        _fake_call,
    )
    servers = McpServers(
        mcp_servers=["demo"],
        mcp_config={"mcpServers": {"demo": {}}},
    )
    servers.tool_list = [
        {
            "type": "function",
            "function": {
                "name": "demo__run",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    results = await servers.call_tool(
        action_list=[
            {"tool_name": "demo", "action_name": "run", "params": {}},
            {"tool_name": "demo", "params": {}},
        ]
    )

    assert len(results) == 2
    assert results[0].success is False
    assert results[0].error == "tool rejected input"
    assert results[0].metadata == {"structured_content": {"reason": "invalid"}}
    assert results[1].success is False
    assert "Missing action_name" in results[1].content


@pytest.mark.asyncio
async def test_call_tool_handles_empty_mcp_content_without_dropping_result(monkeypatch):
    async def _fake_call(**kwargs):
        return CallToolResult(content=[])

    monkeypatch.setattr(
        "aworld.sandbox.run.mcp_servers.call_mcp_tool_with_exit_stack",
        _fake_call,
    )
    servers = McpServers(
        mcp_servers=["demo"],
        mcp_config={"mcpServers": {"demo": {}}},
    )
    servers.tool_list = [{}]

    results = await servers.call_tool(
        action_list=[{"tool_name": "demo", "action_name": "noop", "params": {}}]
    )

    assert len(results) == 1
    assert results[0].success is True
    assert results[0].content == ""
