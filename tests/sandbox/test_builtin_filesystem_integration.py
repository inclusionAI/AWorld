import asyncio
from pathlib import Path

import pytest

from aworld.sandbox import Sandbox


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("reuse", [False, True])
async def test_builtin_filesystem_discovers_and_writes_only_in_workspace(
    tmp_path: Path,
    reuse: bool,
) -> None:
    sandbox = Sandbox(
        builtin_tools=["filesystem"],
        workspaces=[str(tmp_path)],
        reuse=reuse,
    )
    target = tmp_path / "boundary-canary.txt"
    try:
        tools = await asyncio.wait_for(
            sandbox.mcpservers.list_tools(),
            timeout=30,
        )
        tool_names = {item["function"]["name"] for item in tools}
        assert "filesystem__write_file" in tool_names
        assert "filesystem__read_file" in tool_names

        write_result = await asyncio.wait_for(
            sandbox.file.write_file(str(target), "workspace-boundary-ok"),
            timeout=30,
        )
        assert write_result["success"] is True
        assert target.read_text(encoding="utf-8") == "workspace-boundary-ok"

        read_result = await asyncio.wait_for(
            sandbox.file.read_file(str(target)),
            timeout=30,
        )
        assert read_result["success"] is True
        assert "workspace-boundary-ok" in str(read_result["data"])
    finally:
        await sandbox.cleanup()
