from __future__ import annotations

from typing import Any, Protocol

from aworld_gateway.agent_resolver import AgentResolver
from aworld_gateway.session_binding import SessionBinding
from aworld_gateway.types import InboundEnvelope, OutboundEnvelope


class AgentBackend(Protocol):
    async def run(self, *, agent_id: str, session_id: str, text: str) -> str: ...


class LocalCliAgentBackend:
    def __init__(self, registry_cls: Any = None, executor_cls: Any = None) -> None:
        if registry_cls is None or executor_cls is None:
            from aworld_cli.core.agent_registry import LocalAgentRegistry
            from aworld_cli.executors.local import LocalAgentExecutor

            registry_cls = LocalAgentRegistry
            executor_cls = LocalAgentExecutor

        self._registry_cls = registry_cls
        self._executor_cls = executor_cls

    async def run(self, *, agent_id: str, session_id: str, text: str) -> str:
        agent = self._registry_cls.get_agent(agent_id)
        if agent is None:
            raise ValueError(f"Agent not found: {agent_id}")

        swarm = await agent.get_swarm()
        executor = self._executor_cls(
            swarm=swarm,
            context_config=agent.context_config,
            session_id=session_id,
            hooks=agent.hooks,
        )
        return await executor.chat(text)


class GatewayRouter:
    def __init__(
        self,
        *,
        global_default_agent_id: str,
        agent_backend: AgentBackend | None = None,
        agent_resolver: AgentResolver | None = None,
    ) -> None:
        self._agent_backend = agent_backend or LocalCliAgentBackend()
        self._agent_resolver = agent_resolver or AgentResolver(
            global_default_agent_id=global_default_agent_id
        )

    async def handle_inbound(
        self,
        inbound: InboundEnvelope,
        *,
        explicit_agent_id: str | None = None,
    ) -> OutboundEnvelope:
        account_id = self._coalesce(
            inbound.payload.get("account_id"),
            inbound.sender_id,
            "unknown",
        )
        conversation_type = self._coalesce(
            inbound.payload.get("conversation_type"),
            "dm",
        )
        conversation_id = self._coalesce(
            inbound.payload.get("conversation_id"),
            inbound.session_id,
            account_id,
        )

        resolved_agent_id = self._agent_resolver.resolve(
            explicit_agent_id=explicit_agent_id,
            session_agent_id=inbound.payload.get("session_agent_id"),
            channel_default_agent_id=inbound.payload.get("channel_default_agent_id"),
            matched_route_agent_id=inbound.payload.get("matched_route_agent_id"),
        )
        session_id = SessionBinding(
            agent_id=resolved_agent_id,
            channel=inbound.channel,
            account_id=account_id,
            conversation_type=conversation_type,
            conversation_id=conversation_id,
        ).build()

        text = inbound.text or ""
        response_text = await self._agent_backend.run(
            agent_id=resolved_agent_id,
            session_id=session_id,
            text=text,
        )

        reply_to_message_id = self._coalesce(
            inbound.payload.get("reply_to_message_id"),
            inbound.payload.get("message_id"),
        )
        return OutboundEnvelope(
            channel=inbound.channel,
            recipient_id=account_id,
            agent_id=resolved_agent_id,
            text=response_text,
            payload={
                "account_id": account_id,
                "conversation_type": conversation_type,
                "conversation_id": conversation_id,
                "reply_to_message_id": reply_to_message_id,
            },
        )

    @staticmethod
    def _coalesce(*values: str | None) -> str:
        for value in values:
            if value:
                return value
        return ""
