from __future__ import annotations

from subprocess import CompletedProcess

import pytest

from aworld.core.common import ActionResult
from aworld.sandbox import DockerSandbox, SandboxEnvType, create_sandbox
from aworld.sandbox.base import BaseSandbox


@pytest.fixture
def docker_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "aworld.sandbox.implementations.docker.shutil.which",
        lambda binary: "/usr/local/bin/docker" if binary == "docker" else None,
    )
    monkeypatch.setattr(
        "aworld.sandbox.implementations.docker.subprocess.run",
        lambda *args, **kwargs: CompletedProcess(
            args=args[0],
            returncode=0,
            stdout='true\t"/workspace"\n',
            stderr="",
        ),
    )


@pytest.mark.asyncio
async def test_docker_sandbox_attaches_without_owning_container(
    docker_runtime: None,
    tmp_path,
) -> None:
    sandbox = DockerSandbox(
        container="terminal-bench-task",
        max_inline_output_bytes=4096,
        output_head_bytes=1024,
        artifact_directory=str(tmp_path / "tool-output"),
        reuse=False,
    )

    assert sandbox.env_type is SandboxEnvType.DOCKER
    assert sandbox.mode == "remote"
    assert sandbox.container_workdir == "/workspace"
    assert sandbox.allowed_directories == ["/workspace"]
    assert sandbox.metadata["container_lifecycle"] == "external"
    docker_config = sandbox.mcp_config["mcpServers"]["docker"]
    assert docker_config["type"] == "stdio"
    assert docker_config["headers"]["MCP_SERVERS"] == "terminal,filesystem"
    assert docker_config["env"]["AWORLD_DOCKER_CONTAINER"] == "terminal-bench-task"
    assert docker_config["env"]["AWORLD_DOCKER_MAX_OUTPUT_BYTES"] == "4096"
    assert docker_config["env"]["AWORLD_DOCKER_OUTPUT_HEAD_BYTES"] == "1024"
    assert docker_config["env"]["AWORLD_DOCKER_ARTIFACT_DIRECTORY"] == str(
        (tmp_path / "tool-output").resolve()
    )
    assert sandbox.metadata["tool_output_policy"]["strategy"] == "head_tail_artifact"

    await sandbox.cleanup()


@pytest.mark.asyncio
async def test_factory_selects_docker_sandbox(docker_runtime: None) -> None:
    sandbox = create_sandbox(
        env_type=SandboxEnvType.DOCKER,
        container="abc123",
        allowed_directories=["/workspace", "/tests"],
        reuse=False,
    )

    assert isinstance(sandbox, DockerSandbox)
    assert sandbox.allowed_directories == ["/workspace", "/tests"]

    await sandbox.cleanup()


def test_docker_sandbox_rejects_option_like_container_name(docker_runtime: None) -> None:
    with pytest.raises(ValueError, match="container must be"):
        DockerSandbox(container="--context=unexpected", reuse=False)


def test_docker_sandbox_requires_running_container(
    docker_runtime: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "aworld.sandbox.implementations.docker.subprocess.run",
        lambda *args, **kwargs: CompletedProcess(
            args=args[0],
            returncode=0,
            stdout='false\t"/workspace"\n',
            stderr="",
        ),
    )

    with pytest.raises(RuntimeError, match="is not running"):
        DockerSandbox(container="stopped-task", reuse=False)


def test_docker_sandbox_rejects_reserved_user_server(docker_runtime: None) -> None:
    with pytest.raises(ValueError, match="reserved"):
        DockerSandbox(
            container="abc123",
            mcp_config={"mcpServers": {"docker": {"url": "http://example.invalid"}}},
            reuse=False,
        )


def test_docker_sandbox_rejects_invalid_output_policy(docker_runtime: None) -> None:
    with pytest.raises(ValueError, match="max_inline_output_bytes"):
        DockerSandbox(container="abc123", max_inline_output_bytes=0, reuse=False)

    with pytest.raises(ValueError, match="output_head_bytes"):
        DockerSandbox(
            container="abc123",
            max_inline_output_bytes=100,
            output_head_bytes=101,
            reuse=False,
        )

    with pytest.raises(ValueError, match="narrower than"):
        DockerSandbox(
            container="abc123",
            destructive_checkpoint=True,
            tracked_artifact_paths=["/"],
            reuse=False,
        )


def test_docker_sandbox_mutation_classifier_is_generic() -> None:
    assert DockerSandbox._is_mutating_action(
        {"action_name": "write_file", "params": {"path": "/workspace/a"}}
    )
    assert DockerSandbox._is_mutating_action(
        {"action_name": "run_code", "params": {"code": "rm -f output.txt"}}
    )
    assert DockerSandbox._is_mutating_action(
        {"action_name": "run_code", "params": {"code": "python x.py > result"}}
    )
    assert not DockerSandbox._is_mutating_action(
        {"action_name": "run_code", "params": {"code": "cat result"}}
    )


