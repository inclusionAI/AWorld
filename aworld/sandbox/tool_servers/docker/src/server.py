"""Host-side MCP bridge for an already-running local Docker container."""

import asyncio
import base64
import difflib
import fnmatch
import hashlib
import json
import mimetypes
import os
import posixpath
import sys
import time
from pathlib import Path, PurePosixPath
from typing import Any, Optional

from mcp.server import FastMCP
from mcp.server.fastmcp import Context
from mcp.types import TextContent
from pydantic import Field


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
        self.shell = os.environ.get("AWORLD_DOCKER_SHELL", "/bin/sh").strip() or "/bin/sh"
        raw_allowed = os.environ.get("AWORLD_DOCKER_ALLOWED_DIRECTORIES", "")
        try:
            parsed_allowed = json.loads(raw_allowed) if raw_allowed else [self.workdir]
        except json.JSONDecodeError as exc:
            raise RuntimeError("AWORLD_DOCKER_ALLOWED_DIRECTORIES must be a JSON list") from exc
        if not isinstance(parsed_allowed, list) or not parsed_allowed:
            raise RuntimeError("AWORLD_DOCKER_ALLOWED_DIRECTORIES must be a non-empty JSON list")
        self.allowed_directories = [self._normalize_absolute(str(path)) for path in parsed_allowed]
        self.max_output_bytes = int(os.environ.get("AWORLD_DOCKER_MAX_OUTPUT_BYTES", "1048576"))
        self.output_head_bytes = int(
            os.environ.get("AWORLD_DOCKER_OUTPUT_HEAD_BYTES", str(self.max_output_bytes // 2))
        )
        if self.max_output_bytes < 1:
            raise RuntimeError("AWORLD_DOCKER_MAX_OUTPUT_BYTES must be positive")
        if not 0 <= self.output_head_bytes <= self.max_output_bytes:
            raise RuntimeError(
                "AWORLD_DOCKER_OUTPUT_HEAD_BYTES must be between 0 and AWORLD_DOCKER_MAX_OUTPUT_BYTES"
            )
        artifact_directory = os.environ.get("AWORLD_DOCKER_ARTIFACT_DIRECTORY", "").strip()
        self.artifact_directory = Path(artifact_directory).resolve() if artifact_directory else None
        if self.artifact_directory:
            self.artifact_directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _normalize_absolute(path: str) -> str:
        if not PurePosixPath(path).is_absolute():
            raise ValueError(f"Container path must be absolute: {path!r}")
        return posixpath.normpath(path)

    def validate_path(self, path: str) -> str:
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
    ) -> tuple[int, bytes, bytes, bool]:
        args = [self.docker_binary, "exec"]
        if input_bytes is not None:
            args.append("-i")
        if workdir:
            args.extend(["-w", workdir])
        args.extend([self.container, *command])
        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE if input_bytes is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(input_bytes), timeout=timeout)
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
            safe_label = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in label)[:48]
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
            raise ValueError("artifact_ref is not a Tool output artifact from this sandbox")
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


bridge = DockerBridge()
mcp = FastMCP(
    "docker-sandbox-server",
    log_level=os.environ.get("MCP_LOG_LEVEL", "WARNING"),
    instructions="Terminal and filesystem tools scoped to one local Docker container.",
)


def _text(payload: Any) -> TextContent:
    if not isinstance(payload, str):
        payload = json.dumps(payload, ensure_ascii=False)
    return TextContent(type="text", text=payload)


@mcp.tool(description="Execute a shell command inside the attached Docker container.")
async def run_code(
    ctx: Context,
    code: str = Field(description="Shell command to execute inside the container"),
    timeout: int = Field(default=30, description="Command timeout in seconds"),
    output_format: str = Field(default="markdown", description="markdown, json, or text"),
) -> TextContent:
    del ctx, output_format
    started = time.monotonic()
    return_code, stdout, stderr, timed_out = await bridge.shell_command(code, timeout=timeout)
    stdout, stdout_policy = bridge.bound_output(stdout, label="run-code-stdout")
    stderr, stderr_policy = bridge.bound_output(stderr, label="run-code-stderr")
    stdout_text = bridge.decode_inline_text(stdout, stdout_policy)
    stderr_text = bridge.decode_inline_text(stderr, stderr_policy)
    output = "\n".join(part for part in (stderr_text, stdout_text) if part)
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
                "output_truncated": stdout_policy["output_truncated"] or stderr_policy["output_truncated"],
                "output_policy": {
                    "stdout": stdout_policy,
                    "stderr": stderr_policy,
                },
            },
        }
    )


