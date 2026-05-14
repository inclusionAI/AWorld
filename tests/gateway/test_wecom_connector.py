from __future__ import annotations

import asyncio
import base64
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.config import WecomChannelConfig
from aworld_gateway.types import OutboundEnvelope


class _FakeRouter:
    def __init__(self) -> None:
        self.calls: list[tuple[object, str | None]] = []

    async def handle_inbound(self, inbound, *, channel_default_agent_id):
        self.calls.append((inbound, channel_default_agent_id))
        return OutboundEnvelope(
            channel="wecom",
            account_id=inbound.account_id,
            conversation_id=inbound.conversation_id,
            reply_to_message_id=inbound.message_id,
            text=f"echo:{inbound.text}",
        )


class _BlockingWecomRouter:
    def __init__(self) -> None:
        self.started_texts: list[str] = []
        self.first_started = asyncio.Event()
        self.second_started = asyncio.Event()
        self.release_first = asyncio.Event()

    async def handle_inbound(self, inbound, *, channel_default_agent_id):
        del channel_default_agent_id
        self.started_texts.append(inbound.text)
        if len(self.started_texts) == 1:
            self.first_started.set()
            await self.release_first.wait()
        elif len(self.started_texts) == 2:
            self.second_started.set()
        return OutboundEnvelope(
            channel="wecom",
            account_id=inbound.account_id,
            conversation_id=inbound.conversation_id,
            reply_to_message_id=inbound.message_id,
            text=f"echo:{inbound.text}",
        )


class _FakeTransport:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []
        self.closed = False
        self._queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()

    async def send_json(self, payload: dict[str, object]) -> None:
        self.sent.append(payload)
        cmd = str(payload.get("cmd") or "")
        headers = payload.get("headers") if isinstance(payload.get("headers"), dict) else {}
        req_id = str(headers.get("req_id") or "")
        if cmd in {
            "aibot_subscribe",
            "aibot_send_msg",
            "aibot_respond_msg",
            "aibot_upload_media_chunk",
            "ping",
        } and req_id:
            self.feed({"cmd": f"{cmd}_ack", "headers": {"req_id": req_id}, "errcode": 0, "body": {}})
        if cmd == "aibot_upload_media_init" and req_id:
            self.feed(
                {
                    "cmd": "aibot_upload_media_init_ack",
                    "headers": {"req_id": req_id},
                    "errcode": 0,
                    "body": {"upload_id": "upload-1"},
                }
            )
        if cmd == "aibot_upload_media_finish" and req_id:
            self.feed(
                {
                    "cmd": "aibot_upload_media_finish_ack",
                    "headers": {"req_id": req_id},
                    "errcode": 0,
                    "body": {"media_id": "media-1", "type": "image"},
                }
            )

    async def receive_json(self) -> dict[str, object]:
        return await asyncio.wait_for(self._queue.get(), timeout=1.0)

    def feed(self, payload: dict[str, object]) -> None:
        self._queue.put_nowait(payload)

    async def close(self) -> None:
        self.closed = True


class _FlakyTransport(_FakeTransport):
    def __init__(self, *, fail_after_receives: int) -> None:
        super().__init__()
        self._receive_count = 0
        self._fail_after_receives = fail_after_receives

    async def receive_json(self) -> dict[str, object]:
        self._receive_count += 1
        if self._receive_count > self._fail_after_receives:
            self.closed = True
            raise RuntimeError("simulated websocket close")
        return await super().receive_json()


@pytest.mark.asyncio
async def test_connector_start_performs_subscribe_handshake(monkeypatch: pytest.MonkeyPatch) -> None:
    from aworld_gateway.channels.wecom.connector import WecomConnector

    transport = _FakeTransport()
    monkeypatch.setenv("AWORLD_WECOM_BOT_ID", "bot-1")
    monkeypatch.setenv("AWORLD_WECOM_SECRET", "secret-1")
    monkeypatch.setenv("AWORLD_WECOM_WEBSOCKET_URL", "wss://wecom.example.test/ws")

    connector = WecomConnector(
        config=WecomChannelConfig(),
        router=None,
        connect_func=lambda url: asyncio.sleep(0, result=transport),
    )
    await connector.start()

    assert connector.started is True
    assert transport.sent[0]["cmd"] == "aibot_subscribe"
    assert transport.sent[0]["body"] == {
        "bot_id": "bot-1",
        "secret": "secret-1",
    }

    await connector.stop()
    assert transport.closed is True


