import pytest
from mcp.types import TextContent

from aworld.sandbox.namespaces.terminal import TerminalNamespace
from aworld.sandbox.run.mcp_servers import McpServers


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


class _NamespaceSandbox:
    def __init__(self) -> None:
        self.mode = "remote"
        self.sandbox_id = None
        self.reuse = False
        self.env_content_name = None
        self._mcp_config = {"mcpServers": {"terminal": {}}}
        self.mcp_config = self._mcp_config
        self._skill_configs = {
            "browser-use": {
                "asset_root": "/host/skills/browser-use",
                "execution_assets": {
                    "enabled": True,
                    "relative_paths": ["scripts/run.py"],
                    "digest": "feed1234feed1234",
                },
            }
        }
        self.mcpservers = McpServers(
            mcp_servers=["terminal"],
            mcp_config=self._mcp_config,
            sandbox=self,
            skill_configs=self._skill_configs,
        )
        self.mcpservers.tool_list = [_terminal_tool("run_code", "code")]

    async def ensure_skill_execution_assets_ready(
        self,
        skill_name: str,
        skill_config: dict[str, object],
    ) -> str:
        raise RuntimeError(f"sync failed for {skill_name}")

    async def call_tool(self, action_list=None, task_id=None, session_id=None, context=None):
        return await self.mcpservers.call_tool(
            action_list=action_list,
            task_id=task_id,
            session_id=session_id,
            context=context,
        )


@pytest.mark.asyncio
async def test_terminal_namespace_surfaces_remote_sync_failure() -> None:
    sandbox = _NamespaceSandbox()
    terminal = TerminalNamespace(sandbox)

    result = await terminal.run_code("python /skills/browser-use/scripts/run.py")

    assert result["success"] is False
    assert "sync failed for browser-use" in result["data"]
    assert result["error"] is None


class _SuccessfulNamespaceSandbox(_NamespaceSandbox):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def ensure_skill_execution_assets_ready(
        self,
        skill_name: str,
        skill_config: dict[str, object],
    ) -> str:
        self.calls.append((skill_name, skill_config))
        digest = skill_config["execution_assets"]["digest"]
        return f"/remote/workspace/.aworld/skills/{skill_name}/{digest}"


@pytest.mark.asyncio
async def test_terminal_namespace_rewrites_host_skill_paths_for_remote_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def _fake_call(**kwargs):
        captured.update(kwargs)

        class _Result:
            content = [TextContent(type="text", text="done")]

        return _Result()

    monkeypatch.setattr(
        "aworld.sandbox.run.mcp_servers.call_mcp_tool_with_exit_stack",
        _fake_call,
    )

    sandbox = _SuccessfulNamespaceSandbox()
    terminal = TerminalNamespace(sandbox)

    result = await terminal.run_code(
        "cd /host/skills/browser-use && python /host/skills/browser-use/scripts/run.py"
    )

    assert result == {"success": True, "data": "done", "error": None}
    assert captured["parameter"]["code"] == (
        "cd /remote/workspace/.aworld/skills/browser-use/feed1234feed1234"
        " && python /remote/workspace/.aworld/skills/browser-use/feed1234feed1234/scripts/run.py"
    )
    assert sandbox.calls == [("browser-use", sandbox._skill_configs["browser-use"])]


class _AliasedTerminalSandbox(_SuccessfulNamespaceSandbox):
    def __init__(self) -> None:
        super().__init__()
        self._mcp_config = {
            "mcpServers": {
                "workspace_terminal": {
                    "headers": {
                        "MCP_SERVERS": "terminal",
                    }
                }
            }
        }
        self.mcp_config = self._mcp_config
        self.mcpservers = McpServers(
            mcp_servers=["workspace_terminal"],
            mcp_config=self._mcp_config,
            sandbox=self,
            skill_configs=self._skill_configs,
        )
        self.mcpservers.tool_list = [_terminal_tool("run_code", "code")]
        self.mcpservers.tool_list[0]["function"]["name"] = "workspace_terminal__run_code"


@pytest.mark.asyncio
async def test_terminal_namespace_rewrites_remote_paths_for_aliased_terminal_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def _fake_call(**kwargs):
        captured.update(kwargs)

        class _Result:
            content = [TextContent(type="text", text="done")]

        return _Result()

    monkeypatch.setattr(
        "aworld.sandbox.run.mcp_servers.call_mcp_tool_with_exit_stack",
        _fake_call,
    )

    sandbox = _AliasedTerminalSandbox()
    terminal = TerminalNamespace(sandbox)

    result = await terminal.run_code("python /host/skills/browser-use/scripts/run.py")

    assert result == {"success": True, "data": "done", "error": None}
    assert captured["server_name"] == "workspace_terminal"
    assert captured["parameter"]["code"] == (
        "python /remote/workspace/.aworld/skills/browser-use/feed1234feed1234/scripts/run.py"
    )
