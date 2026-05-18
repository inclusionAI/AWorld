from __future__ import annotations

import asyncio
import contextlib
import io
import json
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
from ..core.command_bridge import CommandBridge
from ..core.tool_filter import temporary_tool_filter
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
from ..steering import SessionSteeringRuntime, SteeringCoordinator, STEERING_CAPTURED_ACK
from ..steering.observability import (
    log_applied_steering_event,
    log_queued_steering_event,
)
from .turn_controller import TurnController


_ERROR_DETAIL_MESSAGES = {
    AWORLD_ACP_REQUIRES_HUMAN: "Human approval/input flow is not bridged in phase 1.",
    AWORLD_ACP_APPROVAL_UNSUPPORTED: "Approval flow is not bridged in phase 1.",
}

_PAUSE_NOTICE_MESSAGES = {
    AWORLD_ACP_REQUIRES_HUMAN: "Execution paused. Send another prompt to steer the task forward.",
    AWORLD_ACP_APPROVAL_UNSUPPORTED: (
        "Execution paused at an approval boundary. Send another prompt to steer the task forward."
    ),
}


def _legacy_human_error_mode_enabled() -> bool:
    raw = os.getenv("AWORLD_ACP_LEGACY_HUMAN_ERROR_MODE", "").strip().lower()
    return raw in {"1", "true", "yes"}


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
        self._steering = SteeringCoordinator()
        self._emit_bootstrap_warnings()

    async def stream_outputs(
        self,
        *,
        record: AcpSessionRecord,
        prompt_text: str,
        allowed_tools: list[str] | None = None,
    ):
        executor = None
        restore_sandbox_state = lambda: None
        try:
            agent = await self._resolve_agent()
            if agent is None:
                raise ValueError(
                    "No ACP-capable agent found. Ensure agent bundles are loaded. "
                    "Set AWORLD_ACP_AGENT explicitly or check bootstrap configuration."
                )

            executor, restore_sandbox_state = await self._create_executor(
                agent=agent,
                record=record,
            )
            swarm = getattr(executor, "swarm", None)
            with temporary_tool_filter(swarm, allowed_tools):
                current_prompt = prompt_text
                while True:
                    task = await executor._build_task(
                        current_prompt,
                        session_id=record.aworld_session_id,
                    )
                    task_id = self._task_id(task, fallback=f"acp-{record.aworld_session_id}")
                    executor.context = getattr(task, "context", None)
                    self._steering.begin_task(record.aworld_session_id, task_id)
                    outputs = Runners.streamed_run_task(task=task)
                    chunks: list[str] = []
                    saw_chunk_output = False
                    paused = False

                    async for output in outputs.stream_events():
                        output_type = self._output_type(output)
                        if output_type == "chunk":
                            raw_chunk = getattr(output, "data", None)
                            tool_calls = getattr(raw_chunk, "tool_calls", None) or []
                            if tool_calls and await self._should_pause_for_queued_steering_checkpoint(
                                executor=executor,
                                task_id=task_id,
                                checkpoint="before_tool_call",
                                current_tool=self._tool_name_from_call(tool_calls[0]),
                                partial_answer="".join(chunks).strip(),
                            ):
                                paused = True
                                break

                        chunk = self._extract_visible_text(output)
                        if output_type == "message" and saw_chunk_output:
                            chunk = ""
                        if output_type == "chunk":
                            saw_chunk_output = True
                        if chunk:
                            chunks.append(chunk)

                        yield output

                        if output_type == "message" and await self._should_pause_for_queued_steering_checkpoint(
                            executor=executor,
                            task_id=task_id,
                            checkpoint="after_message_output",
                            current_tool=self._tool_name_from_output(output),
                            partial_answer="".join(chunks).strip(),
                        ):
                            paused = True
                            break

                        if output_type == "tool_call_result" and await self._should_pause_for_queued_steering_checkpoint(
                            executor=executor,
                            task_id=task_id,
                            checkpoint="after_tool_result",
                            current_tool=getattr(output, "tool_name", None),
                            partial_answer="".join(chunks).strip(),
                        ):
                            paused = True
                            break

                    follow_up_prompt, drained_items, _interrupt_requested = self._steering.consume_terminal_fallback(
                        record.aworld_session_id
                    )
                    if not follow_up_prompt:
                        break

                    context = getattr(executor, "context", None)
                    log_applied_steering_event(
                        workspace_path=getattr(context, "workspace_path", None) or record.cwd,
                        session_id=record.aworld_session_id,
                        task_id=getattr(context, "task_id", None),
                        steering_items=drained_items,
                        checkpoint="acp_follow_up",
                    )
                    current_prompt = follow_up_prompt
                    if not paused:
                        continue
        finally:
            self._steering.end_task(record.aworld_session_id, clear_pending=True)
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
        executor._base_runtime = SessionSteeringRuntime(
            workspace_path=record.cwd,
            base_runtime=plugin_runtime,
            steering=self._steering,
        )
        executor._allow_session_steering_checkpoints = True
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

    def queue_steering(self, *, record: AcpSessionRecord, text: str) -> str:
        self._steering.begin_task(record.aworld_session_id, f"acp-{record.aworld_session_id}")
        item = self._steering.enqueue_text(record.aworld_session_id, text)
        self._steering.request_interrupt(record.aworld_session_id)
        snapshot = self._steering.snapshot(record.aworld_session_id)
        log_queued_steering_event(
            workspace_path=record.cwd,
            session_id=record.aworld_session_id,
            task_id=snapshot.get("task_id") if isinstance(snapshot.get("task_id"), str) else None,
            steering_item=item,
        )
        return STEERING_CAPTURED_ACK

    def prepare_paused_resume_prompt(
        self,
        *,
        record: AcpSessionRecord,
        text: str,
    ) -> tuple[str, list[object]]:
        self._steering.begin_task(record.aworld_session_id, f"acp-{record.aworld_session_id}")
        self._steering.enqueue_text(record.aworld_session_id, text)
        follow_up_prompt, drained_items, _interrupt_requested = self._steering.consume_terminal_fallback(
            record.aworld_session_id
        )
        if not follow_up_prompt:
            raise ValueError("expected paused steering follow-up prompt")
        return follow_up_prompt, drained_items

    @staticmethod
    def _output_type(output: Any) -> str:
        output_type_getter = getattr(output, "output_type", None)
        return output_type_getter() if callable(output_type_getter) else ""

    @classmethod
    def _extract_visible_text(cls, output: Any) -> str:
        output_type = cls._output_type(output)
        if output_type in {"tool_call", "tool_call_result", "finished_signal", "step"}:
            return ""
        if output_type == "message":
            response = getattr(output, "response", None)
            if isinstance(response, str):
                return response
        for attr_name in ("content", "payload"):
            value = getattr(output, attr_name, None)
            if isinstance(value, str):
                return value
        data = getattr(output, "data", None)
        if isinstance(data, str):
            return data
        if data is not None:
            data_content = getattr(data, "content", None)
            if isinstance(data_content, str):
                return data_content
        source = getattr(output, "source", None)
        if source is not None:
            source_content = getattr(source, "content", None)
            if isinstance(source_content, str):
                return source_content
        return ""

    @staticmethod
    def _tool_name_from_call(tool_call: Any) -> str | None:
        tool_data = getattr(tool_call, "data", tool_call)
        function = getattr(tool_data, "function", None)
        name = getattr(function, "name", None)
        return str(name).strip() if isinstance(name, str) and name.strip() else None

    @classmethod
    def _tool_name_from_output(cls, output: Any) -> str | None:
        tool_calls = getattr(output, "tool_calls", None)
        if tool_calls:
            return cls._tool_name_from_call(tool_calls[0])
        source = getattr(output, "source", None)
        source_tool_calls = getattr(source, "tool_calls", None)
        if source_tool_calls:
            return cls._tool_name_from_call(source_tool_calls[0])
        return None

    @staticmethod
    def _task_id(task: Any, *, fallback: str) -> str:
        task_id = getattr(task, "id", None)
        return str(task_id).strip() if isinstance(task_id, str) and task_id.strip() else fallback

    @staticmethod
    async def _should_pause_for_queued_steering_checkpoint(
        *,
        executor: Any,
        task_id: str,
        checkpoint: str,
        current_tool: str | None,
        partial_answer: str,
    ) -> bool:
        checker = getattr(executor, "_should_pause_for_queued_steering_checkpoint", None)
        if not callable(checker):
            return False
        return await checker(
            task_id=task_id,
            checkpoint=checkpoint,
            current_tool=current_tool,
            partial_answer=partial_answer,
        )


