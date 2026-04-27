from __future__ import annotations

import asyncio
import contextlib
import io
import os
import sys
from pathlib import Path
from typing import Any

from rich.console import Console
from aworld.logs.util import logger

from aworld.models.model_response import ModelResponse
from aworld.output.base import MessageOutput
from aworld.runner import Runners

from .bootstrap import bootstrap_acp_plugins
from .cron_bridge import AcpCronBridge
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
from .plugin_runtime import AcpPluginRuntime
from .event_mapper import map_runtime_event_to_session_update
from .human_intercept import AcpRequiresHumanError
from .protocol import decode_jsonrpc_line, encode_jsonrpc_message
from .runtime_adapter import adapt_output_to_runtime_events
from .session_runtime import apply_requested_mcp_servers
from .session_store import AcpSessionRecord, AcpSessionStore
from .turn_controller import TurnController


_ERROR_DETAIL_MESSAGES = {
    AWORLD_ACP_REQUIRES_HUMAN: "Human approval/input flow is not bridged in phase 1.",
    AWORLD_ACP_APPROVAL_UNSUPPORTED: "Approval flow is not bridged in phase 1.",
}


class AcpExecutorOutputBridge:
    def __init__(
        self,
        *,
        registry_cls: Any | None = None,
        executor_cls: Any | None = None,
        init_agents_func: Any | None = None,
        bootstrap_func: Any | None = None,
        plugin_runtime_cls: Any | None = None,
        bootstrap_base_dir: Path | None = None,
    ) -> None:
        if registry_cls is None:
            from aworld_cli.core.agent_registry import LocalAgentRegistry

            registry_cls = LocalAgentRegistry
        if executor_cls is None:
            from .executor import AcpLocalExecutor

            executor_cls = AcpLocalExecutor
        if init_agents_func is None:
            from aworld_cli.core.loader import init_agents

            init_agents_func = init_agents
        if bootstrap_func is None:
            bootstrap_func = bootstrap_acp_plugins
        if plugin_runtime_cls is None:
            plugin_runtime_cls = AcpPluginRuntime

        self._registry_cls = registry_cls
        self._executor_cls = executor_cls
        self._init_agents = init_agents_func
        self._plugin_runtime_cls = plugin_runtime_cls
        self._loaded_agent_dirs: set[str] = set()
        self._agent_load_lock = asyncio.Lock()
        self._bootstrap = bootstrap_func(bootstrap_base_dir or Path.cwd())
        self._emit_bootstrap_warnings()

    async def stream_outputs(
        self,
        *,
        record: AcpSessionRecord,
        prompt_text: str,
    ):
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

    async def _resolve_agent(self) -> Any | None:
        await self._load_agents_from_bootstrap()
        await self._load_agents_from_env()

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

    async def _load_agents_from_env(self) -> None:
        raw_dirs = os.getenv("LOCAL_AGENTS_DIR") or os.getenv("AGENTS_DIR") or ""
        if not raw_dirs:
            return

        requested_dirs = [raw_dir.strip() for raw_dir in raw_dirs.split(";") if raw_dir.strip()]
        if not requested_dirs:
            return

        async with self._agent_load_lock:
            for agent_dir in requested_dirs:
                if agent_dir in self._loaded_agent_dirs:
                    continue
                self._loaded_agent_dirs.add(agent_dir)
                with contextlib.redirect_stdout(io.StringIO()):
                    self._init_agents(agent_dir)

    async def _load_agents_from_bootstrap(self) -> None:
        requested_dirs = self._bootstrap_agent_dirs()
        if not requested_dirs:
            return

        async with self._agent_load_lock:
            for agent_dir in requested_dirs:
                if agent_dir in self._loaded_agent_dirs:
                    continue
                self._loaded_agent_dirs.add(agent_dir)
                with contextlib.redirect_stdout(io.StringIO()):
                    self._init_agents(agent_dir)

    def _bootstrap_agent_dirs(self) -> list[str]:
        resolved_dirs: list[str] = []

        explicit_agent_dirs = self._bootstrap.get("agent_dirs") or []
        for agent_dir in explicit_agent_dirs:
            resolved = Path(agent_dir).expanduser().resolve()
            if resolved.exists() and resolved.is_dir():
                resolved_dirs.append(str(resolved))

        plugin_roots = self._bootstrap.get("plugin_roots") or []
        for plugin_root in plugin_roots:
            agent_dir = Path(plugin_root).expanduser().resolve() / "agents"
            if agent_dir.exists() and agent_dir.is_dir():
                resolved_dirs.append(str(agent_dir))

        return list(dict.fromkeys(resolved_dirs))

    def _emit_bootstrap_warnings(self) -> None:
        warnings = self._bootstrap.get("warnings") or []
        for warning in warnings:
            text = str(warning).strip()
            if not text:
                continue
            sys.stderr.write(f"[aworld-cli acp] {text}\n")
        if warnings:
            sys.stderr.flush()

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
            working_directory=record.cwd,
        )
        plugin_runtime = self._build_plugin_runtime(record)
        if plugin_runtime is not None:
            executor._base_runtime = plugin_runtime
        return executor, restore_sandbox_state

    def _build_plugin_runtime(self, record: AcpSessionRecord) -> Any | None:
        plugin_roots = [
            Path(plugin_root).expanduser().resolve()
            for plugin_root in (self._bootstrap.get("plugin_roots") or [])
        ]
        if not plugin_roots or self._plugin_runtime_cls is None:
            return None
        return self._plugin_runtime_cls(
            workspace_path=record.cwd,
            plugin_roots=plugin_roots,
            bootstrap=self._bootstrap,
        )

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
        self._cron_runtime_lock = asyncio.Lock()
        self._cron_runtime_started = False
        self._notification_center = None
        self._scheduler = None
        self._cron_bridge = AcpCronBridge(write_session_update=self._write_session_update)
        self._previous_notification_sink = None
        self._previous_progress_sink = None
        self._installed_notification_sink = None
        self._installed_progress_sink = None

    async def run(self) -> int:
        pending_requests: set[asyncio.Task[Any]] = set()
        try:
            while True:
                line = await asyncio.to_thread(sys.stdin.buffer.readline)
                if not line:
                    break

                request = decode_jsonrpc_line(line)
                if "method" not in request:
                    continue

                task = asyncio.create_task(self._dispatch_request(request))
                pending_requests.add(task)
                task.add_done_callback(pending_requests.discard)

            if pending_requests:
                await asyncio.gather(*pending_requests, return_exceptions=True)
            return 0
        finally:
            await self._shutdown()

    async def _dispatch_request(self, request: dict[str, Any]) -> None:
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
        self._cron_bridge.register_session(record.acp_session_id)
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

        try:
            prompt_text = self._normalize_prompt_text(params.get("prompt"))
        except ValueError as exc:
            detail = self._known_error_detail(str(exc))
            return self._error(
                request_id,
                -32602,
                str(exc),
                data=build_error_data(detail) if detail is not None else None,
            )

        await self._ensure_cron_runtime_started()

        state: dict[str, Any] = {}
        terminal_error: dict[str, Any] | None = None

        async def _run_turn() -> None:
            nonlocal terminal_error
            async for output in self._output_bridge.stream_outputs(
                record=record,
                prompt_text=prompt_text,
            ):
                events = self._normalize_runtime_events(state, output)
                for event in events:
                    if event.get("event_type") == "turn_error":
                        terminal_error = event
                        await self._close_open_tool_lifecycles_with_error(
                            session_id,
                            state,
                            code=str(event["code"]),
                            message=str(event["message"]),
                        )
                        return
                    if event.get("event_type") == "tool_start":
                        tool_call_id = event.get("tool_call_id")
                        if isinstance(tool_call_id, str):
                            state[f"tool_input::{tool_call_id}"] = event.get("raw_input")
                    if event.get("event_type") == "tool_end":
                        tool_name = event.get("tool_name")
                        tool_call_id = event.get("tool_call_id")
                        if isinstance(tool_name, str) and isinstance(tool_call_id, str):
                            state[f"tool_closed::{tool_name}"] = tool_call_id
                        self._cron_bridge.bind_from_tool_result(
                            session_id=session_id,
                            tool_name=tool_name if isinstance(tool_name, str) else None,
                            payload=event.get("raw_output"),
                            tool_input=state.get(f"tool_input::{tool_call_id}") if isinstance(tool_call_id, str) else None,
                        )
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
            await self._close_open_tool_lifecycles_with_error(
                session_id,
                state,
                code=AWORLD_ACP_REQUIRES_HUMAN,
                message=self._error_detail_message(AWORLD_ACP_REQUIRES_HUMAN),
            )
            return self._error(
                request_id,
                -32010,
                AWORLD_ACP_REQUIRES_HUMAN,
                data=build_error_data(
                    AcpErrorDetail(
                        code=AWORLD_ACP_REQUIRES_HUMAN,
                        message=self._error_detail_message(AWORLD_ACP_REQUIRES_HUMAN),
                        retryable=True,
                    )
                ),
            )
        except ValueError as exc:
            detail = self._known_error_detail(str(exc))
            if detail is not None:
                await self._close_open_tool_lifecycles_with_error(
                    session_id,
                    state,
                    code=detail.code,
                    message=detail.message,
                )
                return self._error(
                    request_id,
                    -32010,
                    detail.code,
                    data=build_error_data(detail),
                )
            raise
        if terminal_error is not None:
            detail = AcpErrorDetail(
                code=str(terminal_error["code"]),
                message=str(terminal_error.get("message"))
                if terminal_error.get("message") is not None
                else self._error_detail_message(str(terminal_error["code"])),
                retryable=bool(terminal_error.get("retryable")) if terminal_error.get("retryable") is not None else None,
                data={"origin": terminal_error.get("origin"), "detail": terminal_error.get("message")},
            )
            return self._error(
                request_id,
                -32010,
                str(terminal_error["code"]),
                data=build_error_data(detail),
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
                        continue

                    resource_text = self._extract_embedded_resource_text(block)
                    if resource_text:
                        parts.append(resource_text)
                        continue

                    resource_link_text = self._format_resource_link_reference(block)
                    if resource_link_text:
                        parts.append(resource_link_text)

                if parts:
                    return "\n".join(parts)

        raise ValueError(AWORLD_ACP_UNSUPPORTED_PROMPT_CONTENT)

    @staticmethod
    def _extract_embedded_resource_text(block: dict[str, Any]) -> str | None:
        if block.get("type") != "resource":
            return None

        if isinstance(block.get("text"), str) and block["text"].strip():
            return block["text"].strip()

        resource = block.get("resource")
        if not isinstance(resource, dict):
            return None

        if isinstance(resource.get("text"), str) and resource["text"].strip():
            return resource["text"].strip()

        content = resource.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()

        contents = resource.get("contents")
        if isinstance(contents, list):
            parts: list[str] = []
            for item in contents:
                if not isinstance(item, dict):
                    continue
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
            if parts:
                return "\n".join(parts)

        return None

    @staticmethod
    def _format_resource_link_reference(block: dict[str, Any]) -> str | None:
        if block.get("type") != "resource_link":
            return None

        link = block.get("resource") if isinstance(block.get("resource"), dict) else block
        title = None
        for key in ("title", "name", "label"):
            value = link.get(key)
            if isinstance(value, str) and value.strip():
                title = value.strip()
                break

        uri = None
        for key in ("uri", "url", "href", "path"):
            value = link.get(key)
            if isinstance(value, str) and value.strip():
                uri = value.strip()
                break

        if title and uri:
            return f"Resource link: {title} ({uri})"
        if uri:
            return f"Resource link: {uri}"
        if title:
            return f"Resource link: {title}"
        return None

    async def _write_message(self, message: dict[str, Any]) -> None:
        payload = encode_jsonrpc_message(message)
        async with self._write_lock:
            sys.stdout.buffer.write(payload)
            sys.stdout.buffer.flush()

    async def _write_session_update(self, session_id: str, update: dict[str, Any]) -> None:
        await self._write_message(
            self._notification(
                "sessionUpdate",
                {
                    "sessionId": session_id,
                    "update": update,
                },
            )
        )

    async def _ensure_cron_runtime_started(self) -> None:
        if self._cron_runtime_started:
            return

        async with self._cron_runtime_lock:
            if self._cron_runtime_started:
                return

            try:
                from aworld.core.scheduler import get_scheduler
                from aworld_cli.runtime.cron_notifications import CronNotificationCenter

                self._notification_center = CronNotificationCenter()
                self._scheduler = get_scheduler()
                self._previous_notification_sink = getattr(self._scheduler, "notification_sink", None)
                self._previous_progress_sink = getattr(self._scheduler, "progress_sink", None)

                async def _notification_sink(notification_data: dict[str, Any]) -> None:
                    previous_sink = self._previous_notification_sink
                    if previous_sink is not None:
                        previous_result = previous_sink(notification_data)
                        if asyncio.iscoroutine(previous_result):
                            await previous_result
                    if self._notification_center is not None:
                        await self._notification_center.publish(notification_data)
                    await self._cron_bridge.publish_notification(notification_data)

                async def _progress_sink(progress_data: dict[str, Any]) -> None:
                    previous_sink = self._previous_progress_sink
                    if previous_sink is not None:
                        previous_result = previous_sink(progress_data)
                        if asyncio.iscoroutine(previous_result):
                            await previous_result
                    if self._notification_center is not None:
                        await self._notification_center.publish_progress(progress_data)

                self._installed_notification_sink = _notification_sink
                self._installed_progress_sink = _progress_sink
                self._scheduler.notification_sink = _notification_sink
                self._scheduler.progress_sink = _progress_sink

                if not getattr(self._scheduler, "running", False):
                    await self._scheduler.start()

                self._cron_runtime_started = True
            except Exception as exc:
                logger.warning(f"Failed to bootstrap ACP cron runtime: {exc}")

    async def _shutdown(self) -> None:
        session_ids = list(self._state_by_session.keys())
        for session_id in session_ids:
            self._cron_bridge.unregister_session(session_id)

        scheduler = self._scheduler
        previous_notification_sink = self._previous_notification_sink
        previous_progress_sink = self._previous_progress_sink
        installed_notification_sink = self._installed_notification_sink
        installed_progress_sink = self._installed_progress_sink
        self._state_by_session.clear()
        self._cron_runtime_started = False
        self._scheduler = None
        self._notification_center = None
        self._previous_notification_sink = None
        self._previous_progress_sink = None
        self._installed_notification_sink = None
        self._installed_progress_sink = None

        if scheduler is None:
            return

        if getattr(scheduler, "notification_sink", None) is installed_notification_sink:
            scheduler.notification_sink = previous_notification_sink
        if getattr(scheduler, "progress_sink", None) is installed_progress_sink:
            scheduler.progress_sink = previous_progress_sink

        stop = getattr(scheduler, "stop", None)
        if callable(stop) and getattr(scheduler, "running", False):
            try:
                stop_result = stop()
                if asyncio.iscoroutine(stop_result):
                    await stop_result
            except Exception as exc:
                logger.warning(f"Failed to stop ACP cron runtime: {exc}")

    @staticmethod
    def _normalize_runtime_events(state: dict[str, Any], output: Any) -> list[dict[str, Any]]:
        if isinstance(output, dict) and isinstance(output.get("event_type"), str):
            return [output]
        return adapt_output_to_runtime_events(state, output)

    async def _close_open_tool_lifecycles_with_error(
        self,
        session_id: str,
        state: dict[str, Any],
        *,
        code: str,
        message: str,
    ) -> None:
        open_tool_pairs: list[tuple[str, str]] = []
        open_tool_calls = state.get("open_tool_calls")
        if isinstance(open_tool_calls, list):
            for item in open_tool_calls:
                if not isinstance(item, dict):
                    continue
                tool_name = item.get("tool_name")
                tool_call_id = item.get("tool_call_id")
                if isinstance(tool_name, str) and isinstance(tool_call_id, str):
                    open_tool_pairs.append((tool_name, tool_call_id))
        if not open_tool_pairs:
            open_tool_pairs = [
                (key[len("tool::") :], value)
                for key, value in state.items()
                if key.startswith("tool::") and isinstance(value, str)
            ]
        if not open_tool_pairs:
            return

        closed_ids = {
            value
            for key, value in state.items()
            if key.startswith("tool_closed::") and isinstance(value, str)
        }

        for tool_name, tool_call_id in open_tool_pairs:
            if tool_call_id in closed_ids:
                continue
            update = map_runtime_event_to_session_update(
                session_id,
                {
                    "event_type": "tool_end",
                    "seq": -1,
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "status": "failed",
                    "raw_output": {"code": code, "message": message},
                },
            )
            await self._write_message(self._notification("sessionUpdate", update))
            state[f"tool_closed::{tool_name}"] = tool_call_id
        if isinstance(open_tool_calls, list):
            open_tool_calls.clear()

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
            return AcpErrorDetail(
                code=message,
                message=AcpStdioServer._error_detail_message(message),
                retryable=True,
            )
        if message == AWORLD_ACP_APPROVAL_UNSUPPORTED:
            return AcpErrorDetail(
                code=message,
                message=AcpStdioServer._error_detail_message(message),
                retryable=True,
            )
        return None

    @staticmethod
    def _error_detail_message(code: str) -> str:
        return _ERROR_DETAIL_MESSAGES.get(code, code)


async def run_stdio_server() -> int:
    output_bridge = None
    if os.getenv("AWORLD_ACP_SELF_TEST_BRIDGE", "").strip().lower() in {"1", "true", "yes"}:
        from .self_test_bridge import DeterministicSelfTestOutputBridge

        output_bridge = DeterministicSelfTestOutputBridge()

    server = AcpStdioServer(output_bridge=output_bridge)
    return await server.run()
