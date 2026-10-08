"""Host-side MCP bridge for an already-running local Docker container."""

# ruff: noqa: E402 -- companion source/wheel bootstrap must precede AWorld imports.

import asyncio
import base64
from collections import OrderedDict
from dataclasses import dataclass, replace
import difflib
import fnmatch
import hashlib
import json
import mimetypes
import os
import posixpath
import re
import stat as stat_module
import sys
import time
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Mapping, Optional

# Keep package discovery private to this companion process. In particular, do
# not export PYTHONPATH to commands executed inside the attached task container.
_AWORLD_PACKAGE_ROOT = Path(__file__).resolve().parents[5]
if not (_AWORLD_PACKAGE_ROOT / "aworld" / "__init__.py").is_file():
    raise RuntimeError("Unable to resolve the owning AWorld package")
if str(_AWORLD_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_AWORLD_PACKAGE_ROOT))

from mcp.server import FastMCP
from mcp.server.fastmcp import Context
from mcp.types import TextContent
from pydantic import Field
from pydantic.fields import FieldInfo

from aworld.sandbox.terminal_receipt import (
    TerminalReadRange,
    build_terminal_execution_receipt,
    plan_terminal_execution,
    terminal_command_sha256,
)


_READ_OBSERVATION_RECEIPT_KEY = "read_observation_receipt"
_READ_OBSERVATION_SCHEMA = "aworld.read-observation/v1"
_READ_FACT_CAPACITY = 256
_READ_FACTS: "OrderedDict[tuple[tuple[str, ...], str], dict[str, Any]]" = OrderedDict()
from aworld.sandbox.artifact_observation import (
    ArtifactObservationError,
    artifact_mcp_result,
    observe_artifact_bytes,
)


_TRUSTED_DOCKER_PATH = "/usr/bin:/bin"
_TRUSTED_DOCKER_BINARY_ROOTS = ("/usr/bin", "/bin")
_TRUSTED_DOCKER_SHELL_ENTRYPOINTS = frozenset(
    {
        "/bin/bash",
        "/bin/dash",
        "/bin/sh",
        "/usr/bin/bash",
        "/usr/bin/dash",
        "/usr/bin/sh",
    }
)
_TRUSTED_SHELL_BUILTINS = frozenset(
    {":", "cd", "echo", "false", "printf", "pwd", "test", "true", "type"}
)
_UNTRUSTED_NESTED_INTERPRETERS = frozenset({"py", "python", "python3"})
_MAX_PORTABLE_SHELL_INTEGER = 2_147_483_646
_DOCKER_EXEC_ENV_OVERRIDES = {
    # These variables are consumed by the dynamic loader or a non-interactive
    # shell before command source can clear its environment. Override them at
    # ``docker exec`` itself; the trusted child is then launched via ``env -i``.
    "BASH_ENV": "",
    "CDPATH": "",
    "ENV": "",
    "GLIBC_TUNABLES": "",
    "LD_AUDIT": "",
    "LD_DEBUG": "",
    "LD_LIBRARY_PATH": "",
    "LD_PRELOAD": "",
    "LD_PROFILE": "",
    "PYTHONHOME": "",
    "PYTHONPATH": "",
    "RIPGREP_CONFIG_PATH": "",
}
_DOCKER_EXEC_CONTEXT_PROBE = r"""
set -efu
if (set -o pipefail) 2>/dev/null; then set -o pipefail; fi
PATH=/usr/bin:/bin
export PATH
readlink_bin=
stat_bin=
for candidate in /usr/bin/readlink /bin/readlink; do
    if [ -x "$candidate" ]; then readlink_bin=$candidate; break; fi
done
for candidate in /usr/bin/stat /bin/stat; do
    if [ -x "$candidate" ]; then stat_bin=$candidate; break; fi
done
[ -n "$readlink_bin" ] && [ -n "$stat_bin" ] || exit 71
policy=$1
shift
[ ! -e /etc/ld.so.preload ] || exit 77
if [ "$policy" = immutable ]; then
    for trusted_root in /bin /usr/bin /etc /lib /lib64 /usr/lib /usr/lib64 /usr/local/lib; do
        [ ! -e "$trusted_root" ] || [ ! -w "$trusted_root" ] || exit 78
    done
elif [ "$policy" != stable ]; then
    exit 81
fi
for token do
    case "$token" in
        :|cd|echo|false|printf|pwd|test|true|type) continue ;;
    esac
    case "$token" in
        */*) candidate=$token ;;
        *) candidate=$(command -v "$token") || exit 72 ;;
    esac
    case "$candidate" in /*) ;; *) exit 72 ;; esac
    canonical=$($readlink_bin -f -- "$candidate") || exit 73
    case "$canonical" in /usr/bin/*|/bin/*) ;; *) exit 74 ;; esac
    [ -f "$canonical" ] && [ -x "$canonical" ] || exit 75
    if [ "$policy" = immutable ]; then [ ! -w "$canonical" ] || exit 79; fi
    first_record=
    IFS= read -r first_record < "$canonical" || :
    case "$first_record" in '#!'*) exit 80 ;; esac
    epoch=$($stat_bin -Lc '%d|%i|%f|%s|%Y|%Z|%y|%z' -- "$canonical") || exit 76
    printf '%s\000%s\000%s\000' "$token" "$canonical" "$epoch"
done
"""

_DOCKER_HELD_FD_READER = r"""
set -efu
PATH=/usr/bin:/bin
export PATH
path=$1
mode=$2
hard_limit=$3
first=$4
second=$5
emit=$6
shift 6
[ -f "$path" ] || exit 82
exec 3< "$path" || exit 82
exec 4< "$path" || exit 82
exec 5< "$path" || exit 82
exec 7< "$path" || exit 82
resolved=$(readlink -f /proc/self/fd/3) || exit 83
resolved_hash=$(readlink -f /proc/self/fd/4) || exit 83
resolved_scan=$(readlink -f /proc/self/fd/5) || exit 83
resolved_lines=$(readlink -f /proc/self/fd/7) || exit 83
[ "$resolved" = "$resolved_hash" ] && [ "$resolved" = "$resolved_scan" ] \
    && [ "$resolved" = "$resolved_lines" ] || exit 83
allowed=0
for root do
    if [ "$root" = / ]; then
        case "$resolved" in /*) allowed=1; break ;; esac
    else
        case "$resolved" in "$root"|"$root"/*) allowed=1; break ;; esac
    fi
done
[ "$allowed" -eq 1 ] || exit 84
link_before=$(stat -c '%d|%i|%f|%s|%Y|%Z|%y|%z' "$path") || exit 85
before=$(stat -Lc '%d|%i|%f|%s|%Y|%Z|%y|%z' /proc/self/fd/3) || exit 85
before_hash=$(stat -Lc '%d|%i|%f|%s|%Y|%Z|%y|%z' /proc/self/fd/4) || exit 85
before_scan=$(stat -Lc '%d|%i|%f|%s|%Y|%Z|%y|%z' /proc/self/fd/5) || exit 85
before_lines=$(stat -Lc '%d|%i|%f|%s|%Y|%Z|%y|%z' /proc/self/fd/7) || exit 85
[ "$before" = "$before_hash" ] && [ "$before" = "$before_scan" ] \
    && [ "$before" = "$before_lines" ] || exit 85
saved_ifs=$IFS
IFS='|'
set -- $before
IFS=$saved_ifs
size=$4
count_limit=$((hard_limit + 1))
scan_complete=1
if [ "$mode" = head ] || [ "$mode" = range ]; then
    prefix_count=$(head -c "$count_limit" /proc/self/fd/5 | wc -c) || exit 86
    target_line=$first
    if [ "$mode" = range ]; then target_line=$second; fi
    if [ "$prefix_count" -lt "$size" ]; then
        newline_count=$(head -c "$count_limit" /proc/self/fd/7 | wc -l) || exit 86
        if [ "$newline_count" -lt "$target_line" ]; then scan_complete=0; fi
    fi
fi
raw_selection() {
    fd=$1
    case "$mode" in
        full) head -c "$size" "/proc/self/fd/$fd" ;;
        head) head -c "$count_limit" "/proc/self/fd/$fd" | sed -n "1,${first}p" ;;
        range) head -c "$count_limit" "/proc/self/fd/$fd" | sed -n "${first},${second}p" ;;
        tail) if [ "$scan_complete" -eq 1 ]; then tail -n "$first" "/proc/self/fd/$fd"; fi ;;
        bytes) tail -c "+$((first + 1))" "/proc/self/fd/$fd" | head -c "$second" ;;
        *) exit 87 ;;
    esac
}
selected() { raw_selection "$1" | head -c "$hard_limit"; }
raw_count=$(raw_selection 3 | head -c "$count_limit" | wc -c) || exit 88
selected_count=$raw_count
if [ "$selected_count" -gt "$hard_limit" ]; then selected_count=$hard_limit; fi
if [ "$emit" = 1 ]; then
    exec 6>&1
    content_hash=$(selected 4 | tee /proc/self/fd/6 | sha256sum) || exit 89
elif [ "$emit" = 0 ]; then
    content_hash=$(selected 4 | sha256sum) || exit 89
else
    exit 90
fi
content_hash=${content_hash%% *}
after=$(stat -Lc '%d|%i|%f|%s|%Y|%Z|%y|%z' /proc/self/fd/3) || exit 91
after_hash=$(stat -Lc '%d|%i|%f|%s|%Y|%Z|%y|%z' /proc/self/fd/4) || exit 91
after_scan=$(stat -Lc '%d|%i|%f|%s|%Y|%Z|%y|%z' /proc/self/fd/5) || exit 91
after_lines=$(stat -Lc '%d|%i|%f|%s|%Y|%Z|%y|%z' /proc/self/fd/7) || exit 91
[ "$before" = "$after" ] && [ "$before_hash" = "$after_hash" ] \
    && [ "$before_scan" = "$after_scan" ] \
    && [ "$before_lines" = "$after_lines" ] || exit 92
link_after=$(stat -c '%d|%i|%f|%s|%Y|%Z|%y|%z' "$path") || exit 93
[ "$link_before" = "$link_after" ] || exit 94
printf 'AWORLD_READ_V1\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$resolved" "$link_before" "$before" "$content_hash" \
    "$raw_count" "$selected_count" "$size" "$scan_complete" >&2
"""


