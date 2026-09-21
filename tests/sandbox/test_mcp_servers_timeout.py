from types import SimpleNamespace

import pytest
from mcp.types import CallToolResult, Tool

from aworld.mcp_client import utils as mcp_utils
from aworld.sandbox.run import mcp_servers as mcp_servers_module
from aworld.sandbox.run.mcp_servers import McpServers


_MISSING = object()


@pytest.fixture(params=[False, True], ids=["exit-stack", "reuse"])
def invoke_tool(request, monkeypatch):
    reuse = request.param

    async def invoke(
        parameter,
        schema_default=_MISSING,
        *,
        declared_tool="run_code",
        expect_success=True,
    ):
        calls = []
        scheduled = []

        class Server:
            def __init__(self, name, params=None):
                self.name = name

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def list_tools(self):
                timeout_schema = {"type": "number"}
                if schema_default is not _MISSING:
                    timeout_schema["default"] = schema_default
                return [
                    Tool(
                        name="different_tool",
                        inputSchema={
                            "type": "object",
                            "properties": {"timeout": {"type": "number", "default": 999}},
                        },
                    ),
                    Tool(
                        name=declared_tool,
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "code": {"type": "string"},
                                "timeout": (
                                    timeout_schema if self.name == "terminal" else
                                    {"type": "number", "default": 888}
                                ),
                            },
                        },
                    ),
                ]

        async def get_server(server_name, **kwargs):
            return Server(server_name), None

        class InlineManager:
            async def run_on_sandbox(self, sandbox_id, function, *args, server_name=None):
                scheduled.append(server_name)
                return await function(*args)

        async def call(**kwargs):
            calls.append(kwargs)
            return CallToolResult(content=[])

        async def unexpected_call(**kwargs):
            raise AssertionError("wrong MCP connection mode")

        monkeypatch.setattr(mcp_utils, "MCPServerStdio", Server)
        monkeypatch.setattr(mcp_servers_module, "get_server_instance", get_server)
        monkeypatch.setattr(
            mcp_servers_module.SandboxManager, "get_instance", lambda: InlineManager()
        )
        monkeypatch.setattr(
            mcp_servers_module, "call_mcp_tool_with_reuse", call if reuse else unexpected_call
        )
        monkeypatch.setattr(
            mcp_servers_module, "call_mcp_tool_with_exit_stack", unexpected_call if reuse else call
        )
        servers = McpServers(
            mcp_servers=["other", "terminal"],
            mcp_config={
                "mcpServers": {
                    name: {"type": "stdio", "command": "unused"}
                    for name in ["other", "terminal"]
                }
            },
            sandbox=SimpleNamespace(
                sandbox_id="timeout-test", reuse=reuse, env_content_name=None, mode="local"
            ),
        )
        original_keys = set(parameter)
        results = await servers.call_tool(
            action_list=[
                {"tool_name": "terminal", "action_name": "run_code", "params": parameter}
            ]
        )
        assert len(results) == 1
        if not expect_success:
            assert results[0].success is False
            assert calls == []
            return results[0]
        assert results[0].success
        assert len(calls) == 1
        assert calls[0]["parameter"] is parameter
        assert set(parameter) == original_keys  # Transport defaults are never tool arguments.
        assert bool(scheduled) is reuse
        return calls[0]["timeout"]

    return invoke


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "explicit,default,expected",
    [
        (450, 300, 460),
        (170.5, 300, 180.5),
        (30, 300, 120),
        (_MISSING, 300, 310),
        (_MISSING, 170.5, 180.5),
        (_MISSING, 30, 120),
        (_MISSING, _MISSING, 310),
    ],
)
async def test_call_tool_timeout_uses_explicit_value_then_discovered_schema(
    invoke_tool, explicit, default, expected
):
    parameter = {"code": "long_running_parse()"}
    if explicit is not _MISSING:
        parameter["timeout"] = explicit
    assert await invoke_tool(parameter, default) == expected
    if explicit is not _MISSING:
        assert parameter["timeout"] == explicit


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [True, False, None, -1, 0, float("nan"), float("inf"), -float("inf"), 10**1000])
@pytest.mark.parametrize("source", ["explicit", "schema"])
async def test_call_tool_invalid_timeout_falls_back_without_rewriting_arguments(
    invoke_tool, value, source
):
    parameter = {"code": "run()"}
    default = value if source == "schema" else 300
    if source == "explicit":
        parameter["timeout"] = value
        result = await invoke_tool(
            parameter,
            default,
            expect_success=False,
        )
        assert "timeout" in result.content
        assert parameter["timeout"] is value
    else:
        assert await invoke_tool(parameter, default) == 310


@pytest.mark.asyncio
async def test_call_tool_does_not_borrow_another_tools_timeout(invoke_tool):
    assert await invoke_tool({}, 300, declared_tool="other_run") == 310
