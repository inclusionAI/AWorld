from __future__ import annotations

import hashlib
import importlib
import json
import posixpath
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from aworld.core.common import ActionResult
from aworld.sandbox.tool_observation import SandboxToolObservationRuntime


class _MemoryDockerBridge:
    container = "context-eval"
    workdir = "/workspace"
    shell = "/bin/sh"
    max_output_bytes = 4096
    max_read_bytes = 4096
    max_binary_bytes = 4096
    allowed_directories = ["/workspace"]

    def __init__(self, content: bytes) -> None:
        self.content = content
        self.epoch = 1
        self.shell_calls = 0
        self.bounded_calls: list[list[str]] = []
        self.mutate_on_bounded = False
        self.mutate_on_shell = False
        self.resolved_path = "/workspace/input.txt"
        self.context_epoch = 1
        self.shadowed_executables: dict[str, str] = {}
        self.trusted_shell_calls = 0
        self.login_profile_mutations = 0
        self.mutate_context_on_trusted = False
        self.trusted_context_calls: list[tuple[list[str], dict[str, object]]] = []
        self.trusted_command_calls: list[tuple[list[str], dict[str, object]]] = []
        self.atomic_calls: list[tuple[list[str], dict[str, object]]] = []
        self.trusted_roots_mutable = False
        self.ld_so_preload_present = False
        self.script_executables: set[str] = set()
        self.atomic_swap_content: bytes | None = None
        self.target_mode = 0o100644

    def validate_path(self, value):
        normalized = posixpath.normpath(str(value))
        if not any(
            posixpath.commonpath((normalized, root)) == root
            for root in self.allowed_directories
        ):
            raise ValueError("outside workspace")
        return normalized

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
        if "aworld-execution-context" in command:
            self.trusted_context_calls.append((list(command), dict(kwargs)))
            marker_index = command.index("aworld-execution-context")
            policy = command[marker_index + 1]
            if self.trusted_roots_mutable and policy == "immutable":
                return 78, b"", b"trusted roots are mutable", False
            if self.ld_so_preload_present:
                return 77, b"", b"ld.so.preload is present", False
            tokens = command[marker_index + 2 :]
            if any(token in self.script_executables for token in tokens):
                return 80, b"", b"script executable is untrusted", False
            fields: list[bytes] = []
            for token in tokens:
                canonical = self.shadowed_executables.get(token)
                if canonical is None:
                    canonical = {
                        "/usr/bin/env": "/usr/bin/env",
                        "/bin/env": "/usr/bin/env",
                        "/bin/sh": "/usr/bin/dash",
                        "readlink": "/usr/bin/readlink",
                        "stat": "/usr/bin/stat",
                        "cat": "/usr/bin/cat",
                        "/bin/cat": "/usr/bin/cat",
                        "rg": "/usr/bin/rg",
                    }.get(token, f"/usr/bin/{str(token).rsplit('/', 1)[-1]}")
                seconds = 1_700_000_100 + self.context_epoch
                epoch = (
                    f"1|{200 + self.context_epoch}|81ed|1024|{seconds}|{seconds}|"
                    "2023-11-14 22:15:01.000000000 +0000|"
                    "2023-11-14 22:15:01.000000000 +0000"
                )
                fields.extend(
                    (str(token).encode(), str(canonical).encode(), epoch.encode())
                )
            return 0, b"\0".join(fields) + b"\0", b"", False
        if "aworld-held-fd-read" in command:
            self.atomic_calls.append((list(command), dict(kwargs)))
            marker_index = command.index("aworld-held-fd-read")
            mode = command[marker_index + 2]
            hard_limit = int(command[marker_index + 3])
            first = int(command[marker_index + 4])
            second = int(command[marker_index + 5])
            emit = command[marker_index + 6] == "1"
            active_content = self.atomic_swap_content or self.content
            epoch_value = self.epoch + (100 if self.atomic_swap_content else 0)
            if mode == "bytes":
                start = min(first, len(active_content))
                raw = active_content[start : start + second]
            elif mode == "head":
                raw = b"".join(active_content.splitlines(keepends=True)[:first])
            elif mode == "range":
                raw = b"".join(
                    active_content.splitlines(keepends=True)[first - 1 : second]
                )
            elif mode == "tail":
                raw = b"".join(active_content.splitlines(keepends=True)[-first:])
            else:
                raw = active_content
            data = raw[:hard_limit]
            if emit:
                self.bounded_calls.append(command)
                if self.mutate_on_bounded:
                    self.epoch += 1
                    epoch_value = self.epoch
                if self.mutate_context_on_trusted:
                    self.context_epoch += 1
            seconds = 1_700_000_000 + epoch_value
            record = (
                f"1|{100 + epoch_value}|{self.target_mode:x}|{len(active_content)}|{seconds}|"
                f"{seconds}|2023-11-14 22:13:21.000000000 +0000|"
                "2023-11-14 22:13:21.000000000 +0000"
            )
            digest = hashlib.sha256(data).hexdigest()
            metadata = (
                "AWORLD_READ_V1\t"
                f"{self.resolved_path}\t{record}\t{record}\t{digest}\t"
                f"{len(raw)}\t{len(data)}\t{len(active_content)}\t1\n"
            ).encode()
            return 0, data if emit else b"", metadata, False
        if "aworld-trusted-command" in command:
            self.trusted_command_calls.append((list(command), dict(kwargs)))
            marker_index = command.index("aworld-trusted-command")
            script = command[marker_index - 1]
            paths = command[marker_index + 1 :]
            if "resolved=$(readlink -f" in script:
                return 0, self._epoch_record() * len(paths), b"", False
            if any(
                marker in script
                for marker in ("sed -n", "tail -n", "tail -c", 'head -c "$2"')
            ):
                self.bounded_calls.append(command)
                if script.startswith('tail -c "+$2"'):
                    offset = int(paths[1]) - 1
                    limit = int(paths[2])
                    data = self.content[offset : offset + limit]
                elif script.startswith('sed -n "1,'):
                    count = int(paths[1])
                    limit = int(paths[2])
                    data = b"".join(self.content.splitlines(keepends=True)[:count])
                elif script.startswith("sed -n"):
                    start = int(paths[1])
                    end = int(paths[2])
                    limit = int(paths[3])
                    data = b"".join(
                        self.content.splitlines(keepends=True)[start - 1 : end]
                    )
                elif script.startswith("tail -n"):
                    count = int(paths[1])
                    limit = int(paths[2])
                    data = b"".join(self.content.splitlines(keepends=True)[-count:])
                else:
                    limit = int(paths[1])
                    data = self.content
                data = data[:limit]
                if self.mutate_on_bounded:
                    self.epoch += 1
                return 0, data, b"", False
            self.trusted_shell_calls += 1
            data = self.content
            if self.mutate_on_shell:
                self.epoch += 1
            if self.mutate_context_on_trusted:
                self.context_epoch += 1
            return 0, data, b"", False
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
        self.login_profile_mutations += 1
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