@dataclass(frozen=True, slots=True)
class _DockerExecutionContext:
    env_executable: str
    shell_executable: str
    fingerprint: str
    executable_epochs: tuple[tuple[str, str, str], ...]


_DOCKER_ARTIFACT_READER = r"""
import base64
import json
import os
import stat
import sys


def fail(code):
    sys.stderr.write(code)
    raise SystemExit(73)


try:
    path = os.path.normpath(sys.argv[1])
    allowed = [os.path.normpath(value) for value in json.loads(sys.argv[2])]
    limit = int(sys.argv[3])
    if (
        not os.path.isabs(path)
        or any(character in path for character in ("\x00", "\r", "\n"))
        or os.open not in os.supports_dir_fd
        or not hasattr(os, "O_NOFOLLOW")
        or not hasattr(os, "O_DIRECTORY")
    ):
        fail("artifact_secure_fd_reader_unavailable")
    roots = [
        root
        for root in allowed
        if os.path.commonpath((path, root)) == root
    ]
    if not roots:
        fail("artifact_outside_workspace")
    root = max(roots, key=len)
    relative = os.path.relpath(path, root)
    if relative in {"", "."} or relative.startswith(".." + os.sep):
        fail("artifact_path_invalid")
    parts = [*root.split(os.sep)[1:], *relative.split(os.sep)]
    if any(part in {"", ".", ".."} for part in parts):
        fail("artifact_path_invalid")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(os.sep, directory_flags)
    try:
        for index, part in enumerate(parts):
            flags = file_flags if index == len(parts) - 1 else directory_flags
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            fail("artifact_not_regular_or_oversized")
        proc_path = f"/proc/self/fd/{descriptor}"
        canonical = os.path.realpath(proc_path)
        if canonical != path or os.path.commonpath((canonical, root)) != root:
            fail("artifact_confinement_unproven")
        chunks = []
        remaining = limit + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(descriptor)
        epoch_before = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        epoch_after = (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if epoch_before != epoch_after or len(data) != before.st_size:
            fail("artifact_changed_during_read")
        payload = {
            "data": base64.b64encode(data).decode("ascii"),
            "device": before.st_dev,
            "inode": before.st_ino,
            "mode": before.st_mode,
            "size": before.st_size,
            "mtime_ns": before.st_mtime_ns,
            "ctime_ns": before.st_ctime_ns,
        }
        sys.stdout.write(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    finally:
        os.close(descriptor)
except SystemExit:
    raise
except BaseException:
    fail("artifact_secure_fd_reader_failed")
"""


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


class DockerCommandError(RuntimeError):
    def __init__(self, message: str, *, return_code: int, stdout: bytes, stderr: bytes):
        super().__init__(message)
        self.return_code = return_code
        self.stdout = stdout
        self.stderr = stderr