@mcp.tool(description="Read a text or binary file from the attached container.")
async def read_file(
    ctx: Context,
    path: str = Field(description="Absolute container path"),
    head: Optional[int] = Field(default=None, description="First N lines"),
    tail: Optional[int] = Field(default=None, description="Last N lines"),
    output: str = Field(default="text", description="text or base64"),
) -> TextContent:
    del ctx
    valid_path = bridge.validate_path(path)
    data = await bridge.require_success(["cat", valid_path])
    if output == "base64":
        inline_data, output_policy = bridge.bound_output(data, label=f"read-file-{posixpath.basename(valid_path)}")
        mime_type = mimetypes.guess_type(valid_path)[0] or "application/octet-stream"
        return _text(
            {
                "type": "base64",
                "base64": base64.b64encode(inline_data).decode("ascii"),
                "mimeType": mime_type,
                "fileName": posixpath.basename(valid_path),
                "complete": not output_policy["output_truncated"],
                "output_policy": output_policy,
            }
        )
    if output != "text":
        raise ValueError("output must be 'text' or 'base64'")
    content = data.decode("utf-8")
    lines = content.splitlines(keepends=True)
    if head is not None and tail is not None:
        if head > tail:
            raise ValueError("head must be <= tail when both are specified")
        content = "".join(lines[max(head - 1, 0) : tail])
    elif head is not None:
        content = "".join(lines[:head])
    elif tail is not None:
        content = "".join(lines[-tail:])
    inline_content, output_policy = bridge.bound_output(
        content.encode("utf-8"),
        label=f"read-file-{posixpath.basename(valid_path)}",
    )
    return _text(
        {
            "type": "text",
            "content": bridge.decode_inline_text(inline_content, output_policy),
            "complete": not output_policy["output_truncated"],
            "output_policy": output_policy,
        }
    )


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


@mcp.tool(description="Create or overwrite a UTF-8 text file in the attached container.")
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
async def create_directory(ctx: Context, path: str = Field(description="Absolute container path")) -> TextContent:
    del ctx
    valid_path = bridge.validate_path(path)
    await bridge.require_success(["mkdir", "-p", valid_path])
    return _text(f"Successfully created directory {path}")


@mcp.tool(description="List direct children of a directory in the attached container.")
async def list_directory(ctx: Context, path: str = Field(description="Absolute container path")) -> TextContent:
    del ctx
    valid_path = bridge.validate_path(path)
    script = (
        'for entry in "$1"/* "$1"/.[!.]* "$1"/..?*; do '
        '[ -e "$entry" ] || continue; '
        'if [ -d "$entry" ]; then prefix="[DIR]"; else prefix="[FILE]"; fi; '
        'printf "%s %s\\n" "$prefix" "${entry##*/}"; done'
    )
    data = await bridge.require_success([bridge.shell, "-c", script, "aworld-docker", valid_path])
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


@mcp.tool(description="Download a container file as base64.")
async def download_file(ctx: Context, path: str = Field(description="Absolute container path")) -> TextContent:
    return await read_file(ctx, path, output="base64")


@mcp.tool(description="Read image, audio, or other binary container file as base64.")
async def read_media_file(ctx: Context, path: str = Field(description="Absolute container path")) -> TextContent:
    return await read_file(ctx, path, output="base64")


@mcp.tool(description="Read a bounded chunk from a full Tool output artifact returned by this sandbox.")
async def read_output_artifact(
    ctx: Context,
    artifact_ref: str = Field(description="Artifact reference returned in output_policy.artifact_ref"),
    offset: int = Field(default=0, description="Zero-based byte offset"),
    limit: Optional[int] = Field(default=None, description="Bytes to read; capped by the inline output policy"),
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
            "offset": offset,
            "next_offset": next_offset,
            "total_bytes": total_bytes,
            "complete": next_offset >= total_bytes,
            "content_sha256": artifact_digest,
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


@mcp.tool(description="Search file contents recursively with an extended regular expression.")
async def search_content(
    ctx: Context,
    path: str = Field(description="Absolute file or directory path"),
    pattern: str = Field(description="Extended regular expression"),
    max_matches: Optional[int] = Field(default=None, description="Maximum total matching lines"),
    max_per_file: Optional[int] = Field(default=None, description="Accepted for API compatibility"),
    before: int = Field(default=0, description="Context lines before each match"),
    after: int = Field(default=0, description="Context lines after each match"),
) -> TextContent:
    del ctx, max_per_file
    valid_path = bridge.validate_path(path)
    command = ["grep", "-RInE", "-B", str(before), "-A", str(after), pattern, valid_path]
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
    excludePatterns: list[str] = Field(default_factory=list, description="Glob patterns to exclude"),
) -> TextContent:
    del ctx
    valid_path = bridge.validate_path(path)
    data = await bridge.require_success(["find", valid_path, "-type", "f"], timeout=30)
    matches = []
    for candidate in data.decode("utf-8", errors="replace").splitlines():
        relative = posixpath.relpath(candidate, valid_path)
        if not (fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(posixpath.basename(candidate), pattern)):
            continue
        if any(fnmatch.fnmatch(relative, excluded) for excluded in excludePatterns):
            continue
        matches.append(candidate)
    return _text("\n".join(matches) if matches else "No matches found")


if __name__ == "__main__":
    use_stdio = "--stdio" in sys.argv
    mcp.run(transport="stdio" if use_stdio else "streamable-http")
