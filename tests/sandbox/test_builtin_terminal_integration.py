import asyncio
from pathlib import Path
import shlex
import sys

import pytest

from aworld.mcp_client.utils import process_mcp_tools
from aworld.sandbox import Sandbox


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("reuse", [False, True])
async def test_builtin_terminal_discovers_and_executes_in_workspace(
    tmp_path: Path,
    reuse: bool,
) -> None:
    sandbox = Sandbox(
        builtin_tools=["terminal"],
        workspaces=[str(tmp_path)],
        reuse=reuse,
    )
    try:
        tools = await asyncio.wait_for(
            sandbox.mcpservers.list_tools(),
            timeout=30,
        )
        schemas = {item["function"]["name"]: item["function"] for item in tools}

        assert "terminal__run_code" in schemas
        assert "code" in schemas["terminal__run_code"]["parameters"]["properties"]
        assert "cwd" in schemas["terminal__run_code"]["parameters"]["properties"]
        assert "env" in schemas["terminal__run_code"]["parameters"]["properties"]
        assert "terminal__read_output_artifact" in schemas
        processed_tools, tool_mapping = await process_mcp_tools(tools)
        assert "run_code" in {item["function"]["name"] for item in processed_tools}
        assert tool_mapping["run_code"] == "terminal__run_code"

        result = await asyncio.wait_for(
            sandbox.terminal.run_code(
                'python -c "from pathlib import Path; print(Path.cwd())"'
            ),
            timeout=30,
        )

        payload = result["data"]
        assert result["success"] is True
        assert payload["success"] is True
        assert Path(payload["metadata"]["working_directory"]).resolve() == (
            tmp_path.resolve()
        )
        assert payload["metadata"]["output_data"] is None
        assert str(tmp_path.resolve()) in payload["message"]["stdout"]
        assert payload["metadata"]["timeout_seconds"] == 300
    finally:
        await sandbox.cleanup()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_builtin_terminal_artifact_survives_non_reuse_stdio_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWORLD_TERMINAL_CAPTURE_MAX_BYTES", "2048")
    monkeypatch.setenv("AWORLD_TERMINAL_ARTIFACT_MAX_BYTES", "200000")
    original = "BEGIN" + ("x" * 100_000) + "END"
    sandbox = Sandbox(
        builtin_tools=["terminal"],
        workspaces=[str(tmp_path)],
        reuse=False,
    )
    try:
        result = await asyncio.wait_for(
            sandbox.terminal.run_code(
                f"{shlex.quote(sys.executable)} -c "
                + shlex.quote("print('BEGIN' + ('x' * 100000) + 'END', end='')"),
            ),
            timeout=30,
        )
        policy = result["data"]["metadata"]["output_policy"]["stdout"]
        artifact = await asyncio.wait_for(
            sandbox.terminal.read_output_artifact(
                policy["artifact_ref"],
                offset=0,
                limit=len(original),
            ),
            timeout=30,
        )

        assert artifact["data"]["content"] == original
        assert artifact["data"]["complete"] is True
        assert artifact["data"]["content_sha256"] == policy["content_sha256"]
    finally:
        await sandbox.cleanup()
