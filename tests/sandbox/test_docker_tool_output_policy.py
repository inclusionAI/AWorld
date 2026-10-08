from __future__ import annotations

import importlib
import json

import pytest


class _MemoryDockerBridge:
    container = "context-eval"
    workdir = "/workspace"
    shell = "/bin/sh"
    max_output_bytes = 4096
    max_read_bytes = 4096
    max_binary_bytes = 4096

    def __init__(self, content: bytes) -> None:
        self.content = content
        self.epoch = 1
        self.shell_calls = 0
        self.bounded_calls: list[list[str]] = []
        self.mutate_on_bounded = False
        self.mutate_on_shell = False
        self.resolved_path = "/workspace/input.txt"

    @staticmethod
    def validate_path(value):
        if not str(value).startswith("/workspace"):
            raise ValueError("outside workspace")
        return str(value)

    def _epoch_record(self) -> bytes:
        seconds = 1_700_000_000 + self.epoch
        timestamp = f"2023-11-14 22:13:{20 + self.epoch:02d}.000000000 +0000"
        fields = (
            f"1|{100 + self.epoch}|81a4|{len(self.content)}|{seconds}|{seconds}|"
            f"{timestamp}|{timestamp}"
        )
        return (
            self.resolved_path.encode() + b"\0" + f"{fields}\t{fields}".encode() + b"\0"
        )

    async def execute(self, command, **kwargs):
        marker = command[3] if len(command) > 3 else ""
        if marker == "aworld-read-epoch":
            paths = command[4:]
            return 0, self._epoch_record() * len(paths), b"", False
        if marker in {"aworld-bounded-read", "aworld-bounded-bytes"}:
            self.bounded_calls.append(command)
            assert "cat" not in command[2]
            if marker == "aworld-bounded-bytes":
                offset = int(command[5]) - 1
                limit = int(command[6])
                data = self.content[offset : offset + limit]
                if self.mutate_on_bounded:
                    self.epoch += 1
                return 0, data, b"", False
            script = command[2]
            path_index = 4
            assert command[path_index] == "/workspace/input.txt"
            if script.startswith('sed -n "1,'):
                count = int(command[5])
                limit = int(command[6])
                data = b"".join(self.content.splitlines(keepends=True)[:count])
            elif script.startswith("sed -n"):
                start = int(command[5])
                end = int(command[6])
                limit = int(command[7])
                data = b"".join(self.content.splitlines(keepends=True)[start - 1 : end])
            elif script.startswith("tail -n"):
                count = int(command[5])
                limit = int(command[6])
                data = b"".join(self.content.splitlines(keepends=True)[-count:])
            else:
                limit = int(command[5])
                data = self.content
            data = data[:limit]
            if self.mutate_on_bounded:
                self.epoch += 1
            return 0, data, b"", False
        raise AssertionError(f"unexpected docker execute: {command!r}")

    async def shell_command(self, code, **kwargs):
        self.shell_calls += 1
        data = self.content
        if self.mutate_on_shell:
            self.epoch += 1
        return 0, data, b"", False

    @staticmethod
    def bound_output(value, *, label):
        return value, {
            "output_truncated": False,
            "raw_bytes": len(value),
            "inline_bytes": len(value),
            "offloaded_bytes": 0,
            "content_sha256": "unused",
            "truncation_strategy": "none",
            "artifact_ref": None,
            "head_bytes": len(value),
            "tail_bytes": 0,
        }

    @staticmethod
    def decode_inline_text(value, _policy):
        return value.decode()


def _docker_server(monkeypatch):
    monkeypatch.setenv("AWORLD_DOCKER_CONTAINER", "context-eval")
    monkeypatch.setenv("AWORLD_DOCKER_BINARY", "/usr/bin/docker")
    monkeypatch.setenv("AWORLD_DOCKER_WORKDIR", "/workspace")
    monkeypatch.setenv("AWORLD_DOCKER_ALLOWED_DIRECTORIES", '["/workspace"]')
    return importlib.import_module("aworld.sandbox.tool_servers.docker.src.server")


