from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.channels.dingding.connector import (
    PROCESSING_ACK_TEXT,
    DingTalkConnector,
)
from aworld_gateway.channels.dingding.types import (
    AICardInstance,
    DingdingBridgeResult,
    ExtractedMessage,
    IncomingAttachment,
    PendingFileMessage,
)
from aworld_gateway.config import DingdingChannelConfig
from aworld_gateway.http.artifact_service import ArtifactService
from aworld.output.base import ToolResultOutput


class _FakeBridge:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    @staticmethod
    def _display_input_text(text: object) -> str:
        if isinstance(text, list):
            for part in text:
                if isinstance(part, dict) and part.get("type") == "text":
                    text = str(part.get("text") or "")
                    break
            else:
                return "[multimodal]"
        if not isinstance(text, str):
            return str(text)
        return text.split("\n会话附加信息:\n", 1)[0]

    async def run(
        self,
        *,
        agent_id: str,
        session_id: str,
        text,
        on_text_chunk=None,
        on_output=None,
    ) -> DingdingBridgeResult:
        self.calls.append(
            {
                "agent_id": agent_id,
                "session_id": session_id,
                "text": text,
            }
        )
        display_text = self._display_input_text(text)
        if on_text_chunk is not None:
            await on_text_chunk("echo:")
            await on_text_chunk(display_text)
        return DingdingBridgeResult(text=f"echo:{display_text}")


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
        self.start_forever_calls = 0
        self.stop_calls = 0

    def register_callback_handler(self, topic: str, handler: object) -> None:
        self.register_calls.append((topic, handler))

    def start_forever(self) -> None:
        self.start_forever_calls += 1

    def stop(self) -> None:
        self.stop_calls += 1


class _FakeStreamModule:
    Credential = _FakeCredential
    DingTalkStreamClient = _FakeStreamClient
    ChatbotHandler = _FakeChatbotHandler
    ChatbotMessage = _FakeChatbotMessage
    AckMessage = _FakeAckMessage


class _FakeThread:
    def __init__(self, *, target, name: str, daemon: bool) -> None:
        self.target = target
        self.name = name
        self.daemon = daemon
        self.started = False

    def start(self) -> None:
        self.started = True
        self.target()


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

    async def post(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return self.response

    async def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return self.response

    async def request(self, method: str, url: str, **kwargs):
        self.calls.append((f"{method} {url}", kwargs))
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


def test_connector_start_launches_stream_client_runner(monkeypatch) -> None:
    monkeypatch.setenv("AWORLD_DINGTALK_CLIENT_ID", "ding-id")
    monkeypatch.setenv("AWORLD_DINGTALK_CLIENT_SECRET", "ding-secret")

    connector = DingTalkConnector(
        config=DingdingChannelConfig(default_agent_id="aworld"),
        bridge=_FakeBridge(),
        stream_module=_FakeStreamModule,
        http_client=_FakeHttpClient(),
        thread_cls=_FakeThread,
    )

    asyncio.run(connector.start())

    assert isinstance(connector._client, _FakeStreamClient)
    assert connector._client.start_forever_calls == 1
    assert connector._stream_thread is not None
    assert connector._stream_thread.started is True


def test_connector_stop_closes_http_client(monkeypatch) -> None:
    monkeypatch.setenv("AWORLD_DINGTALK_CLIENT_ID", "ding-id")
    monkeypatch.setenv("AWORLD_DINGTALK_CLIENT_SECRET", "ding-secret")

    http_client = _FakeHttpClient()
    connector = DingTalkConnector(
        config=DingdingChannelConfig(default_agent_id="aworld"),
        bridge=_FakeBridge(),
        stream_module=_FakeStreamModule,
        http_client=http_client,
        thread_cls=_FakeThread,
    )

    asyncio.run(connector.start())
    asyncio.run(connector.stop())

    assert http_client.closed is True
    assert connector._client is not None
    assert connector._client.stop_calls == 1


def test_connector_start_bootstraps_cron_scheduler(monkeypatch) -> None:
    monkeypatch.setenv("AWORLD_DINGTALK_CLIENT_ID", "ding-id")
    monkeypatch.setenv("AWORLD_DINGTALK_CLIENT_SECRET", "ding-secret")

    class _FakeCronExecutor:
        def __init__(self) -> None:
            self.swarm_resolver = None
            self.default_agent_name = None

        def set_swarm_resolver(self, resolver) -> None:
            self.swarm_resolver = resolver

        def set_default_agent_name(self, agent_name: str | None) -> None:
            self.default_agent_name = agent_name

    class _FakeScheduler:
        def __init__(self) -> None:
            self.executor = _FakeCronExecutor()
            self.notification_sink = None
            self.running = False
            self.start_calls = 0
            self.stop_calls = 0

        async def start(self) -> None:
            self.start_calls += 1
            self.running = True

        async def stop(self) -> None:
            self.stop_calls += 1
            self.running = False

    class _FakeAgent:
        async def get_swarm(self, _context):
            return "fake-swarm"

    scheduler = _FakeScheduler()
    monkeypatch.setattr("aworld.core.scheduler.get_scheduler", lambda: scheduler)
    monkeypatch.setattr(
        "aworld_cli.core.agent_registry.LocalAgentRegistry.get_agent",
        lambda agent_id: _FakeAgent() if agent_id == "agent-1" else None,
    )

    connector = DingTalkConnector(
        config=DingdingChannelConfig(default_agent_id="agent-1"),
        bridge=_FakeBridge(),
        stream_module=_FakeStreamModule,
        http_client=_FakeHttpClient(),
        thread_cls=_FakeThread,
    )

    asyncio.run(connector.start())

    assert scheduler.start_calls == 1
    assert scheduler.running is True
    assert scheduler.notification_sink is not None
    assert scheduler.executor.default_agent_name == "agent-1"
    assert scheduler.executor.swarm_resolver is not None
    assert asyncio.run(scheduler.executor.swarm_resolver("agent-1")) == "fake-swarm"

    asyncio.run(connector.stop())

    assert scheduler.stop_calls == 1


def test_connector_stop_restores_previous_scheduler_sink(monkeypatch) -> None:
    monkeypatch.setenv("AWORLD_DINGTALK_CLIENT_ID", "ding-id")
    monkeypatch.setenv("AWORLD_DINGTALK_CLIENT_SECRET", "ding-secret")

    async def previous_sink(notification) -> None:
        return None

    class _FakeScheduler:
        def __init__(self) -> None:
            self.executor = None
            self.notification_sink = previous_sink
            self.running = False

    scheduler = _FakeScheduler()
    monkeypatch.setattr("aworld.core.scheduler.get_scheduler", lambda: scheduler)

    connector = DingTalkConnector(
        config=DingdingChannelConfig(default_agent_id="aworld"),
        bridge=_FakeBridge(),
        stream_module=_FakeStreamModule,
        http_client=_FakeHttpClient(),
        thread_cls=_FakeThread,
    )

    asyncio.run(connector.start())

    assert scheduler.notification_sink is not previous_sink

    asyncio.run(connector.stop())

    assert scheduler.notification_sink is previous_sink


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
                "msgId": "msg-new-1",
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
                "msgId": "msg-new-2",
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

    assert isinstance(bridge.calls[0]["text"], str)
    assert isinstance(bridge.calls[1]["text"], str)
    assert str(bridge.calls[0]["text"]).startswith("hello\n会话附加信息:")
    assert str(bridge.calls[1]["text"]).startswith("again\n会话附加信息:")
    assert all(call["agent_id"] == "agent-1" for call in bridge.calls)
    assert session_id_1 == session_id_2
    assert sent == ["echo:hello", "echo:again"]


def test_connector_isolates_overlapping_callbacks_with_new_session() -> None:
    class _BlockingBridge(_FakeBridge):
        def __init__(self) -> None:
            super().__init__()
            self.first_started = asyncio.Event()
            self.release_first = asyncio.Event()

        async def run(
            self,
            *,
            agent_id: str,
            session_id: str,
            text,
            on_text_chunk=None,
            on_output=None,
        ) -> DingdingBridgeResult:
            self.calls.append(
                {
                    "agent_id": agent_id,
                    "session_id": session_id,
                    "text": text,
                }
            )
            display_text = self._display_input_text(text)
            if display_text == "alpha":
                self.first_started.set()
                await self.release_first.wait()
            if on_text_chunk is not None:
                await on_text_chunk("echo:")
                await on_text_chunk(display_text)
            return DingdingBridgeResult(text=f"echo:{display_text}")

    bridge = _BlockingBridge()
    connector = DingTalkConnector(
        config=DingdingChannelConfig(default_agent_id="agent-1"),
        bridge=bridge,
        stream_module=object(),
    )
    sent: list[str] = []

    async def fake_send_text(*, session_webhook: str, text: str) -> None:
        sent.append(text)

    connector.send_text = fake_send_text  # type: ignore[method-assign]

    async def run_scenario() -> None:
        first_task = asyncio.create_task(
            connector.handle_callback(
                {
                    "sessionWebhook": "https://callback",
                    "conversationId": "conv-1",
                    "senderId": "user-1",
                    "text": {"content": "alpha"},
                }
            )
        )
        await bridge.first_started.wait()

        second_task = asyncio.create_task(
            connector.handle_callback(
                {
                    "sessionWebhook": "https://callback",
                    "conversationId": "conv-1",
                    "senderId": "user-1",
                    "text": {"content": "beta"},
                }
            )
        )
        await second_task
        assert sent == ["echo:beta"]

        bridge.release_first.set()
        await first_task

    asyncio.run(run_scenario())

    assert len(bridge.calls) == 2
    assert bridge.calls[0]["session_id"] != bridge.calls[1]["session_id"]
    assert connector._session_ids["conv-1"] == bridge.calls[1]["session_id"]
    assert sent == ["echo:beta", "echo:alpha"]


def test_connector_sends_processing_ack_for_complex_request() -> None:
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
                "text": {
                    "content": "结合trajectory.log分析今天ai领域的新闻收集整理好生成html发给我",
                },
            }
        )
    )

    assert sent == [
        PROCESSING_ACK_TEXT,
        "echo:结合trajectory.log分析今天ai领域的新闻收集整理好生成html发给我",
    ]


