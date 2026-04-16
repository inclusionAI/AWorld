from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.channels.dingding.adapter import DingdingChannelAdapter
from aworld_gateway.config import DingdingChannelConfig
from aworld_gateway.types import OutboundEnvelope


class _FakeBridge:
    async def run(self, **kwargs):
        return type("BridgeResult", (), {"text": "bridge"})()


class _FakeConnector:
    def __init__(self, *, config, bridge, stream_module) -> None:
        self.config = config
        self.bridge = bridge
        self.stream_module = stream_module
        self.started = False
        self.stopped = False
        self.send_calls: list[tuple[str, str]] = []

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def send_text(self, *, session_webhook: str, text: str) -> None:
        self.send_calls.append((session_webhook, text))


def test_dingding_adapter_metadata_reports_implemented_true() -> None:
    assert DingdingChannelAdapter.metadata().implemented is True


def test_dingding_adapter_start_builds_connector_and_starts(monkeypatch) -> None:
    stream_module = object()
    config = DingdingChannelConfig(default_agent_id="aworld")
    bridge = _FakeBridge()

    monkeypatch.setattr(
        DingdingChannelAdapter,
        "_import_stream_module",
        lambda self: stream_module,
    )

    adapter = DingdingChannelAdapter(
        config=config,
        bridge=bridge,
        connector_cls=_FakeConnector,
    )

    asyncio.run(adapter.start())

    assert adapter._connector is not None
    assert adapter._connector.config is config
    assert adapter._connector.bridge is bridge
    assert adapter._connector.stream_module is stream_module
    assert adapter._connector.started is True


def test_dingding_adapter_send_requires_started_connector() -> None:
    adapter = DingdingChannelAdapter(
        DingdingChannelConfig(),
        bridge=_FakeBridge(),
        connector_cls=_FakeConnector,
    )

    with pytest.raises(RuntimeError, match="not started"):
        asyncio.run(
            adapter.send(
                OutboundEnvelope(
                    channel="dingding",
                    account_id="dingding",
                    conversation_id="conv-1",
                    text="hello",
                    metadata={"session_webhook": "https://callback"},
                )
            )
        )


def test_dingding_adapter_send_delegates_to_connector(monkeypatch) -> None:
    stream_module = object()
    monkeypatch.setattr(
        DingdingChannelAdapter,
        "_import_stream_module",
        lambda self: stream_module,
    )

    adapter = DingdingChannelAdapter(
        DingdingChannelConfig(),
        bridge=_FakeBridge(),
        connector_cls=_FakeConnector,
    )
    asyncio.run(adapter.start())

    asyncio.run(
        adapter.send(
            OutboundEnvelope(
                channel="dingding",
                account_id="dingding",
                conversation_id="conv-1",
                text="reply text",
                metadata={"session_webhook": "https://callback"},
            )
        )
    )

    assert adapter._connector.send_calls == [("https://callback", "reply text")]