@pytest.mark.asyncio
async def test_connector_processes_callback_routes_text_and_uses_reply_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aworld_gateway.channels.wecom.connector import WecomConnector

    router = _FakeRouter()
    transport = _FakeTransport()
    monkeypatch.setenv("AWORLD_WECOM_BOT_ID", "bot-1")
    monkeypatch.setenv("AWORLD_WECOM_SECRET", "secret-1")

    connector = WecomConnector(
        config=WecomChannelConfig(default_agent_id="aworld"),
        router=router,
        connect_func=lambda url: asyncio.sleep(0, result=transport),
    )
    await connector.start()
    transport.feed(
        {
            "cmd": "aibot_msg_callback",
            "headers": {"req_id": "req-callback-1"},
            "body": {
                "msgid": "msg-1",
                "chatid": "chat-1",
                "chattype": "single",
                "from": {"userid": "user-1"},
                "msgtype": "text",
                "text": {"content": "ping"},
            },
        }
    )

    await asyncio.sleep(0.05)

    inbound, channel_default_agent_id = router.calls[0]
    assert inbound.text == "ping"
    assert inbound.conversation_id == "chat-1"
    assert inbound.conversation_type == "dm"
    assert channel_default_agent_id == "aworld"
    assert connector._reply_req_ids["msg-1"] == "req-callback-1"
    assert connector._last_chat_req_ids["chat-1"] == "req-callback-1"
    assert transport.sent[-1]["cmd"] == "aibot_respond_msg"

    await connector.stop()


@pytest.mark.asyncio
async def test_connector_serializes_same_chat_callbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aworld_gateway.channels.wecom.connector import WecomConnector

    router = _BlockingWecomRouter()
    transport = _FakeTransport()
    monkeypatch.setenv("AWORLD_WECOM_BOT_ID", "bot-1")
    monkeypatch.setenv("AWORLD_WECOM_SECRET", "secret-1")

    connector = WecomConnector(
        config=WecomChannelConfig(default_agent_id="aworld"),
        router=router,
        connect_func=lambda url: asyncio.sleep(0, result=transport),
    )
    connector.send_text = AsyncMock(return_value={"errcode": 0})  # type: ignore[method-assign]
    await connector.start()

    connector._schedule_callback(
        {
            "cmd": "aibot_msg_callback",
            "headers": {"req_id": "req-1"},
            "body": {
                "msgid": "msg-1",
                "chatid": "chat-1",
                "chattype": "single",
                "from": {"userid": "user-1"},
                "msgtype": "text",
                "text": {"content": "first"},
            },
        }
    )
    connector._schedule_callback(
        {
            "cmd": "aibot_msg_callback",
            "headers": {"req_id": "req-2"},
            "body": {
                "msgid": "msg-2",
                "chatid": "chat-1",
                "chattype": "single",
                "from": {"userid": "user-1"},
                "msgtype": "text",
                "text": {"content": "second"},
            },
        }
    )

    await asyncio.wait_for(router.first_started.wait(), timeout=1.0)
    await asyncio.sleep(0.05)
    assert router.second_started.is_set() is False

    router.release_first.set()
    await asyncio.wait_for(router.second_started.wait(), timeout=1.0)
    await connector.stop()

    assert router.started_texts == ["first", "second"]


@pytest.mark.asyncio
async def test_connector_send_text_uses_proactive_send_without_reply_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aworld_gateway.channels.wecom.connector import WecomConnector

    transport = _FakeTransport()
    monkeypatch.setenv("AWORLD_WECOM_BOT_ID", "bot-1")
    monkeypatch.setenv("AWORLD_WECOM_SECRET", "secret-1")

    connector = WecomConnector(
        config=WecomChannelConfig(),
        router=None,
        connect_func=lambda url: asyncio.sleep(0, result=transport),
    )
    await connector.start()

    result = await connector.send_text(chat_id="chat-1", text="pong")

    assert transport.sent[-1]["cmd"] == "aibot_send_msg"
    assert result["errcode"] == 0
    await connector.stop()


