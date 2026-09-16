import asyncio
from pathlib import Path

import pytest
from mcp.types import CallToolResult, TextContent

from aworld.mcp_client import utils


def test_iter_obsidian_vault_candidates_returns_parent_directories(tmp_path: Path):
    docs_root = tmp_path / "Documents"
    vault_a = docs_root / "VaultA"
    vault_b = docs_root / "Notes" / "ResearchVault"
    (vault_a / ".obsidian").mkdir(parents=True)
    (vault_b / ".obsidian").mkdir(parents=True)

    candidates = utils._iter_obsidian_vault_candidates((docs_root,))

    assert str(vault_a) in candidates
    assert str(vault_b) in candidates


def test_augment_tool_description_adds_obsidian_vault_hints(monkeypatch):
    monkeypatch.setattr(
        utils,
        "get_obsidian_vault_candidates",
        lambda: ["/Users/test/Documents/wuman_knowledge"],
    )

    augmented = utils._augment_tool_description(
        "terminal",
        "mcp_execute_command",
        "Execute terminal commands safely.",
    )

    assert "Detected Obsidian vaults" in augmented
    assert "/Users/test/Documents/wuman_knowledge" in augmented
    assert "save notes to Obsidian" in augmented


def test_augment_tool_description_leaves_non_terminal_tools_unchanged(monkeypatch):
    monkeypatch.setattr(
        utils,
        "get_obsidian_vault_candidates",
        lambda: ["/Users/test/Documents/wuman_knowledge"],
    )

    original = "Read file content."
    assert utils._augment_tool_description("filesystem", "read_file", original) == original


def test_augment_tool_description_supports_builtin_terminal_tool(monkeypatch):
    monkeypatch.setattr(
        utils,
        "get_obsidian_vault_candidates",
        lambda: ["/Users/test/Documents/wuman_knowledge"],
    )

    augmented = utils._augment_tool_description(
        "terminal",
        "run_code",
        "Execute terminal commands safely.",
    )

    assert "Detected Obsidian vaults" in augmented


@pytest.mark.asyncio
async def test_non_reuse_stdio_discovery_resolves_python_placeholder(monkeypatch):
    captured = {}

    class _FakeStdioServer:
        def __init__(self, *, name, params):
            captured["name"] = name
            captured["params"] = params

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_value, traceback):
            return None

    async def _fake_run(**kwargs):
        return []

    monkeypatch.setenv("AWORLD_PYTHON_EXECUTABLE", "/opt/aworld/python")
    monkeypatch.setattr(utils, "MCPServerStdio", _FakeStdioServer)
    monkeypatch.setattr(utils, "run", _fake_run)

    await utils.mcp_tool_desc_transform_v2(
        tools=["terminal"],
        mcp_config={
            "mcpServers": {
                "terminal": {
                    "command": "${PYTHON_CMD}",
                    "args": ["terminal.py", "--stdio"],
                }
            }
        },
    )

    assert captured["name"] == "terminal"
    assert captured["params"]["command"] == "/opt/aworld/python"


def test_stdio_server_environment_inherits_only_opted_in_prefixes(monkeypatch):
    monkeypatch.setenv(
        "AWORLD_MCP_STDIO_INHERIT_ENV_PREFIXES",
        "AWORLD_REPLAY_,TRACE_CONTEXT_",
    )
    monkeypatch.setenv("AWORLD_REPLAY_ENDPOINT_BROWSER", "http://127.0.0.1:54321")
    monkeypatch.setenv("TRACE_CONTEXT_RUN_ID", "run-123")
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-leak")

    environment = utils._stdio_server_environment(
        {"env": {"EXPLICIT_SETTING": "enabled"}}
    )

    assert environment == {
        "AWORLD_REPLAY_ENDPOINT_BROWSER": "http://127.0.0.1:54321",
        "EXPLICIT_SETTING": "enabled",
        "TRACE_CONTEXT_RUN_ID": "run-123",
    }


def test_stdio_server_environment_preserves_legacy_explicit_only_behavior(monkeypatch):
    monkeypatch.delenv("AWORLD_MCP_STDIO_INHERIT_ENV_PREFIXES", raising=False)
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-leak")

    assert utils._stdio_server_environment({"env": {"ONLY": "this"}}) == {
        "ONLY": "this"
    }


def test_lower_mcp_call_result_preserves_protocol_error_and_structured_content():
    result = utils.lower_mcp_call_result(
        CallToolResult(
            content=[TextContent(type="text", text="validation failed")],
            structuredContent={"code": "INVALID_INPUT", "retryable": False},
            isError=True,
        ),
        server_name="files",
        tool_name="write",
        parameter={"path": "/tmp/out"},
    )

    assert result.success is False
    assert result.error == "validation failed"
    assert result.content == "validation failed"
    assert result.metadata == {
        "structured_content": {"code": "INVALID_INPUT", "retryable": False}
    }
    assert result.tool_name == "files"
    assert result.action_name == "write"
    assert result.parameter == {"path": "/tmp/out"}


def test_lower_mcp_call_result_uses_structured_content_when_blocks_are_empty():
    result = utils.lower_mcp_call_result(
        CallToolResult(content=[], structuredContent={"rows": 0}),
        server_name="db",
        tool_name="query",
    )

    assert result.success is True
    assert result.error is None
    assert result.content == {"rows": 0}
    assert result.metadata == {"structured_content": {"rows": 0}}


