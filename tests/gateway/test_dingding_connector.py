from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.channels.dingding.connector import DingTalkConnector
from aworld_gateway.channels.dingding.types import DingdingBridgeResult
from aworld_gateway.config import DingdingChannelConfig


class _FakeBridge:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def run(
        self,
        *,
        agent_id: str,
        session_id: str,
        text: str,
        on_text_chunk=None,
    ) -> DingdingBridgeResult:
        self.calls.append(
            {
                "agent_id": agent_id,
                "session_id": session_id,
                "text": text,
            }
        )
        return DingdingBridgeResult(text=f"echo:{text}")


def test_connector_reset_command_rotates_session_and_sends_confirmation() -> None:
    bridge = _FakeBridge()
    connector = DingTalkConnector(
        config=DingdingChannelConfig(default_agent_id="aworld"),
        bridge=bridge,
        stream_module=object(),
    )
    sent: list[str] = []

    async def fake_send_text(*, session_webhook: str, text: str) -> None:
        assert session_webhook == "https://callback"
        sent.append(text)

    connector.send_text = fake_send_text  # type: ignore[method-assign]

    asyncio.run(
        connector.handle_callback(
            {
                "sessionWebhook": "https://callback",
                "conversationId": "conv-1",
                "senderId": "user-1",
                "text": {"content": "/new"},
            }
        )
    )
    first_session = connector._session_ids["conv-1"]

    asyncio.run(
        connector.handle_callback(
            {
                "sessionWebhook": "https://callback",
                "conversationId": "conv-1",
                "senderId": "user-1",
                "text": {"content": "/new"},
            }
        )
    )
    second_session = connector._session_ids["conv-1"]

    assert sent == [
        "✨ 已开启新会话，之前的上下文已清空。",
        "✨ 已开启新会话，之前的上下文已清空。",
    ]
    assert first_session != second_session
    assert bridge.calls == []


def test_connector_normal_callback_runs_bridge_and_sends_text() -> None:
    bridge = _FakeBridge()
    connector = DingTalkConnector(
        config=DingdingChannelConfig(default_agent_id="agent-1"),
        bridge=bridge,
        stream_module=object(),
    )
    sent: list[str] = []

    async def fake_send_text(*, session_webhook: str, text: str) -> None:
        assert session_webhook == "https://callback"
        sent.append(text)

    connector.send_text = fake_send_text  # type: ignore[method-assign]

    asyncio.run(
        connector.handle_callback(
            {
                "sessionWebhook": "https://callback",
                "conversationId": "conv-1",
                "senderId": "user-1",
                "text": {"content": "hello"},
            }
        )
    )
    session_id_1 = bridge.calls[0]["session_id"]

    asyncio.run(
        connector.handle_callback(
            {
                "sessionWebhook": "https://callback",
                "conversationId": "conv-1",
                "senderId": "user-1",
                "text": {"content": "again"},
            }
        )
    )
    session_id_2 = bridge.calls[1]["session_id"]

    assert [call["text"] for call in bridge.calls] == ["hello", "again"]
    assert all(call["agent_id"] == "agent-1" for call in bridge.calls)
    assert session_id_1 == session_id_2
    assert sent == ["echo:hello", "echo:again"]
