from __future__ import annotations

import base64
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult, ImageContent

from aworld.mcp_client.utils import lower_mcp_call_result, process_mcp_tools
from aworld.sandbox import Sandbox
from aworld.sandbox.artifact_observation import (
    ArtifactObservationError,
    artifact_mcp_content,
    bind_artifact_sidecar,
    clear_artifact_observation_state,
    hydrate_artifact_reference,
    observe_artifact_bytes,
)
from aworld.sandbox.tool_servers.terminal.src import terminal as terminal_module
from aworld.sandbox.run.mcp_servers import McpServers
from aworld.sandbox.tool_observation import classify_tool_effect


_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "/x8AAusB9Y9Z4E8AAAAASUVORK5CYII="
)
_FILE_EPOCH = "sha256:" + ("0" * 64)


def _observed(scope: dict[str, object] | None = None):
    return observe_artifact_bytes(
        _PNG_1X1,
        suffix=".png",
        path_key="sha256:path",
        file_epoch=_FILE_EPOCH,
        framework_scope=scope or {},
    )


@pytest.fixture(autouse=True)
def _reset_sidecars() -> None:
    clear_artifact_observation_state()


@pytest.mark.asyncio
async def test_terminal_tool_returns_typed_receipt_and_image_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(terminal_module, "workspace", tmp_path)
    image = tmp_path / "pixel.png"
    image.write_bytes(_PNG_1X1)

    result = await terminal_module.observe_artifact(
        None,
        "pixel.png",
        env_content={"tool_call_id": "call-1"},
    )

    receipt = json.loads(result[0].text)
    assert receipt["schema_version"] == "aworld.artifact-observation/v1"
    assert receipt["mime_type"] == "image/png"
    assert result[1].mimeType == "image/png"
    assert base64.b64decode(result[1].data) == _PNG_1X1


def test_mcp_lowering_keeps_bytes_out_of_action_result_and_binds_scope() -> None:
    scope = {
        "task_id": "task-1",
        "session_id": "session-1",
        "task_epoch": "resume:branch/a",
        "tool_call_id": "call-1",
    }
    result = lower_mcp_call_result(
        CallToolResult(content=artifact_mcp_content(_observed(scope))),
        server_name="terminal",
        tool_name="observe_artifact",
    )
    encoded = base64.b64encode(_PNG_1X1).decode("ascii")
    serialized = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
    reference = result.metadata["artifact_observation_ref"]

    assert encoded not in serialized
    assert "data:image" not in serialized
    assert bind_artifact_sidecar(
        reference,
        task_id="task-1",
        session_id="session-1",
        task_epoch="resume:branch/a",
        tool_call_id="call-1",
    )
    assert (
        hydrate_artifact_reference(
            reference,
            task_id="task-1",
            session_id="session-1",
            task_epoch="resume:branch/a",
            tool_call_id="call-1",
        )
        == f"data:image/png;base64,{encoded}"
    )
    assert not bind_artifact_sidecar(
        reference,
        task_id="task-2",
        session_id="session-1",
        task_epoch="resume:branch/a",
        tool_call_id="call-1",
    )

    replay = lower_mcp_call_result(
        CallToolResult(
            content=artifact_mcp_content(_observed({**scope, "tool_call_id": "call-2"}))
        ),
        server_name="terminal",
        tool_name="observe_artifact",
    )
    assert replay.metadata["artifact_observation_ref"] == reference
    assert replay.metadata["artifact_observation"]["cache_state"] == "retained"
    assert bind_artifact_sidecar(
        reference,
        task_id="task-1",
        session_id="session-1",
        task_epoch="resume:branch/a",
        tool_call_id="call-2",
    )


def test_mcp_lowering_rejects_mime_mismatch_without_payload_leak() -> None:
    blocks = artifact_mcp_content(_observed({"tool_call_id": "call-1"}))
    blocks[1] = ImageContent(type="image", data=blocks[1].data, mimeType="image/jpeg")

    result = lower_mcp_call_result(
        CallToolResult(content=blocks),
        server_name="terminal",
        tool_name="observe_artifact",
    )

    assert result.success is False
    assert result.error == "artifact_observation_invalid"
    assert base64.b64encode(_PNG_1X1).decode("ascii") not in json.dumps(
        result.model_dump(mode="json"), ensure_ascii=False
    )