@pytest.mark.asyncio
async def test_failed_mutation_rolls_back_and_emits_artifact_receipt(
    docker_runtime: None, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox = DockerSandbox(
        container="task",
        destructive_checkpoint=True,
        tracked_artifact_paths=["/workspace"],
        checkpoint_directory=str(tmp_path),
        reuse=False,
    )
    archive = tmp_path / "checkpoint.tar"
    archive.write_bytes(b"checkpoint")
    checkpoint = {"id": "checkpoint", "archive": archive, "sha256": "digest"}
    fingerprints = iter(("before", "before"))
    restored = []

    monkeypatch.setattr(sandbox, "_artifact_fingerprint_sync", lambda: next(fingerprints))
    monkeypatch.setattr(sandbox, "_create_checkpoint_sync", lambda: checkpoint)
    monkeypatch.setattr(sandbox, "_restore_checkpoint_sync", lambda value: restored.append(value))

    async def failed_call(self, action_list=None, task_id=None, session_id=None, context=None):
        return [
            ActionResult(
                success=True,
                content='{"success": false, "metadata": {"return_code": 1}}',
            )
        ]

    monkeypatch.setattr(BaseSandbox, "call_tool", failed_call)
    results = await sandbox.call_tool(
        [{"action_name": "run_code", "params": {"code": "rm -f result"}}]
    )
    receipt = results[0].metadata["context_management"]
    assert restored == [checkpoint]
    assert receipt["rollback_performed"] is True
    assert receipt["rollback_reason"] == "tool_failure"
    assert receipt["artifact_changed"] is False
    assert not archive.exists()


@pytest.mark.asyncio
async def test_successful_mutation_reports_artifact_progress_without_rollback(
    docker_runtime: None, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox = DockerSandbox(
        container="task",
        destructive_checkpoint=True,
        tracked_artifact_paths=["result.txt"],
        checkpoint_directory=str(tmp_path),
        reuse=False,
    )
    assert sandbox.tracked_artifact_paths == ["/workspace/result.txt"]
    archive = tmp_path / "checkpoint.tar"
    archive.write_bytes(b"checkpoint")
    checkpoint = {"id": "checkpoint", "archive": archive, "sha256": "digest"}
    fingerprints = iter(("before", "after"))
    restored = []
    monkeypatch.setattr(sandbox, "_artifact_fingerprint_sync", lambda: next(fingerprints))
    monkeypatch.setattr(sandbox, "_create_checkpoint_sync", lambda: checkpoint)
    monkeypatch.setattr(sandbox, "_restore_checkpoint_sync", lambda value: restored.append(value))

    async def successful_call(self, action_list=None, task_id=None, session_id=None, context=None):
        return [ActionResult(success=True, content='{"success": true}')]

    monkeypatch.setattr(BaseSandbox, "call_tool", successful_call)
    results = await sandbox.call_tool(
        [{"action_name": "write_file", "params": {"path": "result.txt"}}]
    )
    receipt = results[0].metadata["context_management"]
    assert restored == []
    assert receipt["artifact_changed"] is True
    assert receipt["rollback_performed"] is False
    assert not archive.exists()


@pytest.mark.asyncio
async def test_docker_sandbox_validation_preserves_argv_and_env_names(
    docker_runtime: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if command[1] == "inspect":
            return CompletedProcess(command, 0, 'true\t"/workspace"\n', "")
        return CompletedProcess(command, 7, "validator output", "failed")

    monkeypatch.setattr(
        "aworld.sandbox.implementations.docker.subprocess.run", fake_run
    )
    sandbox = DockerSandbox(container="task", reuse=False)
    result = await sandbox.run_validation(
        ("/bin/bash", "/tests/test.sh"),
        cwd="/workspace",
        timeout=45,
        env_names=("JUDGE_API_KEY",),
    )
    command, kwargs = calls[-1]
    assert command == [
        "/usr/local/bin/docker",
        "exec",
        "--workdir",
        "/workspace",
        "--env",
        "JUDGE_API_KEY",
        "task",
        "/bin/bash",
        "/tests/test.sh",
    ]
    assert kwargs["timeout"] == 45
    assert result.returncode == 7


@pytest.mark.asyncio
async def test_docker_sandbox_validation_rejects_unsafe_env_name(
    docker_runtime: None,
) -> None:
    sandbox = DockerSandbox(container="task", reuse=False)
    with pytest.raises(ValueError, match="safe identifiers"):
        await sandbox.run_validation(("true",), env_names=("KEY=value",))
