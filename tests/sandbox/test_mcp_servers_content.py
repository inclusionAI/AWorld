import pytest

from aworld.sandbox.run.mcp_servers import (
    McpServers,
    _build_tool_call_failure_result,
    _coalesce_tool_result_content,
)


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
