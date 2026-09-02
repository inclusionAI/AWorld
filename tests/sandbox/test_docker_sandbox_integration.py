from __future__ import annotations

import base64
import hashlib
import importlib
import json
import os
import shutil
import subprocess
from types import SimpleNamespace
import uuid

import pytest

from aworld.core.common import ActionResult
from aworld.core.context.base import Context as AWorldContext
from aworld.core.context.compiler import ToolOutputMode, ToolOutputPolicy
from aworld.core.context.tool_output_runtime import (
    enforce_tool_output_boundary,
    prepare_tool_output_plans,
)
from aworld.sandbox import DockerSandbox


pytestmark = [
    pytest.mark.docker_integration,
    pytest.mark.skipif(
        os.environ.get("AWORLD_RUN_DOCKER_INTEGRATION") != "1",
        reason="set AWORLD_RUN_DOCKER_INTEGRATION=1 to run the real Docker capability gate",
    ),
]


def _payload(content):
    return json.loads(content.text)


@pytest.mark.asyncio
async def test_real_docker_failed_mutation_is_rolled_back(tmp_path):
    docker = shutil.which("docker")
    if not docker:
        pytest.skip("Docker executable is unavailable")
    container = f"aworld-docker-rollback-{uuid.uuid4().hex[:10]}"
    started = subprocess.run(
        [docker, "run", "-d", "--rm", "--name", container, "-w", "/workspace", "alpine:3.20", "sleep", "infinity"],
        capture_output=True,
        text=True,
        check=False,
    )
    if started.returncode != 0:
        pytest.skip(f"unable to start alpine: {started.stderr.strip()}")
    sandbox = None
    try:
        subprocess.run(
            [docker, "exec", container, "sh", "-c", "printf original > protected.txt"],
            check=True,
        )
        sandbox = DockerSandbox(
            container=container,
            workdir="/workspace",
            allowed_directories=["/workspace"],
            destructive_checkpoint=True,
            tracked_artifact_paths=["/workspace"],
            checkpoint_directory=str(tmp_path / "checkpoints"),
            reuse=False,
        )
        results = await sandbox.call_tool(
            [
                {
                    "tool_name": "docker",
                    "action_name": "run_code",
                    "params": {"code": "rm -f protected.txt; exit 7", "output_format": "json"},
                }
            ]
        )
        restored = subprocess.run(
            [docker, "exec", container, "cat", "/workspace/protected.txt"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert restored.stdout == "original"
        receipt = results[0].metadata["context_management"]
        assert receipt["rollback_performed"] is True
        assert receipt["artifact_changed"] is False
        assert not list((tmp_path / "checkpoints").glob("*.tar"))
    finally:
        if sandbox is not None:
            await sandbox.cleanup()
        subprocess.run([docker, "rm", "-f", container], capture_output=True)


@pytest.mark.asyncio
async def test_real_docker_opaque_successful_command_cannot_silently_delete_artifact(
    tmp_path, monkeypatch,
):
    """Cover implicit executable side effects without naming a benchmark program."""
    docker = shutil.which("docker")
    if not docker:
        pytest.skip("Docker executable is unavailable")
    container = f"aworld-docker-implicit-loss-{uuid.uuid4().hex[:10]}"
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
            "alpine:3.20",
            "sleep",
            "infinity",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if started.returncode != 0:
        pytest.skip(f"unable to start alpine: {started.stderr.strip()}")
    sandbox = None
    try:
        subprocess.run(
            [
                docker,
                "exec",
                container,
                "sh",
                "-c",
                (
                    "printf original > protected.sidecar; "
                    "printf '#!/bin/sh\\nrm -f /workspace/protected.sidecar\\n"
                    "printf inspected\\n' > /usr/local/bin/opaque-reader; "
                    "chmod +x /usr/local/bin/opaque-reader"
                ),
            ],
            check=True,
        )
        sandbox = DockerSandbox(
            container=container,
            workdir="/workspace",
            allowed_directories=["/workspace"],
            destructive_checkpoint=True,
            tracked_artifact_paths=["/workspace"],
            checkpoint_directory=str(tmp_path / "checkpoints"),
            reuse=False,
        )
        tool_journal = tmp_path / "tool_actions.journal.jsonl"
        monkeypatch.setenv("AWORLD_TOOL_ACTION_JOURNAL_PATH", str(tool_journal))
        context = AWorldContext(task_id="implicit-side-effect")
        context.agent_info.current_agent_id = "integration-agent"
        results = await sandbox.call_tool(
            [
                {
                    "tool_name": "docker",
                    "action_name": "run_code",
                    "params": {
                        "code": "opaque-reader",
                        "output_format": "json",
                    },
                }
            ],
            context=context,
        )

        restored = subprocess.run(
            [docker, "exec", container, "cat", "/workspace/protected.sidecar"],
            capture_output=True,
            text=True,
            check=True,
        )
        receipt = results[0].metadata["context_management"]
        assert restored.stdout == "original"
        assert results[0].success is False
        assert receipt["mutating_action"] is False
        assert receipt["implicit_artifact_loss_detected"] is True
        assert receipt["rollback_reason"] == "unexpected_implicit_artifact_loss"
        assert receipt["rollback_performed"] is True
        assert not list((tmp_path / "checkpoints").glob("*.tar"))
        from aworld.core.tool_action_journal import read_tool_action_journal

        recovery = read_tool_action_journal(tool_journal)
        transaction = next(
            event
            for event in recovery.events
            if event["event_type"] == "sandbox_transaction_resolved"
        )
        assert transaction["status"] == "rolled_back"
        assert transaction["metadata"]["context_management"]["rollback_reason"] == (
            "unexpected_implicit_artifact_loss"
        )
    finally:
        if sandbox is not None:
            await sandbox.cleanup()
        subprocess.run([docker, "rm", "-f", container], capture_output=True)


@pytest.mark.asyncio
async def test_real_docker_tool_capability_matrix(monkeypatch, tmp_path, record_property):
    docker = shutil.which("docker")
    if not docker:
        pytest.skip("Docker executable is unavailable")
    container = f"aworld-docker-gate-{uuid.uuid4().hex[:10]}"
    image = os.environ.get("AWORLD_DOCKER_TEST_IMAGE", "alpine:3.20")
    started = subprocess.run(
        [docker, "run", "-d", "--rm", "--name", container, "-w", "/workspace", image, "sleep", "infinity"],
        capture_output=True,
        text=True,
        check=False,
    )
    if started.returncode != 0:
        pytest.skip(f"unable to start {image}: {started.stderr.strip()}")

    tools_checked = []
    try:
        image_id = subprocess.run(
            [docker, "inspect", "--format", "{{.Image}}", container],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        monkeypatch.setenv("AWORLD_DOCKER_CONTAINER", container)
        monkeypatch.setenv("AWORLD_DOCKER_BINARY", docker)
        monkeypatch.setenv("AWORLD_DOCKER_WORKDIR", "/workspace")
        monkeypatch.setenv("AWORLD_DOCKER_ALLOWED_DIRECTORIES", '["/workspace"]')
        monkeypatch.setenv("AWORLD_DOCKER_MAX_OUTPUT_BYTES", "16")
        monkeypatch.setenv("AWORLD_DOCKER_OUTPUT_HEAD_BYTES", "8")
        monkeypatch.setenv("AWORLD_DOCKER_ARTIFACT_DIRECTORY", str(tmp_path / "tool-artifacts"))
        from aworld.sandbox.tool_servers.docker.src import server

        server = importlib.reload(server)
        result = _payload(
            await server.run_code(
                None,
                "printf '0123456789abcdefghijklmnop'",
                timeout=30,
                output_format="json",
            )
        )
        tools_checked.append("run_code")
        artifact_ref = result["metadata"]["output_policy"]["stdout"]["artifact_ref"]
        assert artifact_ref
        aworld_context = AWorldContext(
            task_id="docker-artifact-boundary",
            workspace_path=str(tmp_path / "context-workspace"),
        )
        aworld_context.configure_tool_output_boundary(
            ToolOutputPolicy(
                max_inline_tokens=64,
                mode=ToolOutputMode.HEAD_TAIL,
                preserve_fields=("head", "tail", "artifact_ref"),
                tail_tokens=16,
                artifact_retention="task",
                policy_version="aworld-tool-output-v1",
            )
        )
        action = SimpleNamespace(
            tool_call_id="docker-run-code",
            tool_name="docker",
        )
        action_result = ActionResult(
            content=json.dumps(result),
            metadata={},
            tool_call_id=action.tool_call_id,
            tool_name="docker",
            action_name="run_code",
            success=True,
        )
        step_result = (SimpleNamespace(action_result=[action_result]),)
        plans = prepare_tool_output_plans(aworld_context, (action,))
        enforce_tool_output_boundary(
            step_result,
            (action,),
            aworld_context,
            plans,
        )
        # Context keeps its own exact ActionResult snapshot, while the primary
        # model-visible receipt remains owned by the originating Docker Tool.
        assert action_result.content["artifact_ref"] == artifact_ref
        assert action_result.metadata["tool_output_policy"]["artifact_ref"] == artifact_ref
        context_ref = action_result.metadata["tool_output_policy"]["context_artifact_ref"]
        assert context_ref.startswith("aworld-tool-output://")
        assert json.loads(
            aworld_context.read_tool_output_artifact(context_ref).decode("utf-8")
        ) == result
        artifact_chunk = _payload(
            await server.read_output_artifact(
                None,
                action_result.content["artifact_ref"],
                offset=0,
                limit=16,
                output="text",
            )
        )
        tools_checked.append("read_output_artifact")
        assert artifact_chunk["content"] == "0123456789abcdef"
        assert artifact_chunk["complete"] is False
        assert artifact_chunk["artifact_ref"] == artifact_ref
        assert artifact_chunk["returned_bytes"] == 16
        assert artifact_chunk["chunk_sha256"] == hashlib.sha256(
            b"0123456789abcdef"
        ).hexdigest()
        # Bind the actual container read through the same runtime boundary;
        # this proves a checksum-bound receipt, not merely a shaped fake result.
        aworld_context.register_model_tool_choices(
            "docker-read-request", [{"id": "docker-read-artifact"}]
        )
        read_action = SimpleNamespace(
            tool_call_id="docker-read-artifact",
            tool_name="docker",
            action_name="read_output_artifact",
            params={"artifact_ref": artifact_ref, "offset": 0, "limit": 16},
        )
        read_result = ActionResult(
            content=artifact_chunk,
            metadata={},
            tool_call_id=read_action.tool_call_id,
            tool_name="docker",
            action_name="read_output_artifact",
            success=True,
        )
        read_step = (SimpleNamespace(action_result=[read_result]),)
        read_plans = prepare_tool_output_plans(aworld_context, (read_action,))
        enforce_tool_output_boundary(
            read_step, (read_action,), aworld_context, read_plans
        )
        aworld_context.record_model_turn(
            "docker-after-read",
            [{
                "role": "tool",
                "tool_call_id": read_action.tool_call_id,
                "content": read_result.content,
            }],
        )
        retrieval_receipt = aworld_context.get_artifact_retrieval_receipts()[0]
        assert retrieval_receipt.returned_byte_count == 16
        assert retrieval_receipt.chunk_checksum == (
            "sha256:" + hashlib.sha256(b"0123456789abcdef").hexdigest()
        )
        assert retrieval_receipt.consumed is True

        await server.write_file(None, "/workspace/a.txt", "alpha\nbeta\n")
        tools_checked.append("write_file")
        assert "alpha" in _payload(
            await server.read_file(None, "/workspace/a.txt", head=None, tail=None, output="text")
        )["content"]
        tools_checked.append("read_file")

        binary = b"\x00\x01\xff"
        await server.write_file_base64(None, "/workspace/a.bin", base64.b64encode(binary).decode())
        tools_checked.append("write_file_base64")
        downloaded = _payload(await server.download_file(None, "/workspace/a.bin"))
        tools_checked.append("download_file")
        assert base64.b64decode(downloaded["base64"]) == binary
        await server.read_media_file(None, "/workspace/a.bin")
        tools_checked.append("read_media_file")

        await server.edit_file(None, "/workspace/a.txt", 2, 2, "gamma", dryRun=False)
        tools_checked.append("edit_file")
        await server.create_directory(None, "/workspace/nested")
        tools_checked.append("create_directory")
        await server.move_file(None, "/workspace/a.txt", "/workspace/nested/moved.txt")
        tools_checked.append("move_file")
        await server.upload_file(None, "/workspace/nested/moved.txt", "/workspace/copied.txt")
        tools_checked.append("upload_file")
        assert "moved.txt" in (await server.list_directory(None, "/workspace/nested")).text
        tools_checked.append("list_directory")
        assert "/workspace" in (await server.list_allowed_directories(None)).text
        tools_checked.append("list_allowed_directories")
        assert "gamma" in (
            await server.search_content(
                None,
                "/workspace",
                "gamma",
                max_matches=None,
                max_per_file=None,
                before=0,
                after=0,
            )
        ).text
        tools_checked.append("search_content")
        assert "copied.txt" in (await server.search_files(None, "/workspace", "*.txt", [])).text
        tools_checked.append("search_files")

        record_property("docker_image", image)
        record_property("docker_image_id", image_id)
        record_property("tool_matrix", ",".join(tools_checked))
        assert len(tools_checked) == 15
    finally:
        subprocess.run([docker, "rm", "-f", container], capture_output=True, check=False)
