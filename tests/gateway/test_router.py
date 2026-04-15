from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.agent_resolver import AgentResolver
from aworld_gateway.router import GatewayRouter
from aworld_gateway.session_binding import SessionBinding
from aworld_gateway.types import InboundEnvelope


class FakeAgentBackend:
    def __init__(self) -> None:
        self.calls = []

    async def run(self, *, agent_id: str, session_id: str, text: str) -> str:
        self.calls.append(
            {
                "agent_id": agent_id,
                "session_id": session_id,
                "text": text,
            }
        )
        return "backend reply"


def test_handle_inbound_resolves_agent_builds_session_and_routes_execution():
    backend = FakeAgentBackend()
    router = GatewayRouter(
        session_binding=SessionBinding(),
        agent_resolver=AgentResolver(default_agent_id="aworld"),
        agent_backend=backend,
    )
    inbound = InboundEnvelope(
        channel="telegram",
        account_id="acct-1",
        conversation_id="conv-1",
        conversation_type="group",
        sender_id="sender-1",
        sender_name="Sender",
        message_id="msg-9",
        text="hello",
    )

    outbound = asyncio.run(
        router.handle_inbound(
            inbound,
            channel_default_agent_id="channel-agent",
            matched_route_agent_id="route-agent",
        )
    )

    assert backend.calls == [
        {
            "agent_id": "channel-agent",
            "session_id": "gw:channel-agent:telegram:acct-1:group:conv-1",
            "text": "hello",
        }
    ]
    assert outbound.channel == "telegram"
    assert outbound.account_id == "acct-1"
    assert outbound.conversation_id == "conv-1"
    assert outbound.reply_to_message_id == "msg-9"
    assert outbound.text == "backend reply"


def test_handle_inbound_prefers_explicit_agent_id_over_other_sources():
    backend = FakeAgentBackend()
    router = GatewayRouter(
        session_binding=SessionBinding(),
        agent_resolver=AgentResolver(default_agent_id="aworld"),
        agent_backend=backend,
    )
    inbound = InboundEnvelope(
        channel="telegram",
        account_id="acct-2",
        conversation_id="conv-2",
        conversation_type="dm",
        sender_id="acct-2",
        sender_name=None,
        message_id="msg-2",
        text="ping",
    )

    outbound = asyncio.run(
        router.handle_inbound(
            inbound,
            channel_default_agent_id="channel-agent",
            explicit_agent_id="explicit-agent",
            session_agent_id="session-agent",
            matched_route_agent_id="route-agent",
        )
    )

    assert backend.calls[0]["agent_id"] == "explicit-agent"
    assert outbound.reply_to_message_id == "msg-2"
