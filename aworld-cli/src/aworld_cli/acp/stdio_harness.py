from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Callable

from .protocol import decode_jsonrpc_line, encode_jsonrpc_message


def repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def local_server_env(*, extra_env: dict[str, str] | None = None) -> dict[str, str]:
    root = repo_root()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "aworld-cli" / "src") + os.pathsep + str(root)
    if extra_env:
        env.update(extra_env)
    return env


class AcpStdioHarness:
    def __init__(
        self,
        *,
        command: list[str],
        cwd: str,
        env: dict[str, str],
    ) -> None:
        self._command = command
        self._cwd = cwd
        self._env = env
        self._buffered_messages: list[dict[str, Any]] = []
        self._proc: asyncio.subprocess.Process | None = None
        self.stdout_lines: list[str] = []
        self.stderr_text = ""

    @classmethod
    def for_local_server(
        cls,
        *,
        extra_env: dict[str, str] | None = None,
        command: list[str] | None = None,
    ) -> "AcpStdioHarness":
        root = repo_root()
        return cls(
            command=command
            or [sys.executable, "-m", "aworld_cli.main", "--no-banner", "acp"],
            cwd=str(root),
            env=local_server_env(extra_env=extra_env),
        )

    @property
    def process(self) -> asyncio.subprocess.Process:
        if self._proc is None:
            raise RuntimeError("ACP stdio harness has not been started.")
        return self._proc

    async def start(self) -> None:
        if self._proc is not None:
            return
        self._proc = await asyncio.create_subprocess_exec(
            *self._command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._cwd,
            env=self._env,
        )

    async def __aenter__(self) -> "AcpStdioHarness":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def send(self, message: dict[str, Any]) -> None:
        stdin = self.process.stdin
        if stdin is None:
            raise RuntimeError("ACP stdio harness stdin is unavailable.")
        stdin.write(encode_jsonrpc_message(message))
        await stdin.drain()

    async def send_request(self, request_id: int, method: str, params: dict[str, Any]) -> None:
        await self.send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )

    async def read_message(self, *, timeout_seconds: float | None = None) -> dict[str, Any]:
        if self._buffered_messages:
            return self._buffered_messages.pop(0)

        return await self._read_stdout_message(timeout_seconds=timeout_seconds)

    async def _read_stdout_message(self, *, timeout_seconds: float | None = None) -> dict[str, Any]:
        stdout = self.process.stdout
        if stdout is None:
            raise RuntimeError("ACP stdio harness stdout is unavailable.")

        try:
            line = (
                await asyncio.wait_for(stdout.readline(), timeout=timeout_seconds)
                if timeout_seconds is not None
                else await stdout.readline()
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                "ACP stdio harness timed out waiting for a JSON line."
                + (
                    f" timeout_seconds={timeout_seconds}"
                    if timeout_seconds is not None
                    else ""
                )
            ) from exc
        if not line:
            stderr = await self._read_stderr()
            raise RuntimeError(
                "ACP stdio harness expected a JSON line but got EOF."
                + (f" stderr={stderr!r}" if stderr else "")
            )

        decoded = line.decode("utf-8")
        self.stdout_lines.append(decoded.rstrip("\n"))
        return decode_jsonrpc_line(line)

    async def read_matching(
        self,
        predicate: Callable[[dict[str, Any]], bool],
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        for index, message in enumerate(self._buffered_messages):
            if predicate(message):
                return self._buffered_messages.pop(index)

        while True:
            message = await self._read_stdout_message(timeout_seconds=timeout_seconds)
            if predicate(message):
                return message
            self._buffered_messages.append(message)

    async def read_response(
        self,
        request_id: int,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        return await self.read_matching(
            lambda message: int(message.get("id", -1)) == request_id,
            timeout_seconds=timeout_seconds,
        )

    async def read_notification(
        self,
        method: str | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        return await self.read_matching(
            lambda message: "id" not in message
            and (method is None or message.get("method") == method),
            timeout_seconds=timeout_seconds,
        )

    async def read_responses(
        self,
        request_ids: set[int],
        *,
        timeout_seconds: float | None = None,
    ) -> dict[int, dict[str, Any]]:
        responses: dict[int, dict[str, Any]] = {}
        remaining = set(request_ids)
        while remaining:
            message = await self.read_matching(
                lambda item: int(item.get("id", -1)) in remaining,
                timeout_seconds=timeout_seconds,
            )
            response_id = int(message["id"])
            responses[response_id] = message
            remaining.remove(response_id)
        return responses

    async def close(self, *, timeout: float = 5.0) -> None:
        if self._proc is None:
            return

        stdin = self._proc.stdin
        if stdin is not None and not stdin.is_closing():
            stdin.close()

        try:
            await asyncio.wait_for(self._proc.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            self._proc.kill()
            await self._proc.wait()
        finally:
            self.stderr_text = await self._read_stderr()
            self._proc = None

    async def _read_stderr(self) -> str:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return self.stderr_text

        if self.stderr_text:
            return self.stderr_text

        self.stderr_text = (await proc.stderr.read()).decode("utf-8", errors="replace")
        return self.stderr_text
