from __future__ import annotations

import os
from typing import Any

import httpx

from aworld_gateway.channels.base import ChannelAdapter, ChannelMetadata
from aworld_gateway.config import TelegramChannelConfig
from aworld_gateway.types import InboundEnvelope, OutboundEnvelope


class TelegramChannelAdapter(ChannelAdapter):
    def __init__(
        self,
        config: TelegramChannelConfig | None = None,
        *,
        router: object | None = None,
    ) -> None:
        if config is None:
            config = TelegramChannelConfig()
        super().__init__(config)
        self._config = config
        self._router = router
        self._token: str | None = None

    @classmethod
    def metadata(cls) -> ChannelMetadata:
        return ChannelMetadata(name="telegram", implemented=True)

    async def start(self) -> None:
        token_env = self._config.bot_token_env or ""
        token = os.getenv(token_env)
        if not token:
            raise ValueError(f"Missing Telegram token env: {token_env}")
        self._token = token

    async def stop(self) -> None:
        self._token = None

    async def send(self, envelope: OutboundEnvelope) -> dict[str, Any]:
        if self._token is None:
            raise RuntimeError("Telegram channel adapter is not started.")

        payload: dict[str, Any] = {
            "chat_id": envelope.conversation_id,
            "text": envelope.text,
        }
        if envelope.reply_to_message_id is not None:
            payload["reply_to_message_id"] = envelope.reply_to_message_id

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://api.telegram.org/bot{self._token}/sendMessage",
                json=payload,
            )
            response.raise_for_status()

        return payload

    async def handle_update(self, payload: dict[str, Any]) -> None:
        if self._router is None:
            return

        message = payload.get("message")
        if not isinstance(message, dict):
            return

        text = message.get("text")
        if not isinstance(text, str) or not text:
            return

        chat = message.get("chat")
        sender = message.get("from")
        if not isinstance(chat, dict) or not isinstance(sender, dict):
            return

        conversation_type = "group" if chat.get("type") in {"group", "supergroup"} else "dm"
        outbound = await self._router.handle_inbound(
            InboundEnvelope(
                channel="telegram",
                account_id="telegram",
                conversation_id=str(chat.get("id")),
                conversation_type=conversation_type,
                sender_id=str(sender.get("id")),
                sender_name=sender.get("username") or sender.get("first_name"),
                message_id=str(message.get("message_id")),
                text=text,
                raw_payload=payload,
            ),
            channel_default_agent_id=self._config.default_agent_id,
        )
        await self.send(outbound)