def _observation_context():
    return SimpleNamespace(
        task_id="task-a",
        task_epoch=0,
        session_id="session",
        agent_info=SimpleNamespace(current_agent_id="agent"),
        context_lifecycle_state=SimpleNamespace(
            session_id="session",
            session_epoch=0,
            task_epoch=0,
            branch_id="main",
            checkpoint_revision=0,
        ),
    )


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


def test_docker_held_fd_reader_is_shell_valid_and_producer_bounded(
    monkeypatch,
) -> None:
    server = _docker_server(monkeypatch)

    checked = subprocess.run(
        ["/bin/sh", "-n"],
        input=server._DOCKER_HELD_FD_READER,
        text=True,
        capture_output=True,
        check=False,
    )

    assert checked.returncode == 0, checked.stderr
    assert 'raw_selection 3 | head -c "$count_limit" | wc -c' in (
        server._DOCKER_HELD_FD_READER
    )


def test_bounded_read_pipelines_accept_expected_sigpipe_in_real_shells(
    tmp_path,
) -> None:
    source = tmp_path / "large-line.txt"
    source.write_bytes((b"x" * (128 * 1024)) + b"\n")
    script = r"""
set -efu
path=$1
mode=$2
hard_limit=4096
count_limit=$((hard_limit + 1))
size=$(wc -c < "$path")
exec 3< "$path"
exec 4< "$path"
raw_selection() {
    fd=$1
    case "$mode" in
        full) head -c "$size" "/dev/fd/$fd" ;;
        tail) tail -n 1 "/dev/fd/$fd" ;;
        head) head -c "$count_limit" "/dev/fd/$fd" | sed -n '1p' ;;
    esac
}
selected() { raw_selection "$1" | head -c "$hard_limit"; }
raw_count=$(raw_selection 3 | head -c "$count_limit" | wc -c)
content_hash=$(selected 4 | tee "$3" | sha256sum)
content_hash=${content_hash%% *}
printf '%s\t%s\n' "$raw_count" "$content_hash"
"""
    shells = [["/bin/bash"]]
    busybox = shutil.which("busybox")
    if busybox:
        shells.append([busybox, "sh"])

    for shell in shells:
        for mode in ("full", "tail", "head"):
            output = tmp_path / f"{Path(shell[0]).name}-{mode}.bin"
            result = subprocess.run(
                [*shell, "-c", script, "bounded-test", str(source), mode, str(output)],
                text=True,
                capture_output=True,
                check=False,
                timeout=10,
            )
            assert result.returncode == 0, result.stderr
            raw_count, digest = result.stdout.strip().split("\t")
            assert int(raw_count) == 4097
            assert output.stat().st_size == 4096
            assert digest == hashlib.sha256(output.read_bytes()).hexdigest()


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
    assert receipt["potential_effect"] == "read_only"
    assert receipt["effect"] == "unknown"
    assert receipt["cacheable"] is False
    assert receipt["workspace_generation_delta"] == 1


