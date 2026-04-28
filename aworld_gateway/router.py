from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import datetime
from inspect import isawaitable
from typing import Any, Protocol

from aworld.runner import Runners

from aworld_gateway.agent_resolver import AgentResolver
from aworld_gateway.session_binding import SessionBinding
from aworld_gateway.types import InboundEnvelope, OutboundEnvelope

try:
    from aworld.core.context.amni import ApplicationContext, TaskInput
except ImportError:  # pragma: no cover
    ApplicationContext = None
    TaskInput = None


class AgentBackend(Protocol):
    async def run(
        self,
        *,
        agent_id: str,
        session_id: str,
        text: str,
        on_output: Callable[[Any], Any] | None = None,
    ) -> str: ...


class LocalCliAgentBackend:
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
        *,
        agent_id: str,
        session_id: str,
        text: str,
        on_output: Callable[[Any], Any] | None = None,
    ) -> str:
        agent = self._registry_cls.get_agent(agent_id)
        if agent is None:
            raise ValueError(f"Agent not found: {agent_id}")

        context_config = getattr(agent, "context_config", None)
        try:
            swarm = await agent.get_swarm(None)
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
            swarm = await agent.get_swarm(temp_context)

        executor = None
        try:
            executor = self._executor_cls(
                swarm=swarm,
                context_config=context_config,
                session_id=session_id,
                hooks=getattr(agent, "hooks", None),
            )
            if on_output is None:
                return await executor.chat(text)
            return await self._run_with_output_observer(
                executor=executor,
                text=text,
                session_id=session_id,
                on_output=on_output,
            )
        finally:
            if executor is not None and hasattr(executor, "cleanup_resources"):
                cleanup_result = executor.cleanup_resources()
                if isawaitable(cleanup_result):
                    await cleanup_result

    async def _run_with_output_observer(
        self,
        *,
        executor: Any,
        text: str,
        session_id: str,
        on_output: Callable[[Any], Any],
    ) -> str:
        chunks: list[str] = []
        saw_chunk_output = False

        async for output in self._stream_outputs(
            executor=executor,
            text=text,
            session_id=session_id,
        ):
            callback_result = on_output(output)
            if isawaitable(callback_result):
                await callback_result

            output_type = self._output_type(output)
            chunk = self._extract_visible_text(output)
            if output_type == "message" and saw_chunk_output:
                chunk = ""
            if not chunk:
                continue
            if output_type == "chunk":
                saw_chunk_output = True
            chunks.append(chunk)

        return "".join(chunks).strip()

    async def _stream_outputs(
        self,
        *,
        executor: Any,
        text: str,
        session_id: str,
    ) -> AsyncIterator[Any]:
        task = await executor._build_task(text, session_id=session_id)
        outputs = Runners.streamed_run_task(task=task)

        async for output in outputs.stream_events():
            yield output

    @staticmethod
    def _output_type(output: Any) -> str:
        output_type_getter = getattr(output, "output_type", None)
        return output_type_getter() if callable(output_type_getter) else ""

    @staticmethod
    def _extract_visible_text(output: Any) -> str:
        output_type = LocalCliAgentBackend._output_type(output)

        if output_type in {"tool_call", "tool_call_result", "finished_signal", "step"}:
            return ""

        if output_type == "message":
            response = getattr(output, "response", None)
            if isinstance(response, str):
                return response
            return LocalCliAgentBackend._extract_text_fields(output)

        if output_type in {"chunk", "default", ""}:
            return LocalCliAgentBackend._extract_text_fields(output)

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


class GatewayRouter:
    def __init__(
        self,
        *,
        session_binding: SessionBinding,
        agent_resolver: AgentResolver,
        agent_backend: AgentBackend,
    ) -> None:
        self._session_binding = session_binding
        self._agent_resolver = agent_resolver
        self._agent_backend = agent_backend

    async def handle_inbound(
        self,
        inbound: InboundEnvelope,
        *,
        channel_default_agent_id: str | None,
        explicit_agent_id: str | None = None,
        session_agent_id: str | None = None,
        matched_route_agent_id: str | None = None,
        on_output: Callable[[Any], Any] | None = None,
    ) -> OutboundEnvelope:
        resolved_agent_id = self._agent_resolver.resolve(
            explicit_agent_id=explicit_agent_id,
            session_agent_id=session_agent_id,
            channel_default_agent_id=channel_default_agent_id,
            matched_route_agent_id=matched_route_agent_id,
        )
        session_id = self._session_binding.build(
            agent_id=resolved_agent_id,
            channel=inbound.channel,
            account_id=inbound.account_id,
            conversation_type=inbound.conversation_type,
            conversation_id=inbound.conversation_id,
        )

        backend_run_kwargs = {
            "agent_id": resolved_agent_id,
            "session_id": session_id,
            "text": inbound.text,
        }
        if on_output is not None:
            backend_run_kwargs["on_output"] = on_output
        response_text = await self._agent_backend.run(**backend_run_kwargs)

        return OutboundEnvelope(
            channel=inbound.channel,
            account_id=inbound.account_id,
            conversation_id=inbound.conversation_id,
            reply_to_message_id=inbound.message_id,
            text=response_text,
            metadata=dict(inbound.metadata),
        )
