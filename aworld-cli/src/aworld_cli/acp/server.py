from __future__ import annotations

import asyncio
import contextlib
import io
import os
import sys
from pathlib import Path
from typing import Any

from rich.console import Console

from aworld.models.model_response import ModelResponse
from aworld.output.base import MessageOutput
from aworld.runner import Runners

from .errors import (
    AWORLD_ACP_APPROVAL_UNSUPPORTED,
    AWORLD_ACP_INVALID_CWD,
    AWORLD_ACP_SESSION_BUSY,
    AWORLD_ACP_SESSION_NOT_FOUND,
    AWORLD_ACP_REQUIRES_HUMAN,
    AWORLD_ACP_UNSUPPORTED_MCP_SERVERS,
    AWORLD_ACP_UNSUPPORTED_PROMPT_CONTENT,
    AcpErrorDetail,
    AcpBusyError,
    build_error_data,
)
from .event_mapper import map_runtime_event_to_session_update
from .human_intercept import AcpRequiresHumanError
from .protocol import decode_jsonrpc_line, encode_jsonrpc_message
from .runtime_adapter import adapt_output_to_runtime_events
from .session_runtime import apply_requested_mcp_servers
from .session_store import AcpSessionRecord, AcpSessionStore
from .turn_controller import TurnController


class AcpExecutorOutputBridge:
    def __init__(
        self,
        *,
        registry_cls: Any | None = None,
        executor_cls: Any | None = None,
        init_agents_func: Any | None = None,
    ) -> None:
        if registry_cls is None:
            from aworld_cli.core.agent_registry import LocalAgentRegistry

            registry_cls = LocalAgentRegistry
        if executor_cls is None:
            from aworld_cli.executors.local import LocalAgentExecutor

            executor_cls = LocalAgentExecutor
        if init_agents_func is None:
            from aworld_cli.core.loader import init_agents

            init_agents_func = init_agents

        self._registry_cls = registry_cls
        self._executor_cls = executor_cls
        self._init_agents = init_agents_func
        self._loaded_agent_dirs: set[str] = set()
        self._execution_lock = asyncio.Lock()

    async def stream_outputs(
        self,
        *,
        record: AcpSessionRecord,
        prompt_text: str,
    ):
        async with self._execution_lock:
            previous_cwd = os.getcwd()
            os.chdir(record.cwd)
            executor = None
            restore_sandbox_state = lambda: None
            try:
                agent = await self._resolve_agent()
                if agent is None:
                    yield MessageOutput(
                        source=ModelResponse(
                            id=f"acp-fallback-{record.acp_session_id}",
                            model="aworld-cli/acp-fallback",
                            content=prompt_text,
                        )
                    )
                    return

                executor, restore_sandbox_state = await self._create_executor(
                    agent=agent,
                    record=record,
                )
                task = await executor._build_task(
                    prompt_text,
                    session_id=record.aworld_session_id,
                )
                outputs = Runners.streamed_run_task(task=task)
                async for output in outputs.stream_events():
                    yield output
            finally:
                if executor is not None:
                    cleanup = getattr(executor, "cleanup_resources", None)
                    if callable(cleanup):
                        cleanup_result = cleanup()
                        if asyncio.iscoroutine(cleanup_result):
                            await cleanup_result
                restore_sandbox_state()
                os.chdir(previous_cwd)

    async def _resolve_agent(self) -> Any | None:
        self._load_agents_from_env()

        requested_agent = (os.getenv("AWORLD_ACP_AGENT") or "").strip()
        if requested_agent:
            agent = self._registry_cls.get_agent(requested_agent)
            if agent is None:
                raise ValueError(f"ACP agent not found: {requested_agent}")
            return agent

        aworld_agent = self._registry_cls.get_agent("Aworld")
        if aworld_agent is not None:
            return aworld_agent

        agents = list(self._registry_cls.list_agents())
        if not agents:
            return None
        if len(agents) == 1:
            return agents[0]

        available = ", ".join(sorted(agent.name for agent in agents))
        raise ValueError(
            "Multiple ACP-capable agents are loaded. Set AWORLD_ACP_AGENT explicitly. "
            f"Available agents: {available}"
        )

    def _load_agents_from_env(self) -> None:
        raw_dirs = os.getenv("LOCAL_AGENTS_DIR") or os.getenv("AGENTS_DIR") or ""
        if not raw_dirs:
            return

        for raw_dir in raw_dirs.split(";"):
            agent_dir = raw_dir.strip()
            if not agent_dir or agent_dir in self._loaded_agent_dirs:
                continue
            self._loaded_agent_dirs.add(agent_dir)
            with contextlib.redirect_stdout(io.StringIO()):
                self._init_agents(agent_dir)

    async def _create_executor(
        self,
        *,
        agent: Any,
        record: AcpSessionRecord,
    ) -> tuple[Any, Any]:
        swarm = await self._get_swarm_with_context_fallback(agent)
        restore_sandbox_state = apply_requested_mcp_servers(
            swarm,
            record.requested_mcp_servers,
        )
        executor = self._executor_cls(
            swarm=swarm,
            context_config=getattr(agent, "context_config", None),
            console=Console(file=sys.stderr, force_terminal=False, color_system=None),
            session_id=record.aworld_session_id,
            hooks=getattr(agent, "hooks", None),
        )
        return executor, restore_sandbox_state

    @staticmethod
    async def _get_swarm_with_context_fallback(agent: Any) -> Any:
        context_config = getattr(agent, "context_config", None)
        try:
            return await agent.get_swarm(None)
        except (TypeError, AttributeError):
            from aworld.core.context.amni import ApplicationContext, TaskInput

            temp_task_input = TaskInput(
                user_id="acp_user",
                session_id="acp_bootstrap_session",
                task_id="acp_bootstrap_task",
                task_content="",
                origin_user_input="",
            )
            temp_context = await ApplicationContext.from_input(
                temp_task_input,
                context_config=context_config,
            )
            return await agent.get_swarm(temp_context)