@pytest.mark.asyncio
async def test_docker_run_code_compacts_controlled_single_file_reads(
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
        "session_epoch": 0,
        "branch_id": "main",
        "checkpoint_revision": 0,
        "agent_id": "agent",
        "prompt_namespace": "agent",
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
    assert len(test_bridge.bounded_calls) == 1

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
    assert len(test_bridge.bounded_calls) == 2

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
    assert len(test_bridge.bounded_calls) == 3


@pytest.mark.asyncio
async def test_controlled_docker_receipt_is_accepted_across_sandbox_boundary(
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
        "session_epoch": 0,
        "branch_id": "main",
        "checkpoint_revision": 0,
        "agent_id": "agent",
        "prompt_namespace": "agent",
        "sandbox_id": "sandbox",
    }
    action = {
        "tool_name": "docker",
        "action_name": "run_code",
        "params": {"code": "cat /workspace/input.txt", "language": "shell"},
    }
    runtime = SandboxToolObservationRuntime()
    context = _observation_context()

    first_payload = json.loads(
        (
            await server.run_code(
                None,
                action["params"]["code"],
                language="shell",
                env_content=scope,
            )
        ).text
    )
    first = runtime.record(
        action,
        ActionResult(
            success=True,
            content=first_payload["message"],
            parameter=action["params"],
            metadata=first_payload["metadata"],
        ),
        context=context,
    )
    replay_payload = json.loads(
        (
            await server.run_code(
                None,
                action["params"]["code"],
                language="shell",
                env_content=scope,
            )
        ).text
    )
    replay = runtime.record(
        action,
        ActionResult(
            success=True,
            content=replay_payload["message"],
            parameter=action["params"],
            metadata=replay_payload["metadata"],
        ),
        context=context,
    )

    assert first.metadata["sandbox_observation"]["effect"] == "read_only"
    assert first.metadata["sandbox_observation"]["workspace_generation"] == 0
    assert replay.success is True
    assert replay.metadata["sandbox_observation"]["cache_state"] == "provider_replay"
    assert replay.metadata["sandbox_observation"]["cache_hit"] is True


@pytest.mark.asyncio
async def test_docker_overlap_cache_is_bound_to_executable_context(
    monkeypatch,
) -> None:
    server = _docker_server(monkeypatch)
    test_bridge = _MemoryDockerBridge(b"alpha\nbeta\n")
    monkeypatch.setattr(server, "bridge", test_bridge)
    server._READ_FACTS.clear()
    scope = {
        "task_id": "context-bound-overlap",
        "task_epoch": 1,
        "session_id": "session",
        "session_epoch": 0,
        "branch_id": "main",
        "checkpoint_revision": 0,
        "agent_id": "agent",
        "prompt_namespace": "agent",
        "sandbox_id": "sandbox",
    }

    first = json.loads(
        (
            await server.run_code(
                None,
                "head -n 1 /workspace/input.txt",
                env_content=scope,
            )
        ).text
    )
    test_bridge.context_epoch += 1
    repeated = json.loads(
        (
            await server.run_code(
                None,
                "head -n 1 /workspace/input.txt",
                env_content=scope,
            )
        ).text
    )

    first_receipt = first["metadata"]["terminal_execution_receipt"]
    repeated_receipt = repeated["metadata"]["terminal_execution_receipt"]
    assert first_receipt["cacheable"] is True
    assert repeated["metadata"].get("provider_observation_cache_hit") is not True
    assert repeated_receipt["cacheable"] is True
    assert (
        repeated_receipt["execution_context_sha256"]
        != first_receipt["execution_context_sha256"]
    )
    assert len(test_bridge.bounded_calls) == 2


@pytest.mark.asyncio
async def test_docker_absolute_system_read_uses_sanitized_non_login_context(
    monkeypatch,
) -> None:
    server = _docker_server(monkeypatch)
    test_bridge = _MemoryDockerBridge(b"alpha\n")
    monkeypatch.setattr(server, "bridge", test_bridge)
    server._READ_FACTS.clear()

    payload = json.loads(
        (
            await server.run_code(
                None,
                "/bin/cat /workspace/input.txt",
                env_content={
                    "task_id": "trusted-absolute",
                    "task_epoch": 1,
                    "session_id": "session",
                    "session_epoch": 0,
                    "branch_id": "main",
                    "checkpoint_revision": 0,
                    "agent_id": "agent",
                    "prompt_namespace": "agent",
                    "sandbox_id": "sandbox",
                },
            )
        ).text
    )
    receipt = payload["metadata"]["terminal_execution_receipt"]

    assert receipt["effect"] == "read_only"
    assert receipt["cacheable"] is True
    assert receipt["workspace_generation_delta"] == 0
    assert receipt["execution_context_sha256"].startswith("sha256:")
    assert test_bridge.trusted_shell_calls == 0
    assert len(test_bridge.bounded_calls) == 1
    assert test_bridge.shell_calls == 0
    assert test_bridge.login_profile_mutations == 0


@pytest.mark.asyncio
async def test_docker_shadowed_path_command_is_unknown_and_never_cacheable(
    monkeypatch,
) -> None:
    server = _docker_server(monkeypatch)
    test_bridge = _MemoryDockerBridge(b"poisoned\n")
    test_bridge.shadowed_executables["cat"] = "/workspace/bin/cat"
    monkeypatch.setattr(server, "bridge", test_bridge)
    server._READ_FACTS.clear()

    payload = json.loads(
        (
            await server.run_code(
                None,
                "cat /workspace/input.txt",
                env_content={
                    "task_id": "shadowed",
                    "task_epoch": 1,
                    "session_id": "session",
                    "session_epoch": 0,
                    "branch_id": "main",
                    "checkpoint_revision": 0,
                    "agent_id": "agent",
                    "prompt_namespace": "agent",
                    "sandbox_id": "sandbox",
                },
            )
        ).text
    )
    receipt = payload["metadata"]["terminal_execution_receipt"]

    assert receipt["potential_effect"] == "read_only"
    assert receipt["effect"] == "unknown"
    assert receipt["cacheable"] is False
    assert receipt["workspace_generation_delta"] == 1
    assert receipt.get("execution_context_sha256") is None
    assert receipt["effect_source"] == "untrusted_docker_execution_context"
    assert test_bridge.trusted_shell_calls == 0
    assert test_bridge.shell_calls == 1
    assert test_bridge.login_profile_mutations == 1


@pytest.mark.asyncio
async def test_docker_system_loader_preload_revokes_read_contract(
    monkeypatch,
) -> None:
    server = _docker_server(monkeypatch)
    test_bridge = _MemoryDockerBridge(b"alpha\n")
    test_bridge.ld_so_preload_present = True
    monkeypatch.setattr(server, "bridge", test_bridge)
    server._READ_FACTS.clear()

    payload = json.loads(
        (
            await server.run_code(
                None,
                "/bin/cat /workspace/input.txt",
            )
        ).text
    )
    receipt = payload["metadata"]["terminal_execution_receipt"]

    assert receipt["effect"] == "unknown"
    assert receipt["cacheable"] is False
    assert receipt["workspace_generation_delta"] == 1
    assert test_bridge.trusted_shell_calls == 0
    assert test_bridge.shell_calls == 1


@pytest.mark.asyncio
async def test_docker_controlled_read_is_cacheable_in_mutable_root_container(
    monkeypatch,
) -> None:
    server = _docker_server(monkeypatch)
    test_bridge = _MemoryDockerBridge(b"alpha\n")
    test_bridge.trusted_roots_mutable = True
    monkeypatch.setattr(server, "bridge", test_bridge)
    server._READ_FACTS.clear()

    payload = json.loads((await server.run_code(None, "cat /workspace/input.txt")).text)
    receipt = payload["metadata"]["terminal_execution_receipt"]

    assert receipt["effect"] == "read_only"
    assert receipt["cacheable"] is True
    assert receipt["workspace_generation_delta"] == 0
    assert len(test_bridge.bounded_calls) == 1


@pytest.mark.asyncio
async def test_docker_system_path_script_requires_nested_interpreter_contract(
    monkeypatch,
) -> None:
    server = _docker_server(monkeypatch)
    test_bridge = _MemoryDockerBridge(b"alpha\n")
    test_bridge.script_executables.add("cat")
    monkeypatch.setattr(server, "bridge", test_bridge)
    server._READ_FACTS.clear()

    payload = json.loads((await server.run_code(None, "cat /workspace/input.txt")).text)
    receipt = payload["metadata"]["terminal_execution_receipt"]

    assert receipt["potential_effect"] == "read_only"
    assert receipt["effect"] == "unknown"
    assert receipt["cacheable"] is False
    assert receipt["workspace_generation_delta"] == 1
    assert test_bridge.shell_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    (
        "CAT /workspace/input.txt",
        "/bin/CAT /workspace/input.txt",
        "PWD",
    ),
)
async def test_docker_executable_names_are_case_sensitive(
    monkeypatch,
    code,
) -> None:
    server = _docker_server(monkeypatch)
    test_bridge = _MemoryDockerBridge(b"alpha\n")
    monkeypatch.setattr(server, "bridge", test_bridge)
    server._READ_FACTS.clear()

    payload = json.loads((await server.run_code(None, code)).text)
    receipt = payload["metadata"]["terminal_execution_receipt"]

    assert receipt["effect"] == "unknown"
    assert receipt["cacheable"] is False
    assert receipt["workspace_generation_delta"] == 1
    assert test_bridge.trusted_context_calls == []
    assert test_bridge.trusted_shell_calls == 0
    assert test_bridge.shell_calls == 1


