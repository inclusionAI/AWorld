from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.config import TelegramChannelConfig
from aworld_gateway.types import OutboundEnvelope


def test_telegram_adapter_requires_token_env_when_started(monkeypatch) -> None:
    monkeypatch.delenv("AWORLD_TELEGRAM_BOT_TOKEN", raising=False)

    from aworld_gateway.channels.telegram.adapter import TelegramChannelAdapter

    adapter = TelegramChannelAdapter(TelegramChannelConfig())

    with pytest.raises(ValueError, match="AWORLD_TELEGRAM_BOT_TOKEN"):
        asyncio.run(adapter.start())


def test_telegram_adapter_posts_send_message(monkeypatch) -> None:
    calls: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            calls["url"] = url
            calls["json"] = json
            return FakeResponse()

    monkeypatch.setenv("AWORLD_TELEGRAM_BOT_TOKEN", "token-123")

    from aworld_gateway.channels.telegram.adapter import TelegramChannelAdapter

    monkeypatch.setattr(
        "aworld_gateway.channels.telegram.adapter.httpx.AsyncClient",
        FakeClient,
    )

    adapter = TelegramChannelAdapter(TelegramChannelConfig())

    asyncio.run(adapter.start())
    asyncio.run(
        adapter.send(
            OutboundEnvelope(
                channel="telegram",
                account_id="telegram-default",
                conversation_id="1001",
                reply_to_message_id="42",
                text="hello back",
            )
        )
    )

    assert calls["url"] == "https://api.telegram.org/bottoken-123/sendMessage"
    assert calls["json"] == {
        "chat_id": "1001",
        "text": "hello back",
        "reply_to_message_id": "42",
    }