class AcpStdioServer:
    def __init__(self, *, output_bridge: Any | None = None) -> None:
        self._session_store = AcpSessionStore()
        self._turns = TurnController()
        self._state_by_session: dict[str, dict[str, Any]] = {}
        self._write_lock = asyncio.Lock()
        self._output_bridge = output_bridge or AcpExecutorOutputBridge()

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
                detail = self._known_error_detail(str(exc))
                response = self._error(
                    request_id,
                    -32602,
                    str(exc),
                    data=build_error_data(detail) if detail is not None else None,
                )

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
        record = self._session_store.get(session_id)
        if record is None:
            return self._error(
                request_id,
                -32001,
                AWORLD_ACP_SESSION_NOT_FOUND,
                data=build_error_data(
                    AcpErrorDetail(
                        code=AWORLD_ACP_SESSION_NOT_FOUND,
                        message=AWORLD_ACP_SESSION_NOT_FOUND,
                    )
                ),
            )

        prompt_text = self._normalize_prompt_text(params.get("prompt"))
        state = self._state_by_session.setdefault(session_id, {})

        async def _run_turn() -> None:
            async for output in self._output_bridge.stream_outputs(
                record=record,
                prompt_text=prompt_text,
            ):
                for event in adapt_output_to_runtime_events(state, output):
                    update = map_runtime_event_to_session_update(session_id, event)
                    await self._write_message(self._notification("sessionUpdate", update))

        try:
            task = await self._turns.start_turn(session_id, _run_turn())
        except AcpBusyError:
            return self._error(
                request_id,
                -32002,
                AWORLD_ACP_SESSION_BUSY,
                data=build_error_data(
                    AcpErrorDetail(
                        code=AWORLD_ACP_SESSION_BUSY,
                        message=AWORLD_ACP_SESSION_BUSY,
                    )
                ),
            )

        try:
            await task
        except asyncio.CancelledError:
            return self._response(request_id, {"status": "cancelled"})
        except AcpRequiresHumanError:
            return self._error(
                request_id,
                -32010,
                AWORLD_ACP_REQUIRES_HUMAN,
                data=build_error_data(
                    AcpErrorDetail(
                        code=AWORLD_ACP_REQUIRES_HUMAN,
                        message=AWORLD_ACP_REQUIRES_HUMAN,
                        retryable=True,
                    )
                ),
            )
        return self._response(request_id, {"status": "completed"})

    async def _handle_cancel(self, request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        session_id = str(params.get("sessionId") or "")
        if self._session_store.get(session_id) is None:
            return self._error(
                request_id,
                -32001,
                AWORLD_ACP_SESSION_NOT_FOUND,
                data=build_error_data(
                    AcpErrorDetail(
                        code=AWORLD_ACP_SESSION_NOT_FOUND,
                        message=AWORLD_ACP_SESSION_NOT_FOUND,
                    )
                ),
            )

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
    def _error(
        request_id: Any,
        code: int,
        message: str,
        *,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        error = {
            "code": code,
            "message": message,
        }
        if data is not None:
            error["data"] = data

        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": error,
        }

    @staticmethod
    def _notification(method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }

    @staticmethod
    def _known_error_detail(message: str) -> AcpErrorDetail | None:
        if message == AWORLD_ACP_SESSION_NOT_FOUND:
            return AcpErrorDetail(code=message, message=message)
        if message == AWORLD_ACP_SESSION_BUSY:
            return AcpErrorDetail(code=message, message=message)
        if message == AWORLD_ACP_UNSUPPORTED_PROMPT_CONTENT:
            return AcpErrorDetail(code=message, message=message)
        if message == AWORLD_ACP_INVALID_CWD:
            return AcpErrorDetail(code=message, message=message)
        if message == AWORLD_ACP_UNSUPPORTED_MCP_SERVERS:
            return AcpErrorDetail(code=message, message=message)
        if message == AWORLD_ACP_REQUIRES_HUMAN:
            return AcpErrorDetail(code=message, message=message, retryable=True)
        if message == AWORLD_ACP_APPROVAL_UNSUPPORTED:
            return AcpErrorDetail(code=message, message=message, retryable=True)
        return None


async def run_stdio_server() -> int:
    server = AcpStdioServer()
    return await server.run()
