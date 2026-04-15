from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.router import GatewayRouter
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
    router = GatewayRouter(global_default_agent_id="global-default", agent_backend=backend)
    inbound = InboundEnvelope(
        channel="telegram",
        sender_id="sender-1",
        text="hello",
        payload={
            "account_id": "acct-1",
            "conversation_type": "group",
            "conversation_id": "conv-1",
            "message_id": "msg-9",
            "channel_default_agent_id": "channel-agent",
            "matched_route_agent_id": "route-agent",
        },
    )

    outbound = asyncio.run(router.handle_inbound(inbound))

    assert backend.calls == [
        {
            "agent_id": "channel-agent",
            "session_id": "gw:channel-agent:telegram:acct-1:group:conv-1",
            "text": "hello",
        }
    ]
    assert outbound.channel == "telegram"
    assert outbound.recipient_id == "acct-1"
    assert outbound.agent_id == "channel-agent"
    assert outbound.text == "backend reply"
    assert outbound.payload["account_id"] == "acct-1"
    assert outbound.payload["conversation_type"] == "group"
    assert outbound.payload["conversation_id"] == "conv-1"
    assert outbound.payload["reply_to_message_id"] == "msg-9"


def test_handle_inbound_prefers_explicit_agent_id_over_other_sources():
    backend = FakeAgentBackend()
    router = GatewayRouter(global_default_agent_id="global-default", agent_backend=backend)
    inbound = InboundEnvelope(
        channel="telegram",
        sender_id="acct-2",
        text="ping",
        payload={
            "conversation_type": "dm",
            "conversation_id": "conv-2",
            "account_id": "acct-2",
            "session_agent_id": "session-agent",
            "channel_default_agent_id": "channel-agent",
            "matched_route_agent_id": "route-agent",
        },
    )

    outbound = asyncio.run(router.handle_inbound(inbound, explicit_agent_id="explicit-agent"))

    assert backend.calls[0]["agent_id"] == "explicit-agent"
    assert outbound.agent_id == "explicit-agent"