def test_connector_does_not_send_processing_ack_for_fast_short_request() -> None:
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
                "conversationId": "conv-1",
                "senderId": "user-1",
                "text": {"content": "你是谁"},
            }
        )
    )

    assert sent == ["echo:你是谁"]


def test_connector_does_not_send_processing_ack_for_slow_trivial_short_request() -> None:
    class _SlowBridge(_FakeBridge):
        async def run(
            self,
            *,
            agent_id: str,
            session_id: str,
            text,
            on_text_chunk=None,
            on_output=None,
        ) -> DingdingBridgeResult:
            self.calls.append(
                {
                    "agent_id": agent_id,
                    "session_id": session_id,
                    "text": text,
                }
            )
            await asyncio.sleep(0.01)
            return DingdingBridgeResult(text="echo:你是谁")

    connector = DingTalkConnector(
        config=DingdingChannelConfig(default_agent_id="agent-1"),
        bridge=_SlowBridge(),
        stream_module=object(),
    )
    sent: list[str] = []

    async def fake_send_text(*, session_webhook: str, text: str) -> None:
        sent.append(text)

    connector.send_text = fake_send_text  # type: ignore[method-assign]
    connector._processing_ack_delay_seconds = lambda: 0.0  # type: ignore[method-assign]

    asyncio.run(
        connector.handle_callback(
            {
                "sessionWebhook": "https://callback",
                "conversationId": "conv-1",
                "senderId": "user-1",
                "text": {"content": "你是谁"},
            }
        )
    )

    assert sent == ["echo:你是谁"]


def test_connector_sends_delayed_processing_ack_for_slow_non_trivial_request() -> None:
    class _SlowBridge(_FakeBridge):
        async def run(
            self,
            *,
            agent_id: str,
            session_id: str,
            text,
            on_text_chunk=None,
            on_output=None,
        ) -> DingdingBridgeResult:
            self.calls.append(
                {
                    "agent_id": agent_id,
                    "session_id": session_id,
                    "text": text,
                }
            )
            await asyncio.sleep(0.01)
            return DingdingBridgeResult(text="echo:帮我看下这个报错怎么处理")

    connector = DingTalkConnector(
        config=DingdingChannelConfig(default_agent_id="agent-1"),
        bridge=_SlowBridge(),
        stream_module=object(),
    )
    sent: list[str] = []

    async def fake_send_text(*, session_webhook: str, text: str) -> None:
        sent.append(text)

    connector.send_text = fake_send_text  # type: ignore[method-assign]
    connector._processing_ack_delay_seconds = lambda: 0.0  # type: ignore[method-assign]

    asyncio.run(
        connector.handle_callback(
            {
                "sessionWebhook": "https://callback",
                "conversationId": "conv-1",
                "senderId": "user-1",
                "text": {"content": "帮我看下这个报错怎么处理"},
            }
        )
    )

    assert sent == [PROCESSING_ACK_TEXT, "echo:帮我看下这个报错怎么处理"]


