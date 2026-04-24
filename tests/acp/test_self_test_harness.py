from __future__ import annotations

import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_cli.acp.self_test_bridge import SELF_TEST_TEXT_PROMPT
from aworld_cli.acp.stdio_harness import AcpStdioHarness, local_server_env


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_local_server_env_includes_repo_pythonpath_and_overrides() -> None:
    env = local_server_env(extra_env={"AWORLD_ACP_SELF_TEST_BRIDGE": "1"})

    assert str(REPO_ROOT / "aworld-cli" / "src") in env["PYTHONPATH"]
    assert str(REPO_ROOT) in env["PYTHONPATH"]
    assert env["AWORLD_ACP_SELF_TEST_BRIDGE"] == "1"


@pytest.mark.asyncio
async def test_acp_stdio_harness_can_launch_local_server_and_initialize() -> None:
    harness = AcpStdioHarness.for_local_server()

    async with harness:
        await harness.send_request(1, "initialize", {})
        response = await harness.read_response(1)

    assert response["id"] == 1
    assert response["result"]["serverInfo"]["name"] == "aworld-cli"
    assert harness.stdout_lines


@pytest.mark.asyncio
async def test_acp_stdio_harness_buffers_notifications_while_waiting_for_response() -> None:
    harness = AcpStdioHarness.for_local_server(extra_env={"AWORLD_ACP_SELF_TEST_BRIDGE": "1"})

    async with harness:
        await harness.send_request(1, "initialize", {})
        _ = await harness.read_response(1)

        await harness.send_request(2, "newSession", {"cwd": ".", "mcpServers": []})
        new_session = await harness.read_response(2)
        session_id = new_session["result"]["sessionId"]

        await harness.send(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "prompt",
                "params": {
                    "sessionId": session_id,
                    "prompt": {"content": [{"type": "text", "text": SELF_TEST_TEXT_PROMPT}]},
                },
            }
        )
        response = await harness.read_response(3)
        notification = await harness.read_notification("sessionUpdate")

    assert response["result"]["status"] == "completed"
    assert notification["method"] == "sessionUpdate"
    assert notification["params"]["update"]["content"]["text"] == "self-test"
