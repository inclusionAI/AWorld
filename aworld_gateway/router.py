from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import datetime
from inspect import isawaitable
from pathlib import Path
from typing import Any, Protocol

from aworld.runner import Runners

from aworld_gateway.agent_resolver import AgentResolver
from aworld_gateway.logging import get_gateway_logger
from aworld_gateway.session_binding import SessionBinding
from aworld_gateway.types import InboundEnvelope, OutboundEnvelope
from aworld_cli.core.command_bridge import CommandBridge
from aworld_cli.core.tool_filter import temporary_tool_filter

try:
    from aworld.core.context.amni import ApplicationContext, TaskInput
except ImportError:  # pragma: no cover
    ApplicationContext = None
    TaskInput = None


logger = get_gateway_logger("router")


class AgentBackend(Protocol):
    async def run(
        self,
        *,
        agent_id: str,
        session_id: str,
        text: str,
        on_output: Callable[[Any], Any] | None = None,
        allowed_tools: list[str] | None = None,
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
        allowed_tools: list[str] | None = None,
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
            with temporary_tool_filter(swarm, allowed_tools):
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
        task = await executor._build_task(text, session_id=session_id)
        outputs = Runners.streamed_run_task(task=task)

        async for output in outputs.stream_events():
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

        final_answer = self._extract_final_task_answer(outputs)
        if final_answer:
            return final_answer
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

    @staticmethod
    def _extract_final_task_answer(outputs: Any) -> str:
        response_getter = getattr(outputs, "response", None)
        if not callable(response_getter):
            return ""
        task_response = response_getter()
        if task_response is None:
            return ""
        return LocalCliAgentBackend._coerce_final_answer(
            getattr(task_response, "answer", None)
        )

    @staticmethod
    def _coerce_final_answer(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()

        content = getattr(value, "content", None)
        if isinstance(content, str):
            return content.strip()

        return str(value).strip()


class GatewayRouter:
    def __init__(
        self,
        *,
        session_binding: SessionBinding,
        agent_resolver: AgentResolver,
        agent_backend: AgentBackend,
        command_bridge: CommandBridge | None = None,
    ) -> None:
        self._session_binding = session_binding
        self._agent_resolver = agent_resolver
        self._agent_backend = agent_backend
        self._command_bridge = command_bridge or CommandBridge()

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
        logger.info(
            "Gateway router inbound "
            f"channel={inbound.channel} conversation={inbound.conversation_id} "
            f"message_id={inbound.message_id} sender={inbound.sender_id}"
        )
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
        logger.info(
            "Gateway router resolved "
            f"agent={resolved_agent_id} session={session_id} "
            f"channel={inbound.channel} conversation={inbound.conversation_id}"
        )
        router_on_output = on_output

        async def _execute_prompt_command(
            *,
            prompt: str,
            allowed_tools: list[str] | None,
            on_output: Callable[[Any], Any] | None = None,
        ) -> str:
            backend_run_kwargs = {
                "agent_id": resolved_agent_id,
                "session_id": session_id,
                "text": prompt,
                "allowed_tools": allowed_tools,
            }
            output_callback = on_output if on_output is not None else router_on_output
            if output_callback is not None:
                backend_run_kwargs["on_output"] = output_callback
            return await self._agent_backend.run(**backend_run_kwargs)

        command_result = await self._command_bridge.execute(
            text=inbound.text,
            cwd=str(Path.cwd()),
            session_id=session_id,
            prompt_executor=_execute_prompt_command,
            on_output=on_output,
        )
        if command_result.handled:
            outbound = OutboundEnvelope(
                channel=inbound.channel,
                account_id=inbound.account_id,
                conversation_id=inbound.conversation_id,
                reply_to_message_id=inbound.message_id,
                text=command_result.text,
            )
            logger.info(
                "Gateway router command handled "
                f"channel={inbound.channel} conversation={inbound.conversation_id} "
                f"command={command_result.command_name or 'unknown'}"
            )
            return outbound

        backend_run_kwargs = {
            "agent_id": resolved_agent_id,
            "session_id": session_id,
            "text": inbound.text,
        }
        if on_output is not None:
            backend_run_kwargs["on_output"] = on_output
        try:
            response_text = await self._agent_backend.run(**backend_run_kwargs)
        except Exception as exc:
            logger.exception(
                "Gateway router backend failed "
                f"agent={resolved_agent_id} session={session_id} error={exc}"
            )
            raise

        outbound = OutboundEnvelope(
            channel=inbound.channel,
            account_id=inbound.account_id,
            conversation_id=inbound.conversation_id,
            reply_to_message_id=inbound.message_id,
            text=response_text,
            metadata=dict(inbound.metadata),
        )
        logger.info(
            "Gateway router outbound "
            f"channel={outbound.channel} conversation={outbound.conversation_id} "
            f"reply_to={outbound.reply_to_message_id} chars={len(outbound.text)}"
        )
        return outbound
