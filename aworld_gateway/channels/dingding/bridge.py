from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from inspect import isawaitable
from pathlib import Path
from typing import Any

from aworld.runner import Runners

from aworld_gateway.channels.dingding.types import DingdingBridgeResult
from aworld_cli.core.tool_filter import temporary_tool_filter
from aworld_cli.steering import STEERING_CAPTURED_ACK, SessionSteeringRuntime
from aworld_cli.steering.observability import (
    log_applied_steering_event,
    log_queued_steering_event,
)

try:
    from aworld.core.context.amni import ApplicationContext, TaskInput
except ImportError:  # pragma: no cover
    ApplicationContext = None
    TaskInput = None


class AworldDingdingBridge:
    def __init__(self, registry_cls: Any = None, executor_cls: Any = None) -> None:
        if registry_cls is None:
            from aworld_cli.core.agent_registry import LocalAgentRegistry

            registry_cls = LocalAgentRegistry
        if executor_cls is None:
            from aworld_cli.executors.local import LocalAgentExecutor

            executor_cls = LocalAgentExecutor

        self._registry_cls = registry_cls
        self._executor_cls = executor_cls
        self._session_runtime_by_id: dict[str, SessionSteeringRuntime] = {}
        self._active_run_by_session: dict[str, asyncio.Task[Any]] = {}
        self._session_state_lock = asyncio.Lock()

    async def run(
        self,
        agent_id: str,
        session_id: str,
        text: Any,
        on_text_chunk: Callable[[str], Any] | None = None,
        on_output: Callable[[Any], Any] | None = None,
        allowed_tools: list[str] | None = None,
    ) -> DingdingBridgeResult:
        runtime = self._runtime_for_session(session_id)
        current_task = asyncio.current_task()
        async with self._session_state_lock:
            active_task = self._active_run_by_session.get(session_id)
            if active_task is not None and active_task.done():
                self._active_run_by_session.pop(session_id, None)
                active_task = None
            if active_task is not None and active_task is not current_task:
                return DingdingBridgeResult(
                    text=self._queue_session_steering(
                        runtime=runtime,
                        session_id=session_id,
                        text=text,
                    )
                )
            if active_task is None:
                self._active_run_by_session[session_id] = current_task

        executor = None
        agent = self._registry_cls.get_agent(agent_id)
        try:
            if agent is None:
                raise ValueError(f"Agent not found: {agent_id}")

            swarm = await self._get_swarm_with_context_fallback(agent)
            executor = self._executor_cls(
                swarm=swarm,
                context_config=getattr(agent, "context_config", None),
                session_id=session_id,
                hooks=getattr(agent, "hooks", None),
            )
            executor._base_runtime = runtime
            executor._allow_session_steering_checkpoints = True
            runtime._steering.begin_task(session_id, f"dingtalk-{session_id}")
            with temporary_tool_filter(swarm, allowed_tools):
                result_text = await self._run_with_session_steering(
                    executor=executor,
                    text=text,
                    session_id=session_id,
                    on_text_chunk=on_text_chunk,
                    on_output=on_output,
                )
            return DingdingBridgeResult(text=result_text)
        finally:
            runtime._steering.end_task(session_id, clear_pending=True)
            await self._release_active_session(session_id, current_task)
            cleanup = getattr(executor, "cleanup_resources", None)
            if cleanup is not None:
                cleanup_result = cleanup()
                if isawaitable(cleanup_result):
                    await cleanup_result

    def _runtime_for_session(self, session_id: str) -> SessionSteeringRuntime:
        runtime = self._session_runtime_by_id.get(session_id)
        if runtime is None:
            runtime = SessionSteeringRuntime(workspace_path=str(Path.cwd()))
            self._session_runtime_by_id[session_id] = runtime
        return runtime

    async def _release_active_session(
        self,
        session_id: str,
        task: asyncio.Task[Any] | None,
    ) -> None:
        async with self._session_state_lock:
            active_task = self._active_run_by_session.get(session_id)
            if active_task is task:
                self._active_run_by_session.pop(session_id, None)

    def _queue_session_steering(
        self,
        *,
        runtime: SessionSteeringRuntime,
        session_id: str,
        text: Any,
    ) -> str:
        normalized = str(text).strip() if not isinstance(text, str) else text.strip()
        item = runtime._steering.enqueue_text(session_id, normalized)
        runtime.request_session_interrupt(session_id)
        snapshot = runtime.steering_snapshot(session_id)
        log_queued_steering_event(
            workspace_path=runtime.workspace_path,
            session_id=session_id,
            task_id=snapshot.get("task_id") if isinstance(snapshot.get("task_id"), str) else None,
            steering_item=item,
        )
        return STEERING_CAPTURED_ACK

    async def _run_with_session_steering(
        self,
        *,
        executor: Any,
        text: Any,
        session_id: str,
        on_text_chunk: Callable[[str], Any] | None = None,
        on_output: Callable[[Any], Any] | None = None,
    ) -> str:
        current_text = text
        result_text = ""
        runtime = getattr(executor, "_base_runtime", None)
        while True:
            result_text = await self._run_single_round(
                executor=executor,
                text=current_text,
                session_id=session_id,
                on_text_chunk=on_text_chunk,
                on_output=on_output,
            )
            if runtime is None or not hasattr(runtime, "_steering"):
                return result_text

            follow_up_prompt, drained_items, _interrupt_requested = runtime._steering.consume_terminal_fallback(session_id)
            if not follow_up_prompt:
                return result_text

            context = getattr(executor, "context", None)
            log_applied_steering_event(
                workspace_path=getattr(context, "workspace_path", None) or getattr(runtime, "workspace_path", None),
                session_id=session_id,
                task_id=getattr(context, "task_id", None),
                steering_items=drained_items,
                checkpoint="dingtalk_follow_up",
            )
            current_text = follow_up_prompt

    async def _run_single_round(
        self,
        *,
        executor: Any,
        text: Any,
        session_id: str,
        on_text_chunk: Callable[[str], Any] | None = None,
        on_output: Callable[[Any], Any] | None = None,
    ) -> str:
        chunks: list[str] = []
        saw_chunk_output = False
        task = await executor._build_task(text, session_id=session_id)
        task_id = self._task_id(task, fallback=f"dingtalk-{session_id}")
        executor.context = getattr(task, "context", None)
        runtime = getattr(executor, "_base_runtime", None)
        steering = getattr(runtime, "_steering", None) if runtime is not None else None
        if steering is not None and session_id:
            steering.begin_task(session_id, task_id)
        outputs = Runners.streamed_run_task(task=task)

        async for output in outputs.stream_events():
            if on_output is not None:
                output_callback_result = on_output(output)
                if isawaitable(output_callback_result):
                    await output_callback_result

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
                    return "".join(chunks).strip()

            chunk = self._extract_visible_text(output)
            if output_type == "message" and saw_chunk_output:
                chunk = ""
            if output_type == "chunk":
                saw_chunk_output = True
            if chunk:
                chunks.append(chunk)
                if on_text_chunk is not None:
                    callback_result = on_text_chunk(chunk)
                    if isawaitable(callback_result):
                        await callback_result

            if output_type == "message" and await self._should_pause_for_queued_steering_checkpoint(
                executor=executor,
                task_id=task_id,
                checkpoint="after_message_output",
                current_tool=self._tool_name_from_output(output),
                partial_answer="".join(chunks).strip(),
            ):
                return "".join(chunks).strip()

            if output_type == "tool_call_result" and await self._should_pause_for_queued_steering_checkpoint(
                executor=executor,
                task_id=task_id,
                checkpoint="after_tool_result",
                current_tool=getattr(output, "tool_name", None),
                partial_answer="".join(chunks).strip(),
            ):
                return "".join(chunks).strip()

        return "".join(chunks).strip()

    async def _stream_outputs(
        self,
        *,
        executor: Any,
        text: Any,
        session_id: str,
    ) -> AsyncIterator[Any]:
        task = await executor._build_task(text, session_id=session_id)
        outputs = Runners.streamed_run_task(task=task)

        async for output in outputs.stream_events():
            yield output

    async def _stream_text(
        self,
        *,
        executor: Any,
        text: Any,
        session_id: str,
    ) -> AsyncIterator[str]:
        saw_chunk_output = False
        async for output in self._stream_outputs(
            executor=executor,
            text=text,
            session_id=session_id,
        ):
            output_type = self._output_type(output)
            chunk = self._extract_visible_text(output)
            if output_type == "message" and saw_chunk_output:
                chunk = ""
            if chunk:
                if output_type == "chunk":
                    saw_chunk_output = True
                yield chunk

    @staticmethod
    def _output_type(output: Any) -> str:
        output_type_getter = getattr(output, "output_type", None)
        return output_type_getter() if callable(output_type_getter) else ""

    @staticmethod
    async def _get_swarm_with_context_fallback(agent: Any, refresh: bool = False) -> Any:
        context_config = getattr(agent, "context_config", None)
        try:
            if refresh:
                try:
                    return await agent.get_swarm(None, refresh=True)
                except TypeError as exc:
                    if "unexpected keyword argument 'refresh'" not in str(exc):
                        raise
            return await agent.get_swarm(None)
        except (TypeError, AttributeError):
            if TaskInput is None or ApplicationContext is None:
                raise
            temp_task_input = TaskInput(
                user_id="gateway_user",
                session_id=f"temp_session_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                task_id=f"temp_task_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                task_content="",
                origin_user_input="",
            )
            temp_context = await ApplicationContext.from_input(
                temp_task_input,
                context_config=context_config,
            )
            if refresh:
                try:
                    return await agent.get_swarm(temp_context, refresh=True)
                except TypeError as exc:
                    if "unexpected keyword argument 'refresh'" not in str(exc):
                        raise
            return await agent.get_swarm(temp_context)

    @staticmethod
    def _extract_visible_text(output: Any) -> str:
        output_type = AworldDingdingBridge._output_type(output)

        if output_type in {"tool_call", "tool_call_result", "finished_signal", "step"}:
            return ""

        if output_type == "message":
            response = getattr(output, "response", None)
            if isinstance(response, str):
                return response
            return AworldDingdingBridge._extract_text_fields(output)

        if output_type in {"chunk", "default", ""}:
            return AworldDingdingBridge._extract_text_fields(output)

        return ""

    @staticmethod
    def _extract_text_fields(output: Any) -> str:
        content = getattr(output, "content", None)
        if isinstance(content, str):
            return content

        payload = getattr(output, "payload", None)
        if isinstance(payload, str):
            return payload

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
