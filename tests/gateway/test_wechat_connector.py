from __future__ import annotations

import asyncio
import base64
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.config import WechatChannelConfig
from aworld_gateway.types import OutboundEnvelope


class _FakeRouter:
    def __init__(self) -> None:
        self.calls: list[tuple[object, str | None, object | None]] = []

    async def handle_inbound(self, inbound, *, channel_default_agent_id, on_output=None):
        self.calls.append((inbound, channel_default_agent_id, on_output))
        return OutboundEnvelope(
            channel="wechat",
            account_id=inbound.account_id,
            conversation_id=inbound.conversation_id,
            reply_to_message_id=inbound.message_id,
            text=f"echo:{inbound.text}",
        )


@pytest.mark.asyncio
async def test_connector_process_message_caches_context_token_and_routes_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aworld_gateway.channels.wechat.connector import WechatConnector

    router = _FakeRouter()
    cfg = WechatChannelConfig(default_agent_id="aworld")
    monkeypatch.setenv("AWORLD_WECHAT_ACCOUNT_ID", "wx-account")
    monkeypatch.setenv("AWORLD_WECHAT_TOKEN", "wx-token")
    monkeypatch.setenv("AWORLD_WECHAT_BASE_URL", "https://ilink.example.test")

    sent: list[tuple[str, str, dict | None]] = []

    async def fake_send_text(*, chat_id: str, text: str, metadata: dict | None = None):
        sent.append((chat_id, text, metadata))
        return {"chat_id": chat_id, "text": text}

    connector = WechatConnector(
        config=cfg,
        router=router,
        storage_root=tmp_path,
    )
    connector.send_text = fake_send_text  # type: ignore[method-assign]
    await connector.start()
    await connector._process_message(
        {
            "message_id": "m-1",
            "from_user_id": "user-1",
            "context_token": "ctx-1",
            "item_list": [{"type": 1, "text_item": {"text": "ping"}}],
        }
    )

    inbound, channel_default_agent_id, on_output = router.calls[0]
    assert inbound.text == "ping"
    assert inbound.conversation_id == "user-1"
    assert channel_default_agent_id == "aworld"
    assert callable(on_output)
    assert connector._token_store.get("wx-account", "user-1") == "ctx-1"
    assert sent == [("user-1", "echo:ping", {})]
    await connector.stop()


@pytest.mark.asyncio
async def test_connector_send_text_reuses_latest_context_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aworld_gateway.channels.wechat.connector import WechatConnector

    seen: dict[str, object] = {}

    async def fake_send_message(
        *,
        session,
        base_url: str,
        token: str,
        to: str,
        text: str,
        context_token: str | None,
        client_id: str,
    ) -> dict[str, object]:
        seen.update(
            {
                "session": session,
                "base_url": base_url,
                "token": token,
                "to": to,
                "text": text,
                "context_token": context_token,
                "client_id": client_id,
            }
        )
        return {"ret": 0, "client_id": client_id}

    cfg = WechatChannelConfig()
    monkeypatch.setenv("AWORLD_WECHAT_ACCOUNT_ID", "wx-account")
    monkeypatch.setenv("AWORLD_WECHAT_TOKEN", "wx-token")
    monkeypatch.setenv("AWORLD_WECHAT_BASE_URL", "https://ilink.example.test")
    connector = WechatConnector(
        config=cfg,
        router=None,
        storage_root=tmp_path,
        send_message_func=fake_send_message,
    )
    await connector.start()
    connector._token_store.set("wx-account", "user-1", "ctx-9")

    result = await connector.send_text(chat_id="user-1", text="pong")

    assert seen["base_url"] == "https://ilink.example.test"
    assert seen["token"] == "wx-token"
    assert seen["to"] == "user-1"
    assert seen["text"] == "pong"
    assert seen["context_token"] == "ctx-9"
    assert isinstance(seen["client_id"], str)
    assert result["client_id"] == seen["client_id"]
    await connector.stop()


@pytest.mark.asyncio
async def test_connector_send_text_requires_started_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aworld_gateway.channels.wechat.connector import WechatConnector

    cfg = WechatChannelConfig()
    monkeypatch.setenv("AWORLD_WECHAT_ACCOUNT_ID", "wx-account")
    monkeypatch.setenv("AWORLD_WECHAT_TOKEN", "wx-token")

    connector = WechatConnector(config=cfg, router=None, storage_root=tmp_path)

    with pytest.raises(RuntimeError, match="not started"):
        await connector.send_text(chat_id="user-1", text="pong")