def test_head_tail_policy_preserves_full_output_as_artifact(monkeypatch, tmp_path):
    monkeypatch.setenv("AWORLD_DOCKER_CONTAINER", "context-eval")
    monkeypatch.setenv("AWORLD_DOCKER_BINARY", "/usr/bin/docker")
    monkeypatch.setenv("AWORLD_DOCKER_WORKDIR", "/workspace")
    monkeypatch.setenv("AWORLD_DOCKER_ALLOWED_DIRECTORIES", '["/workspace"]')
    monkeypatch.setenv("AWORLD_DOCKER_MAX_OUTPUT_BYTES", "10")
    monkeypatch.setenv("AWORLD_DOCKER_OUTPUT_HEAD_BYTES", "4")
    monkeypatch.setenv("AWORLD_DOCKER_ARTIFACT_DIRECTORY", str(tmp_path))

    server = _docker_server(monkeypatch)
    test_bridge = server.DockerBridge()
    raw = b"0123456789abcdefghij"

    inline, metadata = test_bridge.bound_output(raw, label="stdout")

    assert inline == b"0123efghij"
    assert metadata["raw_bytes"] == 20
    assert metadata["inline_bytes"] == 10
    assert metadata["offloaded_bytes"] == 10
    assert metadata["truncation_strategy"] == "head_tail_artifact"
    assert metadata["output_truncated"] is True
    assert open(metadata["artifact_ref"], "rb").read() == raw


@pytest.mark.asyncio
async def test_docker_run_code_uses_explicit_python_execution_contract(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AWORLD_DOCKER_CONTAINER", "context-eval")
    monkeypatch.setenv("AWORLD_DOCKER_BINARY", "/usr/bin/docker")
    monkeypatch.setenv("AWORLD_DOCKER_WORKDIR", "/workspace")
    monkeypatch.setenv("AWORLD_DOCKER_ALLOWED_DIRECTORIES", '["/workspace"]')
    monkeypatch.setenv("AWORLD_DOCKER_PYTHON", "/opt/python")
    server = _docker_server(monkeypatch)
    captured: dict[str, object] = {}

    class _Bridge:
        container = "context-eval"
        workdir = "/workspace"
        shell = "/bin/sh"

        @staticmethod
        def validate_path(value):
            return value

        @staticmethod
        async def execute(command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs
            return 0, b"3\n", b"", False

        @staticmethod
        def bound_output(value, *, label):
            return value, {"output_truncated": False, "label": label}

        @staticmethod
        def decode_inline_text(value, _policy):
            return value.decode()

    monkeypatch.setattr(server, "bridge", _Bridge())

    result = await server.run_code(
        None,
        "print(1 + 2)",
        timeout=30,
        language="python",
    )
    payload = json.loads(result.text)
    receipt = payload["metadata"]["terminal_execution_receipt"]

    assert captured["command"] == ["/opt/python", "-c", "print(1 + 2)"]
    assert captured["kwargs"] == {"timeout": 30, "workdir": "/workspace"}
    assert payload["success"] is True
    assert receipt["requested_language"] == "python"
    assert receipt["effective_language"] == "python"


@pytest.mark.asyncio
async def test_docker_run_code_revalidates_container_epochs_before_compact_reuse(
    monkeypatch,
) -> None:
    server = _docker_server(monkeypatch)
    test_bridge = _MemoryDockerBridge(b"alpha\nbeta\n")
    monkeypatch.setattr(server, "bridge", test_bridge)
    server._READ_FACTS.clear()
    scope = {
        "task_id": "task-a",
        "task_epoch": 0,
        "session_id": "session",
        "checkpoint_revision": 0,
        "sandbox_id": "sandbox",
    }

    first = json.loads(
        (
            await server.run_code(None, "cat /workspace/input.txt", env_content=scope)
        ).text
    )
    repeated = json.loads(
        (
            await server.run_code(None, "cat /workspace/input.txt", env_content=scope)
        ).text
    )

    first_receipt = first["metadata"]["terminal_execution_receipt"]
    assert first_receipt["cacheable"] is True
    assert first_receipt["read_ranges"] == [{"kind": "full"}]
    assert first_receipt["read_path_epochs"][0]["authority"].startswith(
        "docker:sha256:"
    )
    assert repeated["metadata"]["provider_observation_cache_hit"] is True
    repeated_receipt = repeated["metadata"]["terminal_execution_receipt"]
    assert repeated_receipt["executed"] is False
    assert repeated_receipt["cache_hit"] is True
    assert repeated_receipt["observation_id"] == first_receipt["observation_id"]
    assert json.loads(repeated["message"])["type"] == "unchanged"
    assert test_bridge.shell_calls == 1

    rewritten_scope = {**scope, "checkpoint_revision": 1}
    rewritten = json.loads(
        (
            await server.run_code(
                None,
                "cat /workspace/input.txt",
                env_content=rewritten_scope,
            )
        ).text
    )
    assert rewritten["metadata"].get("provider_observation_cache_hit") is not True
    assert test_bridge.shell_calls == 2

    test_bridge.epoch += 1
    changed = json.loads(
        (
            await server.run_code(
                None,
                "cat /workspace/input.txt",
                env_content=rewritten_scope,
            )
        ).text
    )
    assert changed["metadata"].get("provider_observation_cache_hit") is not True
    assert test_bridge.shell_calls == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "expected", "kind"),
    (
        ({"head": 2}, "line-1\nline-2\n", "line_range"),
        ({"head": 2, "tail": 3}, "line-2\nline-3\n", "line_range"),
        ({"tail": 2}, "line-4\nline-5\n", "tail_lines"),
    ),
)
async def test_docker_read_file_executes_bounded_ranges_in_container(
    monkeypatch,
    kwargs,
    expected,
    kind,
) -> None:
    server = _docker_server(monkeypatch)
    test_bridge = _MemoryDockerBridge(b"line-1\nline-2\nline-3\nline-4\nline-5\n")
    monkeypatch.setattr(server, "bridge", test_bridge)
    server._READ_FACTS.clear()

    result = await server.read_file(
        None,
        "/workspace/input.txt",
        output="text",
        env_content={
            "task_id": "bounded",
            "task_epoch": 1,
            "session_id": "session",
            "checkpoint_revision": 0,
            "sandbox_id": "sandbox",
        },
        **kwargs,
    )
    payload = json.loads(result.text)
    receipt = result.model_extra["metadata"]["read_observation_receipt"]

    assert payload["content"] == expected
    assert payload["coverage"]["kind"] == kind
    assert receipt["epoch"]["authority"].startswith("docker:sha256:")
    assert test_bridge.bounded_calls
    assert all("cat" not in command[2] for command in test_bridge.bounded_calls)


