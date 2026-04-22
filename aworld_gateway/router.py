from __future__ import annotations

from datetime import datetime
from inspect import isawaitable
from typing import Any, Protocol

from aworld_gateway.agent_resolver import AgentResolver
from aworld_gateway.session_binding import SessionBinding
from aworld_gateway.types import InboundEnvelope, OutboundEnvelope

try:
    from aworld.core.context.amni import ApplicationContext, TaskInput
except ImportError:  # pragma: no cover
    ApplicationContext = None
    TaskInput = None


class AgentBackend(Protocol):
    async def run(self, *, agent_id: str, session_id: str, text: str) -> str: ...


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

    async def run(self, *, agent_id: str, session_id: str, text: str) -> str:
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
            return await executor.chat(text)
        finally:
            if executor is not None and hasattr(executor, "cleanup_resources"):
                cleanup_result = executor.cleanup_resources()
                if isawaitable(cleanup_result):
                    await cleanup_result


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

        response_text = await self._agent_backend.run(
            agent_id=resolved_agent_id,
            session_id=session_id,
            text=inbound.text,
        )

        return OutboundEnvelope(
            channel=inbound.channel,
            account_id=inbound.account_id,
            conversation_id=inbound.conversation_id,
            reply_to_message_id=inbound.message_id,
            text=response_text,
            metadata=dict(inbound.metadata),
        )