@pytest.mark.asyncio
async def test_connector_logs_inbound_and_outbound_message_flow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from aworld_gateway.channels.wechat.connector import WechatConnector

    router = _FakeRouter()

    async def fake_send_message(
        *,
        session,
        base_url: str,
        token: str,
        to: str,
        text: str,
        context_token: str | None,
        client_id: str,
    ) -> dict[str, object]:
        return {"ret": 0, "client_id": client_id}

    cfg = WechatChannelConfig(default_agent_id="aworld")
    monkeypatch.setenv("AWORLD_WECHAT_ACCOUNT_ID", "wx-account")
    monkeypatch.setenv("AWORLD_WECHAT_TOKEN", "wx-token")
    monkeypatch.setenv("AWORLD_GATEWAY_LOG_PATH", str(tmp_path / "logs" / "gateway.log"))
    caplog.set_level(logging.INFO, logger="aworld.gateway")

    connector = WechatConnector(
        config=cfg,
        router=router,
        storage_root=tmp_path,
        send_message_func=fake_send_message,
    )
    await connector.start()
    await connector._process_message(
        {
            "message_id": "m-1",
            "from_user_id": "user-1",
            "context_token": "ctx-1",
            "item_list": [{"type": 1, "text_item": {"text": "ping"}}],
        }
    )
    await connector.stop()

    assert "WeChat connector started account=wx-account" in caplog.text
    assert "WeChat inbound message conversation=user-1 sender=user-1 message_id=m-1" in caplog.text
    assert "WeChat outbound text chunk sent chat_id=user-1" in caplog.text
    assert "WeChat connector stopped account=wx-account" in caplog.text


@pytest.mark.asyncio
async def test_connector_logs_outbound_text_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from aworld_gateway.channels.wechat.connector import WechatConnector

    async def fake_send_message(
        *,
        session,
        base_url: str,
        token: str,
        to: str,
        text: str,
        context_token: str | None,
        client_id: str,
    ) -> dict[str, object]:
        raise RuntimeError("send failed")

    cfg = WechatChannelConfig()
    monkeypatch.setenv("AWORLD_WECHAT_ACCOUNT_ID", "wx-account")
    monkeypatch.setenv("AWORLD_WECHAT_TOKEN", "wx-token")
    monkeypatch.setenv("AWORLD_GATEWAY_LOG_PATH", str(tmp_path / "logs" / "gateway.log"))
    caplog.set_level(logging.INFO, logger="aworld.gateway")

    connector = WechatConnector(
        config=cfg,
        router=None,
        storage_root=tmp_path,
        send_message_func=fake_send_message,
    )
    await connector.start()

    with pytest.raises(RuntimeError, match="send failed"):
        await connector.send_text(chat_id="user-1", text="pong")

    await connector.stop()

    assert "WeChat outbound text chunk failed chat_id=user-1" in caplog.text


@pytest.mark.asyncio
async def test_connector_logs_outbound_media_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from aworld_gateway.channels.wechat.connector import WechatConnector

    attachment = tmp_path / "demo.txt"
    attachment.write_text("hello", encoding="utf-8")

    async def fake_get_upload_url(
        *,
        session,
        base_url: str,
        token: str,
        to_user_id: str,
        media_type: int,
        filekey: str,
        rawsize: int,
        rawfilemd5: str,
        filesize: int,
        aeskey_hex: str,
    ) -> dict[str, object]:
        raise RuntimeError("upload url failed")

    cfg = WechatChannelConfig()
    monkeypatch.setenv("AWORLD_WECHAT_ACCOUNT_ID", "wx-account")
    monkeypatch.setenv("AWORLD_WECHAT_TOKEN", "wx-token")
    monkeypatch.setenv("AWORLD_GATEWAY_LOG_PATH", str(tmp_path / "logs" / "gateway.log"))
    caplog.set_level(logging.INFO, logger="aworld.gateway")

    connector = WechatConnector(
        config=cfg,
        router=None,
        storage_root=tmp_path,
        get_upload_url_func=fake_get_upload_url,
    )
    await connector.start()

    with pytest.raises(RuntimeError, match="upload url failed"):
        await connector.send_text(
            chat_id="user-1",
            text="",
            metadata={"outbound_attachments": [{"path": str(attachment), "type": "file"}]},
        )

    await connector.stop()

    assert "WeChat outbound media failed chat_id=user-1" in caplog.text