@pytest.mark.asyncio
async def test_docker_facts_require_same_representation_and_task_scope(
    monkeypatch,
) -> None:
    server = _docker_server(monkeypatch)
    test_bridge = _MemoryDockerBridge(b"alpha\nbeta\n")
    monkeypatch.setattr(server, "bridge", test_bridge)
    server._READ_FACTS.clear()
    scope_a = {
        "task_id": "task-a",
        "task_epoch": 1,
        "session_id": "session",
        "checkpoint_revision": 0,
        "sandbox_id": "sandbox",
    }
    scope_b = {
        "task_id": "task-b",
        "task_epoch": 1,
        "session_id": "session",
        "checkpoint_revision": 0,
        "sandbox_id": "sandbox",
    }

    await server.run_code(
        None,
        "cat /workspace/input.txt",
        env_content=scope_a,
    )
    first_read = await server.read_file(
        None,
        "/workspace/input.txt",
        output="text",
        env_content=scope_a,
    )
    reused = await server.read_file(
        None,
        "/workspace/input.txt",
        output="text",
        env_content=scope_a,
    )
    isolated = await server.read_file(
        None,
        "/workspace/input.txt",
        output="text",
        env_content=scope_b,
    )

    assert json.loads(first_read.text)["type"] == "text"
    assert json.loads(reused.text)["type"] == "unchanged"
    assert json.loads(isolated.text)["type"] == "text"
    assert len(test_bridge.bounded_calls) == 2


