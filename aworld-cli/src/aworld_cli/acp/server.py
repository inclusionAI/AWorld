from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from .errors import (
    AWORLD_ACP_SESSION_BUSY,
    AWORLD_ACP_SESSION_NOT_FOUND,
    AWORLD_ACP_UNSUPPORTED_PROMPT_CONTENT,
    AcpBusyError,
)
from .event_mapper import map_runtime_event_to_session_update
from .protocol import decode_jsonrpc_line, encode_jsonrpc_message
from .runtime_adapter import normalize_final_text
from .session_store import AcpSessionStore
from .turn_controller import TurnController


class AcpStdioServer:
    def __init__(self) -> None:
        self._session_store = AcpSessionStore()
        self._turns = TurnController()
        self._state_by_session: dict[str, dict[str, Any]] = {}
        self._write_lock = asyncio.Lock()

    async def run(self) -> int:
        while True:
            line = await asyncio.to_thread(sys.stdin.buffer.readline)
            if not line:
                return 0

            request = decode_jsonrpc_line(line)
            if "method" not in request:
                continue

            method = request["method"]
            request_id = request.get("id")
            params = request.get("params") or {}

            try:
                if method == "initialize":
                    response = self._response(
                        request_id,
                        {
                            "protocolVersion": "0.1",
                            "serverInfo": {"name": "aworld-cli", "version": "0.1"},
                            "agentCapabilities": {"loadSession": False},
                        },
                    )
                elif method == "newSession":
                    response = self._response(request_id, self._handle_new_session(params))
                elif method == "prompt":
                    response = await self._handle_prompt(request_id, params)
                elif method == "cancel":
                    response = await self._handle_cancel(request_id, params)
                else:
                    response = self._error(request_id, -32601, f"Unsupported method: {method}")
            except ValueError as exc:
                response = self._error(request_id, -32602, str(exc))

            await self._write_message(response)

    def _handle_new_session(self, params: dict[str, Any]) -> dict[str, Any]:
        cwd = params.get("cwd")
        if cwd is None:
            cwd = str(Path.cwd())

        record = self._session_store.create_session(
            cwd=str(Path(cwd)),
            requested_mcp_servers=params.get("mcpServers") or [],
        )
        self._state_by_session[record.acp_session_id] = {}
        return {"sessionId": record.acp_session_id}

    async def _handle_prompt(self, request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        session_id = str(params.get("sessionId") or "")
        if self._session_store.get(session_id) is None:
            return self._error(request_id, -32001, AWORLD_ACP_SESSION_NOT_FOUND)

        prompt_text = self._normalize_prompt_text(params.get("prompt"))
        state = self._state_by_session.setdefault(session_id, {})

        async def _run_turn() -> None:
            event = normalize_final_text(state, prompt_text)
            update = map_runtime_event_to_session_update(session_id, event)
            await self._write_message(self._notification("sessionUpdate", update))

        try:
            task = await self._turns.start_turn(session_id, _run_turn())
        except AcpBusyError:
            return self._error(request_id, -32002, AWORLD_ACP_SESSION_BUSY)

        await task
        return self._response(request_id, {"status": "completed"})

    async def _handle_cancel(self, request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        session_id = str(params.get("sessionId") or "")
        if self._session_store.get(session_id) is None:
            return self._error(request_id, -32001, AWORLD_ACP_SESSION_NOT_FOUND)

        result = await self._turns.cancel_turn(session_id)
        return self._response(request_id, {"ok": True, "status": result})

    def _normalize_prompt_text(self, prompt: Any) -> str:
        if isinstance(prompt, str):
            text = prompt.strip()
            if text:
                return text

        if isinstance(prompt, dict):
            if isinstance(prompt.get("text"), str) and prompt["text"].strip():
                return prompt["text"].strip()

            content = prompt.get("content")
            if isinstance(content, list):
                parts: list[str] = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text" and isinstance(block.get("text"), str):
                        text = block["text"].strip()
                        if text:
                            parts.append(text)
                    elif (
                        block.get("type") == "resource"
                        and isinstance(block.get("text"), str)
                        and block["text"].strip()
                    ):
                        parts.append(block["text"].strip())

                if parts:
                    return "\n".join(parts)

        raise ValueError(AWORLD_ACP_UNSUPPORTED_PROMPT_CONTENT)

    async def _write_message(self, message: dict[str, Any]) -> None:
        payload = encode_jsonrpc_message(message)
        async with self._write_lock:
            sys.stdout.buffer.write(payload)
            sys.stdout.buffer.flush()

    @staticmethod
    def _response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": result,
        }

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": code,
                "message": message,
            },
        }

    @staticmethod
    def _notification(method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }


async def run_stdio_server() -> int:
    server = AcpStdioServer()
    return await server.run()