class AcpStdioServer:
    def __init__(
        self,
        *,
        output_bridge: Any | None = None,
        command_bridge: Any | None = None,
    ) -> None:
        self._session_store = AcpSessionStore()
        self._turns = TurnController()
        self._state_by_session: dict[str, dict[str, Any]] = {}
        self._write_lock = asyncio.Lock()
        self._output_bridge = output_bridge or AcpExecutorOutputBridge()
        self._command_bridge = command_bridge or CommandBridge()
        self._cron_runtime_lock = asyncio.Lock()
        self._cron_runtime_started = False
        self._notification_center = None
        self._scheduler = None
        self._cron_bridge = AcpCronBridge(write_session_update=self._write_session_update_for_session)
        self._previous_notification_sink = None
        self._previous_progress_sink = None
        self._installed_notification_sink = None
        self._installed_progress_sink = None
        self._session_update_method = "sessionUpdate"

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
                protocol_version = params.get("protocolVersion")
                if isinstance(protocol_version, int) and protocol_version >= 1:
                    self._session_update_method = "session/update"
                response = self._response(
                    request_id,
                    {
                        "protocolVersion": "0.1",
                        "serverInfo": {"name": "aworld-cli", "version": "0.1"},
                        "agentCapabilities": {"loadSession": False},
                    },
                )
            elif method in ("newSession", "session/new"):
                response = self._response(request_id, self._handle_new_session(params))
            elif method in ("prompt", "session/prompt"):
                response = await self._handle_prompt(request_id, params)
            elif method in ("cancel", "session/cancel"):
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

        if request_id is not None:
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

        resume_paused = self._turns.is_paused(session_id)

        if (
            not resume_paused
            and self._turns.has_active_turn(session_id)
            and hasattr(self._output_bridge, "queue_steering")
        ):
            ack_text = self._output_bridge.queue_steering(record=record, text=prompt_text)
            await self._write_session_update_for_session(
                session_id,
                {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"text": ack_text},
                },
            )
            return self._response(request_id, {"status": "queued"})

        async def _run_streaming_prompt(
            *,
            executed_prompt_text: str,
            allowed_tools: list[str] | None = None,
            resume_paused: bool = False,
        ) -> dict[str, Any]:
            state: dict[str, Any] = {}
            terminal_error: dict[str, Any] | None = None
            paused_code: str | None = None

            async def _run_turn() -> None:
                nonlocal terminal_error, paused_code
                stream_kwargs: dict[str, Any] = {
                    "record": record,
                    "prompt_text": executed_prompt_text,
                }
                if allowed_tools is not None:
                    stream_kwargs["allowed_tools"] = allowed_tools

                try:
                    async for output in self._output_bridge.stream_outputs(**stream_kwargs):
                        events = self._normalize_runtime_events(state, output)
                        for event in events:
                            if event.get("event_type") == "turn_error":
                                event_code = str(event["code"])
                                if (
                                    not _legacy_human_error_mode_enabled()
                                    and self._is_happy_compatible_pause_code(event_code)
                                ):
                                    paused_code = event_code
                                    await self._close_open_tool_lifecycles_with_error(
                                        session_id,
                                        state,
                                        code=event_code,
                                        message=str(event["message"]),
                                    )
                                    self._turns.pause_turn(session_id)
                                    await self._emit_pause_notice(session_id, code=event_code)
                                    return
                                terminal_error = event
                                await self._close_open_tool_lifecycles_with_error(
                                    session_id,
                                    state,
                                    code=event_code,
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
                                    tool_input=(
                                        state.get(f"tool_input::{tool_call_id}")
                                        if isinstance(tool_call_id, str)
                                        else None
                                    ),
                                )
                            update = map_runtime_event_to_session_update(session_id, event)
                            await self._write_session_update(update)
                except AcpRequiresHumanError:
                    if _legacy_human_error_mode_enabled():
                        raise
                    paused_code = AWORLD_ACP_REQUIRES_HUMAN
                    await self._close_open_tool_lifecycles_with_error(
                        session_id,
                        state,
                        code=AWORLD_ACP_REQUIRES_HUMAN,
                        message=self._error_detail_message(AWORLD_ACP_REQUIRES_HUMAN),
                    )
                    self._turns.pause_turn(session_id)
                    await self._emit_pause_notice(session_id, code=AWORLD_ACP_REQUIRES_HUMAN)
                except ValueError as exc:
                    detail = self._known_error_detail(str(exc))
                    if detail is not None and _legacy_human_error_mode_enabled():
                        raise
                    if detail is not None and self._is_happy_compatible_pause_code(detail.code):
                        paused_code = detail.code
                        await self._close_open_tool_lifecycles_with_error(
                            session_id,
                            state,
                            code=detail.code,
                            message=detail.message,
                        )
                        self._turns.pause_turn(session_id)
                        await self._emit_pause_notice(session_id, code=detail.code)
                        return
                    raise

            try:
                if resume_paused:
                    task = await self._turns.resume_turn(session_id, _run_turn())
                else:
                    task = await self._turns.start_turn(session_id, _run_turn())
            except AcpBusyError:
                if hasattr(self._output_bridge, "queue_steering"):
                    ack_text = self._output_bridge.queue_steering(
                        record=record,
                        text=executed_prompt_text,
                    )
                    await self._write_session_update_for_session(
                        session_id,
                        {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"text": ack_text},
                        },
                    )
                    return self._response(request_id, {"status": "queued"})
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

            if paused_code is not None and not _legacy_human_error_mode_enabled():
                return self._response(request_id, {"status": "completed"})
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

        if resume_paused:
            resume_prompt = self._prepare_paused_resume_prompt(
                record=record,
                steering_text=prompt_text,
            )
            return await _run_streaming_prompt(
                executed_prompt_text=resume_prompt,
                resume_paused=True,
            )

        prompt_command_response: dict[str, Any] | None = None

        async def _execute_prompt_command(
            *,
            prompt: str,
            allowed_tools: list[str] | None,
            on_output=None,
        ) -> str:
            nonlocal prompt_command_response
            prompt_command_response = await _run_streaming_prompt(
                executed_prompt_text=prompt,
                allowed_tools=allowed_tools,
            )
            return ""

        command_result = await self._command_bridge.execute(
            text=prompt_text,
            cwd=record.cwd,
            session_id=session_id,
            runtime=self,
            prompt_executor=_execute_prompt_command,
        )
        if command_result.handled:
            if prompt_command_response is not None:
                return prompt_command_response
            if command_result.text:
                await self._write_session_update_for_session(
                    session_id,
                    {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"text": command_result.text},
                    },
                )
            return self._response(request_id, {"status": "completed"})

        return await _run_streaming_prompt(executed_prompt_text=prompt_text)

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

        if isinstance(prompt, list):
            text = self._normalize_content_blocks(prompt)
            if text:
                return text

        if isinstance(prompt, dict):
            if isinstance(prompt.get("text"), str) and prompt["text"].strip():
                return prompt["text"].strip()

            content = prompt.get("content")
            if isinstance(content, list):
                text = self._normalize_content_blocks(content)
                if text:
                    return text

        raise ValueError(AWORLD_ACP_UNSUPPORTED_PROMPT_CONTENT)

    def _normalize_content_blocks(self, content: list[Any]) -> str | None:
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
        return None

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

    async def _write_session_update_for_session(self, session_id: str, update: dict[str, Any]) -> None:
        await self._write_session_update(
            {
                "sessionId": session_id,
                "update": update,
            }
        )

    async def _write_session_update(self, params: dict[str, Any]) -> None:
        method = self._session_update_method
        if method == "session/update":
            params = self._to_current_session_update(params)
        self._log_session_update_summary(method, params)
        await self._write_message(self._notification(method, params))

    @staticmethod
    def _should_emit_current_session_update(params: dict[str, Any]) -> bool:
        return True

    @staticmethod
    def _log_session_update_summary(method: str, params: dict[str, Any]) -> None:
        update = params.get("update")
        if not isinstance(update, dict):
            return

        summary = [f"ACP session update method={method}"]

        session_id = params.get("sessionId")
        if isinstance(session_id, str) and session_id.strip():
            summary.append(f"session_id={session_id.strip()}")

        update_type = update.get("sessionUpdate")
        if isinstance(update_type, str) and update_type.strip():
            summary.append(f"type={update_type.strip()}")

        tool_call_id = update.get("toolCallId")
        if isinstance(tool_call_id, str) and tool_call_id.strip():
            summary.append(f"tool_call_id={tool_call_id.strip()}")

        kind = update.get("kind")
        if isinstance(kind, str) and kind.strip():
            summary.append(f"kind={kind.strip()}")

        title = AcpStdioServer._session_update_summary_title(update)
        if title:
            summary.append(f"title={title}")

        status = update.get("status")
        if isinstance(status, str) and status.strip():
            summary.append(f"status={status.strip()}")

        preview = AcpStdioServer._session_update_preview(update)
        if preview:
            summary.append(f"preview={preview}")

        logger.info(" ".join(summary))

    @staticmethod
    def _session_update_summary_title(update: dict[str, Any]) -> str | None:
        content = update.get("content")
        if isinstance(content, dict):
            tool_call = content.get("toolCall")
            if isinstance(tool_call, dict):
                nested_title = tool_call.get("title")
                if isinstance(nested_title, str) and nested_title.strip():
                    return nested_title.strip()

            command_title = AcpStdioServer._command_title(content.get("command"))
            if command_title:
                return command_title

        title = update.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
        return None

    @staticmethod
    def _session_update_preview(update: dict[str, Any]) -> str | None:
        content = update.get("content")
        if isinstance(content, dict):
            text = content.get("text")
            if isinstance(text, str):
                return AcpStdioServer._preview_text(text)
            stdout = content.get("stdout")
            if isinstance(stdout, str):
                return AcpStdioServer._preview_text(stdout)
            stderr = content.get("stderr")
            if isinstance(stderr, str):
                return AcpStdioServer._preview_text(stderr)
        if isinstance(content, str):
            return AcpStdioServer._preview_text(content)
        return None

    @staticmethod
    def _preview_text(value: str, *, limit: int = 80) -> str | None:
        normalized = " ".join(value.split())
        if not normalized:
            return None
        if len(normalized) <= limit:
            return normalized
        return f"{normalized[: limit - 3]}..."

    @staticmethod
    def _to_current_session_update(params: dict[str, Any]) -> dict[str, Any]:
        converted = dict(params)
        update = dict(converted.get("update") or {})
        converted["update"] = update
        update_type = update.get("sessionUpdate")

        if update_type in {"agent_message_chunk", "agent_thought_chunk", "user_message_chunk"}:
            content = update.get("content")
            if isinstance(content, dict) and isinstance(content.get("text"), str) and "type" not in content:
                update["content"] = {"type": "text", "text": content["text"]}
            return converted

        if update_type == "tool_call":
            raw_input = update.get("content")
            update["kind"] = AcpStdioServer._current_tool_kind(update.get("kind"))
            command_title = (
                AcpStdioServer._command_title(raw_input.get("command"))
                if update["kind"] == "execute" and isinstance(raw_input, dict)
                else None
            )
            update["title"] = command_title or str(update.get("title") or update.get("kind") or "tool")
            update["rawInput"] = raw_input
            update["content"] = AcpStdioServer._current_tool_input_content(update["kind"], raw_input)
            return converted

        if update_type == "tool_call_update":
            raw_output = update.get("content")
            title = str(update.get("title") or update.get("kind") or "tool")
            update["title"] = title
            update["kind"] = AcpStdioServer._current_tool_kind(update.get("kind"))
            update["status"] = AcpStdioServer._current_tool_status(update.get("status"))
            update["rawOutput"] = raw_output
            update["content"] = AcpStdioServer._current_tool_output_content(raw_output)
            return converted

        return converted

    @staticmethod
    def _current_tool_kind(kind: Any) -> str:
        normalized = str(kind or "").strip()
        lowered = normalized.lower()
        if normalized in {"Agent", "Task", "AskUserQuestion"}:
            return normalized
        if lowered in {"read", "edit", "delete", "move", "search", "execute", "think", "fetch", "switch_mode", "step"}:
            return lowered
        if lowered in {"shell", "terminal", "bash", "command"}:
            return "execute"
        if "spawn_subagent" in lowered or "subagent" in lowered:
            return "Agent"
        if "ask_user" in lowered or "question" in lowered:
            return "AskUserQuestion"
        if lowered.startswith("task") or lowered.endswith("_task"):
            return "Task"
        if lowered:
            return "think"
        return "other"

    @staticmethod
    def _current_tool_status(status: Any) -> str | None:
        if status is None:
            return None
        normalized = str(status).strip().lower()
        if normalized in {"pending", "in_progress", "completed", "failed"}:
            return normalized
        if normalized == "running":
            return "in_progress"
        if normalized in {"cancelled", "canceled", "error"}:
            return "failed"
        return "completed"

    @staticmethod
    def _current_tool_input_content(kind: str, value: Any) -> list[dict[str, Any]]:
        if kind == "execute" and isinstance(value, dict):
            command_title = AcpStdioServer._command_title(value.get("command"))
            if command_title:
                return AcpStdioServer._current_tool_text_content(command_title)
        return AcpStdioServer._current_tool_content(value)

    @staticmethod
    def _current_tool_output_content(value: Any) -> list[dict[str, Any]]:
        return AcpStdioServer._current_tool_content(value)

    @staticmethod
    def _current_tool_content(value: Any) -> list[dict[str, Any]]:
        if value is None:
            return []
        if isinstance(value, str):
            text = value
        else:
            try:
                text = json.dumps(value, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                text = str(value)
        if not text:
            return []
        return AcpStdioServer._current_tool_text_content(text)

    @staticmethod
    def _current_tool_text_content(text: str) -> list[dict[str, Any]]:
        return [
            {
                "type": "content",
                "content": {
                    "type": "text",
                    "text": text,
                },
            }
        ]

    @staticmethod
    def _command_title(command: Any) -> str | None:
        if isinstance(command, str):
            stripped = command.strip()
            return stripped or None
        if isinstance(command, list):
            parts = [str(part).strip() for part in command if str(part).strip()]
            return " ".join(parts) or None
        return None

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
            await self._write_session_update(update)
            state[f"tool_closed::{tool_name}"] = tool_call_id
        if isinstance(open_tool_calls, list):
            open_tool_calls.clear()

    def _prepare_paused_resume_prompt(
        self,
        *,
        record: AcpSessionRecord,
        steering_text: str,
    ) -> str:
        preparer = getattr(self._output_bridge, "prepare_paused_resume_prompt", None)
        if callable(preparer):
            follow_up_prompt, _drained_items = preparer(record=record, text=steering_text)
            return follow_up_prompt
        return (
            "Continue the current task with this additional operator steering:\n\n"
            f"1. {steering_text.strip()}"
        )

    async def _emit_pause_notice(self, session_id: str, *, code: str) -> None:
        text = _PAUSE_NOTICE_MESSAGES.get(
            code,
            "Execution paused. Send another prompt to steer the task forward.",
        )
        await self._write_session_update_for_session(
            session_id,
            {
                "sessionUpdate": "agent_message_chunk",
                "content": {"text": text},
            },
        )

    @staticmethod
    def _is_happy_compatible_pause_code(code: str) -> bool:
        return code in {AWORLD_ACP_REQUIRES_HUMAN, AWORLD_ACP_APPROVAL_UNSUPPORTED}

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