@pytest.mark.asyncio
async def test_connector_logs_start_callback_and_send_flow(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from aworld_gateway.channels.wecom.connector import WecomConnector

    router = _FakeRouter()
    transport = _FakeTransport()
    monkeypatch.setenv("AWORLD_WECOM_BOT_ID", "bot-1")
    monkeypatch.setenv("AWORLD_WECOM_SECRET", "secret-1")
    caplog.set_level(logging.INFO, logger="aworld.gateway")

    connector = WecomConnector(
        config=WecomChannelConfig(default_agent_id="aworld"),
        router=router,
        connect_func=lambda url: asyncio.sleep(0, result=transport),
    )
    await connector.start()
    transport.feed(
        {
            "cmd": "aibot_msg_callback",
            "headers": {"req_id": "req-callback-1"},
            "body": {
                "msgid": "msg-1",
                "chatid": "chat-1",
                "chattype": "single",
                "from": {"userid": "user-1"},
                "msgtype": "text",
                "text": {"content": "ping"},
            },
        }
    )

    await asyncio.sleep(0.05)
    await connector.stop()

    assert "WeCom connector starting bot_id=bot-1" in caplog.text
    assert "WeCom connection opened ws_url=" in caplog.text
    assert "WeCom inbound message conversation=chat-1 sender=user-1 message_id=msg-1" in caplog.text
    assert "WeCom outbound send requested chat_id=chat-1" in caplog.text
    assert "WeCom connector stopped bot_id=bot-1" in caplog.text


@pytest.mark.asyncio
async def test_connector_skips_group_message_when_group_policy_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aworld_gateway.channels.wecom.connector import WecomConnector

    router = _FakeRouter()
    transport = _FakeTransport()
    monkeypatch.setenv("AWORLD_WECOM_BOT_ID", "bot-1")
    monkeypatch.setenv("AWORLD_WECOM_SECRET", "secret-1")

    connector = WecomConnector(
        config=WecomChannelConfig(default_agent_id="aworld", group_policy="disabled"),
        router=router,
        connect_func=lambda url: asyncio.sleep(0, result=transport),
    )
    await connector.start()
    transport.feed(
        {
            "cmd": "aibot_msg_callback",
            "headers": {"req_id": "req-group-1"},
            "body": {
                "msgid": "msg-1",
                "chatid": "group-1",
                "chattype": "group",
                "from": {"userid": "user-1"},
                "msgtype": "text",
                "text": {"content": "ping"},
            },
        }
    )

    await asyncio.sleep(0.05)

    assert router.calls == []
    await connector.stop()


@pytest.mark.asyncio
async def test_connector_processes_image_callback_into_structured_media_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aworld_gateway.channels.wecom.connector import WecomConnector

    router = _FakeRouter()
    transport = _FakeTransport()
    monkeypatch.setenv("AWORLD_WECOM_BOT_ID", "bot-1")
    monkeypatch.setenv("AWORLD_WECOM_SECRET", "secret-1")

    image_bytes = b"\x89PNG\r\n\x1a\nfake"
    connector = WecomConnector(
        config=WecomChannelConfig(default_agent_id="aworld"),
        router=router,
        connect_func=lambda url: asyncio.sleep(0, result=transport),
    )
    await connector.start()
    transport.feed(
        {
            "cmd": "aibot_msg_callback",
            "headers": {"req_id": "req-image-1"},
            "body": {
                "msgid": "msg-image-1",
                "chatid": "chat-1",
                "chattype": "single",
                "from": {"userid": "user-1"},
                "msgtype": "image",
                "image": {"base64": base64.b64encode(image_bytes).decode("ascii")},
            },
        }
    )

    await asyncio.sleep(0.05)

    inbound, _channel_default_agent_id = router.calls[0]
    assert inbound.text.startswith("Attachments:")
    assert inbound.metadata["attachments"][0]["type"] == "image"
    image_path = Path(inbound.metadata["attachments"][0]["path"])
    assert image_path.read_bytes() == image_bytes
    assert inbound.metadata["wecom_media"] == [
        {
            "kind": "image",
            "local_path": str(image_path),
            "file_name": "image.png",
            "mime_type": "image/png",
            "size_bytes": len(image_bytes),
            "item_index": 0,
        }
    ]
    assert inbound.metadata["multimodal_parts"][0]["type"] == "image_url"
    await connector.stop()


@pytest.mark.asyncio
async def test_connector_processes_file_callback_into_attachment_prompt_without_multimodal_parts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aworld_gateway.channels.wecom.connector import WecomConnector

    router = _FakeRouter()
    transport = _FakeTransport()
    monkeypatch.setenv("AWORLD_WECOM_BOT_ID", "bot-1")
    monkeypatch.setenv("AWORLD_WECOM_SECRET", "secret-1")

    file_bytes = b"meeting notes"
    connector = WecomConnector(
        config=WecomChannelConfig(default_agent_id="aworld"),
        router=router,
        connect_func=lambda url: asyncio.sleep(0, result=transport),
    )
    await connector.start()
    transport.feed(
        {
            "cmd": "aibot_msg_callback",
            "headers": {"req_id": "req-file-1"},
            "body": {
                "msgid": "msg-file-1",
                "chatid": "chat-1",
                "chattype": "single",
                "from": {"userid": "user-1"},
                "msgtype": "file",
                "file": {
                    "name": "notes.txt",
                    "base64": base64.b64encode(file_bytes).decode("ascii"),
                },
            },
        }
    )

    await asyncio.sleep(0.05)

    inbound, _channel_default_agent_id = router.calls[0]
    assert inbound.text.startswith("Attachments:")
    assert inbound.metadata["attachments"][0]["type"] == "file"
    assert inbound.metadata["wecom_media"] == [
        {
            "kind": "file",
            "local_path": inbound.metadata["attachments"][0]["path"],
            "file_name": "notes.txt",
            "mime_type": "text/plain",
            "size_bytes": len(file_bytes),
            "item_index": 0,
        }
    ]
    assert inbound.metadata["multimodal_parts"] == []
    await connector.stop()


@pytest.mark.asyncio
async def test_connector_send_text_uploads_local_image_and_sends_followup_markdown(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aworld_gateway.channels.wecom.connector import WecomConnector

    image_path = tmp_path / "chart.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    transport = _FakeTransport()
    monkeypatch.setenv("AWORLD_WECOM_BOT_ID", "bot-1")
    monkeypatch.setenv("AWORLD_WECOM_SECRET", "secret-1")

    connector = WecomConnector(
        config=WecomChannelConfig(),
        router=None,
        connect_func=lambda url: asyncio.sleep(0, result=transport),
    )
    await connector.start()
    connector._last_chat_req_ids["chat-1"] = "req-chat-1"

    result = await connector.send_text(
        chat_id="chat-1",
        text="caption text",
        metadata={
            "outbound_attachments": [
                {"path": f"file://{image_path}", "type": "image"},
            ]
        },
    )

    assert [payload["cmd"] for payload in transport.sent[-5:]] == [
        "aibot_upload_media_init",
        "aibot_upload_media_chunk",
        "aibot_upload_media_finish",
        "aibot_respond_msg",
        "aibot_respond_msg",
    ]
    assert transport.sent[-2]["body"]["msgtype"] == "image"
    assert transport.sent[-1]["body"]["msgtype"] == "markdown"
    assert result["media"]["body"]["media_id"] == "media-1"
    assert result["caption"]["body"] == {}
    await connector.stop()


@pytest.mark.asyncio
async def test_connector_send_text_downgrades_large_image_to_file_and_sends_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aworld_gateway.channels.wecom.connector import WecomConnector

    transport = _FakeTransport()
    monkeypatch.setenv("AWORLD_WECOM_BOT_ID", "bot-1")
    monkeypatch.setenv("AWORLD_WECOM_SECRET", "secret-1")

    connector = WecomConnector(
        config=WecomChannelConfig(),
        router=None,
        connect_func=lambda url: asyncio.sleep(0, result=transport),
    )
    await connector.start()
    connector._last_chat_req_ids["chat-1"] = "req-chat-1"

    large_image = Path("/tmp/large-image.png")
    connector._load_outbound_media = lambda source, file_name=None: asyncio.sleep(  # type: ignore[method-assign]
        0,
        result=(b"x" * (10 * 1024 * 1024 + 1), "image/png", large_image.name),
    )

    result = await connector.send_text(
        chat_id="chat-1",
        text="",
        metadata={
            "outbound_attachments": [
                {"path": f"file://{large_image}", "type": "image"},
            ]
        },
    )

    assert transport.sent[-2]["body"]["msgtype"] == "file"
    assert transport.sent[-1]["body"]["msgtype"] == "markdown"
    assert "已转为文件格式发送" in transport.sent[-1]["body"]["markdown"]["content"]
    assert result["media"]["body"]["type"] == "file"
    await connector.stop()


@pytest.mark.asyncio
async def test_connector_logs_media_downgrade_and_rejection(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from aworld_gateway.channels.wecom.connector import WecomConnector

    transport = _FakeTransport()
    monkeypatch.setenv("AWORLD_WECOM_BOT_ID", "bot-1")
    monkeypatch.setenv("AWORLD_WECOM_SECRET", "secret-1")
    caplog.set_level(logging.INFO, logger="aworld.gateway")

    connector = WecomConnector(
        config=WecomChannelConfig(),
        router=None,
        connect_func=lambda url: asyncio.sleep(0, result=transport),
    )
    await connector.start()
    connector._last_chat_req_ids["chat-1"] = "req-chat-1"

    large_image = Path("/tmp/large-image.png")
    connector._load_outbound_media = lambda source, file_name=None: asyncio.sleep(  # type: ignore[method-assign]
        0,
        result=(b"x" * (10 * 1024 * 1024 + 1), "image/png", large_image.name),
    )
    await connector.send_text(
        chat_id="chat-1",
        text="",
        metadata={"outbound_attachments": [{"path": f"file://{large_image}", "type": "image"}]},
    )

    too_large = Path("/tmp/too-large.bin")
    connector._load_outbound_media = lambda source, file_name=None: asyncio.sleep(  # type: ignore[method-assign]
        0,
        result=(b"x" * (20 * 1024 * 1024 + 1), "application/octet-stream", too_large.name),
    )
    await connector.send_text(
        chat_id="chat-1",
        text="",
        metadata={"outbound_attachments": [{"path": f"file://{too_large}", "type": "file"}]},
    )

    await connector.stop()

    assert "WeCom outbound media downgraded chat_id=chat-1 original_type=image final_type=file" in caplog.text
    assert "WeCom outbound media rejected chat_id=chat-1 attachment_type=file" in caplog.text


@pytest.mark.asyncio
async def test_connector_send_text_downgrades_non_amr_voice_to_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aworld_gateway.channels.wecom.connector import WecomConnector

    transport = _FakeTransport()
    monkeypatch.setenv("AWORLD_WECOM_BOT_ID", "bot-1")
    monkeypatch.setenv("AWORLD_WECOM_SECRET", "secret-1")

    connector = WecomConnector(
        config=WecomChannelConfig(),
        router=None,
        connect_func=lambda url: asyncio.sleep(0, result=transport),
    )
    await connector.start()
    connector._last_chat_req_ids["chat-1"] = "req-chat-1"

    voice_path = Path("/tmp/voice.mp3")
    connector._load_outbound_media = lambda source, file_name=None: asyncio.sleep(  # type: ignore[method-assign]
        0,
        result=(b"voice-bytes", "audio/mpeg", voice_path.name),
    )

    result = await connector.send_text(
        chat_id="chat-1",
        text="",
        metadata={
            "outbound_attachments": [
                {"path": f"file://{voice_path}", "type": "voice"},
            ]
        },
    )

    assert transport.sent[-2]["body"]["msgtype"] == "file"
    assert "AMR" in transport.sent[-1]["body"]["markdown"]["content"]
    assert result["media"]["body"]["type"] == "file"
    await connector.stop()


@pytest.mark.asyncio
async def test_connector_send_text_rejects_media_over_absolute_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aworld_gateway.channels.wecom.connector import WecomConnector

    transport = _FakeTransport()
    monkeypatch.setenv("AWORLD_WECOM_BOT_ID", "bot-1")
    monkeypatch.setenv("AWORLD_WECOM_SECRET", "secret-1")

    connector = WecomConnector(
        config=WecomChannelConfig(),
        router=None,
        connect_func=lambda url: asyncio.sleep(0, result=transport),
    )
    await connector.start()
    connector._last_chat_req_ids["chat-1"] = "req-chat-1"

    file_path = Path("/tmp/too-large.bin")
    connector._load_outbound_media = lambda source, file_name=None: asyncio.sleep(  # type: ignore[method-assign]
        0,
        result=(b"x" * (20 * 1024 * 1024 + 1), "application/octet-stream", file_path.name),
    )

    result = await connector.send_text(
        chat_id="chat-1",
        text="",
        metadata={
            "outbound_attachments": [
                {"path": f"file://{file_path}", "type": "file"},
            ]
        },
    )

    assert all(payload["cmd"] != "aibot_upload_media_init" for payload in transport.sent[-2:])
    assert transport.sent[-1]["body"]["msgtype"] == "markdown"
    assert "20MB" in transport.sent[-1]["body"]["markdown"]["content"]
    assert result["error"]
    await connector.stop()


@pytest.mark.asyncio
async def test_connector_start_schedules_application_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aworld_gateway.channels.wecom.connector as wecom_connector_module
    from aworld_gateway.channels.wecom.connector import WecomConnector

    transport = _FakeTransport()
    monkeypatch.setenv("AWORLD_WECOM_BOT_ID", "bot-1")
    monkeypatch.setenv("AWORLD_WECOM_SECRET", "secret-1")
    monkeypatch.setattr(wecom_connector_module, "HEARTBEAT_INTERVAL_SECONDS", 0.01)

    connector = WecomConnector(
        config=WecomChannelConfig(),
        router=None,
        connect_func=lambda url: asyncio.sleep(0, result=transport),
    )
    await connector.start()

    await asyncio.sleep(0.03)

    assert any(payload["cmd"] == "ping" for payload in transport.sent[1:])
    await connector.stop()


@pytest.mark.asyncio
async def test_connector_reconnects_after_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aworld_gateway.channels.wecom.connector as wecom_connector_module
    from aworld_gateway.channels.wecom.connector import WecomConnector

    router = _FakeRouter()
    first_transport = _FlakyTransport(fail_after_receives=1)
    second_transport = _FakeTransport()
    transports = [first_transport, second_transport]
    connect_calls: list[str] = []

    async def _connect(url: str):
        connect_calls.append(url)
        return transports[len(connect_calls) - 1]

    monkeypatch.setenv("AWORLD_WECOM_BOT_ID", "bot-1")
    monkeypatch.setenv("AWORLD_WECOM_SECRET", "secret-1")
    monkeypatch.setattr(wecom_connector_module, "RECONNECT_BACKOFF_SECONDS", [0.01])

    connector = WecomConnector(
        config=WecomChannelConfig(default_agent_id="aworld"),
        router=router,
        connect_func=_connect,
    )
    await connector.start()

    await asyncio.sleep(0.05)

    assert len(connect_calls) >= 2
    assert second_transport.sent[0]["cmd"] == "aibot_subscribe"

    second_transport.feed(
        {
            "cmd": "aibot_msg_callback",
            "headers": {"req_id": "req-reconnected-1"},
            "body": {
                "msgid": "msg-reconnected-1",
                "chatid": "chat-1",
                "chattype": "single",
                "from": {"userid": "user-1"},
                "msgtype": "text",
                "text": {"content": "after reconnect"},
            },
        }
    )

    await asyncio.sleep(0.05)

    inbound, channel_default_agent_id = router.calls[0]
    assert inbound.text == "after reconnect"
    assert channel_default_agent_id == "aworld"
    assert second_transport.sent[-1]["cmd"] == "aibot_respond_msg"

    await connector.stop()
