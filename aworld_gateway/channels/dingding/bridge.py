from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import datetime
from inspect import isawaitable
from typing import Any

from aworld.runner import Runners

from aworld_gateway.channels.dingding.types import DingdingBridgeResult

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

    async def run(
        self,
        agent_id: str,
        session_id: str,
        text: Any,
        on_text_chunk: Callable[[str], Any] | None = None,
        on_output: Callable[[Any], Any] | None = None,
    ) -> DingdingBridgeResult:
        agent = self._registry_cls.get_agent(agent_id)
        if agent is None:
            raise ValueError(f"Agent not found: {agent_id}")

        swarm = await self._get_swarm_with_context_fallback(agent)
        executor = self._executor_cls(
            swarm=swarm,
            context_config=getattr(agent, "context_config", None),
            session_id=session_id,
            hooks=getattr(agent, "hooks", None),
        )

        try:
            chunks: list[str] = []
            saw_chunk_output = False
            async for output in self._stream_outputs(
                executor=executor,
                text=text,
                session_id=session_id,
            ):
                if on_output is not None:
                    output_callback_result = on_output(output)
                    if isawaitable(output_callback_result):
                        await output_callback_result
                output_type = self._output_type(output)
                chunk = self._extract_visible_text(output)
                if output_type == "message" and saw_chunk_output:
                    chunk = ""
                if not chunk:
                    continue
                if output_type == "chunk":
                    saw_chunk_output = True
                chunks.append(chunk)
                if on_text_chunk is not None:
                    callback_result = on_text_chunk(chunk)
                    if isawaitable(callback_result):
                        await callback_result
            return DingdingBridgeResult(text="".join(chunks).strip())
        finally:
            cleanup = getattr(executor, "cleanup_resources", None)
            if cleanup is not None:
                cleanup_result = cleanup()
                if isawaitable(cleanup_result):
                    await cleanup_result

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
    async def _get_swarm_with_context_fallback(agent: Any) -> Any:
        context_config = getattr(agent, "context_config", None)
        try:
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
