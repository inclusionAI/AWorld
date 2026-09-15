import asyncio
from pathlib import Path

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
        assert str(tmp_path.resolve()) in payload["message"]
        assert payload["metadata"]["timeout_seconds"] == 300
    finally:
        await sandbox.cleanup()
