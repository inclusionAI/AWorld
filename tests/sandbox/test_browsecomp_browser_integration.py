from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from aworld.core.context.compiler import preserve_unmanaged_tool_namespaces
from aworld.mcp_client.utils import process_mcp_tools
from aworld.sandbox import DockerSandbox
from examples.sandbox.docker_terminal_bench import load_external_mcp_config


ROOT = Path(__file__).resolve().parents[2]
PLAYWRIGHT_CONFIG = (
    ROOT
    / "examples"
    / "sandbox"
    / "context_eval_tools"
    / "browsecomp-playwright-mcp.json"
)

pytestmark = [
    pytest.mark.docker_integration,
    pytest.mark.skipif(
        os.environ.get("AWORLD_RUN_BROWSECOMP_INTEGRATION") != "1",
        reason=(
            "set AWORLD_RUN_BROWSECOMP_INTEGRATION=1 to run the real Docker + "
            "Playwright BrowseComp capability gate"
        ),
    ),
]


@pytest.mark.asyncio
async def test_real_browsecomp_browser_and_docker_tool_surface(record_property):
    docker = shutil.which("docker")
    npx = shutil.which("npx")
    chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if not docker or not npx or not chrome.is_file():
        pytest.skip("Docker, npx, and Google Chrome are required")

    container = f"aworld-browsecomp-gate-{uuid.uuid4().hex[:10]}"
    image = os.environ.get("AWORLD_DOCKER_TEST_IMAGE", "alpine:3.20")
    started = subprocess.run(
        [
            docker,
            "run",
            "-d",
            "--rm",
            "--name",
            container,
            "-w",
            "/workspace",
            image,
            "sleep",
            "infinity",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if started.returncode != 0:
        pytest.skip(f"unable to start {image}: {started.stderr.strip()}")

    sandbox = None
    try:
        config, evidence = load_external_mcp_config(PLAYWRIGHT_CONFIG)
        sandbox = DockerSandbox(
            container=container,
            mcp_config=config,
            reuse=True,
        )
        tools = await sandbox.list_tools()
        names = {tool["function"]["name"] for tool in tools}
        assert "docker__run_code" in names
        assert "ms-playwright__browser_navigate" in names
        assert "ms-playwright__browser_snapshot" in names
        processed, identity_mapping = await process_mcp_tools(tools)
        available_ids = tuple(
            tool["function"]["name"] for tool in processed
        )
        unmanaged = preserve_unmanaged_tool_namespaces(
            available_ids,
            requested_tools=("run_code", "read_file", "write_file"),
            tool_identity_mapping=identity_mapping,
        )
        assert "browser_navigate" in unmanaged
        assert "browser_snapshot" in unmanaged

        results = await sandbox.call_tool(
            action_list=[
                {
                    "tool_name": "ms-playwright",
                    "action_name": "browser_navigate",
                    "params": {"url": "https://example.com"},
                }
            ]
        )
        assert len(results) == 1
        assert results[0].success is True
        assert "Example Domain" in str(results[0])

        record_property("external_mcp_config_sha256", evidence["config_sha256"])
        record_property("tool_count", len(names))
        record_property("browser_navigation", "example.com")
    finally:
        if sandbox is not None:
            await sandbox.cleanup()
        subprocess.run(
            [docker, "rm", "-f", container], capture_output=True, check=False
        )
