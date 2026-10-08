from __future__ import annotations

import base64
import importlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult, ImageContent, TextContent

import aworld.sandbox.artifact_observation as artifact_module
from aworld.mcp_client.utils import lower_mcp_call_result, process_mcp_tools
from aworld.sandbox import Sandbox
from aworld.sandbox.config.templates import (
    get_docker_script_path,
    get_server_env,
    get_terminal_script_path,
)
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
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4"
    "z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
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

    receipt = json.loads(result.content[0].text)
    assert receipt["schema_version"] == "aworld.artifact-observation/v1"
    assert receipt["mime_type"] == "image/png"
    assert result.content[1].mimeType == "image/png"
    assert base64.b64decode(result.content[1].data) == _PNG_1X1


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
        trusted_artifact_observation=True,
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
        trusted_artifact_observation=True,
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
        trusted_artifact_observation=True,
    )

    assert result.success is False
    assert result.error == "artifact_observation_contract_invalid"
    assert base64.b64encode(_PNG_1X1).decode("ascii") not in json.dumps(
        result.model_dump(mode="json"), ensure_ascii=False
    )


@pytest.mark.parametrize(
    "mutation",
    (
        "extra_text",
        "structured",
        "block_metadata",
        "annotations",
        "unknown_receipt_key",
    ),
)
def test_artifact_mcp_contract_rejects_all_extra_payload_surfaces(
    mutation: str,
) -> None:
    sentinel = "data:image/png;base64,PRIVATE-SENTINEL"
    blocks = artifact_mcp_content(_observed({"tool_call_id": "call-1"}))
    kwargs = {}
    if mutation == "extra_text":
        blocks.append(TextContent(type="text", text=sentinel))
    elif mutation == "structured":
        kwargs["structuredContent"] = {"secret": sentinel}
    elif mutation == "block_metadata":
        blocks[0] = TextContent(
            type="text",
            text=blocks[0].text,
            metadata={"secret": sentinel},
        )
    elif mutation == "annotations":
        blocks[0] = TextContent(
            type="text",
            text=blocks[0].text,
            annotations={"audience": ["assistant"]},
        )
    else:
        receipt = json.loads(blocks[0].text)
        receipt["private"] = sentinel
        blocks[0] = TextContent(type="text", text=json.dumps(receipt))

    result = lower_mcp_call_result(
        CallToolResult(content=blocks, **kwargs),
        server_name="terminal",
        tool_name="observe_artifact",
        trusted_artifact_observation=True,
    )
    serialized = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)

    assert result.success is False
    assert result.error == "artifact_observation_contract_invalid"
    assert "artifact_observation_ref" not in result.metadata
    assert sentinel not in serialized
    assert base64.b64encode(_PNG_1X1).decode("ascii") not in serialized


def test_failed_artifact_protocol_result_never_registers_or_binds_image() -> None:
    result = lower_mcp_call_result(
        CallToolResult(
            content=artifact_mcp_content(_observed({"tool_call_id": "call-1"})),
            isError=True,
        ),
        server_name="terminal",
        tool_name="observe_artifact",
        trusted_artifact_observation=True,
    )

    assert result.success is False
    assert result.error == "artifact_observation_tool_failed"
    assert result.metadata["artifact_observation"]["status"] == "failed"
    assert "artifact_observation_ref" not in result.metadata


def test_external_same_named_tool_is_not_hijacked_by_artifact_contract() -> None:
    result = lower_mcp_call_result(
        CallToolResult(content=artifact_mcp_content(_observed())),
        server_name="external-media",
        tool_name="observe_artifact",
    )

    assert result.success is True
    assert "artifact_observation" not in result.metadata
    assert isinstance(result.content, list)


def test_sidecar_call_authorizations_are_hash_only_bounded_lru() -> None:
    scope = {
        "task_id": "task-1",
        "session_id": "session-1",
        "task_epoch": 1,
    }
    reference = None
    for index in range(96):
        call_id = f"private-call-{index}"
        result = lower_mcp_call_result(
            CallToolResult(
                content=artifact_mcp_content(
                    _observed({**scope, "tool_call_id": call_id})
                )
            ),
            server_name="terminal",
            tool_name="observe_artifact",
            trusted_artifact_observation=True,
        )
        reference = result.metadata["artifact_observation_ref"]
        assert bind_artifact_sidecar(
            reference,
            **scope,
            tool_call_id=call_id,
        )

    token = reference.removeprefix(artifact_module.ARTIFACT_OBSERVATION_URI_PREFIX)
    entry = artifact_module._sidecars[token]
    assert len(entry.authorized_call_hashes) == 32
    assert len(entry.bound_call_hashes) == 32
    serialized = repr((entry.authorized_call_hashes, entry.bound_call_hashes))
    assert "private-call" not in serialized
    assert all(key.startswith("sha256:") for key in entry.authorized_call_hashes)
    assert all(key.startswith("sha256:") for key in entry.bound_call_hashes)


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