def test_connector_reports_error_when_no_agent_id_is_configured() -> None:
    connector = DingTalkConnector(
        config=DingdingChannelConfig(default_agent_id=None),
        bridge=_FakeBridge(),
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

    assert sent == ["抱歉，调用 Agent 失败：No agent id configured for DingTalk channel."]


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


def test_connector_suppresses_duplicate_callbacks() -> None:
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

    payload = {
        "msgId": "msg-hello-1",
        "sessionWebhook": "https://callback",
        "conversationId": "conv-1",
        "senderId": "user-1",
        "text": {"content": "hello"},
    }

    asyncio.run(connector.handle_callback(payload))
    asyncio.run(connector.handle_callback(payload))

    assert len(bridge.calls) == 1
    assert sent == ["echo:hello"]


def test_connector_suppresses_duplicate_callbacks_without_provider_ids() -> None:
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

    payload = {
        "sessionWebhook": "https://callback",
        "conversationId": "conv-1",
        "senderId": "user-1",
        "msgtype": "text",
        "text": {"content": "hello"},
    }

    asyncio.run(connector.handle_callback(payload))
    asyncio.run(connector.handle_callback(dict(payload)))

    assert len(bridge.calls) == 1
    assert sent == ["echo:hello"]


def test_connector_logs_inbound_runtime_outputs_and_final_reply(monkeypatch) -> None:
    class _LoggingBridge(_FakeBridge):
        async def run(
            self,
            *,
            agent_id: str,
            session_id: str,
            text: str,
            on_text_chunk=None,
            on_output=None,
        ) -> DingdingBridgeResult:
            self.calls.append(
                {
                    "agent_id": agent_id,
                    "session_id": session_id,
                    "text": text,
                }
            )
            if on_output is not None:
                await on_output(
                    ToolResultOutput(
                        tool_name="cron",
                        action_name="cron_tool",
                        data={"success": True, "job_id": "job-log-1"},
                    )
                )
            if on_text_chunk is not None:
                await on_text_chunk("已")
                await on_text_chunk("创建提醒")
            return DingdingBridgeResult(text="已创建提醒")

    connector = DingTalkConnector(
        config=DingdingChannelConfig(default_agent_id="agent-1"),
        bridge=_LoggingBridge(),
        stream_module=object(),
    )
    sent: list[str] = []
    info_logs: list[str] = []

    async def fake_send_text(*, session_webhook: str, text: str) -> None:
        sent.append(text)

    monkeypatch.setattr(
        "aworld_gateway.channels.dingding.connector.logger.info",
        lambda message, *args, **kwargs: info_logs.append(str(message)),
    )
    connector.send_text = fake_send_text  # type: ignore[method-assign]

    asyncio.run(
        connector.handle_callback(
            {
                "sessionWebhook": "https://callback",
                "conversationId": "conv-1",
                "senderId": "user-1",
                "text": {"content": "一分钟后提醒我喝水"},
            }
        )
    )

    assert sent == [PROCESSING_ACK_TEXT, "已创建提醒"]
    assert any("DingTalk inbound message" in entry for entry in info_logs)
    assert any("一分钟后提醒我喝水" in entry for entry in info_logs)
    assert any("DingTalk AI Card unavailable" in entry for entry in info_logs)
    assert any("DingTalk runtime output" in entry for entry in info_logs)
    assert any("tool_call_result" in entry and "cron" in entry for entry in info_logs)
    assert any("DingTalk final reply" in entry and "已创建提醒" in entry for entry in info_logs)
    assert any("DingTalk stream summary" in entry and "fallback_to_text=True" in entry for entry in info_logs)


def test_connector_mirrors_business_logs_to_std_logging(monkeypatch) -> None:
    class _LoggingBridge(_FakeBridge):
        async def run(
            self,
            *,
            agent_id: str,
            session_id: str,
            text,
            on_text_chunk=None,
            on_output=None,
        ) -> DingdingBridgeResult:
            if on_output is not None:
                await on_output(
                    ToolResultOutput(
                        tool_name="cron",
                        action_name="cron_tool",
                        data={"success": True, "job_id": "job-log-1"},
                    )
                )
            if on_text_chunk is not None:
                await on_text_chunk("已创建提醒")
            return DingdingBridgeResult(text="已创建提醒")

    connector = DingTalkConnector(
        config=DingdingChannelConfig(default_agent_id="agent-1"),
        bridge=_LoggingBridge(),
        stream_module=object(),
    )
    standard_logs: list[str] = []

    async def fake_send_text(*, session_webhook: str, text: str) -> None:
        return None

    class _CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            standard_logs.append(record.getMessage())

    capture_logger = logging.getLogger("aworld")
    capture_handler = _CaptureHandler()
    old_level = capture_logger.level
    capture_logger.setLevel(logging.INFO)
    capture_logger.addHandler(capture_handler)
    connector.send_text = fake_send_text  # type: ignore[method-assign]

    try:
        asyncio.run(
            connector.handle_callback(
                {
                    "sessionWebhook": "https://callback",
                    "conversationId": "conv-1",
                    "senderId": "user-1",
                    "text": {"content": "一分钟后提醒我喝水"},
                }
            )
        )
    finally:
        capture_logger.removeHandler(capture_handler)
        capture_logger.setLevel(old_level)

    assert any("DingTalk inbound message" in entry for entry in standard_logs)
    assert any("DingTalk runtime output" in entry for entry in standard_logs)
    assert any("DingTalk final reply" in entry for entry in standard_logs)


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
            on_output=None,
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
            "msgId": "msg-raw-1",
            "sessionWebhook": "https://callback",
            "conversationId": "conv-1",
            "senderId": "user-1",
            "content": "hello from raw content",
        }
    )
    asyncio.run(connector.handle_callback(payload))

    assert isinstance(bridge.calls[0]["text"], str)
    assert str(bridge.calls[0]["text"]).startswith("hello from raw content\n会话附加信息:")
    assert sent == ["（空响应）"]