def test_hidden_artifact_scope_includes_authoritative_tool_call_id() -> None:
    sandbox = SimpleNamespace(env_content={"caller_note": "ok"})
    servers = McpServers(sandbox=sandbox)
    servers._env_content_param_mapping = {"terminal__observe_artifact": "env_content"}
    parameter = {}

    servers._inject_env_content_parameter(
        "terminal__observe_artifact",
        parameter,
        SimpleNamespace(
            task_id="task-1",
            session_id="session-1",
            task_epoch="resume:branch/a",
        ),
        tool_call_id="call-1",
    )

    expected = {
        "caller_note": "ok",
        "task_id": "task-1",
        "session_id": "session-1",
        "task_epoch": "resume:branch/a",
        "tool_call_id": "call-1",
    }
    assert {key: parameter["env_content"][key] for key in expected} == expected
    assert parameter["env_content"]["task_budget"]["authority"] == "aworld_task"


def test_artifact_observation_is_terminal_read_only_but_provider_cache_owned() -> None:
    effect = classify_tool_effect(
        {
            "tool_name": "terminal",
            "action_name": "observe_artifact",
            "params": {"path": "frame.png"},
        }
    )

    assert effect.effect == "read_only"
    assert effect.cacheable is False


@pytest.mark.asyncio
async def test_docker_tool_uses_same_terminal_artifact_contract(monkeypatch) -> None:
    monkeypatch.setenv("AWORLD_DOCKER_CONTAINER", "context-eval")
    monkeypatch.setenv("AWORLD_DOCKER_BINARY", "/usr/bin/docker")
    monkeypatch.setenv("AWORLD_DOCKER_WORKDIR", "/workspace")
    monkeypatch.setenv("AWORLD_DOCKER_ALLOWED_DIRECTORIES", '["/workspace"]')
    server = importlib.import_module("aworld.sandbox.tool_servers.docker.src.server")

    class _Bridge:
        @staticmethod
        async def observe_artifact(path, *, expected_mime, framework_scope):
            assert path == "/workspace/pixel.png"
            return _observed(framework_scope)

    monkeypatch.setattr(server, "bridge", _Bridge())
    result = await server.observe_artifact(
        None,
        "/workspace/pixel.png",
        expected_mime="image/png",
        env_content={"tool_call_id": "call-1"},
    )
    assert json.loads(result[0].text)["schema_version"] == (
        "aworld.artifact-observation/v1"
    )
    assert base64.b64decode(result[1].data) == _PNG_1X1


@pytest.mark.asyncio
async def test_docker_bridge_fails_closed_for_symlink_and_unstable_read(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AWORLD_DOCKER_CONTAINER", "context-eval")
    monkeypatch.setenv("AWORLD_DOCKER_BINARY", "/usr/bin/docker")
    monkeypatch.setenv("AWORLD_DOCKER_WORKDIR", "/workspace")
    monkeypatch.setenv("AWORLD_DOCKER_ALLOWED_DIRECTORIES", '["/workspace"]')
    server = importlib.import_module("aworld.sandbox.tool_servers.docker.src.server")
    bridge = server.DockerBridge()
    bridge.execute = AsyncMock(return_value=(42, b"", b"", False))
    with pytest.raises(ArtifactObservationError, match="symlink"):
        await bridge.observe_artifact("/workspace/link.png")

    bridge.execute = AsyncMock(return_value=(0, b"", b"", False))
    stable_stat = f"81a4|{len(_PNG_1X1)}|12|100\n".encode()
    bridge.require_success = AsyncMock(
        side_effect=[
            b"/workspace/pixel.png\n",
            stable_stat,
            _PNG_1X1[:-1],
            stable_stat,
        ]
    )
    with pytest.raises(ArtifactObservationError, match="changed"):
        await bridge.observe_artifact("/workspace/pixel.png")


@pytest.mark.asyncio
async def test_builtin_terminal_stdio_exposes_observe_artifact(tmp_path: Path) -> None:
    image = tmp_path / "pixel.png"
    image.write_bytes(_PNG_1X1)
    sandbox = Sandbox(
        builtin_tools=["terminal"], workspaces=[str(tmp_path)], reuse=False
    )
    try:
        tools = await sandbox.mcpservers.list_tools()
        schemas = {item["function"]["name"] for item in tools}
        assert "terminal__observe_artifact" in schemas
        processed, mapping = await process_mcp_tools(tools)
        assert "observe_artifact" in {item["function"]["name"] for item in processed}
        assert mapping["observe_artifact"] == "terminal__observe_artifact"
        result = await sandbox.terminal.observe_artifact("pixel.png")
        assert result["success"] is True
        assert result["data"]["mime_type"] == "image/png"
    finally:
        await sandbox.cleanup()