@pytest.mark.asyncio
async def test_docker_nested_python_read_requires_separate_trust_contract(
    monkeypatch,
) -> None:
    server = _docker_server(monkeypatch)
    test_bridge = _MemoryDockerBridge(b"alpha\n")
    monkeypatch.setattr(server, "bridge", test_bridge)
    server._READ_FACTS.clear()

    payload = json.loads(
        (
            await server.run_code(
                None,
                "python -c \"print(open('/workspace/input.txt').read())\"",
                env_content={
                    "task_id": "nested-python",
                    "task_epoch": 1,
                    "session_id": "session",
                    "session_epoch": 0,
                    "branch_id": "main",
                    "checkpoint_revision": 0,
                    "agent_id": "agent",
                    "prompt_namespace": "agent",
                    "sandbox_id": "sandbox",
                },
            )
        ).text
    )
    receipt = payload["metadata"]["terminal_execution_receipt"]

    assert receipt["potential_effect"] == "read_only"
    assert receipt["effect"] == "unknown"
    assert receipt["cacheable"] is False
    assert receipt["workspace_generation_delta"] == 1
    assert test_bridge.trusted_shell_calls == 0
    assert test_bridge.shell_calls == 1


@pytest.mark.asyncio
async def test_docker_changed_executable_epoch_revokes_read_only_receipt(
    monkeypatch,
) -> None:
    server = _docker_server(monkeypatch)
    test_bridge = _MemoryDockerBridge(b"alpha\n")
    test_bridge.mutate_context_on_trusted = True
    monkeypatch.setattr(server, "bridge", test_bridge)
    server._READ_FACTS.clear()

    payload = json.loads(
        (
            await server.run_code(
                None,
                "cat /workspace/input.txt",
                env_content={
                    "task_id": "changed-context",
                    "task_epoch": 1,
                    "session_id": "session",
                    "session_epoch": 0,
                    "branch_id": "main",
                    "checkpoint_revision": 0,
                    "agent_id": "agent",
                    "prompt_namespace": "agent",
                    "sandbox_id": "sandbox",
                },
            )
        ).text
    )
    receipt = payload["metadata"]["terminal_execution_receipt"]

    assert receipt["potential_effect"] == "read_only"
    assert receipt["effect"] == "unknown"
    assert receipt["cacheable"] is False
    assert receipt["workspace_generation_delta"] == 1
    assert receipt["effect_source"] == "docker_execution_context_changed"
    assert receipt.get("execution_context_sha256") is None


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
            "session_epoch": 0,
            "branch_id": "main",
            "checkpoint_revision": 0,
            "agent_id": "agent",
            "prompt_namespace": "agent",
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
    assert all(
        "aworld-held-fd-read" in command and any("exec 3<" in item for item in command)
        for command in test_bridge.bounded_calls
    )


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
        "session_epoch": 0,
        "branch_id": "main",
        "checkpoint_revision": 0,
        "agent_id": "agent-a",
        "prompt_namespace": "agent-a",
        "sandbox_id": "sandbox",
    }
    scope_b = {
        "task_id": "task-b",
        "task_epoch": 1,
        "session_id": "session",
        "session_epoch": 0,
        "branch_id": "main",
        "checkpoint_revision": 0,
        "agent_id": "agent-b",
        "prompt_namespace": "agent-b",
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
    assert len(test_bridge.bounded_calls) == 3


@pytest.mark.asyncio
async def test_docker_read_file_uses_attested_sanitized_helpers_and_context_cache(
    monkeypatch,
) -> None:
    server = _docker_server(monkeypatch)
    test_bridge = _MemoryDockerBridge(b"alpha\nbeta\n")
    monkeypatch.setattr(server, "bridge", test_bridge)
    server._READ_FACTS.clear()
    scope = {
        "task_id": "read-context",
        "task_epoch": 1,
        "session_id": "session",
        "session_epoch": 0,
        "branch_id": "main",
        "checkpoint_revision": 0,
        "agent_id": "agent",
        "prompt_namespace": "agent",
        "sandbox_id": "sandbox",
    }

    first = await server.read_file(
        None,
        "/workspace/input.txt",
        output="text",
        env_content=scope,
    )
    test_bridge.context_epoch += 1
    repeated = await server.read_file(
        None,
        "/workspace/input.txt",
        output="text",
        env_content=scope,
    )

    assert json.loads(first.text)["type"] == "text"
    assert json.loads(repeated.text)["type"] == "text"
    assert len(test_bridge.bounded_calls) == 2
    first_receipt = first.model_extra["metadata"]["read_observation_receipt"]
    repeated_receipt = repeated.model_extra["metadata"]["read_observation_receipt"]
    assert first_receipt["execution_context_sha256"].startswith("sha256:")
    assert (
        first_receipt["execution_context_sha256"]
        != repeated_receipt["execution_context_sha256"]
    )
    assert test_bridge.atomic_calls
    for command, kwargs in test_bridge.atomic_calls:
        assert command[1] == "-i"
        assert command[2] == "PATH=/usr/bin:/bin"
        overrides = kwargs["environment_overrides"]
        assert overrides["BASH_ENV"] == ""
        assert overrides["LD_PRELOAD"] == ""
        assert overrides["RIPGREP_CONFIG_PATH"] == ""


@pytest.mark.asyncio
async def test_docker_atomic_read_never_attributes_swapped_bytes_to_restored_epoch(
    monkeypatch,
) -> None:
    server = _docker_server(monkeypatch)
    test_bridge = _MemoryDockerBridge(b"alpha\n")
    test_bridge.atomic_swap_content = b"omega\n"
    monkeypatch.setattr(server, "bridge", test_bridge)
    server._READ_FACTS.clear()
    scope = {
        "task_id": "atomic-swap",
        "task_epoch": 1,
        "session_id": "session",
        "session_epoch": 0,
        "branch_id": "main",
        "checkpoint_revision": 0,
        "agent_id": "agent",
        "prompt_namespace": "agent",
        "sandbox_id": "sandbox",
    }

    swapped = await server.read_file(
        None,
        "/workspace/input.txt",
        output="text",
        env_content=scope,
    )
    test_bridge.atomic_swap_content = None
    restored = await server.read_file(
        None,
        "/workspace/input.txt",
        output="text",
        env_content=scope,
    )

    swapped_payload = json.loads(swapped.text)
    restored_payload = json.loads(restored.text)
    swapped_receipt = swapped.model_extra["metadata"]["read_observation_receipt"]
    restored_receipt = restored.model_extra["metadata"]["read_observation_receipt"]
    assert swapped_payload["content"] == "omega\n"
    assert restored_payload["content"] == "alpha\n"
    assert swapped_payload["contentSha256"] != restored_payload["contentSha256"]
    assert swapped_receipt["epoch"] != restored_receipt["epoch"]
    assert len(test_bridge.bounded_calls) == 2


@pytest.mark.asyncio
async def test_docker_read_file_fails_closed_for_shadowed_atomic_reader(
    monkeypatch,
) -> None:
    server = _docker_server(monkeypatch)
    test_bridge = _MemoryDockerBridge(b"alpha\n")
    test_bridge.shadowed_executables["sha256sum"] = "/workspace/bin/sha256sum"
    monkeypatch.setattr(server, "bridge", test_bridge)
    server._READ_FACTS.clear()

    with pytest.raises(RuntimeError, match="trusted file-read context"):
        await server.read_file(None, "/workspace/input.txt", output="text")

    assert test_bridge.bounded_calls == []


@pytest.mark.asyncio
async def test_docker_read_file_fails_closed_for_loader_preload(
    monkeypatch,
) -> None:
    server = _docker_server(monkeypatch)
    test_bridge = _MemoryDockerBridge(b"alpha\n")
    test_bridge.ld_so_preload_present = True
    monkeypatch.setattr(server, "bridge", test_bridge)
    server._READ_FACTS.clear()

    with pytest.raises(RuntimeError, match="trusted file-read context"):
        await server.read_file(None, "/workspace/input.txt", output="text")

    assert test_bridge.bounded_calls == []


@pytest.mark.asyncio
async def test_docker_read_file_supports_root_minimal_container_without_python(
    monkeypatch,
) -> None:
    server = _docker_server(monkeypatch)
    test_bridge = _MemoryDockerBridge(b"alpha\n")
    test_bridge.trusted_roots_mutable = True
    monkeypatch.delenv("AWORLD_DOCKER_PYTHON", raising=False)
    monkeypatch.setattr(server, "bridge", test_bridge)
    server._READ_FACTS.clear()

    result = await server.read_file(None, "/workspace/input.txt", output="text")

    assert json.loads(result.text)["content"] == "alpha\n"
    assert test_bridge.bounded_calls
    assert all("python" not in " ".join(call) for call in test_bridge.bounded_calls)
    assert all("timeout" in call for call in test_bridge.bounded_calls)


@pytest.mark.asyncio
async def test_docker_read_file_supports_default_allowed_root(
    monkeypatch,
) -> None:
    server = _docker_server(monkeypatch)
    test_bridge = _MemoryDockerBridge(b"root-file\n")
    test_bridge.allowed_directories = ["/"]
    test_bridge.workdir = "/"
    test_bridge.resolved_path = "/etc/input.txt"
    monkeypatch.setattr(server, "bridge", test_bridge)
    server._READ_FACTS.clear()

    result = await server.read_file(None, "/etc/input.txt", output="text")

    payload = json.loads(result.text)
    assert payload["content"] == "root-file\n"
    assert (
        result.model_extra["metadata"]["read_observation_receipt"]["epoch"][
            "resolved_path"
        ]
        == "/etc/input.txt"
    )


@pytest.mark.asyncio
async def test_docker_read_file_follows_symlink_only_to_allowed_target(
    monkeypatch,
) -> None:
    server = _docker_server(monkeypatch)
    test_bridge = _MemoryDockerBridge(b"target\n")
    test_bridge.resolved_path = "/workspace/target.txt"
    monkeypatch.setattr(server, "bridge", test_bridge)
    server._READ_FACTS.clear()

    result = await server.read_file(None, "/workspace/input.txt", output="text")

    payload = json.loads(result.text)
    receipt = result.model_extra["metadata"]["read_observation_receipt"]
    assert payload["content"] == "target\n"
    assert receipt["epoch"]["path"] == "/workspace/input.txt"
    assert receipt["epoch"]["resolved_path"] == "/workspace/target.txt"


@pytest.mark.asyncio
async def test_docker_read_file_rejects_symlink_target_outside_allowed_root(
    monkeypatch,
) -> None:
    server = _docker_server(monkeypatch)
    test_bridge = _MemoryDockerBridge(b"secret\n")
    test_bridge.resolved_path = "/etc/passwd"
    monkeypatch.setattr(server, "bridge", test_bridge)
    server._READ_FACTS.clear()

    with pytest.raises(RuntimeError, match="held-fd receipt"):
        await server.read_file(None, "/workspace/input.txt", output="text")


@pytest.mark.asyncio
async def test_docker_read_file_rejects_non_regular_held_fd(
    monkeypatch,
) -> None:
    server = _docker_server(monkeypatch)
    test_bridge = _MemoryDockerBridge(b"not-a-file")
    test_bridge.target_mode = 0o040755
    monkeypatch.setattr(server, "bridge", test_bridge)
    server._READ_FACTS.clear()

    with pytest.raises(RuntimeError, match="held-fd receipt"):
        await server.read_file(None, "/workspace/input.txt", output="text")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "field"),
    (
        ({"output": "base64", "offset": 2**64}, "offset"),
        ({"output": "base64", "limit": 2**64}, "limit"),
        ({"output": "text", "head": 2**64}, "head"),
        ({"output": "text", "tail": 2**64}, "tail"),
    ),
)
async def test_docker_read_file_rejects_nonportable_selector_integers(
    monkeypatch,
    kwargs,
    field,
) -> None:
    server = _docker_server(monkeypatch)
    test_bridge = _MemoryDockerBridge(b"alpha\n")
    monkeypatch.setattr(server, "bridge", test_bridge)

    with pytest.raises(ValueError, match=f"{field} exceeds portable integer bounds"):
        await server.read_file(None, "/workspace/input.txt", **kwargs)

    assert test_bridge.atomic_calls == []