def test_connector_appends_user_context_before_bridge_call() -> None:
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
                "conversationId": "conv-1",
                "senderId": "user-1",
                "senderNick": "Alice",
                "robotCode": "robot-1",
                "text": {"content": "帮我总结一下"},
            }
        )
    )

    assert sent == ["echo:帮我总结一下"]
    assert bridge.calls
    assert bridge.calls[0]["agent_id"] == "agent-1"
    assert bridge.calls[0]["session_id"] == connector._session_ids["conv-1"]
    assert bridge.calls[0]["text"] == (
        "帮我总结一下\n"
        "会话附加信息:\n"
        " - userId: user-1\n"
        " - userName: Alice\n"
        " - conversationId: conv-1\n"
        " - robotCode: robot-1\n"
        "\n"
        "执行要求:\n"
        " - 严格保留用户原始请求中的文件或日志名、时间范围、输出格式与交付动作。\n"
        " - 如需拆分或改写任务，不得遗漏这些明确约束。\n"
        " - 如果用户要求生成 HTML 或其他文件产物，必须生成对应产物，或在最终答复中明确说明阻塞原因。"
    )


def test_connector_builds_multimodal_bridge_input_from_downloaded_attachments(
    tmp_path: Path,
) -> None:
    connector = DingTalkConnector(
        config=DingdingChannelConfig(
            default_agent_id="agent-1",
            enable_attachments=True,
            workspace_dir=str(tmp_path / "workspace"),
        ),
        bridge=_FakeBridge(),
        stream_module=object(),
    )
    image_path = tmp_path / "chart.png"
    doc_path = tmp_path / "notes.txt"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    doc_path.write_text("meeting notes", encoding="utf-8")

    downloads = iter([str(image_path), str(doc_path)])
    connector._download_attachment = (  # type: ignore[method-assign]
        lambda attachment, session_key: asyncio.sleep(0, result=next(downloads))
    )

    result = asyncio.run(
        connector._build_llm_user_input(
            message=ExtractedMessage(
                text="请分析附件",
                attachments=[
                    IncomingAttachment(download_code="img-code", file_name="chart.png"),
                    IncomingAttachment(download_code="doc-code", file_name="notes.txt"),
                ],
            ),
            session_key="conv-1",
        )
    )

    assert isinstance(result, list)
    assert result[0] == {
        "type": "text",
        "text": f"请分析附件\n\n附件列表:\n  - {doc_path}",
    }
    assert result[1]["type"] == "image_url"
    assert str(result[1]["image_url"]["url"]).startswith("data:image/png;base64,")


def test_connector_throttles_ai_card_stream_updates(monkeypatch) -> None:
    class _ChunkyBridge(_FakeBridge):
        async def run(
            self,
            *,
            agent_id: str,
            session_id: str,
            text,
            on_text_chunk=None,
            on_output=None,
        ) -> DingdingBridgeResult:
            self.calls.append(
                {
                    "agent_id": agent_id,
                    "session_id": session_id,
                    "text": text,
                }
            )
            if on_text_chunk is not None:
                await on_text_chunk("A")
                await on_text_chunk("B")
                await on_text_chunk("C")
            return DingdingBridgeResult(text="ABC")

    connector = DingTalkConnector(
        config=DingdingChannelConfig(default_agent_id="aworld", enable_ai_card=True),
        bridge=_ChunkyBridge(),
        stream_module=object(),
        http_client=_FakeHttpClient(),
    )
    calls: list[tuple[str, str]] = []
    ticks = iter([0.0, 0.1, 0.39])

    async def fake_create_ai_card(data):
        return AICardInstance(card_instance_id="card-1", access_token="token")

    async def fake_stream_ai_card(card: AICardInstance, content: str, finished: bool) -> bool:
        calls.append(("stream", f"final:{content}" if finished else content))
        return True

    async def fake_finish_ai_card(card: AICardInstance, content: str) -> bool:
        calls.append(("finish", content))
        return True

    async def fake_send_pending_files(session_webhook: str, pending_files: list[PendingFileMessage]) -> None:
        calls.append(("files", str(len(pending_files))))

    connector._try_create_ai_card = fake_create_ai_card  # type: ignore[method-assign]
    connector._stream_ai_card = fake_stream_ai_card  # type: ignore[method-assign]
    connector._finish_ai_card = fake_finish_ai_card  # type: ignore[method-assign]
    connector._send_pending_files = fake_send_pending_files  # type: ignore[method-assign]
    connector._process_local_media_links = lambda content: asyncio.sleep(0, result=(content, []))  # type: ignore[method-assign]
    connector._now_for_ai_card_stream = lambda: next(ticks)  # type: ignore[method-assign]

    asyncio.run(
        connector.handle_callback(
            {
                "sessionWebhook": "https://callback",
                "conversationId": "conv-1",
                "senderId": "user-1",
                "text": {"content": "hi"},
            }
        )
    )

    assert calls == [
        ("stream", "A"),
        ("stream", "ABC"),
        ("finish", "ABC"),
        ("files", "0"),
    ]


def test_connector_does_not_send_processing_ack_when_ai_card_unavailable_for_fast_short_request(
    monkeypatch,
) -> None:
    connector = DingTalkConnector(
        config=DingdingChannelConfig(default_agent_id="aworld", enable_ai_card=False),
        bridge=_FakeBridge(),
        stream_module=object(),
        http_client=_FakeHttpClient(),
    )
    sent: list[str] = []
    info_logs: list[str] = []

    async def fake_send_text(*, session_webhook: str, text: str) -> None:
        sent.append(text)

    monkeypatch.setattr(
        "aworld_gateway.channels.dingding.connector.logger.info",
        lambda message, *args, **kwargs: info_logs.append(str(message)),
    )
    connector.send_text = fake_send_text  # type: ignore[method-assign]

    asyncio.run(
        connector.handle_callback(
            {
                "sessionWebhook": "https://callback",
                "conversationId": "conv-1",
                "senderId": "user-1",
                "text": {"content": "hi"},
            }
        )
    )

    assert sent == ["echo:hi"]
    assert any("DingTalk AI Card unavailable" in entry and "reason=disabled" in entry for entry in info_logs)
    assert any(
        "DingTalk stream summary" in entry
        and "fallback_to_text=True" in entry
        and "ack_sent=False" in entry
        for entry in info_logs
    )