class DockerBridge:
    """Execute commands and file operations inside one fixed container."""

    def __init__(self) -> None:
        self.container = _required_env("AWORLD_DOCKER_CONTAINER")
        self.docker_binary = _required_env("AWORLD_DOCKER_BINARY")
        self.workdir = os.environ.get("AWORLD_DOCKER_WORKDIR", "/").strip() or "/"
        self.shell = (
            os.environ.get("AWORLD_DOCKER_SHELL", "/bin/sh").strip() or "/bin/sh"
        )
        raw_allowed = os.environ.get("AWORLD_DOCKER_ALLOWED_DIRECTORIES", "")
        try:
            parsed_allowed = json.loads(raw_allowed) if raw_allowed else [self.workdir]
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "AWORLD_DOCKER_ALLOWED_DIRECTORIES must be a JSON list"
            ) from exc
        if not isinstance(parsed_allowed, list) or not parsed_allowed:
            raise RuntimeError(
                "AWORLD_DOCKER_ALLOWED_DIRECTORIES must be a non-empty JSON list"
            )
        self.allowed_directories = [
            self._normalize_absolute(str(path)) for path in parsed_allowed
        ]
        self.max_output_bytes = int(
            os.environ.get("AWORLD_DOCKER_MAX_OUTPUT_BYTES", "1048576")
        )
        self.output_head_bytes = int(
            os.environ.get(
                "AWORLD_DOCKER_OUTPUT_HEAD_BYTES", str(self.max_output_bytes // 2)
            )
        )
        if self.max_output_bytes < 1:
            raise RuntimeError("AWORLD_DOCKER_MAX_OUTPUT_BYTES must be positive")
        if not 0 <= self.output_head_bytes <= self.max_output_bytes:
            raise RuntimeError(
                "AWORLD_DOCKER_OUTPUT_HEAD_BYTES must be between 0 and AWORLD_DOCKER_MAX_OUTPUT_BYTES"
            )
        self.max_read_bytes = min(
            max(
                int(os.environ.get("AWORLD_FILESYSTEM_MAX_READ_BYTES", "1048576")),
                4096,
            ),
            16 * 1024 * 1024,
        )
        self.max_binary_bytes = min(
            max(
                int(os.environ.get("AWORLD_FILESYSTEM_MAX_BINARY_BYTES", "1048576")),
                4096,
            ),
            16 * 1024 * 1024,
        )
        artifact_directory = os.environ.get(
            "AWORLD_DOCKER_ARTIFACT_DIRECTORY", ""
        ).strip()
        self.artifact_directory = (
            Path(artifact_directory).resolve() if artifact_directory else None
        )
        if self.artifact_directory:
            self.artifact_directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _normalize_absolute(path: str) -> str:
        if not PurePosixPath(path).is_absolute():
            raise ValueError(f"Container path must be absolute: {path!r}")
        return posixpath.normpath(path)

    def validate_path(self, path: str) -> str:
        if any(marker in path for marker in ("\0", "\r", "\n")):
            raise ValueError("Container path contains a forbidden control character")
        normalized = self._normalize_absolute(path)
        for allowed in self.allowed_directories:
            if posixpath.commonpath([normalized, allowed]) == allowed:
                return normalized
        raise ValueError(
            f"Path {path!r} is outside allowed container directories: "
            f"{', '.join(self.allowed_directories)}"
        )

    async def execute(
        self,
        command: list[str],
        *,
        input_bytes: Optional[bytes] = None,
        timeout: int = 30,
        workdir: Optional[str] = None,
        environment_overrides: Mapping[str, str] | None = None,
    ) -> tuple[int, bytes, bytes, bool]:
        args = [self.docker_binary, "exec"]
        if input_bytes is not None:
            args.append("-i")
        if workdir:
            args.extend(["-w", workdir])
        for key, value in sorted((environment_overrides or {}).items()):
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) is None:
                raise ValueError(f"Invalid Docker environment key: {key!r}")
            if any(marker in value for marker in ("\0", "\r", "\n")):
                raise ValueError(f"Invalid Docker environment value for {key!r}")
            args.extend(["--env", f"{key}={value}"])
        args.extend([self.container, *command])
        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE if input_bytes is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input_bytes), timeout=timeout
            )
            return process.returncode or 0, stdout, stderr, False
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            return -1, b"", f"Command timed out after {timeout} seconds".encode(), True

    async def shell_command(
        self,
        code: str,
        *,
        timeout: int = 30,
        workdir: Optional[str] = None,
        input_bytes: Optional[bytes] = None,
    ) -> tuple[int, bytes, bytes, bool]:
        return await self.execute(
            [self.shell, "-lc", code],
            input_bytes=input_bytes,
            timeout=timeout,
            workdir=workdir or self.workdir,
        )

    def bound_output(self, data: bytes, *, label: str) -> tuple[bytes, dict[str, Any]]:
        """Return a deterministic inline view and persist full bytes when needed."""
        digest = hashlib.sha256(data).hexdigest()
        metadata: dict[str, Any] = {
            "raw_bytes": len(data),
            "inline_bytes": len(data),
            "offloaded_bytes": 0,
            "content_sha256": digest,
            "output_truncated": False,
            "truncation_strategy": "none",
            "artifact_ref": None,
            "head_bytes": len(data),
            "tail_bytes": 0,
        }
        if len(data) <= self.max_output_bytes:
            return data, metadata

        tail_bytes = self.max_output_bytes - self.output_head_bytes
        inline = data[: self.output_head_bytes]
        if tail_bytes:
            inline += data[-tail_bytes:]
        metadata.update(
            {
                "inline_bytes": len(inline),
                "offloaded_bytes": len(data) - len(inline),
                "output_truncated": True,
                "truncation_strategy": "head_tail_artifact",
                "head_bytes": self.output_head_bytes,
                "tail_bytes": tail_bytes,
            }
        )
        if self.artifact_directory:
            safe_label = "".join(
                ch if ch.isalnum() or ch in "-_" else "-" for ch in label
            )[:48]
            artifact_path = self.artifact_directory / f"{safe_label}-{digest}.bin"
            if not artifact_path.exists():
                artifact_path.write_bytes(data)
            metadata["artifact_ref"] = str(artifact_path)
        return inline, metadata

    @staticmethod
    def decode_inline_text(data: bytes, metadata: dict[str, Any]) -> str:
        if not metadata.get("output_truncated"):
            return data.decode("utf-8", errors="replace")
        head_bytes = int(metadata.get("head_bytes") or 0)
        head = data[:head_bytes].decode("utf-8", errors="replace")
        tail = data[head_bytes:].decode("utf-8", errors="replace")
        marker = (
            f"\n... [{metadata.get('offloaded_bytes', 0)} bytes offloaded; "
            f"sha256={metadata.get('content_sha256')}] ...\n"
        )
        return head + marker + tail

    def validate_artifact_ref(self, artifact_ref: str) -> Path:
        if self.artifact_directory is None:
            raise ValueError("Tool output artifact storage is not configured")
        artifact = Path(artifact_ref).resolve()
        if artifact.parent != self.artifact_directory or not artifact.is_file():
            raise ValueError(
                "artifact_ref is not a Tool output artifact from this sandbox"
            )
        return artifact

    async def require_success(
        self,
        command: list[str],
        *,
        input_bytes: Optional[bytes] = None,
        timeout: int = 30,
    ) -> bytes:
        return_code, stdout, stderr, _ = await self.execute(
            command,
            input_bytes=input_bytes,
            timeout=timeout,
        )
        if return_code != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise DockerCommandError(
                detail or f"docker exec failed with code {return_code}",
                return_code=return_code,
                stdout=stdout,
                stderr=stderr,
            )
        return stdout

    async def observe_artifact(
        self,
        path: str,
        *,
        expected_mime: str | None = None,
        framework_scope: dict[str, Any] | None = None,
    ):
        """Read one stable, regular, non-symlink image from the container."""

        valid_path = self.validate_path(path)
        if any(character in valid_path for character in ("\x00", "\r", "\n")):
            raise ArtifactObservationError("container artifact path is invalid")
        try:
            configured_limit = int(
                os.environ.get("AWORLD_ARTIFACT_OBSERVATION_MAX_BYTES", "5242880")
            )
        except ValueError:
            configured_limit = 5 * 1024 * 1024
        limit = max(1, min(configured_limit, 16 * 1024 * 1024))
        python = os.environ.get("AWORLD_DOCKER_PYTHON", "python3")
        return_code, stdout, stderr, timed_out = await self.execute(
            [
                python,
                "-I",
                "-c",
                _DOCKER_ARTIFACT_READER,
                valid_path,
                json.dumps(self.allowed_directories, separators=(",", ":")),
                str(limit),
            ],
            timeout=30,
            workdir=self.workdir,
        )
        if timed_out or return_code != 0 or stderr:
            raise ArtifactObservationError(
                "container cannot prove secure artifact confinement"
            )
        try:
            payload = json.loads(stdout.decode("utf-8", errors="strict"))
            expected_keys = {
                "data",
                "device",
                "inode",
                "mode",
                "size",
                "mtime_ns",
                "ctime_ns",
            }
            if not isinstance(payload, dict) or set(payload) != expected_keys:
                raise ValueError("invalid payload keys")
            for key in expected_keys - {"data"}:
                if isinstance(payload[key], bool) or not isinstance(payload[key], int):
                    raise TypeError("invalid stat value")
            if payload["size"] < 0 or payload["size"] > limit:
                raise ValueError("invalid artifact size")
            data = base64.b64decode(payload["data"], validate=True)
            if len(data) != payload["size"]:
                raise ValueError("artifact size mismatch")
        except (TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
            raise ArtifactObservationError(
                "container artifact receipt is invalid"
            ) from exc
        epoch = {
            key: payload[key]
            for key in ("device", "inode", "mode", "size", "mtime_ns", "ctime_ns")
        }
        return observe_artifact_bytes(
            data,
            suffix=PurePosixPath(valid_path).suffix,
            expected_mime=expected_mime,
            path_key="sha256:" + hashlib.sha256(valid_path.encode()).hexdigest(),
            file_epoch=(
                "sha256:"
                + hashlib.sha256(
                    json.dumps(epoch, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
            ),
            framework_scope=framework_scope,
            max_bytes=limit,
        )


bridge = DockerBridge()
mcp = FastMCP(
    "docker-sandbox-server",
    log_level=os.environ.get("MCP_LOG_LEVEL", "WARNING"),
    instructions="Terminal and filesystem tools scoped to one local Docker container.",
)


def _text(
    payload: Any,
    *,
    metadata: Optional[dict[str, Any]] = None,
) -> TextContent:
    if not isinstance(payload, str):
        payload = json.dumps(payload, ensure_ascii=False)
    return TextContent(type="text", text=payload, **{"metadata": metadata or {}})


def _literal_container_write_paths(
    code: str,
    plan: Any,
) -> list[str] | None:
    if plan.effect != "mutating" or not plan.write_paths:
        return None
    shell_changes_directory = plan.language == "shell" and bool(
        re.search(r"(?:^|[;&|]\s*)cd\s+", code)
    )
    paths: list[str] = []
    for value in plan.write_paths:
        if "\n" in value or "\r" in value:
            return None
        candidate = value
        if not PurePosixPath(candidate).is_absolute():
            if shell_changes_directory:
                return None
            candidate = posixpath.join(bridge.workdir, candidate)
        try:
            paths.append(bridge.validate_path(candidate))
        except ValueError:
            return None
    return paths or None


async def _container_path_states(
    paths: list[str] | None,
    *,
    timeout: int,
) -> tuple[str, ...] | None:
    if not paths:
        return None
    script = (
        'for p do if [ -e "$p" ] || [ -L "$p" ]; then '
        "stat -c '%f|%s|%y|%z|%i|%N' -- \"$p\" || exit 7; "
        "else printf '%s\\n' missing; fi; done"
    )
    return_code, stdout, _stderr, timed_out = await bridge.execute(
        [bridge.shell, "-c", script, "aworld-stat", *paths],
        timeout=max(1, min(timeout, 5)),
        workdir=bridge.workdir,
    )
    if timed_out or return_code != 0:
        return None
    states = tuple(stdout.decode("utf-8", errors="replace").splitlines())
    return states if len(states) == len(paths) else None


def _framework_scope(env_content: Any) -> tuple[str, ...]:
    if not isinstance(env_content, Mapping):
        return ("",) * 9
    epoch = env_content.get("task_epoch")
    session_epoch = env_content.get("session_epoch")
    checkpoint_revision = env_content.get("checkpoint_revision")
    return (
        str(env_content.get("task_id") or "").strip(),
        "" if epoch is None or isinstance(epoch, bool) else str(epoch),
        str(env_content.get("session_id") or "").strip(),
        (
            str(session_epoch)
            if isinstance(session_epoch, int)
            and not isinstance(session_epoch, bool)
            and session_epoch >= 0
            else ""
        ),
        str(env_content.get("branch_id") or "").strip(),
        (
            str(checkpoint_revision)
            if isinstance(checkpoint_revision, int)
            and not isinstance(checkpoint_revision, bool)
            and checkpoint_revision >= 0
            else ""
        ),
        str(env_content.get("agent_id") or "").strip(),
        str(env_content.get("prompt_namespace") or "").strip(),
        str(env_content.get("sandbox_id") or "").strip(),
    )


def _framework_scope_is_complete(scope: tuple[str, ...]) -> bool:
    return all(bool(value) for value in scope)


def _container_epoch_authority() -> str:
    return (
        "docker:sha256:"
        + hashlib.sha256(bridge.container.encode("utf-8", errors="replace")).hexdigest()
    )


def _plan_read_paths(plan: Any) -> list[str] | None:
    if not plan.read_paths:
        return []
    if not plan.command_cwd_safe:
        return None
    workdir = bridge.workdir
    if plan.command_cwd:
        workdir = (
            posixpath.normpath(plan.command_cwd)
            if PurePosixPath(plan.command_cwd).is_absolute()
            else posixpath.normpath(posixpath.join(workdir, plan.command_cwd))
        )
    try:
        workdir = bridge.validate_path(workdir)
    except ValueError:
        return None
    paths: list[str] = []
    for raw_path in plan.read_paths:
        if any(marker in raw_path for marker in ("\0", "\n", "\r")):
            return None
        candidate = (
            posixpath.normpath(raw_path)
            if PurePosixPath(raw_path).is_absolute()
            else posixpath.normpath(posixpath.join(workdir, raw_path))
        )
        try:
            paths.append(bridge.validate_path(candidate))
        except ValueError:
            return None
    return paths


def _trusted_system_binary(path: str) -> bool:
    if not PurePosixPath(path).is_absolute():
        return False
    normalized = posixpath.normpath(path)
    return any(
        posixpath.commonpath((normalized, root)) == root
        for root in _TRUSTED_DOCKER_BINARY_ROOTS
    )


def _parse_execution_context_probe(
    *,
    stdout: bytes,
    tokens: tuple[str, ...],
    env_executable: str,
    policy: str,
) -> _DockerExecutionContext | None:
    if len(stdout) > 32 * 1024:
        return None
    fields = stdout.split(b"\0")
    if not fields or fields[-1] != b"" or len(fields) != len(tokens) * 3 + 1:
        return None
    epochs: list[tuple[str, str, str]] = []
    try:
        for index, expected_token in enumerate(tokens):
            token = fields[index * 3].decode("utf-8", errors="strict")
            canonical = fields[index * 3 + 1].decode("utf-8", errors="strict")
            epoch = fields[index * 3 + 2].decode("utf-8", errors="strict")
            if token != expected_token or len(canonical) > 1024 or len(epoch) > 1024:
                return None
            if posixpath.normpath(canonical) != canonical:
                return None
            if not _trusted_system_binary(canonical):
                return None
            epoch_fields = epoch.split("|", 7)
            if len(epoch_fields) != 8:
                return None
            for field_index, numeric in enumerate(epoch_fields[:6]):
                int(numeric, 16 if field_index == 2 else 10)
            epochs.append((token, canonical, epoch))
    except (UnicodeDecodeError, ValueError, IndexError):
        return None
    payload = {
        "authority": _container_epoch_authority(),
        "path": _TRUSTED_DOCKER_PATH,
        "workdir": bridge.workdir,
        "policy": policy,
        "epochs": epochs,
    }
    fingerprint = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    )
    return _DockerExecutionContext(
        env_executable=env_executable,
        shell_executable=bridge.shell,
        fingerprint=fingerprint,
        executable_epochs=tuple(epochs),
    )


async def _trusted_docker_helper_context(
    *,
    executable_tokens: tuple[str, ...],
    timeout: int,
    require_immutable: bool = True,
) -> _DockerExecutionContext | None:
    """Attest fixed-PATH helpers used by one provider-owned read operation.

    ``run_code`` requires immutable system binary/loader roots. Provider-owned
    held-fd reads may request the compatibility policy for root/minimal images:
    every helper identity is still bound before/after, loader preloads remain
    forbidden, and selected bytes are authenticated by a fresh content hash.
    """

    if (
        posixpath.normpath(bridge.shell) not in _TRUSTED_DOCKER_SHELL_ENTRYPOINTS
        or not _trusted_system_binary(bridge.shell)
        or len(executable_tokens) > 16
    ):
        return None
    for token in executable_tokens:
        if (
            not token
            or len(token) > 512
            or any(marker in token for marker in ("\0", "\r", "\n", "$", "`"))
            or any(marker in token for marker in ("*", "?", "[", "]"))
        ):
            return None
    command_tokens = tuple(
        token
        for token in executable_tokens
        if not ("/" not in token and token in _TRUSTED_SHELL_BUILTINS)
    )
    for env_executable in ("/usr/bin/env", "/bin/env"):
        policy = "immutable" if require_immutable else "stable"
        tokens = (env_executable, bridge.shell, "readlink", "stat", *command_tokens)
        return_code, stdout, stderr, timed_out = await bridge.execute(
            [
                env_executable,
                "-i",
                f"PATH={_TRUSTED_DOCKER_PATH}",
                bridge.shell,
                "-c",
                _DOCKER_EXEC_CONTEXT_PROBE,
                "aworld-execution-context",
                policy,
                *tokens,
            ],
            timeout=max(1, min(timeout, 5)),
            workdir=bridge.workdir,
            environment_overrides=_DOCKER_EXEC_ENV_OVERRIDES,
        )
        if timed_out or return_code != 0 or stderr:
            continue
        context = _parse_execution_context_probe(
            stdout=stdout,
            tokens=tokens,
            env_executable=env_executable,
            policy=policy,
        )
        if context is not None:
            return context
    return None


async def _trusted_docker_execution_context(
    *,
    plan: Any,
    timeout: int,
) -> _DockerExecutionContext | None:
    """Prove the exact, sanitized context used for a Docker run-code read.

    A parser-level read classification is insufficient when the command runs
    through a login shell with inherited PATH/configuration. This authority
    accepts only ordinary Shell commands, resolves every executable under a
    fixed system PATH, records canonical file epochs, and executes through an
    empty non-login environment. Nested interpreters and callback-bearing
    commands stay unknown until they have their own equally strong contract.
    """

    if (
        plan.effect != "read_only"
        or not plan.parsed
        or not plan.read_set_complete
        or plan.language != "shell"
        or plan.nested_languages
        or plan.callback_kinds
        or not plan.command_cwd_safe
        or not getattr(plan, "executable_set_complete", False)
    ):
        return None
    raw_tokens = tuple(getattr(plan, "executable_tokens", ()) or ())
    if any(
        PurePosixPath(token).name in _UNTRUSTED_NESTED_INTERPRETERS
        for token in raw_tokens
    ):
        return None
    return await _trusted_docker_helper_context(
        executable_tokens=(*raw_tokens, "timeout"),
        timeout=timeout,
    )


async def _execute_in_trusted_context(
    context: _DockerExecutionContext,
    code: str,
    *,
    timeout: int,
    arguments: tuple[str, ...] = (),
) -> tuple[int, bytes, bytes, bool]:
    return await bridge.execute(
        [
            context.env_executable,
            "-i",
            f"PATH={_TRUSTED_DOCKER_PATH}",
            "timeout",
            "-s",
            "KILL",
            str(max(1, min(timeout - 1 if timeout > 1 else 1, 29))),
            context.shell_executable,
            "-c",
            code,
            "aworld-trusted-command",
            *arguments,
        ],
        timeout=timeout,
        workdir=bridge.workdir,
        environment_overrides=_DOCKER_EXEC_ENV_OVERRIDES,
    )


async def _held_fd_container_file_read(
    context: _DockerExecutionContext,
    *,
    path: str,
    request: Mapping[str, Any],
    emit: bool,
    timeout: int,
) -> tuple[
    bytes,
    TerminalReadRange,
    bool,
    dict[str, Any],
    dict[str, Any],
    str,
]:
    mode = str(request.get("mode") or "")
    hard_limit = int(request.get("hard_limit") or 0)
    if mode == "head":
        first, second = int(request["end"]), 0
    elif mode == "range":
        first, second = int(request["start"]), int(request["end"])
    elif mode == "tail":
        first, second = int(request["count"]), 0
    elif mode == "bytes":
        first, second = int(request["offset"]), int(request["limit"])
    elif mode == "full":
        first = second = 0
    else:
        raise ValueError("unsupported held-fd read mode")
    if hard_limit < 1 or hard_limit > 16 * 1024 * 1024:
        raise ValueError("invalid held-fd read limit")
    if min(first, second) < 0 or max(first, second) > _MAX_PORTABLE_SHELL_INTEGER:
        raise ValueError("held-fd selector exceeds portable integer bounds")
    return_code, stdout, stderr, timed_out = await bridge.execute(
        [
            context.env_executable,
            "-i",
            f"PATH={_TRUSTED_DOCKER_PATH}",
            "timeout",
            "-s",
            "KILL",
            str(max(1, min(timeout - 1 if timeout > 1 else 1, 29))),
            context.shell_executable,
            "-c",
            _DOCKER_HELD_FD_READER,
            "aworld-held-fd-read",
            path,
            mode,
            str(hard_limit),
            str(first),
            str(second),
            "1" if emit else "0",
            *bridge.allowed_directories,
        ],
        timeout=timeout,
        workdir=bridge.workdir,
        environment_overrides=_DOCKER_EXEC_ENV_OVERRIDES,
    )
    if timed_out or return_code != 0 or len(stdout) > hard_limit:
        raise RuntimeError("container held-fd file reader failed")
    try:
        stderr_text = stderr.decode("utf-8", errors="strict")
        lines = stderr_text.splitlines()
        if len(lines) != 1:
            raise ValueError("invalid held-fd metadata lines")
        fields = lines[0].split("\t")
        if len(fields) != 9 or fields[0] != "AWORLD_READ_V1":
            raise ValueError("invalid held-fd metadata")
        (
            _,
            resolved_path,
            link_record,
            target_record,
            digest,
            raw_text,
            selected_text,
            size_text,
            scan_complete_text,
        ) = fields
        if (
            len(resolved_path) > 1024
            or bridge.validate_path(resolved_path) != posixpath.normpath(resolved_path)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise ValueError("invalid held-fd identity")
        link = link_record.split("|", 7)
        target = target_record.split("|", 7)
        if len(link) != 8 or len(target) != 8:
            raise ValueError("invalid held-fd epoch")
        if not stat_module.S_ISREG(int(target[2], 16)):
            raise ValueError("held-fd target is not a regular file")
        raw_count = int(raw_text)
        selected_count = int(selected_text)
        total_bytes = int(size_text)
        if scan_complete_text not in {"0", "1"}:
            raise ValueError("invalid held-fd scan completeness")
        scan_complete = scan_complete_text == "1"
        if min(raw_count, selected_count, total_bytes) < 0:
            raise ValueError("invalid held-fd sizes")
        if selected_count != min(raw_count, hard_limit):
            raise ValueError("invalid held-fd selection")
        content_sha256 = "sha256:" + digest
        if emit:
            if len(stdout) != selected_count or (
                "sha256:" + hashlib.sha256(stdout).hexdigest() != content_sha256
            ):
                raise ValueError("held-fd bytes do not match receipt")
        elif stdout:
            raise ValueError("hash-only held-fd read returned bytes")
        if int(target[3]) != total_bytes:
            raise ValueError("held-fd size mismatch")
    except (UnicodeDecodeError, ValueError, IndexError) as exc:
        raise RuntimeError("container held-fd receipt is invalid") from exc
    if mode == "bytes":
        effective_offset = min(first, total_bytes)
        next_offset = effective_offset + selected_count
        coverage = TerminalReadRange("byte_range", effective_offset, next_offset)
        complete = scan_complete and next_offset >= total_bytes
        metadata: dict[str, Any] = {
            "offset": first,
            "nextOffset": next_offset,
            "returnedBytes": selected_count,
            "totalBytes": total_bytes,
            "truncated": not complete,
            "complete": complete,
        }
    elif mode == "head":
        coverage = TerminalReadRange("line_range", 1, first)
        complete = scan_complete and raw_count <= hard_limit
        metadata = {
            "complete": complete,
            "returnedBytes": selected_count,
            "totalBytes": total_bytes,
        }
    elif mode == "range":
        coverage = TerminalReadRange("line_range", first, second)
        complete = scan_complete and raw_count <= hard_limit
        metadata = {
            "complete": complete,
            "returnedBytes": selected_count,
            "totalBytes": total_bytes,
        }
    elif mode == "tail":
        coverage = TerminalReadRange("tail_lines", first, None)
        complete = scan_complete and raw_count <= hard_limit
        metadata = {
            "complete": complete,
            "returnedBytes": selected_count,
            "totalBytes": total_bytes,
        }
    else:
        coverage = (
            TerminalReadRange("full")
            if total_bytes <= hard_limit
            else TerminalReadRange("byte_range", 0, selected_count)
        )
        complete = (
            scan_complete and raw_count <= hard_limit and raw_count == total_bytes
        )
        metadata = {
            "complete": complete,
            "returnedBytes": selected_count,
            "totalBytes": total_bytes,
        }
    epoch_record = {
        "path": path,
        "resolved_path": resolved_path,
        "link_inode": int(link[1]),
        "link_mtime_ns": int(link[4]) * 1_000_000_000,
        "mode": int(target[2], 16),
        "size": int(target[3]),
        "mtime_ns": int(target[4]) * 1_000_000_000,
        "ctime_ns": int(target[5]) * 1_000_000_000,
        "inode": int(target[1]),
        "authority": _container_epoch_authority(),
    }
    epoch_record["fingerprint"] = (
        "sha256:"
        + hashlib.sha256(
            (resolved_path + "\0" + link_record + "\0" + target_record).encode("utf-8")
        ).hexdigest()
    )
    return stdout, coverage, complete, metadata, epoch_record, content_sha256


def _plan_read_ranges(plan: Any) -> tuple[TerminalReadRange, ...]:
    ranges = tuple(getattr(plan, "read_ranges", ()) or ())
    if len(ranges) != len(plan.read_paths):
        return tuple(TerminalReadRange("full") for _ in plan.read_paths)
    return ranges


def _controlled_run_code_read_request(
    plan: Any,
    *,
    hard_limit: int,
) -> dict[str, Any] | None:
    if (
        plan.language != "shell"
        or plan.effect != "read_only"
        or not plan.parsed
        or not plan.read_set_complete
        or plan.nested_languages
        or plan.callback_kinds
        or not plan.command_cwd_safe
        or not plan.read_projection_reusable
        or len(plan.read_paths) != 1
        or len(plan.read_ranges) != 1
    ):
        return None
    coverage = plan.read_ranges[0]
    if coverage.kind == "full":
        return {"mode": "full", "hard_limit": hard_limit}
    if (
        coverage.kind == "line_range"
        and coverage.start is not None
        and coverage.end is not None
    ):
        if coverage.start == 1:
            return {"mode": "head", "hard_limit": hard_limit, "end": coverage.end}
        return {
            "mode": "range",
            "hard_limit": hard_limit,
            "start": coverage.start,
            "end": coverage.end,
        }
    if coverage.kind == "tail_lines" and coverage.start is not None:
        return {"mode": "tail", "hard_limit": hard_limit, "count": coverage.start}
    if (
        coverage.kind == "byte_range"
        and coverage.start is not None
        and coverage.end is not None
        and coverage.end > coverage.start
    ):
        return {
            "mode": "bytes",
            "hard_limit": hard_limit,
            "offset": coverage.start,
            "limit": coverage.end - coverage.start,
        }
    return None


def _coverage_contains(
    stored: TerminalReadRange,
    requested: TerminalReadRange,
) -> bool:
    if stored == requested:
        return True
    if stored.kind == "full" and requested.kind in {
        "full",
        "line_range",
        "byte_range",
        "tail_lines",
        "tail_bytes",
    }:
        return True
    if stored.kind != requested.kind:
        return False
    if stored.kind in {"line_range", "byte_range"}:
        return (
            stored.start is not None
            and stored.end is not None
            and requested.start is not None
            and requested.end is not None
            and stored.start <= requested.start
            and stored.end >= requested.end
        )
    if stored.kind in {"tail_lines", "tail_bytes"}:
        return (stored.start or 0) >= (requested.start or 0)
    return False


def _fact_operation_key(*, kind: str, value: Any) -> str:
    encoded = json.dumps(
        {"kind": kind, "value": value},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _lookup_read_fact(
    *,
    scope: tuple[str, ...],
    operation_key: str,
    paths: list[str],
    ranges: tuple[TerminalReadRange, ...],
    epochs: list[dict[str, Any]],
    allow_overlap: bool,
    representation: str,
    execution_context_sha256: str,
) -> dict[str, Any] | None:
    if (
        not _framework_scope_is_complete(scope)
        or len(paths) != len(ranges)
        or len(paths) != len(epochs)
        or not paths
    ):
        return None
    exact_key = (scope, operation_key)
    exact = _READ_FACTS.get(exact_key)
    if (
        exact is not None
        and exact.get("epochs") == epochs
        and exact.get("representation") == representation
        and exact.get("execution_context_sha256") == execution_context_sha256
    ):
        _READ_FACTS.move_to_end(exact_key)
        return exact
    if not allow_overlap or len(paths) != 1:
        return None
    for key in reversed(_READ_FACTS):
        candidate = _READ_FACTS[key]
        if (
            key[0] == scope
            and candidate.get("coverage_complete") is True
            and candidate.get("representation") == representation
            and candidate.get("execution_context_sha256") == execution_context_sha256
            and candidate.get("paths") == paths
            and candidate.get("epochs") == epochs
            and len(candidate.get("ranges") or ()) == 1
            and _coverage_contains(candidate["ranges"][0], ranges[0])
        ):
            _READ_FACTS.move_to_end(key)
            return candidate
    return None


def _store_read_fact(
    *,
    scope: tuple[str, ...],
    operation_key: str,
    paths: list[str],
    ranges: tuple[TerminalReadRange, ...],
    epochs: list[dict[str, Any]],
    content_sha256: str,
    coverage_complete: bool,
    representation: str,
    execution_context_sha256: str,
    projection_content_sha256: str | None = None,
) -> dict[str, Any] | None:
    if (
        not _framework_scope_is_complete(scope)
        or len(paths) != len(ranges)
        or len(paths) != len(epochs)
        or not paths
    ):
        return None
    observation_id = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                {
                    "scope": scope,
                    "operation": operation_key,
                    "epochs": epochs,
                    "ranges": [item.to_dict() for item in ranges],
                    "content": content_sha256,
                    "execution_context": execution_context_sha256,
                    "projection_content": projection_content_sha256,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    )
    fact = {
        "observation_id": observation_id,
        "content_sha256": content_sha256,
        "paths": list(paths),
        "ranges": tuple(ranges),
        "epochs": [dict(epoch) for epoch in epochs],
        "coverage_complete": bool(coverage_complete),
        "representation": representation,
        "execution_context_sha256": execution_context_sha256,
        "projection_content_sha256": projection_content_sha256,
        "source_checkpoint_revision": int(scope[5]),
    }
    key = (scope, operation_key)
    _READ_FACTS[key] = fact
    _READ_FACTS.move_to_end(key)
    while len(_READ_FACTS) > _READ_FACT_CAPACITY:
        _READ_FACTS.popitem(last=False)
    return fact


def _compact_read_fact_payload(
    fact: Mapping[str, Any],
    *,
    coverage: TerminalReadRange,
) -> dict[str, Any]:
    return {
        "type": "unchanged",
        "observationId": fact["observation_id"],
        "contentSha256": fact["content_sha256"],
        "coverage": coverage.to_dict(),
        "message": "unchanged since the referenced observation; reuse retained facts",
    }


@mcp.tool(
    description=(
        "Execute Shell commands, or explicit raw Python, inside the attached "
        "Docker container. Shell remains the default and may invoke Python."
    )
)
async def run_code(
    ctx: Context,
    code: str = Field(
        description="Shell command or raw Python source, according to language"
    ),
    timeout: int = Field(default=30, description="Command timeout in seconds"),
    output_format: str = Field(
        default="markdown", description="markdown, json, or text"
    ),
    language: Literal["shell", "python"] = Field(
        default="shell",
        description="Use 'python' only when code itself is raw Python source",
    ),
    env_content: Optional[dict[str, Any]] = Field(
        default=None,
        description="Framework-injected task scope; hidden from the model schema",
    ),
) -> TextContent:
    del ctx, output_format
    if isinstance(timeout, FieldInfo):
        timeout = timeout.default
    if isinstance(language, FieldInfo):
        language = language.default
    if isinstance(env_content, FieldInfo):
        env_content = env_content.default
    started = time.monotonic()
    if language not in {"shell", "python"}:
        raise ValueError("language must be either 'shell' or 'python'")
    potential_plan = plan_terminal_execution(code, language=language)
    execution_plan = potential_plan
    effect_source = "parser_contract"
    execution_context: _DockerExecutionContext | None = None
    controlled_read_request: dict[str, Any] | None = None
    controlled_read_probe: (
        tuple[
            bytes,
            TerminalReadRange,
            bool,
            dict[str, Any],
            dict[str, Any],
            str,
        ]
        | None
    ) = None
    controlled_helper_tokens: tuple[str, ...] = ()
    read_paths: list[str] = []
    if potential_plan.effect == "read_only":
        planned_paths = _plan_read_paths(potential_plan)
        if planned_paths is None:
            execution_plan = replace(
                potential_plan,
                effect="unknown",
                cacheable=False,
                read_projection_reusable=False,
            )
            effect_source = "untrusted_execution_context"
        else:
            candidate_request = _controlled_run_code_read_request(
                potential_plan,
                hard_limit=int(getattr(bridge, "max_output_bytes", 1024 * 1024)),
            )
            if candidate_request is not None and len(planned_paths) == 1:
                controlled_helper_tokens = (
                    *tuple(getattr(potential_plan, "executable_tokens", ()) or ()),
                    "head",
                    "sed",
                    "sha256sum",
                    "tail",
                    "tee",
                    "timeout",
                    "wc",
                )
                execution_context = await _trusted_docker_helper_context(
                    executable_tokens=controlled_helper_tokens,
                    timeout=timeout,
                    require_immutable=False,
                )
                if execution_context is not None:
                    try:
                        probe = await _held_fd_container_file_read(
                            execution_context,
                            path=planned_paths[0],
                            request=candidate_request,
                            emit=False,
                            timeout=timeout,
                        )
                    except (RuntimeError, ValueError):
                        execution_context = None
                    else:
                        if probe[2] and probe[1] == potential_plan.read_ranges[0]:
                            controlled_read_request = candidate_request
                            controlled_read_probe = probe
            if controlled_read_probe is None:
                execution_context = await _trusted_docker_execution_context(
                    plan=potential_plan,
                    timeout=timeout,
                )
            if execution_context is None:
                execution_plan = replace(
                    potential_plan,
                    effect="unknown",
                    cacheable=False,
                    read_projection_reusable=False,
                )
                effect_source = "untrusted_docker_execution_context"
            else:
                read_paths = planned_paths
                effect_source = "trusted_docker_command_contract"
                if read_paths and controlled_read_probe is None:
                    # Shell utilities reopen workspace paths after the provider
                    # preflight. Their bytes cannot be bound to a path epoch in
                    # the same fd transaction, so they may establish a
                    # read-only effect but never reusable observation facts.
                    execution_plan = replace(
                        potential_plan,
                        cacheable=False,
                        read_projection_reusable=False,
                    )
    read_ranges = _plan_read_ranges(execution_plan)
    projection_kind = (
        read_ranges[0].kind
        if execution_plan.read_projection_reusable and len(read_ranges) == 1
        else "exact"
    )
    representation = f"docker.run-code.text.{projection_kind}/v1"
    if controlled_read_probe is not None:
        representation = f"docker.run-code.text.{projection_kind}/v2"
        read_epochs_before = [controlled_read_probe[4]]
        projection_content_before = controlled_read_probe[5]
    else:
        read_epochs_before = []
        projection_content_before = None
    operation_key = _fact_operation_key(
        kind="run_code",
        value={
            "command": terminal_command_sha256(code),
            "language": language,
            "execution_context": (
                execution_context.fingerprint if execution_context is not None else None
            ),
        },
    )
    scope = _framework_scope(env_content)
    cached_fact = _lookup_read_fact(
        scope=scope,
        operation_key=operation_key,
        paths=read_paths,
        ranges=read_ranges,
        epochs=read_epochs_before,
        representation=representation,
        execution_context_sha256=(
            execution_context.fingerprint if execution_context is not None else ""
        ),
        allow_overlap=(
            execution_plan.read_projection_reusable
            and len(read_ranges) == 1
            and read_ranges[0].kind
            in {"full", "line_range", "byte_range", "tail_lines", "tail_bytes"}
        ),
    )
    if cached_fact is not None and (
        controlled_read_request is not None
        and cached_fact.get("projection_content_sha256") == projection_content_before
    ):
        confirmed_context = await _trusted_docker_helper_context(
            executable_tokens=controlled_helper_tokens,
            timeout=timeout,
            require_immutable=False,
        )
        confirmed_probe = None
        if confirmed_context is not None:
            try:
                confirmed_probe = await _held_fd_container_file_read(
                    confirmed_context,
                    path=read_paths[0],
                    request=controlled_read_request,
                    emit=False,
                    timeout=timeout,
                )
            except (RuntimeError, ValueError):
                confirmed_probe = None
        if (
            confirmed_probe is not None
            and [confirmed_probe[4]] == read_epochs_before
            and confirmed_probe[5] == projection_content_before
            and confirmed_context is not None
            and confirmed_context.fingerprint == execution_context.fingerprint
        ):
            compact = _compact_read_fact_payload(
                cached_fact,
                coverage=read_ranges[0],
            )
            return _text(
                {
                    "success": True,
                    "message": json.dumps(compact, ensure_ascii=False),
                    "metadata": {
                        "command": code,
                        "container": bridge.container,
                        "working_directory": bridge.workdir,
                        "return_code": 0,
                        "timeout_seconds": timeout,
                        "timed_out": False,
                        "execution_time": time.monotonic() - started,
                        "provider_observation_cache_hit": True,
                        "terminal_execution_receipt": build_terminal_execution_receipt(
                            code=code,
                            plan=execution_plan,
                            executed=False,
                            exit_code=0,
                            timed_out=False,
                            potential_effect=potential_plan.effect,
                            effect_source=effect_source,
                            read_path_epochs=[confirmed_probe[4]],
                            requested_language=language,
                            cache_hit=True,
                            observation_id=str(cached_fact["observation_id"]),
                            content_sha256=str(cached_fact["content_sha256"]),
                            representation=representation,
                            source_checkpoint_revision=int(scope[5]),
                            execution_context_sha256=execution_context.fingerprint,
                        ),
                    },
                }
            )
        if confirmed_context is None or (
            confirmed_context.fingerprint != execution_context.fingerprint
        ):
            execution_plan = replace(
                potential_plan,
                effect="unknown",
                cacheable=False,
                read_projection_reusable=False,
            )
            effect_source = "docker_execution_context_changed"
            execution_context = None
            controlled_read_request = None
            controlled_read_probe = None
    write_paths = _literal_container_write_paths(code, potential_plan)
    before_write_states = await _container_path_states(
        write_paths,
        timeout=timeout,
    )
    controlled_execution: (
        tuple[
            bytes,
            TerminalReadRange,
            bool,
            dict[str, Any],
            dict[str, Any],
            str,
        ]
        | None
    ) = None
    if controlled_read_request is not None and execution_context is not None:
        try:
            candidate_execution = await _held_fd_container_file_read(
                execution_context,
                path=read_paths[0],
                request=controlled_read_request,
                emit=True,
                timeout=timeout,
            )
        except (RuntimeError, ValueError):
            candidate_execution = None
        if (
            candidate_execution is not None
            and candidate_execution[2]
            and candidate_execution[1] == read_ranges[0]
        ):
            controlled_execution = candidate_execution
            return_code, stdout, stderr, timed_out = (
                0,
                candidate_execution[0],
                b"",
                False,
            )
        else:
            execution_plan = replace(
                potential_plan,
                cacheable=False,
                read_projection_reusable=False,
            )
            controlled_read_request = None
            controlled_read_probe = None
    if (
        controlled_execution is None
        and language == "shell"
        and execution_plan.effect == "read_only"
        and execution_context is not None
    ):
        return_code, stdout, stderr, timed_out = await _execute_in_trusted_context(
            execution_context,
            code,
            timeout=timeout,
        )
    elif controlled_execution is None and language == "shell":
        return_code, stdout, stderr, timed_out = await bridge.shell_command(
            code,
            timeout=timeout,
        )
    elif controlled_execution is None:
        return_code, stdout, stderr, timed_out = await bridge.execute(
            [os.environ.get("AWORLD_DOCKER_PYTHON", "python3"), "-c", code],
            timeout=timeout,
            workdir=bridge.workdir,
        )
    after_write_states = await _container_path_states(
        write_paths,
        timeout=timeout,
    )
    mutation_observed = (
        before_write_states != after_write_states
        if before_write_states is not None and after_write_states is not None
        else None
    )
    stable_execution_context = execution_context
    if execution_context is not None:
        if controlled_execution is not None:
            confirmed_context = await _trusted_docker_helper_context(
                executable_tokens=controlled_helper_tokens,
                timeout=timeout,
                require_immutable=False,
            )
        else:
            confirmed_context = await _trusted_docker_execution_context(
                plan=potential_plan,
                timeout=timeout,
            )
        if (
            confirmed_context is None
            or confirmed_context.fingerprint != execution_context.fingerprint
        ):
            execution_plan = replace(
                potential_plan,
                effect="unknown",
                cacheable=False,
                read_projection_reusable=False,
            )
            effect_source = "docker_execution_context_changed"
            stable_execution_context = None
    read_epochs_after = (
        [controlled_execution[4]]
        if controlled_execution is not None and stable_execution_context is not None
        else []
    )
    read_path_epochs = (
        read_epochs_after
        if read_epochs_before == read_epochs_after
        and (
            controlled_execution is None
            or controlled_execution[5] == projection_content_before
        )
        else []
    )
    raw_content_sha256 = (
        "sha256:"
        + hashlib.sha256(b"stdout\0" + stdout + b"\0stderr\0" + stderr).hexdigest()
    )
    projection_content_sha256 = (
        controlled_execution[5] if controlled_execution is not None else None
    )
    stdout, stdout_policy = bridge.bound_output(stdout, label="run-code-stdout")
    stderr, stderr_policy = bridge.bound_output(stderr, label="run-code-stderr")
    stdout_text = bridge.decode_inline_text(stdout, stdout_policy)
    stderr_text = bridge.decode_inline_text(stderr, stderr_policy)
    output = "\n".join(part for part in (stderr_text, stdout_text) if part)
    terminal_receipt = build_terminal_execution_receipt(
        code=code,
        plan=execution_plan,
        executed=True,
        exit_code=return_code,
        timed_out=timed_out,
        mutation_observed=mutation_observed,
        potential_effect=potential_plan.effect,
        effect_source=effect_source,
        read_path_epochs=read_path_epochs,
        requested_language=language,
        representation=representation,
        source_checkpoint_revision=(
            int(scope[5]) if _framework_scope_is_complete(scope) else None
        ),
        execution_context_sha256=(
            stable_execution_context.fingerprint
            if stable_execution_context is not None
            else None
        ),
    )
    stored_fact = None
    if terminal_receipt["cacheable"] and read_paths:
        stored_fact = _store_read_fact(
            scope=scope,
            operation_key=operation_key,
            paths=read_paths,
            ranges=read_ranges,
            epochs=read_path_epochs,
            content_sha256=raw_content_sha256,
            coverage_complete=not (
                stdout_policy["output_truncated"] or stderr_policy["output_truncated"]
            )
            and execution_plan.read_projection_reusable,
            representation=representation,
            execution_context_sha256=(
                stable_execution_context.fingerprint
                if stable_execution_context is not None
                else ""
            ),
            projection_content_sha256=projection_content_sha256,
        )
        if stored_fact is not None:
            terminal_receipt = build_terminal_execution_receipt(
                code=code,
                plan=execution_plan,
                executed=True,
                exit_code=return_code,
                timed_out=timed_out,
                mutation_observed=mutation_observed,
                potential_effect=potential_plan.effect,
                effect_source=effect_source,
                read_path_epochs=read_path_epochs,
                requested_language=language,
                observation_id=str(stored_fact["observation_id"]),
                content_sha256=str(stored_fact["content_sha256"]),
                representation=representation,
                source_checkpoint_revision=int(scope[5]),
                execution_context_sha256=(
                    stable_execution_context.fingerprint
                    if stable_execution_context is not None
                    else None
                ),
            )
    return _text(
        {
            "success": return_code == 0,
            "message": output,
            "metadata": {
                "command": code,
                "container": bridge.container,
                "working_directory": bridge.workdir,
                "return_code": return_code,
                "timeout_seconds": timeout,
                "timed_out": timed_out,
                "execution_time": time.monotonic() - started,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "output_truncated": stdout_policy["output_truncated"]
                or stderr_policy["output_truncated"],
                "output_policy": {
                    "stdout": stdout_policy,
                    "stderr": stderr_policy,
                },
                "provider_observation_id": (
                    stored_fact["observation_id"] if stored_fact is not None else None
                ),
                "terminal_execution_receipt": terminal_receipt,
            },
        }
    )


def _read_observation_receipt(
    *,
    path: str,
    epoch: Mapping[str, Any] | None,
    coverage: TerminalReadRange,
    content_sha256: str,
    observation_id: str | None,
    cache_hit: bool,
    coverage_complete: bool,
    representation: str,
    source_checkpoint_revision: int,
    execution_context_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": _READ_OBSERVATION_SCHEMA,
        "authority": _container_epoch_authority(),
        "path": path,
        "epoch": dict(epoch) if epoch is not None else None,
        "coverage": coverage.to_dict(),
        "coverage_complete": bool(coverage_complete),
        "content_sha256": content_sha256,
        "observation_id": observation_id,
        "cache_hit": bool(cache_hit),
        "executed": not cache_hit,
        "representation": representation,
        "source_checkpoint_revision": source_checkpoint_revision,
        "execution_context_sha256": execution_context_sha256,
    }


@mcp.tool(
    description="Read a text or binary file from the attached container using producer-bounded ranges."
)
async def read_file(
    ctx: Context,
    path: str = Field(description="Absolute container path"),
    head: Optional[int] = Field(default=None, description="First N lines"),
    tail: Optional[int] = Field(default=None, description="Last N lines"),
    output: str = Field(default="text", description="text or base64"),
    offset: int = Field(default=0, description="Binary byte offset"),
    limit: Optional[int] = Field(default=None, description="Bounded binary byte count"),
    refresh: bool = Field(
        default=False, description="Bypass retained observation facts"
    ),
    env_content: Optional[dict[str, Any]] = Field(
        default=None,
        description="Framework-injected task scope; hidden from the model schema",
    ),
) -> TextContent:
    del ctx
    if isinstance(head, FieldInfo):
        head = head.default
    if isinstance(tail, FieldInfo):
        tail = tail.default
    if isinstance(output, FieldInfo):
        output = output.default
    if isinstance(offset, FieldInfo):
        offset = offset.default
    if isinstance(limit, FieldInfo):
        limit = limit.default
    if isinstance(refresh, FieldInfo):
        refresh = refresh.default
    if isinstance(env_content, FieldInfo):
        env_content = env_content.default
    if isinstance(head, bool) or (head is not None and not isinstance(head, int)):
        raise ValueError("head must be an integer or null")
    if isinstance(tail, bool) or (tail is not None and not isinstance(tail, int)):
        raise ValueError("tail must be an integer or null")
    if isinstance(offset, bool) or not isinstance(offset, int):
        raise ValueError("offset must be an integer")
    if isinstance(limit, bool) or (limit is not None and not isinstance(limit, int)):
        raise ValueError("limit must be an integer or null")
    if not isinstance(refresh, bool):
        refresh = False
    valid_path = bridge.validate_path(path)
    if output not in {"text", "base64"}:
        raise ValueError("output must be 'text' or 'base64'")
    if head is not None and head < 1:
        raise ValueError("head must be a positive line number/count")
    if tail is not None and tail < 1:
        raise ValueError("tail must be a positive line number/count")
    if head is not None and tail is not None and head > tail:
        raise ValueError("head must be <= tail when both are specified")
    if offset < 0:
        raise ValueError("offset must be non-negative")
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    for name, value in (
        ("head", head),
        ("tail", tail),
        ("offset", offset),
        ("limit", limit),
    ):
        if value is not None and value > _MAX_PORTABLE_SHELL_INTEGER:
            raise ValueError(f"{name} exceeds portable integer bounds")
    if output == "text" and (offset != 0 or limit is not None):
        raise ValueError("offset and limit are only supported with output='base64'")
    if (
        output == "base64"
        and (head is not None or tail is not None)
        and (offset != 0 or limit is not None)
    ):
        raise ValueError("offset/limit cannot be combined with head/tail")
    helper_tokens = (
        "head",
        "sed",
        "sha256sum",
        "tail",
        "tee",
        "timeout",
        "wc",
    )
    execution_context = await _trusted_docker_helper_context(
        executable_tokens=helper_tokens,
        timeout=5,
        require_immutable=False,
    )
    if execution_context is None:
        raise RuntimeError("container cannot prove a trusted file-read context")
    max_output = int(getattr(bridge, "max_output_bytes", 1024 * 1024))
    text_hard_limit = min(
        int(getattr(bridge, "max_read_bytes", 1024 * 1024)),
        max_output,
    )
    binary_hard_limit = min(
        int(getattr(bridge, "max_binary_bytes", 1024 * 1024)),
        max_output,
    )
    binary_requested = (
        binary_hard_limit if limit is None else min(limit, binary_hard_limit)
    )
    if output == "base64" and head is None and tail is None:
        read_request: dict[str, Any] = {
            "mode": "bytes",
            "hard_limit": binary_hard_limit,
            "offset": offset,
            "limit": binary_requested,
        }
    elif head is not None and tail is not None:
        read_request = {
            "mode": "range",
            "hard_limit": text_hard_limit,
            "start": head,
            "end": tail,
        }
    elif head is not None:
        read_request = {
            "mode": "head",
            "hard_limit": text_hard_limit,
            "end": head,
        }
    elif tail is not None:
        read_request = {
            "mode": "tail",
            "hard_limit": text_hard_limit,
            "count": tail,
        }
    else:
        read_request = {"mode": "full", "hard_limit": text_hard_limit}
    (
        _,
        requested_coverage,
        _probed_complete,
        probed_metadata,
        epoch_before,
        probed_content_sha256,
    ) = await _held_fd_container_file_read(
        execution_context,
        path=valid_path,
        request=read_request,
        emit=False,
        timeout=30,
    )
    total_bytes = int(probed_metadata["totalBytes"])
    selector = (
        "range"
        if head is not None and tail is not None
        else "head"
        if head is not None
        else "tail"
        if tail is not None
        else "bytes"
        if output == "base64"
        else "prefix"
        if requested_coverage.kind == "byte_range"
        else "full"
    )
    representation = f"docker.read-file.{output}.{selector}/v1"
    operation_key = _fact_operation_key(
        kind="read_file",
        value={
            "path": valid_path,
            "output": output,
            "coverage": requested_coverage.to_dict(),
            "execution_context": execution_context.fingerprint,
        },
    )
    scope = _framework_scope(env_content)
    cached_fact = (
        None
        if refresh
        else _lookup_read_fact(
            scope=scope,
            operation_key=operation_key,
            paths=[valid_path],
            ranges=(requested_coverage,),
            epochs=[epoch_before],
            allow_overlap=True,
            representation=representation,
            execution_context_sha256=execution_context.fingerprint,
        )
    )
    if cached_fact is not None and (
        cached_fact.get("content_sha256") == probed_content_sha256
    ):
        confirmed_context = await _trusted_docker_helper_context(
            executable_tokens=helper_tokens,
            timeout=5,
            require_immutable=False,
        )
        if (
            confirmed_context is None
            or confirmed_context.fingerprint != execution_context.fingerprint
        ):
            raise RuntimeError("container file-read context changed during reuse")
        (
            _,
            confirmed_coverage,
            _,
            _,
            confirmed_epoch,
            confirmed_content_sha256,
        ) = await _held_fd_container_file_read(
            confirmed_context,
            path=valid_path,
            request=read_request,
            emit=False,
            timeout=30,
        )
        if (
            confirmed_epoch == epoch_before
            and confirmed_coverage == requested_coverage
            and confirmed_content_sha256 == probed_content_sha256
        ):
            payload = _compact_read_fact_payload(
                cached_fact,
                coverage=requested_coverage,
            )
            receipt = _read_observation_receipt(
                path=valid_path,
                epoch=confirmed_epoch,
                coverage=requested_coverage,
                content_sha256=str(cached_fact["content_sha256"]),
                observation_id=str(cached_fact["observation_id"]),
                cache_hit=True,
                coverage_complete=bool(cached_fact.get("coverage_complete")),
                representation=representation,
                source_checkpoint_revision=int(scope[5]),
                execution_context_sha256=execution_context.fingerprint,
            )
            return _text(payload, metadata={_READ_OBSERVATION_RECEIPT_KEY: receipt})

    (
        data,
        coverage,
        coverage_complete,
        read_metadata,
        read_epoch,
        content_sha256,
    ) = await _held_fd_container_file_read(
        execution_context,
        path=valid_path,
        request=read_request,
        emit=True,
        timeout=30,
    )
    if not coverage_complete:
        read_metadata.setdefault("truncationReason", "read_bytes")
    if (
        head is None
        and tail is None
        and output == "text"
        and total_bytes > text_hard_limit
    ):
        read_metadata.update(
            {
                "complete": False,
                "defaultBounded": True,
                "truncationReason": "default_bytes",
                "nextOffset": text_hard_limit,
            }
        )
    confirmed_context = await _trusted_docker_helper_context(
        executable_tokens=helper_tokens,
        timeout=5,
        require_immutable=False,
    )
    if (
        confirmed_context is None
        or confirmed_context.fingerprint != execution_context.fingerprint
    ):
        raise RuntimeError("container file-read context changed during execution")
    fact = _store_read_fact(
        scope=scope,
        operation_key=operation_key,
        paths=[valid_path],
        ranges=(coverage,),
        epochs=[read_epoch],
        content_sha256=content_sha256,
        coverage_complete=coverage_complete,
        representation=representation,
        execution_context_sha256=execution_context.fingerprint,
        projection_content_sha256=content_sha256,
    )
    payload: dict[str, Any]
    if output == "base64":
        payload = {
            "type": "base64",
            "base64": base64.b64encode(data).decode("ascii"),
            "mimeType": mimetypes.guess_type(valid_path)[0]
            or "application/octet-stream",
            "fileName": posixpath.basename(valid_path),
            **read_metadata,
        }
    else:
        payload = {
            "type": "text",
            "content": data.decode("utf-8", errors="replace"),
            **read_metadata,
        }
    if fact is not None:
        payload["observationId"] = fact["observation_id"]
    payload["contentSha256"] = content_sha256
    payload["coverage"] = coverage.to_dict()
    receipt = _read_observation_receipt(
        path=valid_path,
        epoch=read_epoch,
        coverage=coverage,
        content_sha256=content_sha256,
        observation_id=fact["observation_id"] if fact is not None else None,
        cache_hit=False,
        coverage_complete=coverage_complete,
        representation=representation,
        source_checkpoint_revision=(
            int(scope[5]) if _framework_scope_is_complete(scope) else 0
        ),
        execution_context_sha256=execution_context.fingerprint,
    )
    return _text(payload, metadata={_READ_OBSERVATION_RECEIPT_KEY: receipt})


async def _write_bytes(path: str, content: bytes) -> None:
    valid_path = bridge.validate_path(path)
    script = 'mkdir -p "$(dirname "$1")" && cat > "$1"'
    return_code, _, stderr, _ = await bridge.execute(
        [bridge.shell, "-c", script, "aworld-docker", valid_path],
        input_bytes=content,
        timeout=30,
    )
    if return_code != 0:
        raise RuntimeError(stderr.decode("utf-8", errors="replace"))


@mcp.tool(
    description="Create or overwrite a UTF-8 text file in the attached container."
)
async def write_file(
    ctx: Context,
    path: str = Field(description="Absolute container path"),
    content: str = Field(description="File content"),
) -> TextContent:
    del ctx
    await _write_bytes(path, content.encode("utf-8"))
    return _text(f"Successfully wrote to {path}")


@mcp.tool(description="Create or overwrite a file from base64-encoded bytes.")
async def write_file_base64(
    ctx: Context,
    path: str = Field(description="Absolute container path"),
    content_base64: str = Field(description="Base64-encoded content"),
) -> TextContent:
    del ctx
    await _write_bytes(path, base64.b64decode(content_base64, validate=True))
    return _text(f"Successfully wrote binary content to {path}")


@mcp.tool(description="Edit an inclusive 1-based line range in a container file.")
async def edit_file(
    ctx: Context,
    path: str = Field(description="Absolute container path"),
    start_line: int = Field(description="Start line, 1-based and inclusive"),
    end_line: int = Field(description="End line, 1-based and inclusive"),
    new_content: str = Field(default="", description="Replacement content"),
    dryRun: bool = Field(default=False, description="Return a diff without writing"),
) -> TextContent:
    del ctx
    if start_line < 1 or end_line < start_line:
        raise ValueError("line range must satisfy 1 <= start_line <= end_line")
    valid_path = bridge.validate_path(path)
    original = (await bridge.require_success(["cat", valid_path])).decode("utf-8")
    lines = original.splitlines(keepends=True)
    replacement = new_content.splitlines(keepends=True)
    if new_content and not new_content.endswith(("\n", "\r")):
        replacement[-1] += "\n"
    updated_lines = lines[: start_line - 1] + replacement + lines[end_line:]
    updated = "".join(updated_lines)
    diff = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=valid_path,
            tofile=valid_path,
        )
    )
    if not dryRun:
        await _write_bytes(valid_path, updated.encode("utf-8"))
    return _text(diff)


@mcp.tool(description="Create a directory recursively in the attached container.")
async def create_directory(
    ctx: Context, path: str = Field(description="Absolute container path")
) -> TextContent:
    del ctx
    valid_path = bridge.validate_path(path)
    await bridge.require_success(["mkdir", "-p", valid_path])
    return _text(f"Successfully created directory {path}")


@mcp.tool(description="List direct children of a directory in the attached container.")
async def list_directory(
    ctx: Context, path: str = Field(description="Absolute container path")
) -> TextContent:
    del ctx
    valid_path = bridge.validate_path(path)
    script = (
        'for entry in "$1"/* "$1"/.[!.]* "$1"/..?*; do '
        '[ -e "$entry" ] || continue; '
        'if [ -d "$entry" ]; then prefix="[DIR]"; else prefix="[FILE]"; fi; '
        'printf "%s %s\\n" "$prefix" "${entry##*/}"; done'
    )
    data = await bridge.require_success(
        [bridge.shell, "-c", script, "aworld-docker", valid_path]
    )
    return _text(data.decode("utf-8", errors="replace"))


@mcp.tool(description="Move or rename a path inside the attached container.")
async def move_file(
    ctx: Context,
    source: str = Field(description="Absolute source path"),
    destination: str = Field(description="Absolute destination path"),
) -> TextContent:
    del ctx
    valid_source = bridge.validate_path(source)
    valid_destination = bridge.validate_path(destination)
    await bridge.require_success(["mv", valid_source, valid_destination])
    return _text(f"Successfully moved {source} to {destination}")


@mcp.tool(description="List container directories allowed for filesystem tools.")
async def list_allowed_directories(ctx: Context) -> TextContent:
    del ctx
    return _text("Allowed directories:\n" + "\n".join(bridge.allowed_directories))


@mcp.tool(description="Download one bounded container-file chunk as base64.")
async def download_file(
    ctx: Context,
    path: str = Field(description="Absolute container path"),
    offset: int = Field(default=0, description="Zero-based byte offset"),
    limit: Optional[int] = Field(default=None, description="Bounded byte count"),
    env_content: Optional[dict[str, Any]] = Field(
        default=None,
        description="Framework-injected task scope; hidden from the model schema",
    ),
) -> TextContent:
    return await read_file(
        ctx,
        path,
        head=None,
        tail=None,
        output="base64",
        offset=offset,
        limit=limit,
        refresh=False,
        env_content=env_content,
    )


@mcp.tool(description="Read one bounded media-file chunk as base64.")
async def read_media_file(
    ctx: Context,
    path: str = Field(description="Absolute container path"),
    offset: int = Field(default=0, description="Zero-based byte offset"),
    limit: Optional[int] = Field(default=None, description="Bounded byte count"),
    env_content: Optional[dict[str, Any]] = Field(
        default=None,
        description="Framework-injected task scope; hidden from the model schema",
    ),
) -> TextContent:
    return await read_file(
        ctx,
        path,
        head=None,
        tail=None,
        output="base64",
        offset=offset,
        limit=limit,
        refresh=False,
        env_content=env_content,
    )


@mcp.tool(
    description=(
        "Observe one bounded PNG, JPEG, WebP, or GIF artifact inside the attached "
        "container. For video, first generate and select one bounded frame image."
    )
)
async def observe_artifact(
    ctx: Context,
    path: str = Field(description="Absolute container image path"),
    expected_mime: Optional[str] = Field(
        default=None, description="Optional expected image MIME type"
    ),
    env_content: Optional[dict[str, Any]] = Field(
        default=None,
        description="Framework-injected task scope; hidden from the model schema",
    ),
) -> Any:
    del ctx
    observed = await bridge.observe_artifact(
        path,
        expected_mime=expected_mime,
        framework_scope=env_content,
    )
    return artifact_mcp_result(observed)


@mcp.tool(
    description="Read a bounded chunk from a full Tool output artifact returned by this sandbox."
)
async def read_output_artifact(
    ctx: Context,
    artifact_ref: str = Field(
        description="Artifact reference returned in output_policy.artifact_ref"
    ),
    offset: int = Field(default=0, description="Zero-based byte offset"),
    limit: Optional[int] = Field(
        default=None, description="Bytes to read; capped by the inline output policy"
    ),
    output: str = Field(default="text", description="text or base64"),
) -> TextContent:
    del ctx
    if offset < 0:
        raise ValueError("offset must be non-negative")
    requested = bridge.max_output_bytes if limit is None else limit
    if requested < 1 or requested > bridge.max_output_bytes:
        raise ValueError(f"limit must be between 1 and {bridge.max_output_bytes}")
    if output not in {"text", "base64"}:
        raise ValueError("output must be 'text' or 'base64'")
    artifact = bridge.validate_artifact_ref(artifact_ref)
    total_bytes = artifact.stat().st_size
    with artifact.open("rb") as stream:
        stream.seek(offset)
        data = stream.read(requested)
    next_offset = offset + len(data)
    content = (
        data.decode("utf-8", errors="replace")
        if output == "text"
        else base64.b64encode(data).decode("ascii")
    )
    artifact_digest = artifact.stem.rsplit("-", 1)[-1]
    return _text(
        {
            "type": output,
            "content": content,
            "artifact_ref": artifact_ref,
            "offset": offset,
            "next_offset": next_offset,
            "returned_bytes": len(data),
            "total_bytes": total_bytes,
            "complete": next_offset >= total_bytes,
            "content_sha256": artifact_digest,
            "chunk_sha256": hashlib.sha256(data).hexdigest(),
        }
    )


@mcp.tool(description="Copy a file between two allowed paths inside the container.")
async def upload_file(
    ctx: Context,
    source_path: str = Field(description="Absolute source path inside the container"),
    target_path: str = Field(description="Absolute target path inside the container"),
) -> TextContent:
    del ctx
    valid_source = bridge.validate_path(source_path)
    valid_target = bridge.validate_path(target_path)
    await bridge.require_success(["cp", valid_source, valid_target])
    return _text(f"Successfully copied {source_path} to {target_path}")


@mcp.tool(
    description="Search file contents recursively with an extended regular expression."
)
async def search_content(
    ctx: Context,
    path: str = Field(description="Absolute file or directory path"),
    pattern: str = Field(description="Extended regular expression"),
    max_matches: Optional[int] = Field(
        default=None, description="Maximum total matching lines"
    ),
    max_per_file: Optional[int] = Field(
        default=None, description="Accepted for API compatibility"
    ),
    before: int = Field(default=0, description="Context lines before each match"),
    after: int = Field(default=0, description="Context lines after each match"),
) -> TextContent:
    del ctx, max_per_file
    valid_path = bridge.validate_path(path)
    command = [
        "grep",
        "-RInE",
        "-B",
        str(before),
        "-A",
        str(after),
        pattern,
        valid_path,
    ]
    return_code, stdout, stderr, _ = await bridge.execute(command, timeout=30)
    if return_code not in (0, 1):
        raise RuntimeError(stderr.decode("utf-8", errors="replace"))
    lines = stdout.decode("utf-8", errors="replace").splitlines()
    if max_matches is not None:
        lines = lines[:max_matches]
    return _text("\n".join(lines) if lines else "No matches found")


@mcp.tool(description="Search recursively for container files matching a glob pattern.")
async def search_files(
    ctx: Context,
    path: str = Field(description="Absolute directory path"),
    pattern: str = Field(description="Glob pattern"),
    excludePatterns: list[str] = Field(
        default_factory=list, description="Glob patterns to exclude"
    ),
) -> TextContent:
    del ctx
    valid_path = bridge.validate_path(path)
    data = await bridge.require_success(["find", valid_path, "-type", "f"], timeout=30)
    matches = []
    for candidate in data.decode("utf-8", errors="replace").splitlines():
        relative = posixpath.relpath(candidate, valid_path)
        if not (
            fnmatch.fnmatch(relative, pattern)
            or fnmatch.fnmatch(posixpath.basename(candidate), pattern)
        ):
            continue
        if any(fnmatch.fnmatch(relative, excluded) for excluded in excludePatterns):
            continue
        matches.append(candidate)
    return _text("\n".join(matches) if matches else "No matches found")


if __name__ == "__main__":
    use_stdio = "--stdio" in sys.argv
    mcp.run(transport="stdio" if use_stdio else "streamable-http")