@pytest.mark.asyncio
async def test_docker_large_default_read_is_atomically_bounded_and_cacheable(
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
        "session_epoch": 0,
        "branch_id": "main",
        "checkpoint_revision": 0,
        "agent_id": "agent",
        "prompt_namespace": "agent",
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
    assert first_payload["observationId"].startswith("sha256:")
    assert second_payload["type"] == "unchanged"
    assert len(test_bridge.bounded_calls) == 1
    assert all(
        command[command.index("aworld-held-fd-read") + 2] == "full"
        for command in test_bridge.bounded_calls
    )


@pytest.mark.asyncio
async def test_docker_large_tail_returns_requested_content_not_empty_fallback(
    monkeypatch,
) -> None:
    server = _docker_server(monkeypatch)
    content = b"prefix\n" + (b"x" * (128 * 1024)) + b"\nlast-line\n"
    test_bridge = _MemoryDockerBridge(content)
    monkeypatch.setattr(server, "bridge", test_bridge)
    server._READ_FACTS.clear()

    result = await server.read_file(
        None,
        "/workspace/input.txt",
        output="text",
        tail=1,
    )

    payload = json.loads(result.text)
    assert payload["content"] == "last-line\n"
    assert payload["complete"] is True
    assert payload["coverage"] == {"kind": "tail_lines", "start": 1}


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
        "session_epoch": 0,
        "branch_id": "main",
        "checkpoint_revision": 0,
        "agent_id": "agent",
        "prompt_namespace": "agent",
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
    assert callback_free_rg["cacheable"] is False
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
    assert len(test_bridge.bounded_calls) == 2


@pytest.mark.asyncio
async def test_docker_scope_separates_agent_branch_and_session_epoch(
    monkeypatch,
) -> None:
    server = _docker_server(monkeypatch)
    test_bridge = _MemoryDockerBridge(b"alpha\n")
    monkeypatch.setattr(server, "bridge", test_bridge)
    server._READ_FACTS.clear()
    scope = {
        "task_id": "scope-task",
        "task_epoch": 0,
        "session_id": "session",
        "session_epoch": 0,
        "branch_id": "main",
        "checkpoint_revision": 0,
        "agent_id": "agent-a",
        "prompt_namespace": "agent-a",
        "sandbox_id": "sandbox",
    }
    variants = (
        scope,
        {**scope, "agent_id": "agent-b", "prompt_namespace": "agent-b"},
        {**scope, "branch_id": "rewind-1"},
        {**scope, "session_epoch": 1},
    )

    results = [
        json.loads(
            (
                await server.run_code(
                    None,
                    "cat /workspace/input.txt",
                    env_content=value,
                )
            ).text
        )
        for value in variants
    ]

    assert all(
        result["metadata"].get("provider_observation_cache_hit") is not True
        for result in results
    )
    assert len(test_bridge.bounded_calls) == len(variants)


@pytest.mark.asyncio
@pytest.mark.parametrize("marker", ("\n", "\r", "\0"))
async def test_docker_direct_paths_reject_record_delimiters(
    monkeypatch,
    marker,
) -> None:
    server = _docker_server(monkeypatch)
    test_bridge = server.DockerBridge()

    with pytest.raises(ValueError, match="forbidden control character"):
        test_bridge.validate_path(f"/workspace/input{marker}.txt")
