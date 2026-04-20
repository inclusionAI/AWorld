from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.channels.dingding.connector import DingTalkConnector
from aworld_gateway.channels.dingding.types import (
    AICardInstance,
    DingdingBridgeResult,
    IncomingAttachment,
    PendingFileMessage,
)
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
        if on_text_chunk is not None:
            await on_text_chunk("echo:")
            await on_text_chunk(text)
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
        ("stream", "echo:hi"),
        ("stream", "final:echo:hi"),
        ("file", "report.txt"),
    ]


def test_connector_falls_back_to_text_when_ai_card_finalize_fails() -> None:
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
        return not finished

    async def fake_send_text(*, session_webhook: str, text: str) -> None:
        calls.append(("text", text))

    async def fake_process_local_media_links(content: str):
        return content, []

    connector._try_create_ai_card = fake_create_ai_card  # type: ignore[method-assign]
    connector._stream_ai_card = fake_stream_ai_card  # type: ignore[method-assign]
    connector.send_text = fake_send_text  # type: ignore[method-assign]
    connector._process_local_media_links = fake_process_local_media_links  # type: ignore[method-assign]

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

    assert calls[-1] == ("text", "echo:hi")


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

    assert calls["publish"] >= 1
    assert cleaned == original
    assert pending_files == []