@pytest.mark.asyncio
async def test_connector_logs_inbound_media_download_failure_and_continues(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from aworld_gateway.channels.wechat.connector import WechatConnector

    router = _FakeRouter()
    sent: list[tuple[str, str, dict | None]] = []

    async def fake_download_media(
        *,
        session,
        cdn_base_url: str,
        encrypted_query_param: str | None,
        aes_key_b64: str | None,
        full_url: str | None,
        timeout_seconds: float,
    ) -> bytes:
        raise RuntimeError("download failed")

    async def fake_send_text(*, chat_id: str, text: str, metadata: dict | None = None):
        sent.append((chat_id, text, metadata))
        return {"chat_id": chat_id, "text": text}

    cfg = WechatChannelConfig(default_agent_id="aworld")
    monkeypatch.setenv("AWORLD_WECHAT_ACCOUNT_ID", "wx-account")
    monkeypatch.setenv("AWORLD_WECHAT_TOKEN", "wx-token")
    monkeypatch.setenv("AWORLD_GATEWAY_LOG_PATH", str(tmp_path / "logs" / "gateway.log"))
    caplog.set_level(logging.INFO, logger="aworld.gateway")

    connector = WechatConnector(
        config=cfg,
        router=router,
        storage_root=tmp_path,
        download_media_func=fake_download_media,
    )
    connector.send_text = fake_send_text  # type: ignore[method-assign]
    await connector.start()
    await connector._process_message(
        {
            "message_id": "m-1",
            "from_user_id": "user-1",
            "item_list": [
                {"type": 1, "text_item": {"text": "ping"}},
                {
                    "type": 2,
                    "image_item": {
                        "media": {"full_url": "https://cdn.example.test/image.jpg"}
                    },
                },
            ],
        }
    )
    await connector.stop()

    assert "WeChat inbound media download failed message_id=m-1 index=1" in caplog.text
    assert router.calls[0][0].text == "ping"
    assert sent == [("user-1", "echo:ping", {})]


@pytest.mark.asyncio
async def test_connector_skips_group_message_when_group_policy_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aworld_gateway.channels.wechat.connector import WechatConnector

    router = _FakeRouter()
    cfg = WechatChannelConfig(default_agent_id="aworld", group_policy="disabled")
    monkeypatch.setenv("AWORLD_WECHAT_ACCOUNT_ID", "wx-account")
    monkeypatch.setenv("AWORLD_WECHAT_TOKEN", "wx-token")

    connector = WechatConnector(
        config=cfg,
        router=router,
        storage_root=tmp_path,
    )
    await connector.start()
    await connector._process_message(
        {
            "message_id": "m-group",
            "from_user_id": "user-1",
            "room_id": "group-1",
            "item_list": [{"type": 1, "text_item": {"text": "ping"}}],
        }
    )

    assert router.calls == []
    await connector.stop()


@pytest.mark.asyncio
async def test_connector_split_multiline_messages_sends_multiple_chunks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aworld_gateway.channels.wechat.connector import WechatConnector

    calls: list[tuple[str, str | None]] = []

    async def fake_send_message(
        *,
        session,
        base_url: str,
        token: str,
        to: str,
        text: str,
        context_token: str | None,
        client_id: str,
    ) -> dict[str, object]:
        calls.append((text, context_token))
        return {"ret": 0, "client_id": client_id}

    cfg = WechatChannelConfig(split_multiline_messages=True)
    monkeypatch.setenv("AWORLD_WECHAT_ACCOUNT_ID", "wx-account")
    monkeypatch.setenv("AWORLD_WECHAT_TOKEN", "wx-token")

    connector = WechatConnector(
        config=cfg,
        router=None,
        storage_root=tmp_path,
        send_message_func=fake_send_message,
    )
    await connector.start()
    connector._token_store.set("wx-account", "user-1", "ctx-1")

    await connector.send_text(chat_id="user-1", text="line1\nline2\n\nline3")

    assert calls == [("line1", "ctx-1"), ("line2", "ctx-1"), ("line3", "ctx-1")]
    await connector.stop()


@pytest.mark.asyncio
async def test_connector_start_launches_poll_loop_and_processes_updates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aworld_gateway.channels.wechat.connector import WechatConnector

    monkeypatch.setenv("AWORLD_WECHAT_ACCOUNT_ID", "wx-account")
    monkeypatch.setenv("AWORLD_WECHAT_TOKEN", "wx-token")

    updates_called = 0
    processed: list[str] = []

    async def fake_get_updates(
        *,
        session,
        base_url: str,
        token: str,
        sync_buf: str,
        timeout_ms: int,
    ) -> dict[str, object]:
        nonlocal updates_called
        updates_called += 1
        if updates_called == 1:
            return {
                "ret": 0,
                "get_updates_buf": "buf-1",
                "msgs": [
                    {
                        "message_id": "m-1",
                        "from_user_id": "user-1",
                        "item_list": [{"type": 1, "text_item": {"text": "poll"}}],
                    }
                ],
            }
        await asyncio.sleep(0.01)
        return {"ret": 0, "get_updates_buf": "buf-1", "msgs": []}

    connector = WechatConnector(
        config=WechatChannelConfig(),
        router=None,
        storage_root=tmp_path,
        get_updates_func=fake_get_updates,
    )

    async def fake_process_message(message: dict[str, object]) -> None:
        processed.append(str(message["message_id"]))
        connector.started = False

    connector._process_message = fake_process_message  # type: ignore[method-assign]
    await connector.start()
    await asyncio.sleep(0.05)
    await connector.stop()

    assert updates_called >= 1
    assert processed == ["m-1"]


@pytest.mark.asyncio
async def test_connector_poll_loop_continues_after_process_message_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aworld_gateway.channels.wechat.connector import WechatConnector

    monkeypatch.setenv("AWORLD_WECHAT_ACCOUNT_ID", "wx-account")
    monkeypatch.setenv("AWORLD_WECHAT_TOKEN", "wx-token")

    responses = [
        {
            "ret": 0,
            "get_updates_buf": "buf-1",
            "msgs": [
                {"message_id": "m-1", "from_user_id": "user-1", "item_list": []},
                {"message_id": "m-2", "from_user_id": "user-2", "item_list": []},
            ],
        },
        {"ret": 0, "get_updates_buf": "buf-2", "msgs": []},
    ]
    processed: list[str] = []

    async def fake_get_updates(
        *,
        session,
        base_url: str,
        token: str,
        sync_buf: str,
        timeout_ms: int,
    ) -> dict[str, object]:
        del session, base_url, token, sync_buf, timeout_ms
        response = responses.pop(0)
        if not responses:
            connector.started = False
        return response

    connector = WechatConnector(
        config=WechatChannelConfig(),
        router=None,
        storage_root=tmp_path,
        get_updates_func=fake_get_updates,
    )
    connector.started = True
    connector._poll_session = object()
    connector._account_id = "wx-account"
    connector._token = "wx-token"

    async def fake_process_message(message: dict[str, object]) -> None:
        processed.append(str(message["message_id"]))
        if message["message_id"] == "m-1":
            raise RuntimeError("boom")

    connector._process_message = fake_process_message  # type: ignore[method-assign]

    result = await asyncio.gather(connector._poll_loop(), return_exceptions=True)

    assert result == [None]
    assert processed == ["m-1", "m-2"]


@pytest.mark.asyncio
async def test_connector_process_message_deduplicates_repeated_message_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aworld_gateway.channels.wechat.connector import WechatConnector

    router = _FakeRouter()
    monkeypatch.setenv("AWORLD_WECHAT_ACCOUNT_ID", "wx-account")
    monkeypatch.setenv("AWORLD_WECHAT_TOKEN", "wx-token")

    connector = WechatConnector(
        config=WechatChannelConfig(default_agent_id="aworld"),
        router=router,
        storage_root=tmp_path,
    )
    connector.send_text = lambda **kwargs: asyncio.sleep(0, result=kwargs)  # type: ignore[method-assign]
    await connector.start()

    message = {
        "message_id": "m-dup",
        "from_user_id": "user-1",
        "item_list": [{"type": 1, "text_item": {"text": "ping"}}],
    }

    await connector._process_message(message)
    await connector._process_message(message)

    assert len(router.calls) == 1
    await connector.stop()


@pytest.mark.asyncio
async def test_connector_process_message_bounds_seen_message_ids(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aworld_gateway.channels.wechat.connector import DEDUP_MAX_SIZE, WechatConnector

    monkeypatch.setenv("AWORLD_WECHAT_ACCOUNT_ID", "wx-account")
    monkeypatch.setenv("AWORLD_WECHAT_TOKEN", "wx-token")

    connector = WechatConnector(
        config=WechatChannelConfig(),
        router=None,
        storage_root=tmp_path,
    )
    connector._account_id = "wx-account"

    for index in range(DEDUP_MAX_SIZE + 25):
        await connector._process_message(
            {
                "message_id": f"m-{index}",
                "from_user_id": "user-1",
                "item_list": [{"type": 1, "text_item": {"text": "ping"}}],
            }
        )

    assert len(connector._seen_message_ids) == DEDUP_MAX_SIZE
    assert "m-0" not in connector._seen_message_ids
    assert f"m-{DEDUP_MAX_SIZE + 24}" in connector._seen_message_ids


@pytest.mark.asyncio
async def test_connector_start_restores_persisted_token_and_base_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aworld_gateway.channels.wechat.account_store import save_account
    from aworld_gateway.channels.wechat.connector import WechatConnector

    save_account(
        tmp_path,
        account_id="wx-account",
        token="persisted-token",
        base_url="https://persisted.example.test",
        user_id="user-1",
    )
    monkeypatch.setenv("AWORLD_WECHAT_ACCOUNT_ID", "wx-account")
    monkeypatch.delenv("AWORLD_WECHAT_TOKEN", raising=False)
    monkeypatch.delenv("AWORLD_WECHAT_BASE_URL", raising=False)

    connector = WechatConnector(
        config=WechatChannelConfig(),
        router=None,
        storage_root=tmp_path,
    )
    await connector.start()

    assert connector._account_id == "wx-account"
    assert connector._token == "persisted-token"
    assert connector._base_url == "https://persisted.example.test"

    await connector.stop()


@pytest.mark.asyncio
async def test_default_send_message_posts_ilink_payload_with_context_token() -> None:
    from aworld_gateway.channels.wechat.connector import _default_send_message

    calls: dict[str, object] = {}

    class _FakeResponse:
        ok = True
        status = 200

        async def text(self) -> str:
            return '{"ret":0,"msg":"ok"}'

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _FakeSession:
        def post(self, url: str, *, data: str, headers: dict[str, str], timeout):
            calls["url"] = url
            calls["data"] = data
            calls["headers"] = headers
            calls["timeout"] = timeout
            return _FakeResponse()

    result = await _default_send_message(
        session=_FakeSession(),
        base_url="https://ilink.example.test",
        token="wx-token",
        to="user-1",
        text="pong",
        context_token="ctx-1",
        client_id="client-1",
    )

    assert calls["url"] == "https://ilink.example.test/ilink/bot/sendmessage"
    assert '"context_token":"ctx-1"' in str(calls["data"])
    assert '"to_user_id":"user-1"' in str(calls["data"])
    assert calls["headers"]["Authorization"] == "Bearer wx-token"
    assert result["ret"] == 0


@pytest.mark.asyncio
async def test_default_get_updates_returns_empty_payload_on_timeout() -> None:
    from aworld_gateway.channels.wechat.connector import _default_get_updates

    class _TimeoutSession:
        def post(self, *args, **kwargs):
            raise asyncio.TimeoutError

    result = await _default_get_updates(
        session=_TimeoutSession(),
        base_url="https://ilink.example.test",
        token="wx-token",
        sync_buf="buf-1",
        timeout_ms=1234,
    )

    assert result == {"ret": 0, "msgs": [], "get_updates_buf": "buf-1"}


@pytest.mark.asyncio
async def test_connector_process_message_downloads_image_attachment_and_routes_prompt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aworld_gateway.channels.wechat.connector import WechatConnector

    router = _FakeRouter()
    cfg = WechatChannelConfig(default_agent_id="aworld")
    monkeypatch.setenv("AWORLD_WECHAT_ACCOUNT_ID", "wx-account")
    monkeypatch.setenv("AWORLD_WECHAT_TOKEN", "wx-token")

    async def fake_download_media(
        *,
        session,
        cdn_base_url: str,
        encrypted_query_param: str | None,
        aes_key_b64: str | None,
        full_url: str | None,
        timeout_seconds: float,
    ) -> bytes:
        del session, cdn_base_url, encrypted_query_param, aes_key_b64, full_url, timeout_seconds
        return b"image-bytes"

    sent: list[tuple[str, str, dict | None]] = []

    async def fake_send_text(*, chat_id: str, text: str, metadata: dict | None = None):
        sent.append((chat_id, text, metadata))
        return {"chat_id": chat_id, "text": text}

    connector = WechatConnector(
        config=cfg,
        router=router,
        storage_root=tmp_path,
        download_media_func=fake_download_media,
    )
    connector.send_text = fake_send_text  # type: ignore[method-assign]
    await connector.start()
    await connector._process_message(
        {
            "message_id": "m-image",
            "from_user_id": "user-1",
            "item_list": [
                {
                    "type": 2,
                    "image_item": {
                        "media": {
                            "encrypt_query_param": "eqp-1",
                            "aes_key": base64.b64encode(b"0123456789abcdef").decode("ascii"),
                        }
                    },
                }
            ],
        }
    )

    inbound, _channel_default_agent_id, _on_output = router.calls[0]
    assert inbound.text.startswith("Attachments:")
    assert len(inbound.metadata["attachments"]) == 1
    attachment = inbound.metadata["attachments"][0]
    assert attachment["type"] == "image"
    attachment_path = Path(attachment["path"])
    assert attachment_path.exists() is True
    assert attachment_path.read_bytes() == b"image-bytes"
    assert inbound.metadata["wechat_media"] == [
        {
            "kind": "image",
            "local_path": str(attachment_path),
            "file_name": "image.jpg",
            "mime_type": "image/jpeg",
            "size_bytes": len(b"image-bytes"),
            "item_index": 0,
        }
    ]
    assert len(inbound.metadata["multimodal_parts"]) == 1
    assert inbound.metadata["multimodal_parts"][0]["type"] == "image_url"
    assert str(inbound.metadata["multimodal_parts"][0]["image_url"]["url"]).startswith(
        "data:image/jpeg;base64,"
    )
    assert sent == [("user-1", inbound.text.replace("Attachments:", "echo:Attachments:", 1), {})]
    await connector.stop()


@pytest.mark.asyncio
async def test_connector_process_message_builds_structured_file_metadata_without_multimodal_parts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aworld_gateway.channels.wechat.connector import WechatConnector

    router = _FakeRouter()
    monkeypatch.setenv("AWORLD_WECHAT_ACCOUNT_ID", "wx-account")
    monkeypatch.setenv("AWORLD_WECHAT_TOKEN", "wx-token")

    async def fake_download_media(
        *,
        session,
        cdn_base_url: str,
        encrypted_query_param: str | None,
        aes_key_b64: str | None,
        full_url: str | None,
        timeout_seconds: float,
    ) -> bytes:
        del session, cdn_base_url, encrypted_query_param, aes_key_b64, full_url, timeout_seconds
        return b"file-bytes"

    connector = WechatConnector(
        config=WechatChannelConfig(default_agent_id="aworld"),
        router=router,
        storage_root=tmp_path,
        download_media_func=fake_download_media,
    )
    connector.send_text = lambda **kwargs: asyncio.sleep(0, result=kwargs)  # type: ignore[method-assign]
    await connector.start()
    await connector._process_message(
        {
            "message_id": "m-file",
            "from_user_id": "user-1",
            "item_list": [
                {
                    "type": 4,
                    "file_item": {
                        "file_name": "report.txt",
                        "media": {
                            "encrypt_query_param": "eqp-1",
                            "aes_key": base64.b64encode(b"0123456789abcdef").decode("ascii"),
                        },
                    },
                }
            ],
        }
    )

    inbound, _channel_default_agent_id, _on_output = router.calls[0]
    assert inbound.metadata["attachments"][0]["type"] == "file"
    assert inbound.metadata["wechat_media"] == [
        {
            "kind": "file",
            "local_path": inbound.metadata["attachments"][0]["path"],
            "file_name": "report.txt",
            "mime_type": "text/plain",
            "size_bytes": len(b"file-bytes"),
            "item_index": 0,
        }
    ]
    assert inbound.metadata.get("multimodal_parts") == []
    await connector.stop()


def test_assert_wechat_cdn_url_rejects_untrusted_host() -> None:
    from aworld_gateway.channels.wechat.media import assert_wechat_cdn_url

    with pytest.raises(ValueError, match="allowlist"):
        assert_wechat_cdn_url("https://evil.example.test/file.bin")


@pytest.mark.asyncio
async def test_connector_send_text_uploads_markdown_local_image_reference(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aworld_gateway.channels.wechat.connector import WechatConnector

    image_path = tmp_path / "chart.png"
    image_path.write_bytes(b"png-bytes")

    text_messages: list[str] = []
    media_messages: list[dict[str, object]] = []
    upload_calls: list[dict[str, object]] = []
    get_upload_calls: list[dict[str, object]] = []

    async def fake_send_message(
        *,
        session,
        base_url: str,
        token: str,
        to: str,
        text: str,
        context_token: str | None,
        client_id: str,
    ) -> dict[str, object]:
        del session, base_url, token, to, context_token
        text_messages.append(text)
        return {"ret": 0, "client_id": client_id}

    async def fake_get_upload_url(
        *,
        session,
        base_url: str,
        token: str,
        to_user_id: str,
        media_type: int,
        filekey: str,
        rawsize: int,
        rawfilemd5: str,
        filesize: int,
        aeskey_hex: str,
    ) -> dict[str, object]:
        del session, base_url, token, to_user_id, rawfilemd5, aeskey_hex
        get_upload_calls.append(
            {
                "media_type": media_type,
                "filekey": filekey,
                "rawsize": rawsize,
                "filesize": filesize,
            }
        )
        return {"upload_param": "upload-token"}

    async def fake_upload_ciphertext(
        *,
        session,
        ciphertext: bytes,
        upload_url: str,
    ) -> str:
        del session
        upload_calls.append({"ciphertext": ciphertext, "upload_url": upload_url})
        return "encrypted-param-1"

    async def fake_send_media_message(
        *,
        session,
        base_url: str,
        token: str,
        to: str,
        item: dict[str, object],
        context_token: str | None,
        client_id: str,
    ) -> dict[str, object]:
        del session, base_url, token, to, context_token
        media_messages.append({"item": item, "client_id": client_id})
        return {"ret": 0, "client_id": client_id}

    monkeypatch.setenv("AWORLD_WECHAT_ACCOUNT_ID", "wx-account")
    monkeypatch.setenv("AWORLD_WECHAT_TOKEN", "wx-token")
    monkeypatch.setenv("AWORLD_WECHAT_CDN_BASE_URL", "https://cdn.example.test/c2c")

    connector = WechatConnector(
        config=WechatChannelConfig(),
        router=None,
        storage_root=tmp_path,
        send_message_func=fake_send_message,
        get_upload_url_func=fake_get_upload_url,
        upload_ciphertext_func=fake_upload_ciphertext,
        send_media_message_func=fake_send_media_message,
    )
    await connector.start()
    connector._token_store.set("wx-account", "user-1", "ctx-1")

    result = await connector.send_text(
        chat_id="user-1",
        text=f"share ![chart](file://{image_path})",
    )

    assert text_messages == ["share"]
    assert get_upload_calls[0]["media_type"] == 1
    assert get_upload_calls[0]["rawsize"] == len(b"png-bytes")
    assert upload_calls[0]["upload_url"] == "https://cdn.example.test/c2c/upload?encrypted_query_param=upload-token&filekey=" + str(get_upload_calls[0]["filekey"])
    assert media_messages[0]["item"]["type"] == 2
    assert media_messages[0]["item"]["image_item"]["media"]["encrypt_query_param"] == "encrypted-param-1"
    assert result["client_id"] == media_messages[0]["client_id"]
    await connector.stop()


@pytest.mark.asyncio
async def test_connector_send_text_honors_force_file_attachment_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aworld_gateway.channels.wechat.connector import WechatConnector

    image_path = tmp_path / "chart.png"
    image_path.write_bytes(b"png-bytes")

    text_messages: list[str] = []
    media_messages: list[dict[str, object]] = []

    async def fake_send_message(
        *,
        session,
        base_url: str,
        token: str,
        to: str,
        text: str,
        context_token: str | None,
        client_id: str,
    ) -> dict[str, object]:
        del session, base_url, token, to, context_token
        text_messages.append(text)
        return {"ret": 0, "client_id": client_id}

    async def fake_get_upload_url(
        *,
        session,
        base_url: str,
        token: str,
        to_user_id: str,
        media_type: int,
        filekey: str,
        rawsize: int,
        rawfilemd5: str,
        filesize: int,
        aeskey_hex: str,
    ) -> dict[str, object]:
        del session, base_url, token, to_user_id, media_type, filekey, rawsize, rawfilemd5, filesize, aeskey_hex
        return {"upload_param": "upload-token"}

    async def fake_upload_ciphertext(
        *,
        session,
        ciphertext: bytes,
        upload_url: str,
    ) -> str:
        del session, ciphertext, upload_url
        return "encrypted-param-1"

    async def fake_send_media_message(
        *,
        session,
        base_url: str,
        token: str,
        to: str,
        item: dict[str, object],
        context_token: str | None,
        client_id: str,
    ) -> dict[str, object]:
        del session, base_url, token, to, context_token
        media_messages.append({"item": item, "client_id": client_id})
        return {"ret": 0, "client_id": client_id}

    monkeypatch.setenv("AWORLD_WECHAT_ACCOUNT_ID", "wx-account")
    monkeypatch.setenv("AWORLD_WECHAT_TOKEN", "wx-token")

    connector = WechatConnector(
        config=WechatChannelConfig(),
        router=None,
        storage_root=tmp_path,
        send_message_func=fake_send_message,
        get_upload_url_func=fake_get_upload_url,
        upload_ciphertext_func=fake_upload_ciphertext,
        send_media_message_func=fake_send_media_message,
    )
    await connector.start()

    result = await connector.send_text(
        chat_id="user-1",
        text="",
        metadata={
            "outbound_attachments": [
                {"path": f"file://{image_path}", "type": "file"},
            ]
        },
    )

    assert text_messages == []
    assert media_messages[0]["item"]["type"] == 4
    assert media_messages[0]["item"]["file_item"]["file_name"] == "chart.png"
    assert result["client_id"] == media_messages[0]["client_id"]
    await connector.stop()


@pytest.mark.asyncio
async def test_connector_send_text_honors_explicit_video_attachment_type(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aworld_gateway.channels.wechat.connector import WechatConnector

    video_like_path = tmp_path / "movie.bin"
    video_like_path.write_bytes(b"video-bytes")

    media_messages: list[dict[str, object]] = []

    async def fake_get_upload_url(
        *,
        session,
        base_url: str,
        token: str,
        to_user_id: str,
        media_type: int,
        filekey: str,
        rawsize: int,
        rawfilemd5: str,
        filesize: int,
        aeskey_hex: str,
    ) -> dict[str, object]:
        del session, base_url, token, to_user_id, filekey, rawsize, rawfilemd5, filesize, aeskey_hex
        assert media_type == 2
        return {"upload_param": "upload-token"}

    async def fake_upload_ciphertext(
        *,
        session,
        ciphertext: bytes,
        upload_url: str,
    ) -> str:
        del session, ciphertext, upload_url
        return "encrypted-param-1"

    async def fake_send_media_message(
        *,
        session,
        base_url: str,
        token: str,
        to: str,
        item: dict[str, object],
        context_token: str | None,
        client_id: str,
    ) -> dict[str, object]:
        del session, base_url, token, to, context_token
        media_messages.append({"item": item, "client_id": client_id})
        return {"ret": 0, "client_id": client_id}

    monkeypatch.setenv("AWORLD_WECHAT_ACCOUNT_ID", "wx-account")
    monkeypatch.setenv("AWORLD_WECHAT_TOKEN", "wx-token")

    connector = WechatConnector(
        config=WechatChannelConfig(),
        router=None,
        storage_root=tmp_path,
        get_upload_url_func=fake_get_upload_url,
        upload_ciphertext_func=fake_upload_ciphertext,
        send_media_message_func=fake_send_media_message,
    )
    await connector.start()

    result = await connector.send_text(
        chat_id="user-1",
        text="",
        metadata={
            "outbound_attachments": [
                {"path": f"file://{video_like_path}", "type": "video"},
            ]
        },
    )

    assert media_messages[0]["item"]["type"] == 5
    assert media_messages[0]["item"]["video_item"]["media"]["encrypt_query_param"] == "encrypted-param-1"
    assert result["client_id"] == media_messages[0]["client_id"]
    await connector.stop()


@pytest.mark.asyncio
async def test_connector_send_text_honors_explicit_voice_attachment_type(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aworld_gateway.channels.wechat.connector import WechatConnector

    voice_like_path = tmp_path / "audio.bin"
    voice_like_path.write_bytes(b"voice-bytes")

    media_messages: list[dict[str, object]] = []

    async def fake_get_upload_url(
        *,
        session,
        base_url: str,
        token: str,
        to_user_id: str,
        media_type: int,
        filekey: str,
        rawsize: int,
        rawfilemd5: str,
        filesize: int,
        aeskey_hex: str,
    ) -> dict[str, object]:
        del session, base_url, token, to_user_id, filekey, rawsize, rawfilemd5, filesize, aeskey_hex
        assert media_type == 4
        return {"upload_param": "upload-token"}

    async def fake_upload_ciphertext(
        *,
        session,
        ciphertext: bytes,
        upload_url: str,
    ) -> str:
        del session, ciphertext, upload_url
        return "encrypted-param-1"

    async def fake_send_media_message(
        *,
        session,
        base_url: str,
        token: str,
        to: str,
        item: dict[str, object],
        context_token: str | None,
        client_id: str,
    ) -> dict[str, object]:
        del session, base_url, token, to, context_token
        media_messages.append({"item": item, "client_id": client_id})
        return {"ret": 0, "client_id": client_id}

    monkeypatch.setenv("AWORLD_WECHAT_ACCOUNT_ID", "wx-account")
    monkeypatch.setenv("AWORLD_WECHAT_TOKEN", "wx-token")

    connector = WechatConnector(
        config=WechatChannelConfig(),
        router=None,
        storage_root=tmp_path,
        get_upload_url_func=fake_get_upload_url,
        upload_ciphertext_func=fake_upload_ciphertext,
        send_media_message_func=fake_send_media_message,
    )
    await connector.start()

    result = await connector.send_text(
        chat_id="user-1",
        text="",
        metadata={
            "outbound_attachments": [
                {"path": f"file://{voice_like_path}", "type": "voice"},
            ]
        },
    )

    assert media_messages[0]["item"]["type"] == 3
    assert media_messages[0]["item"]["voice_item"]["media"]["encrypt_query_param"] == "encrypted-param-1"
    assert result["client_id"] == media_messages[0]["client_id"]
    await connector.stop()
