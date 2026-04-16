from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

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


class _FakeCredential:
    def __init__(self, client_id: str, client_secret: str) -> None:
        self.client_id = client_id
        self.client_secret = client_secret


class _FakeChatbotHandler:
    pass


class _FakeChatbotMessage:
    TOPIC = "chatbot-topic"


class _FakeAckMessage:
    STATUS_OK = "ok"


class _FakeStreamClient:
    def __init__(self, credential) -> None:
        self.credential = credential
        self.register_calls: list[tuple[str, object]] = []

    def register_callback_handler(self, topic: str, handler: object) -> None:
        self.register_calls.append((topic, handler))


class _FakeStreamModule:
    Credential = _FakeCredential
    DingTalkStreamClient = _FakeStreamClient
    ChatbotHandler = _FakeChatbotHandler
    ChatbotMessage = _FakeChatbotMessage
    AckMessage = _FakeAckMessage


class _FakeResponse:
    def __init__(self) -> None:
        self.raised = False

    def raise_for_status(self) -> None:
        self.raised = True


class _FakeHttpClient:
    def __init__(self) -> None:
        self.closed = False
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.response = _FakeResponse()

    async def post(self, url: str, *, json: dict[str, object]):
        self.calls.append((url, json))
        return self.response

    async def aclose(self) -> None:
        self.closed = True


def test_connector_start_registers_stream_callback_handler(monkeypatch) -> None:
    monkeypatch.setenv("AWORLD_DINGTALK_CLIENT_ID", "ding-id")
    monkeypatch.setenv("AWORLD_DINGTALK_CLIENT_SECRET", "ding-secret")

    connector = DingTalkConnector(
        config=DingdingChannelConfig(default_agent_id="aworld"),
        bridge=_FakeBridge(),
        stream_module=_FakeStreamModule,
        http_client=_FakeHttpClient(),
    )

    asyncio.run(connector.start())

    assert isinstance(connector._client, _FakeStreamClient)
    assert connector._client.credential.client_id == "ding-id"
    assert connector._client.credential.client_secret == "ding-secret"
    assert connector._client.register_calls
    topic, handler = connector._client.register_calls[0]
    assert topic == "chatbot-topic"

    callback = type("Callback", (), {"data": {"sessionWebhook": "", "senderId": ""}})()
    status, message = asyncio.run(handler.process(callback))
    assert status == "ok"
    assert message == "OK"


def test_connector_stop_closes_http_client() -> None:
    http_client = _FakeHttpClient()
    connector = DingTalkConnector(
        config=DingdingChannelConfig(default_agent_id="aworld"),
        bridge=_FakeBridge(),
        stream_module=object(),
        http_client=http_client,
    )

    asyncio.run(connector.stop())

    assert http_client.closed is True


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


def test_connector_uses_sender_id_when_conversation_id_missing() -> None:
    bridge = _FakeBridge()
    connector = DingTalkConnector(
        config=DingdingChannelConfig(default_agent_id="agent-1"),
        bridge=bridge,
        stream_module=object(),
    )
    sent: list[str] = []

    async def fake_send_text(*, session_webhook: str, text: str) -> None:
        sent.append(text)

    connector.send_text = fake_send_text  # type: ignore[method-assign]

    asyncio.run(
        connector.handle_callback(
            {
                "sessionWebhook": "https://callback",
                "senderStaffId": "staff-1",
                "text": {"content": "hello"},
            }
        )
    )

    assert bridge.calls[0]["session_id"].startswith("dingtalk_staff-1_")
    assert sent == ["echo:hello"]


def test_connector_ignores_invalid_or_empty_callbacks() -> None:
    bridge = _FakeBridge()
    connector = DingTalkConnector(
        config=DingdingChannelConfig(default_agent_id="agent-1"),
        bridge=bridge,
        stream_module=object(),
    )
    sent: list[str] = []

    async def fake_send_text(*, session_webhook: str, text: str) -> None:
        sent.append(text)

    connector.send_text = fake_send_text  # type: ignore[method-assign]

    asyncio.run(connector.handle_callback({"senderId": "user-1", "text": {"content": "hello"}}))
    asyncio.run(connector.handle_callback({"sessionWebhook": "https://callback", "text": {"content": "hello"}}))
    asyncio.run(
        connector.handle_callback(
            {
                "sessionWebhook": "https://callback",
                "senderId": "user-1",
                "text": {"content": "   "},
            }
        )
    )
    asyncio.run(connector.handle_callback("not-json"))

    assert bridge.calls == []
    assert sent == []


def test_connector_supports_string_payload_and_empty_bridge_result() -> None:
    class _EmptyBridge(_FakeBridge):
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
            return DingdingBridgeResult(text="")

    bridge = _EmptyBridge()
    connector = DingTalkConnector(
        config=DingdingChannelConfig(default_agent_id="agent-1"),
        bridge=bridge,
        stream_module=object(),
    )
    sent: list[str] = []

    async def fake_send_text(*, session_webhook: str, text: str) -> None:
        sent.append(text)

    connector.send_text = fake_send_text  # type: ignore[method-assign]

    payload = json.dumps(
        {
            "sessionWebhook": "https://callback",
            "conversationId": "conv-1",
            "senderId": "user-1",
            "content": "hello from raw content",
        }
    )
    asyncio.run(connector.handle_callback(payload))

    assert bridge.calls[0]["text"] == "hello from raw content"
    assert sent == ["（空响应）"]


def test_connector_send_text_posts_dingtalk_text_payload() -> None:
    http_client = _FakeHttpClient()
    connector = DingTalkConnector(
        config=DingdingChannelConfig(default_agent_id="aworld"),
        bridge=_FakeBridge(),
        stream_module=object(),
        http_client=http_client,
    )

    asyncio.run(
        connector.send_text(
            session_webhook="https://callback",
            text="hello",
        )
    )

    assert http_client.calls == [
        (
            "https://callback",
            {"msgtype": "text", "text": {"content": "hello"}},
        )
    ]
    assert http_client.response.raised is True


def test_connector_required_env_raises_on_missing(monkeypatch) -> None:
    monkeypatch.delenv("AWORLD_DINGTALK_CLIENT_ID", raising=False)

    with pytest.raises(ValueError, match="AWORLD_DINGTALK_CLIENT_ID"):
        DingTalkConnector._required_env("AWORLD_DINGTALK_CLIENT_ID")