def test_connector_try_create_ai_card_logs_missing_template_configuration(
    monkeypatch,
) -> None:
    connector = DingTalkConnector(
        config=DingdingChannelConfig(default_agent_id="aworld", enable_ai_card=True),
        bridge=_FakeBridge(),
        stream_module=object(),
        http_client=_FakeHttpClient(),
    )
    info_logs: list[str] = []

    monkeypatch.delenv("AWORLD_DINGTALK_CARD_TEMPLATE_ID", raising=False)
    monkeypatch.setattr(
        "aworld_gateway.channels.dingding.connector.logger.info",
        lambda message, *args, **kwargs: info_logs.append(str(message)),
    )

    result = asyncio.run(
        connector._try_create_ai_card(  # type: ignore[attr-defined]
            {
                "conversationId": "conv-1",
                "senderId": "user-1",
            }
        )
    )

    assert result is None
    assert any(
        "DingTalk AI Card unavailable" in entry and "reason=missing_card_template_id" in entry
        for entry in info_logs
    )


def test_connector_send_text_posts_dingtalk_text_payload() -> None:
    http_client = _FakeHttpClient()
    connector = DingTalkConnector(
        config=DingdingChannelConfig(default_agent_id="aworld"),
        bridge=_FakeBridge(),
        stream_module=object(),
        http_client=http_client,
    )
    connector._get_access_token = lambda: asyncio.sleep(0, result="access-token")  # type: ignore[method-assign]

    asyncio.run(
        connector.send_text(
            session_webhook="https://callback",
            text="hello",
        )
    )

    assert http_client.calls == [
        (
            "https://callback",
            {
                "headers": {
                    "x-acs-dingtalk-access-token": "access-token",
                    "Content-Type": "application/json",
                },
                "json": {"msgtype": "text", "text": {"content": "hello"}},
            },
        )
    ]
    assert http_client.response.raised is True


def test_connector_required_env_raises_on_missing(monkeypatch) -> None:
    monkeypatch.delenv("AWORLD_DINGTALK_CLIENT_ID", raising=False)

    with pytest.raises(ValueError, match="AWORLD_DINGTALK_CLIENT_ID"):
        DingTalkConnector._required_env("AWORLD_DINGTALK_CLIENT_ID")


def test_connector_extract_message_supports_rich_text_and_attachments() -> None:
    message = DingTalkConnector._extract_message(
        {
            "msgtype": "richText",
            "content": {
                "richText": [
                    {"text": "请分析"},
                    {"text": "这个文件"},
                    {"downloadCode": "code-1", "fileName": "report.pdf"},
                ]
            },
        }
    )

    assert message.text == "请分析这个文件"
    assert message.attachments == [
        IncomingAttachment(download_code="code-1", file_name="report.pdf")
    ]


