from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.config import WechatChannelConfig
from aworld_gateway.types import OutboundEnvelope


class _FakeConnector:
    def __init__(self, *, config, router) -> None:
        self.config = config
        self.router = router
        self.started = False
        self.stopped = False
        self.send_calls: list[tuple[str, str, dict | None]] = []

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def send_text(
        self,
        *,
        chat_id: str,
        text: str,
        metadata: dict | None = None,
    ) -> dict[str, object]:
        self.send_calls.append((chat_id, text, metadata))
        return {"chat_id": chat_id, "text": text, "metadata": metadata or {}}


def test_wechat_adapter_metadata_reports_implemented_true() -> None:
    from aworld_gateway.channels.wechat.adapter import WechatChannelAdapter

    assert WechatChannelAdapter.metadata().implemented is True


def test_wechat_adapter_start_builds_connector_and_starts() -> None:
    from aworld_gateway.channels.wechat.adapter import WechatChannelAdapter

    router = object()
    config = WechatChannelConfig(default_agent_id="aworld")
    adapter = WechatChannelAdapter(
        config=config,
        router=router,
        connector_cls=_FakeConnector,
    )

    asyncio.run(adapter.start())

    assert adapter._connector is not None
    assert adapter._connector.config is config
    assert adapter._connector.router is router
    assert adapter._connector.started is True


def test_wechat_adapter_send_requires_started_connector() -> None:
    from aworld_gateway.channels.wechat.adapter import WechatChannelAdapter

    adapter = WechatChannelAdapter(WechatChannelConfig(), connector_cls=_FakeConnector)

    with pytest.raises(RuntimeError, match="not started"):
        asyncio.run(
            adapter.send(
                OutboundEnvelope(
                    channel="wechat",
                    account_id="wechat",
                    conversation_id="peer-1",
                    text="hello",
                )
            )
        )


def test_wechat_adapter_send_delegates_to_connector() -> None:
    from aworld_gateway.channels.wechat.adapter import WechatChannelAdapter

    adapter = WechatChannelAdapter(
        WechatChannelConfig(),
        connector_cls=_FakeConnector,
    )
    asyncio.run(adapter.start())

    result = asyncio.run(
        adapter.send(
            OutboundEnvelope(
                channel="wechat",
                account_id="wechat",
                conversation_id="peer-1",
                text="reply text",
                metadata={"source": "test"},
            )
        )
    )

    assert adapter._connector is not None
    assert adapter._connector.send_calls == [("peer-1", "reply text", {"source": "test"})]
    assert result == {"chat_id": "peer-1", "text": "reply text", "metadata": {"source": "test"}}


def test_wechat_adapter_send_translates_media_events_into_outbound_attachments() -> None:
    from aworld_gateway.channels.wechat.adapter import WechatChannelAdapter

    adapter = WechatChannelAdapter(
        WechatChannelConfig(),
        connector_cls=_FakeConnector,
    )
    asyncio.run(adapter.start())

    result = asyncio.run(
        adapter.send(
            OutboundEnvelope(
                channel="wechat",
                account_id="wechat",
                conversation_id="peer-1",
                text="reply text",
                metadata={"source": "test"},
                events=[
                    {"type": "image", "path": "/tmp/chart.png"},
                    {"type": "file", "file_path": "/tmp/report.png"},
                ],
            )
        )
    )

    assert adapter._connector is not None
    assert adapter._connector.send_calls == [
        (
            "peer-1",
            "reply text",
            {
                "source": "test",
                "outbound_attachments": [
                    {"path": "/tmp/chart.png", "type": "image", "force_file_attachment": False},
                    {"path": "/tmp/report.png", "type": "file", "force_file_attachment": True},
                ],
            },
        )
    ]
    assert result["metadata"]["outbound_attachments"][1]["force_file_attachment"] is True


def test_wechat_adapter_send_preserves_explicit_video_and_voice_event_types() -> None:
    from aworld_gateway.channels.wechat.adapter import WechatChannelAdapter

    adapter = WechatChannelAdapter(
        WechatChannelConfig(),
        connector_cls=_FakeConnector,
    )
    asyncio.run(adapter.start())

    asyncio.run(
        adapter.send(
            OutboundEnvelope(
                channel="wechat",
                account_id="wechat",
                conversation_id="peer-1",
                text="reply text",
                events=[
                    {"type": "video", "path": "/tmp/movie.bin"},
                    {"type": "voice", "path": "/tmp/audio.bin"},
                ],
            )
        )
    )

    assert adapter._connector is not None
    assert adapter._connector.send_calls == [
        (
            "peer-1",
            "reply text",
            {
                "outbound_attachments": [
                    {"path": "/tmp/movie.bin", "type": "video", "force_file_attachment": False},
                    {"path": "/tmp/audio.bin", "type": "voice", "force_file_attachment": False},
                ]
            },
        )
    ]


def test_wechat_adapter_stop_delegates_to_connector() -> None:
    from aworld_gateway.channels.wechat.adapter import WechatChannelAdapter

    adapter = WechatChannelAdapter(
        WechatChannelConfig(),
        connector_cls=_FakeConnector,
    )
    asyncio.run(adapter.start())
    asyncio.run(adapter.stop())

    assert adapter._connector is not None
    assert adapter._connector.stopped is True


def test_account_store_round_trips_credentials(tmp_path: Path) -> None:
    from aworld_gateway.channels.wechat.account_store import load_account, save_account

    save_account(
        tmp_path,
        account_id="wx-account",
        token="wx-token",
        base_url="https://ilink.example.test",
        user_id="user-1",
    )

    payload = load_account(tmp_path, "wx-account")

    assert payload is not None
    assert payload["token"] == "wx-token"
    assert payload["base_url"] == "https://ilink.example.test"
    assert payload["user_id"] == "user-1"

    raw = json.loads((tmp_path / "wx-account.json").read_text(encoding="utf-8"))
    assert raw["token"] == "wx-token"


def test_context_token_store_persists_latest_peer_token(tmp_path: Path) -> None:
    from aworld_gateway.channels.wechat.context_token_store import ContextTokenStore

    store = ContextTokenStore(tmp_path)
    store.set("wx-account", "peer-1", "ctx-1")
    store.set("wx-account", "peer-2", "ctx-2")

    restored = ContextTokenStore(tmp_path)
    restored.restore("wx-account")

    assert restored.get("wx-account", "peer-1") == "ctx-1"
    assert restored.get("wx-account", "peer-2") == "ctx-2"