def test_lower_mcp_call_result_handles_completely_empty_success():
    result = utils.lower_mcp_call_result(
        CallToolResult(content=[]),
        server_name="noop",
        tool_name="run",
    )

    assert result.success is True
    assert result.content == ""
    assert result.metadata == {}


def test_lower_mcp_call_result_preserves_every_content_block_in_order():
    result = utils.lower_mcp_call_result(
        CallToolResult(
            content=[
                TextContent(type="text", text="first"),
                TextContent(type="text", text="second"),
            ]
        ),
        server_name="demo",
        tool_name="multi",
    )

    assert result.success is True
    assert result.content == ["first", "second"]


def test_mcp_tool_retry_safe_requires_explicit_tool_configuration():
    config = {
        "mcpServers": {
            "catalog": {"retry_safe_tools": ["read"]},
            "legacy": {},
        }
    }

    assert utils.mcp_tool_retry_safe(config, "catalog", "read") is True
    assert utils.mcp_tool_retry_safe(config, "catalog", "write") is False
    assert utils.mcp_tool_retry_safe(config, "legacy", "read") is False
    assert utils.mcp_tool_retry_safe(None, "catalog", "read") is False
    assert utils.mcp_tool_retry_safe({"mcpServers": []}, "catalog", "read") is False


class _FakeMcpServer:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.cleanup_calls = 0

    async def call_tool(self, **kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def cleanup(self):
        self.cleanup_calls += 1


@pytest.mark.asyncio
async def test_exit_stack_call_propagates_cancellation_without_retry(monkeypatch):
    server = _FakeMcpServer([asyncio.CancelledError()])

    async def _server_instance(**kwargs):
        return server, None

    monkeypatch.setattr(utils, "get_server_instance", _server_instance)

    with pytest.raises(asyncio.CancelledError):
        await utils.call_mcp_tool_with_exit_stack(
            server_name="terminal",
            tool_name="run",
            parameter={},
            mcp_config={"mcpServers": {"terminal": {}}},
            max_retry=3,
        )

    assert server.calls == 1
    assert server.cleanup_calls == 1


@pytest.mark.asyncio
async def test_exit_stack_call_does_not_replay_unsafe_tool_after_exception(monkeypatch):
    server = _FakeMcpServer([RuntimeError("connection lost after dispatch")])

    async def _server_instance(**kwargs):
        return server, None

    monkeypatch.setattr(utils, "get_server_instance", _server_instance)

    result = await utils.call_mcp_tool_with_exit_stack(
        server_name="payments",
        tool_name="create",
        parameter={"amount": 10},
        mcp_config={"mcpServers": {"payments": {}}},
        max_retry=3,
    )

    assert server.calls == 1
    assert result.isError is True
    assert "connection lost after dispatch" in result.content[0].text


@pytest.mark.asyncio
async def test_exit_stack_call_retries_only_with_explicit_safe_opt_in(monkeypatch):
    servers = [
        _FakeMcpServer([RuntimeError("temporary")]),
        _FakeMcpServer([CallToolResult(content=[])]),
    ]

    async def _server_instance(**kwargs):
        return servers.pop(0), None

    monkeypatch.setattr(utils, "get_server_instance", _server_instance)

    result = await utils.call_mcp_tool_with_exit_stack(
        server_name="catalog",
        tool_name="read",
        parameter={},
        mcp_config={"mcpServers": {"catalog": {}}},
        max_retry=3,
        retry_safe=True,
    )

    assert result.isError is False
    assert not servers


@pytest.mark.asyncio
async def test_reuse_call_propagates_cancellation_without_retry():
    server = _FakeMcpServer([asyncio.CancelledError()])
    server_instances = {"terminal": server}

    with pytest.raises(asyncio.CancelledError):
        await utils.call_mcp_tool_with_reuse(
            server_name="terminal",
            tool_name="run",
            parameter={},
            server_instances=server_instances,
            mcp_config={"mcpServers": {"terminal": {}}},
            max_retry=3,
        )

    assert server.calls == 1
    assert server.cleanup_calls == 1
    assert "terminal" not in server_instances


@pytest.mark.asyncio
async def test_reuse_call_does_not_replay_unsafe_tool_after_exception():
    server = _FakeMcpServer([RuntimeError("outcome unknown")])
    server_instances = {"payments": server}

    result = await utils.call_mcp_tool_with_reuse(
        server_name="payments",
        tool_name="create",
        parameter={},
        server_instances=server_instances,
        mcp_config={"mcpServers": {"payments": {}}},
        max_retry=3,
    )

    assert server.calls == 1
    assert server.cleanup_calls == 1
    assert "payments" not in server_instances
    assert result.isError is True
    assert "outcome unknown" in result.content[0].text


@pytest.mark.asyncio
async def test_reuse_call_reconnects_before_retrying_explicitly_safe_tool(
    monkeypatch,
):
    failed = _FakeMcpServer([RuntimeError("connection lost")])
    recovered = _FakeMcpServer([CallToolResult(content=[])])
    server_instances = {"catalog": failed}

    async def _server_instance(**kwargs):
        return recovered, None

    monkeypatch.setattr(utils, "get_server_instance", _server_instance)

    result = await utils.call_mcp_tool_with_reuse(
        server_name="catalog",
        tool_name="read",
        parameter={},
        server_instances=server_instances,
        mcp_config={"mcpServers": {"catalog": {}}},
        max_retry=3,
        retry_safe=True,
    )

    assert result.isError is False
    assert failed.calls == 1
    assert failed.cleanup_calls == 1
    assert recovered.calls == 1
    assert server_instances["catalog"] is recovered
