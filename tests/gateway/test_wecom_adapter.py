from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.config import WecomChannelConfig
from aworld_gateway.types import OutboundEnvelope


class _FakeConnector:
    def __init__(self, *, config, router) -> None:
        self.config = config
        self.router = router
        self.started = False
        self.stopped = False
        self.send_calls: list[tuple[str, str, str | None, dict | None]] = []

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def send_text(
        self,
        *,
        chat_id: str,
        text: str,
        reply_to_message_id: str | None = None,
        metadata: dict | None = None,
    ) -> dict[str, object]:
        self.send_calls.append((chat_id, text, reply_to_message_id, metadata))
        return {
            "chat_id": chat_id,
            "text": text,
            "reply_to_message_id": reply_to_message_id,
            "metadata": metadata or {},
        }


def test_wecom_adapter_metadata_reports_implemented_true() -> None:
    from aworld_gateway.channels.wecom.adapter import WecomChannelAdapter

    assert WecomChannelAdapter.metadata().implemented is True


def test_wecom_adapter_start_builds_connector_and_starts() -> None:
    from aworld_gateway.channels.wecom.adapter import WecomChannelAdapter

    router = object()
    config = WecomChannelConfig(default_agent_id="aworld")
    adapter = WecomChannelAdapter(
        config=config,
        router=router,
        connector_cls=_FakeConnector,
    )

    asyncio.run(adapter.start())

    assert adapter._connector is not None
    assert adapter._connector.config is config
    assert adapter._connector.router is router
    assert adapter._connector.started is True


def test_wecom_adapter_send_requires_started_connector() -> None:
    from aworld_gateway.channels.wecom.adapter import WecomChannelAdapter

    adapter = WecomChannelAdapter(WecomChannelConfig(), connector_cls=_FakeConnector)

    with pytest.raises(RuntimeError, match="not started"):
        asyncio.run(
            adapter.send(
                OutboundEnvelope(
                    channel="wecom",
                    account_id="wecom",
                    conversation_id="chat-1",
                    text="hello",
                )
            )
        )


def test_wecom_adapter_send_delegates_to_connector() -> None:
    from aworld_gateway.channels.wecom.adapter import WecomChannelAdapter

    adapter = WecomChannelAdapter(
        WecomChannelConfig(),
        connector_cls=_FakeConnector,
    )
    asyncio.run(adapter.start())

    result = asyncio.run(
        adapter.send(
            OutboundEnvelope(
                channel="wecom",
                account_id="wecom",
                conversation_id="chat-1",
                reply_to_message_id="msg-1",
                text="reply text",
                metadata={"source": "test"},
            )
        )
    )

    assert adapter._connector is not None
    assert adapter._connector.send_calls == [
        ("chat-1", "reply text", "msg-1", {"source": "test"})
    ]
    assert result == {
        "chat_id": "chat-1",
        "text": "reply text",
        "reply_to_message_id": "msg-1",
        "metadata": {"source": "test"},
    }


def test_wecom_adapter_send_translates_media_events_into_outbound_attachments() -> None:
    from aworld_gateway.channels.wecom.adapter import WecomChannelAdapter

    adapter = WecomChannelAdapter(
        WecomChannelConfig(),
        connector_cls=_FakeConnector,
    )
    asyncio.run(adapter.start())

    result = asyncio.run(
        adapter.send(
            OutboundEnvelope(
                channel="wecom",
                account_id="wecom",
                conversation_id="chat-1",
                text="reply text",
                metadata={"source": "test"},
                events=[
                    {"type": "image", "path": "/tmp/chart.png"},
                    {"type": "file", "file_path": "/tmp/report.txt"},
                ],
            )
        )
    )

    assert adapter._connector is not None
    assert adapter._connector.send_calls == [
        (
            "chat-1",
            "reply text",
            None,
            {
                "source": "test",
                "outbound_attachments": [
                    {"path": "/tmp/chart.png", "type": "image", "force_file_attachment": False},
                    {"path": "/tmp/report.txt", "type": "file", "force_file_attachment": True},
                ],
            },
        )
    ]
    assert result["metadata"]["outbound_attachments"][1]["force_file_attachment"] is True


def test_wecom_adapter_stop_delegates_to_connector() -> None:
    from aworld_gateway.channels.wecom.adapter import WecomChannelAdapter

    adapter = WecomChannelAdapter(
        WecomChannelConfig(),
        connector_cls=_FakeConnector,
    )
    asyncio.run(adapter.start())
    asyncio.run(adapter.stop())

    assert adapter._connector is not None
    assert adapter._connector.stopped is True