@pytest.mark.asyncio
async def test_docker_large_default_read_is_producer_bounded_and_races_do_not_cache(
    monkeypatch,
) -> None:
    server = _docker_server(monkeypatch)
    test_bridge = _MemoryDockerBridge(b"x" * (2 * 1024 * 1024))
    test_bridge.mutate_on_bounded = True
    monkeypatch.setattr(server, "bridge", test_bridge)
    server._READ_FACTS.clear()
    scope = {
        "task_id": "race",
        "task_epoch": 1,
        "session_id": "session",
        "checkpoint_revision": 0,
        "sandbox_id": "sandbox",
    }

    first = await server.read_file(
        None,
        "/workspace/input.txt",
        output="text",
        env_content=scope,
    )
    second = await server.read_file(
        None,
        "/workspace/input.txt",
        output="text",
        env_content=scope,
    )

    first_payload = json.loads(first.text)
    second_payload = json.loads(second.text)
    assert len(first_payload["content"].encode()) == test_bridge.max_read_bytes
    assert first_payload["complete"] is False
    assert first_payload["defaultBounded"] is True
    assert "observationId" not in first_payload
    assert second_payload["type"] == "text"
    assert len(test_bridge.bounded_calls) == 2
    assert all(
        command[2].startswith("head -c") for command in test_bridge.bounded_calls
    )


@pytest.mark.asyncio
async def test_docker_symlink_epoch_outside_allowed_scope_fails_cache_closed(
    monkeypatch,
) -> None:
    server = _docker_server(monkeypatch)
    test_bridge = _MemoryDockerBridge(b"secret")
    test_bridge.resolved_path = "/etc/passwd"
    monkeypatch.setattr(server, "bridge", test_bridge)
    server._READ_FACTS.clear()

    result = json.loads(
        (
            await server.run_code(
                None,
                "cat /workspace/input.txt",
                env_content={"task_id": "symlink", "task_epoch": 1},
            )
        ).text
    )

    receipt = result["metadata"]["terminal_execution_receipt"]
    assert receipt["effect"] == "read_only"
    assert receipt["cacheable"] is False
    assert receipt["read_path_epochs"] == []


@pytest.mark.asyncio
async def test_docker_callback_sensitive_reads_fail_authority_closed(
    monkeypatch,
) -> None:
    server = _docker_server(monkeypatch)
    test_bridge = _MemoryDockerBridge(b"needle\n")
    monkeypatch.setattr(server, "bridge", test_bridge)
    server._READ_FACTS.clear()
    scope = {
        "task_id": "callbacks",
        "task_epoch": 0,
        "session_id": "session",
        "checkpoint_revision": 0,
        "sandbox_id": "sandbox",
    }

    configured_rg = json.loads(
        (
            await server.run_code(
                None,
                "rg needle /workspace/input.txt",
                env_content=scope,
            )
        ).text
    )["metadata"]["terminal_execution_receipt"]
    callback_free_rg = json.loads(
        (
            await server.run_code(
                None,
                "rg --no-config needle /workspace/input.txt",
                env_content=scope,
            )
        ).text
    )["metadata"]["terminal_execution_receipt"]
    git_status = json.loads(
        (
            await server.run_code(
                None,
                "git --no-pager status --short",
                env_content=scope,
            )
        ).text
    )["metadata"]["terminal_execution_receipt"]

    assert configured_rg["effect"] == "unknown"
    assert configured_rg["cacheable"] is False
    assert callback_free_rg["effect"] == "read_only"
    assert callback_free_rg["cacheable"] is True
    assert git_status["effect"] == "unknown"
    assert git_status["cacheable"] is False


@pytest.mark.asyncio
async def test_docker_missing_authoritative_scope_never_reuses_facts(
    monkeypatch,
) -> None:
    server = _docker_server(monkeypatch)
    test_bridge = _MemoryDockerBridge(b"alpha\n")
    monkeypatch.setattr(server, "bridge", test_bridge)
    server._READ_FACTS.clear()
    incomplete_scope = {
        "task_id": "missing-session",
        "task_epoch": 0,
        "checkpoint_revision": 0,
    }

    first = json.loads(
        (
            await server.run_code(
                None,
                "cat /workspace/input.txt",
                env_content=incomplete_scope,
            )
        ).text
    )
    second = json.loads(
        (
            await server.run_code(
                None,
                "cat /workspace/input.txt",
                env_content=incomplete_scope,
            )
        ).text
    )

    assert first["metadata"].get("provider_observation_id") is None
    assert second["metadata"].get("provider_observation_cache_hit") is not True
    assert test_bridge.shell_calls == 2