def test_connector_process_local_media_links_uploads_and_collects_files(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "chart.png"
    file_path = tmp_path / "report.txt"
    image_path.write_bytes(b"img")
    file_path.write_text("report", encoding="utf-8")

    connector = DingTalkConnector(
        config=DingdingChannelConfig(enable_attachments=True),
        bridge=_FakeBridge(),
        stream_module=object(),
        http_client=_FakeHttpClient(),
    )
    connector._get_oapi_access_token = lambda: asyncio.sleep(0, result="oapi-token")  # type: ignore[method-assign]

    async def fake_upload(local_path: Path, media_type: str, oapi_token: str) -> str | None:
        assert oapi_token == "oapi-token"
        return f"{media_type}:{local_path.name}"

    connector._upload_local_file_to_dingtalk = fake_upload  # type: ignore[method-assign]

    content = (
        f"结果如下 ![图表](attachment://{image_path})\n"
        f"[下载报告](attachment://{file_path})"
    )
    cleaned, pending_files = asyncio.run(connector._process_local_media_links(content))

    assert cleaned == "结果如下 ![图表](image:chart.png)"
    assert pending_files == [
        PendingFileMessage(
            media_id="file:report.txt",
            file_name="report.txt",
            file_type="txt",
        )
    ]


def test_connector_streams_ai_card_and_sends_pending_files() -> None:
    connector = DingTalkConnector(
        config=DingdingChannelConfig(default_agent_id="aworld", enable_ai_card=True),
        bridge=_FakeBridge(),
        stream_module=object(),
        http_client=_FakeHttpClient(),
    )
    calls: list[tuple[str, str]] = []

    async def fake_create_ai_card(data):
        return AICardInstance(card_instance_id="card-1", access_token="token")

    async def fake_stream_ai_card(card: AICardInstance, content: str, finished: bool) -> bool:
        calls.append(("stream", f"final:{content}" if finished else content))
        return True

    async def fake_send_text(*, session_webhook: str, text: str) -> None:
        calls.append(("text", text))

    async def fake_process_local_media_links(content: str):
        return content.replace(" [下载]", ""), [
            PendingFileMessage(
                media_id="file:report.txt",
                file_name="report.txt",
                file_type="txt",
            )
        ]

    async def fake_send_pending_files(session_webhook: str, pending_files: list[PendingFileMessage]) -> None:
        calls.append(("file", pending_files[0].file_name))

    connector._try_create_ai_card = fake_create_ai_card  # type: ignore[method-assign]
    connector._stream_ai_card = fake_stream_ai_card  # type: ignore[method-assign]
    connector.send_text = fake_send_text  # type: ignore[method-assign]
    connector._process_local_media_links = fake_process_local_media_links  # type: ignore[method-assign]
    connector._send_pending_files = fake_send_pending_files  # type: ignore[method-assign]

    asyncio.run(
        connector.handle_callback(
            {
                "sessionWebhook": "https://callback",
                "conversationId": "conv-1",
                "senderId": "user-1",
                "text": {"content": "hi"},
            }
        )
    )

    assert calls == [
        ("stream", "echo:"),
        ("stream", "final:echo:hi"),
        ("file", "report.txt"),
    ]


def test_connector_binds_dingding_cron_jobs_and_fanouts_notifications(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class _CronBridge(_FakeBridge):
        async def run(
            self,
            *,
            agent_id: str,
            session_id: str,
            text: str,
            on_text_chunk=None,
            on_output=None,
        ) -> DingdingBridgeResult:
            self.calls.append(
                {
                    "agent_id": agent_id,
                    "session_id": session_id,
                    "text": text,
                }
            )
            if on_output is not None:
                await on_output(
                    ToolResultOutput(
                        tool_name="cron",
                        action_name="cron_tool",
                        data={
                            "success": True,
                            "job_id": "job-main",
                            "advance_reminder": {"job_id": "job-advance"},
                        },
                    )
                )
            return DingdingBridgeResult(text="已创建提醒")

    class _FakeScheduler:
        def __init__(self) -> None:
            self.notification_sink = None

    scheduler = _FakeScheduler()
    monkeypatch.setattr("aworld.core.scheduler.get_scheduler", lambda: scheduler)

    connector = DingTalkConnector(
        config=DingdingChannelConfig(
            default_agent_id="agent-1",
            workspace_dir=str(tmp_path / "workspace"),
        ),
        bridge=_CronBridge(),
        stream_module=object(),
    )
    sent: list[tuple[str, str]] = []

    async def fake_send_text(*, session_webhook: str, text: str) -> None:
        sent.append((session_webhook, text))

    connector.send_text = fake_send_text  # type: ignore[method-assign]
    asyncio.run(connector._prepare_cron_runtime())

    asyncio.run(
        connector.handle_callback(
            {
                "sessionWebhook": "https://callback",
                "conversationId": "conv-1",
                "senderId": "user-1",
                "text": {"content": "一分钟后提醒我喝水"},
            }
        )
    )

    assert scheduler.notification_sink is not None
    assert connector._cron_push_bridge._binding_store.get("job-main") == {
        "job_id": "job-main",
        "channel": "dingtalk",
        "conversation_id": "conv-1",
        "sender_id": "user-1",
        "target": {"session_webhook": "https://callback"},
    }
    assert connector._cron_push_bridge._binding_store.get("job-advance") == {
        "job_id": "job-advance",
        "channel": "dingtalk",
        "conversation_id": "conv-1",
        "sender_id": "user-1",
        "target": {"session_webhook": "https://callback"},
    }

    asyncio.run(
        scheduler.notification_sink(
            {
                "job_id": "job-main",
                "summary": 'Cron task "喝水提醒" completed',
                "detail": "提醒我喝水",
                "next_run_at": None,
            }
        )
    )

    assert sent == [
        ("https://callback", PROCESSING_ACK_TEXT),
        ("https://callback", "已创建提醒"),
        ("https://callback", 'Cron task "喝水提醒" completed\n提醒我喝水'),
    ]
    assert connector._cron_push_bridge._binding_store.get("job-main") is None
    assert connector._cron_push_bridge._binding_store.get("job-advance") == {
        "job_id": "job-advance",
        "channel": "dingtalk",
        "conversation_id": "conv-1",
        "sender_id": "user-1",
        "target": {"session_webhook": "https://callback"},
    }


def test_connector_preserves_dingding_cron_binding_for_recurring_notifications(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class _CronBridge(_FakeBridge):
        async def run(
            self,
            *,
            agent_id: str,
            session_id: str,
            text: str,
            on_text_chunk=None,
            on_output=None,
        ) -> DingdingBridgeResult:
            self.calls.append(
                {
                    "agent_id": agent_id,
                    "session_id": session_id,
                    "text": text,
                }
            )
            if on_output is not None:
                await on_output(
                    ToolResultOutput(
                        tool_name="cron",
                        action_name="cron_tool",
                        data={
                            "success": True,
                            "job_id": "job-main",
                        },
                    )
                )
            return DingdingBridgeResult(text="已创建提醒")

    class _FakeScheduler:
        def __init__(self) -> None:
            self.notification_sink = None

    scheduler = _FakeScheduler()
    monkeypatch.setattr("aworld.core.scheduler.get_scheduler", lambda: scheduler)

    connector = DingTalkConnector(
        config=DingdingChannelConfig(
            default_agent_id="agent-1",
            workspace_dir=str(tmp_path / "workspace"),
        ),
        bridge=_CronBridge(),
        stream_module=object(),
    )
    sent: list[tuple[str, str]] = []

    async def fake_send_text(*, session_webhook: str, text: str) -> None:
        sent.append((session_webhook, text))

    connector.send_text = fake_send_text  # type: ignore[method-assign]
    asyncio.run(connector._prepare_cron_runtime())

    asyncio.run(
        connector.handle_callback(
            {
                "sessionWebhook": "https://callback",
                "conversationId": "conv-1",
                "senderId": "user-1",
                "text": {"content": "明天早上九点提醒我开会"},
            }
        )
    )

    assert scheduler.notification_sink is not None

    asyncio.run(
        scheduler.notification_sink(
            {
                "job_id": "job-main",
                "summary": 'Cron task "开会提醒" completed',
                "detail": "提醒我开会",
                "next_run_at": "2026-04-29T09:00:00+08:00",
            }
        )
    )

    assert sent == [
        ("https://callback", PROCESSING_ACK_TEXT),
        ("https://callback", "已创建提醒"),
        (
            "https://callback",
            'Cron task "开会提醒" completed\n提醒我开会\n下次执行：2026-04-29T09:00:00+08:00',
        ),
    ]
    assert connector._cron_push_bridge._binding_store.get("job-main") == {
        "job_id": "job-main",
        "channel": "dingtalk",
        "conversation_id": "conv-1",
        "sender_id": "user-1",
        "target": {"session_webhook": "https://callback"},
    }


def test_connector_migrates_legacy_dingding_cron_bindings_to_shared_shape(
    tmp_path: Path,
) -> None:
    workspace_dir = (tmp_path / "workspace").resolve()
    workspace_dir.mkdir(parents=True, exist_ok=True)
    binding_store_path = workspace_dir.parent / "cron-bindings.json"
    binding_store_path.write_text(
        json.dumps(
            {
                "job-main": {
                    "job_id": "job-main",
                    "session_webhook": "https://callback",
                    "conversation_id": "conv-1",
                    "sender_id": "user-1",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    connector = DingTalkConnector(
        config=DingdingChannelConfig(
            default_agent_id="agent-1",
            workspace_dir=str(workspace_dir),
        ),
        bridge=_FakeBridge(),
        stream_module=object(),
    )

    assert connector._cron_push_bridge._binding_store.get("job-main") == {
        "job_id": "job-main",
        "channel": "dingtalk",
        "conversation_id": "conv-1",
        "sender_id": "user-1",
        "target": {"session_webhook": "https://callback"},
    }


def test_connector_falls_back_to_text_when_ai_card_finalize_fails() -> None:
    connector = DingTalkConnector(
        config=DingdingChannelConfig(default_agent_id="aworld", enable_ai_card=True),
        bridge=_FakeBridge(),
        stream_module=object(),
        http_client=_FakeHttpClient(),
    )
    calls: list[tuple[str, str]] = []
    info_logs: list[str] = []

    async def fake_create_ai_card(data):
        return AICardInstance(card_instance_id="card-1", access_token="token")

    async def fake_stream_ai_card(card: AICardInstance, content: str, finished: bool) -> bool:
        calls.append(("stream", f"final:{content}" if finished else content))
        return not finished

    async def fake_send_text(*, session_webhook: str, text: str) -> None:
        calls.append(("text", text))

    async def fake_process_local_media_links(content: str):
        return content, []

    connector._try_create_ai_card = fake_create_ai_card  # type: ignore[method-assign]
    connector._stream_ai_card = fake_stream_ai_card  # type: ignore[method-assign]
    connector.send_text = fake_send_text  # type: ignore[method-assign]
    connector._process_local_media_links = fake_process_local_media_links  # type: ignore[method-assign]
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "aworld_gateway.channels.dingding.connector.logger.info",
        lambda message, *args, **kwargs: info_logs.append(str(message)),
    )

    try:
        asyncio.run(
            connector.handle_callback(
                {
                    "sessionWebhook": "https://callback",
                    "conversationId": "conv-1",
                    "senderId": "user-1",
                    "text": {"content": "hi"},
                }
            )
        )
    finally:
        monkeypatch.undo()

    assert calls[-1] == ("text", "echo:hi")
    assert any("DingTalk AI Card finalize failed" in entry for entry in info_logs)


def test_connector_rewrites_artifact_scheme_to_gateway_url(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "workspace"
    report_path = workspace_dir / "reports" / "summary.html"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("summary", encoding="utf-8")

    class _FakeArtifactService:
        def publish(self, path: Path | str) -> str:
            assert Path(path) == report_path
            return "artifact-token"

        def build_external_url(self, token: str) -> str:
            assert token == "artifact-token"
            return "https://gateway.example.com/artifacts/artifact-token"

    connector = DingTalkConnector(
        config=DingdingChannelConfig(enable_attachments=True, workspace_dir=str(workspace_dir)),
        bridge=_FakeBridge(),
        stream_module=object(),
        http_client=_FakeHttpClient(),
        artifact_service=_FakeArtifactService(),
    )
    connector._get_oapi_access_token = lambda: asyncio.sleep(0, result=None)  # type: ignore[method-assign]

    cleaned, pending_files = asyncio.run(
        connector._process_local_media_links("查看 [HTML 报告](artifact://reports/summary.html)")
    )

    assert cleaned == "查看 [HTML 报告](https://gateway.example.com/artifacts/artifact-token)"
    assert pending_files == []


def test_connector_rewrites_legacy_attachment_url_when_native_upload_unavailable(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "report.txt"
    report_path.write_text("report", encoding="utf-8")

    class _FakeArtifactService:
        def publish(self, path: Path | str) -> str:
            assert Path(path) == report_path
            return "legacy-token"

        def build_external_url(self, token: str) -> str:
            assert token == "legacy-token"
            return "https://gateway.example.com/artifacts/legacy-token"

    connector = DingTalkConnector(
        config=DingdingChannelConfig(enable_attachments=True),
        bridge=_FakeBridge(),
        stream_module=object(),
        http_client=_FakeHttpClient(),
        artifact_service=_FakeArtifactService(),
    )
    connector._get_oapi_access_token = lambda: asyncio.sleep(0, result=None)  # type: ignore[method-assign]

    cleaned, pending_files = asyncio.run(
        connector._process_local_media_links(
            f"下载 [报告](attachment://{report_path})"
        )
    )

    assert cleaned == "下载 [报告](https://gateway.example.com/artifacts/legacy-token)"
    assert pending_files == []


def test_connector_keeps_native_file_upload_when_oapi_token_available(tmp_path: Path) -> None:
    report_path = tmp_path / "report.txt"
    report_path.write_text("report", encoding="utf-8")

    class _FakeArtifactService:
        def publish(self, path: Path | str) -> str:
            return "should-not-be-used"

        def build_external_url(self, token: str) -> str:
            return f"https://gateway.example.com/artifacts/{token}"

    connector = DingTalkConnector(
        config=DingdingChannelConfig(enable_attachments=True),
        bridge=_FakeBridge(),
        stream_module=object(),
        http_client=_FakeHttpClient(),
        artifact_service=_FakeArtifactService(),
    )
    connector._get_oapi_access_token = lambda: asyncio.sleep(0, result="oapi-token")  # type: ignore[method-assign]
    connector._upload_local_file_to_dingtalk = (  # type: ignore[method-assign]
        lambda local_path, media_type, oapi_token: asyncio.sleep(0, result="media-file-1")
    )

    cleaned, pending_files = asyncio.run(
        connector._process_local_media_links(
            f"请查看 [报告](attachment://{report_path})"
        )
    )

    assert cleaned == "请查看"
    assert pending_files == [
        PendingFileMessage(
            media_id="media-file-1",
            file_name="report.txt",
            file_type="txt",
        )
    ]


def test_connector_skips_artifact_publish_when_build_external_url_fails(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "workspace"
    report_path = workspace_dir / "reports" / "summary.html"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("summary", encoding="utf-8")

    calls = {"publish": 0}

    class _FailingArtifactService:
        def publish(self, path: Path | str) -> str:
            calls["publish"] += 1
            assert Path(path) == report_path
            return "artifact-token"

        def build_external_url(self, token: str) -> str:
            raise ValueError("missing base url")

    connector = DingTalkConnector(
        config=DingdingChannelConfig(enable_attachments=True, workspace_dir=str(workspace_dir)),
        bridge=_FakeBridge(),
        stream_module=object(),
        http_client=_FakeHttpClient(),
        artifact_service=_FailingArtifactService(),
    )
    connector._get_oapi_access_token = lambda: asyncio.sleep(0, result=None)  # type: ignore[method-assign]

    original = "查看 [HTML 报告](artifact://reports/summary.html)"
    cleaned, pending_files = asyncio.run(connector._process_local_media_links(original))

    assert calls["publish"] == 1
    assert cleaned == original
    assert pending_files == []


def test_connector_does_not_publish_artifact_when_public_base_url_missing(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "workspace"
    report_path = workspace_dir / "report.html"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    report_path.write_text("summary", encoding="utf-8")
    service = ArtifactService(public_base_url=None, allowed_roots=[workspace_dir])

    connector = DingTalkConnector(
        config=DingdingChannelConfig(enable_attachments=True, workspace_dir=str(workspace_dir)),
        bridge=_FakeBridge(),
        stream_module=object(),
        http_client=_FakeHttpClient(),
        artifact_service=service,
    )
    connector._get_oapi_access_token = lambda: asyncio.sleep(0, result=None)  # type: ignore[method-assign]

    original = "[HTML 报告](artifact://report.html)"
    cleaned, pending_files = asyncio.run(connector._process_local_media_links(original))

    assert cleaned == original
    assert pending_files == []
    assert len(service._artifacts) == 0


def test_connector_rewrites_plain_artifact_reference_with_trailing_period(
    tmp_path: Path,
) -> None:
    workspace_dir = tmp_path / "workspace"
    report_path = workspace_dir / "reports" / "summary.html"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("summary", encoding="utf-8")

    class _FakeArtifactService:
        def publish(self, path: Path | str) -> str:
            assert Path(path) == report_path
            return "token"

        def build_external_url(self, token: str) -> str:
            assert token == "token"
            return "https://gateway.example.com/artifacts/token"

    connector = DingTalkConnector(
        config=DingdingChannelConfig(enable_attachments=True, workspace_dir=str(workspace_dir)),
        bridge=_FakeBridge(),
        stream_module=object(),
        http_client=_FakeHttpClient(),
        artifact_service=_FakeArtifactService(),
    )
    connector._get_oapi_access_token = lambda: asyncio.sleep(0, result=None)  # type: ignore[method-assign]

    cleaned, pending_files = asyncio.run(
        connector._process_local_media_links("请查看 artifact://reports/summary.html.")
    )

    assert cleaned == "请查看 https://gateway.example.com/artifacts/token."
    assert pending_files == []


def test_connector_rewrites_inline_code_local_path_to_gateway_url(
    tmp_path: Path,
) -> None:
    workspace_dir = tmp_path / "workspace"
    report_path = workspace_dir / "reports" / "summary.html"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("summary", encoding="utf-8")

    class _FakeArtifactService:
        def publish(self, path: Path | str) -> str:
            assert Path(path) == report_path
            return "inline-token"

        def build_external_url(self, token: str) -> str:
            assert token == "inline-token"
            return "https://gateway.example.com/artifacts/inline-token"

    connector = DingTalkConnector(
        config=DingdingChannelConfig(enable_attachments=True, workspace_dir=str(workspace_dir)),
        bridge=_FakeBridge(),
        stream_module=object(),
        http_client=_FakeHttpClient(),
        artifact_service=_FakeArtifactService(),
    )
    connector._get_oapi_access_token = lambda: asyncio.sleep(0, result=None)  # type: ignore[method-assign]

    cleaned, pending_files = asyncio.run(
        connector._process_local_media_links(f"路径: `{report_path}`")
    )

    assert cleaned == "路径: https://gateway.example.com/artifacts/inline-token"
    assert pending_files == []


def test_connector_does_not_publish_external_local_path_outside_allowed_roots(
    tmp_path: Path,
) -> None:
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    external_dir = tmp_path / "survey"
    external_dir.mkdir(parents=True, exist_ok=True)
    report_path = external_dir / "ai_news_report.html"
    report_path.write_text("summary", encoding="utf-8")
    service = ArtifactService(
        public_base_url="https://gateway.example.com",
        allowed_roots=[workspace_dir.resolve()],
    )

    connector = DingTalkConnector(
        config=DingdingChannelConfig(enable_attachments=True, workspace_dir=str(workspace_dir)),
        bridge=_FakeBridge(),
        stream_module=object(),
        http_client=_FakeHttpClient(),
        artifact_service=service,
    )
    connector._get_oapi_access_token = lambda: asyncio.sleep(0, result=None)  # type: ignore[method-assign]

    cleaned, pending_files = asyncio.run(
        connector._process_local_media_links(f"路径: {report_path}")
    )

    assert cleaned == f"路径: {report_path}"
    assert pending_files == []
    assert not (workspace_dir / "published").exists()


def test_connector_rewrites_bare_windows_path_in_plain_text(tmp_path: Path) -> None:
    local_file = tmp_path / "report.txt"
    local_file.write_text("report", encoding="utf-8")

    class _FakeArtifactService:
        def publish(self, path: Path | str) -> str:
            assert Path(path) == local_file
            return "windows-token"

        def build_external_url(self, token: str) -> str:
            assert token == "windows-token"
            return "https://gateway.example.com/artifacts/windows-token"

    connector = DingTalkConnector(
        config=DingdingChannelConfig(enable_attachments=True),
        bridge=_FakeBridge(),
        stream_module=object(),
        http_client=_FakeHttpClient(),
        artifact_service=_FakeArtifactService(),
    )
    connector._get_oapi_access_token = lambda: asyncio.sleep(0, result=None)  # type: ignore[method-assign]

    original_extract_local_file_path = connector._extract_local_file_path

    def fake_extract_local_file_path(raw_url: str):  # type: ignore[no-untyped-def]
        if raw_url == "C:/tmp/report.txt":
            return local_file
        return original_extract_local_file_path(raw_url)

    connector._extract_local_file_path = fake_extract_local_file_path  # type: ignore[assignment]

    cleaned, pending_files = asyncio.run(
        connector._process_local_media_links("请查看 C:/tmp/report.txt")
    )

    assert cleaned == "请查看 https://gateway.example.com/artifacts/windows-token"
    assert pending_files == []


def test_connector_rewrites_mixed_markdown_and_plain_artifact_references(
    tmp_path: Path,
) -> None:
    workspace_dir = tmp_path / "workspace"
    report_path = workspace_dir / "report.html"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    report_path.write_text("report", encoding="utf-8")

    class _FakeArtifactService:
        def __init__(self) -> None:
            self._counter = 0

        def publish(self, path: Path | str) -> str:
            assert Path(path) == report_path
            self._counter += 1
            return f"token-{self._counter}"

        def build_external_url(self, token: str) -> str:
            return f"https://gateway.example.com/artifacts/{token}"

    connector = DingTalkConnector(
        config=DingdingChannelConfig(enable_attachments=True, workspace_dir=str(workspace_dir)),
        bridge=_FakeBridge(),
        stream_module=object(),
        http_client=_FakeHttpClient(),
        artifact_service=_FakeArtifactService(),
    )
    connector._get_oapi_access_token = lambda: asyncio.sleep(0, result=None)  # type: ignore[method-assign]

    cleaned, pending_files = asyncio.run(
        connector._process_local_media_links(
            "[报告](artifact://report.html) artifact://report.html"
        )
    )

    assert "[报告](https://gateway.example.com/artifacts/" in cleaned
    assert "https://gateway.example.com/artifacts/" in cleaned
    assert "artifact://report.html" not in cleaned
    assert pending_files == []
