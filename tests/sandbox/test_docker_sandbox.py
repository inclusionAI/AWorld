from __future__ import annotations

from subprocess import CompletedProcess

import pytest

from aworld.sandbox import DockerSandbox, SandboxEnvType, create_sandbox


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
async def test_docker_sandbox_attaches_without_owning_container(docker_runtime: None) -> None:
    sandbox = DockerSandbox(container="terminal-bench-task", reuse=False)

    assert sandbox.env_type is SandboxEnvType.DOCKER
    assert sandbox.mode == "remote"
    assert sandbox.container_workdir == "/workspace"
    assert sandbox.allowed_directories == ["/workspace"]
    assert sandbox.metadata["container_lifecycle"] == "external"
    docker_config = sandbox.mcp_config["mcpServers"]["docker"]
    assert docker_config["type"] == "stdio"
    assert docker_config["headers"]["MCP_SERVERS"] == "terminal,filesystem"
    assert docker_config["env"]["AWORLD_DOCKER_CONTAINER"] == "terminal-bench-task"

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