def test_companion_server_environment_never_exports_pythonpath(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONPATH", "/private/task-controlled-imports")

    assert "PYTHONPATH" not in get_server_env()


@pytest.mark.parametrize("server", ["terminal", "docker"])
def test_companion_server_bootstraps_owning_package_under_isolated_python(
    server: str,
) -> None:
    script = (
        get_terminal_script_path() if server == "terminal" else get_docker_script_path()
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONHOME"}
    }
    if server == "docker":
        environment.update(
            {
                "AWORLD_DOCKER_CONTAINER": "not-used",
                "AWORLD_DOCKER_BINARY": "/usr/bin/docker",
                "AWORLD_DOCKER_WORKDIR": "/workspace",
                "AWORLD_DOCKER_ALLOWED_DIRECTORIES": '["/workspace"]',
            }
        )
    completed = subprocess.run(
        [sys.executable, "-I", script, "--stdio"],
        input=b"",
        capture_output=True,
        env=environment,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 0
    assert b"ModuleNotFoundError" not in completed.stderr


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
    assert json.loads(result.content[0].text)["schema_version"] == (
        "aworld.artifact-observation/v1"
    )
    assert base64.b64decode(result.content[1].data) == _PNG_1X1


@pytest.mark.asyncio
async def test_docker_bridge_uses_one_atomic_reader_and_fails_closed(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AWORLD_DOCKER_CONTAINER", "context-eval")
    monkeypatch.setenv("AWORLD_DOCKER_BINARY", "/usr/bin/docker")
    monkeypatch.setenv("AWORLD_DOCKER_WORKDIR", "/workspace")
    monkeypatch.setenv("AWORLD_DOCKER_ALLOWED_DIRECTORIES", '["/workspace"]')
    server = importlib.import_module("aworld.sandbox.tool_servers.docker.src.server")
    bridge = server.DockerBridge()
    bridge.execute = AsyncMock(
        return_value=(
            73,
            b"",
            b"artifact_confinement_unproven",
            False,
        )
    )
    with pytest.raises(ArtifactObservationError, match="secure artifact confinement"):
        await bridge.observe_artifact("/workspace/link.png")
    assert bridge.execute.await_count == 1

    payload = {
        "data": base64.b64encode(_PNG_1X1).decode("ascii"),
        "device": 1,
        "inode": 2,
        "mode": 0o100644,
        "size": len(_PNG_1X1),
        "mtime_ns": 3,
        "ctime_ns": 4,
    }
    bridge.execute = AsyncMock(
        return_value=(
            0,
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
            b"",
            False,
        )
    )
    observed = await bridge.observe_artifact("/workspace/pixel.png")
    assert observed.data == _PNG_1X1
    assert bridge.execute.await_count == 1
    command = bridge.execute.await_args.args[0]
    assert command[1:4] == ["-I", "-c", server._DOCKER_ARTIFACT_READER]

    payload["size"] += 1
    bridge.execute = AsyncMock(
        return_value=(0, json.dumps(payload).encode(), b"", False)
    )
    with pytest.raises(ArtifactObservationError, match="receipt is invalid"):
        await bridge.observe_artifact("/workspace/pixel.png")


def test_docker_atomic_reader_holds_one_confined_file_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not Path("/proc/self/fd").is_dir():
        pytest.skip("Docker atomic reader requires Linux /proc fd identities")
    monkeypatch.setenv("AWORLD_DOCKER_CONTAINER", "context-eval")
    monkeypatch.setenv("AWORLD_DOCKER_BINARY", "/usr/bin/docker")
    monkeypatch.setenv("AWORLD_DOCKER_WORKDIR", "/workspace")
    monkeypatch.setenv("AWORLD_DOCKER_ALLOWED_DIRECTORIES", '["/workspace"]')
    server = importlib.import_module("aworld.sandbox.tool_servers.docker.src.server")
    workspace = tmp_path.resolve() / "workspace"
    workspace.mkdir()
    image = workspace / "pixel.png"
    image.write_bytes(_PNG_1X1)
    command = [
        sys.executable,
        "-I",
        "-c",
        server._DOCKER_ARTIFACT_READER,
        str(image),
        json.dumps([str(workspace)]),
        "1024",
    ]

    completed = subprocess.run(command, capture_output=True, check=False)

    assert completed.returncode == 0
    assert completed.stderr == b""
    payload = json.loads(completed.stdout)
    assert base64.b64decode(payload["data"]) == _PNG_1X1

    outside = tmp_path / "outside.png"
    outside.write_bytes(_PNG_1X1)
    image.unlink()
    image.symlink_to(outside)
    rejected = subprocess.run(command, capture_output=True, check=False)
    assert rejected.returncode != 0
    assert rejected.stdout == b""


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

        (tmp_path / "input.txt").write_text("stable", encoding="utf-8")
        command = (
            f"{shlex.quote(sys.executable)} <<'PY'\n"
            "from pathlib import Path\n"
            "print(Path('input.txt').read_text())\n"
            "PY\n"
        )
        execution = await sandbox.terminal.run_code(command)
        receipt = execution["data"]["metadata"]["terminal_execution_receipt"]
        assert receipt["effect"] == "read_only"
        assert receipt["cacheable"] is True
        assert receipt["effect_source"] == "trusted_command_contract"
    finally:
        await sandbox.cleanup()
