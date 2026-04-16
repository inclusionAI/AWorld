from __future__ import annotations

import json
import os
from uuid import uuid4

import httpx

from aworld_gateway.channels.dingding.bridge import AworldDingdingBridge
from aworld_gateway.channels.dingding.types import ExtractedMessage, NEW_SESSION_COMMANDS
from aworld_gateway.config import DingdingChannelConfig


class DingTalkConnector:
    def __init__(
        self,
        *,
        config: DingdingChannelConfig,
        bridge: AworldDingdingBridge,
        stream_module,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._bridge = bridge
        self._stream_module = stream_module
        self._http = http_client or httpx.AsyncClient(timeout=60.0)
        self._session_ids: dict[str, str] = {}
        self._client = None

    async def start(self) -> None:
        credential = self._stream_module.Credential(
            self._required_env(self._config.client_id_env),
            self._required_env(self._config.client_secret_env),
        )
        self._client = self._stream_module.DingTalkStreamClient(credential)
        connector = self

        class _MessageHandler(self._stream_module.ChatbotHandler):
            async def process(self, callback):
                payload = getattr(callback, "data", callback)
                await connector.handle_callback(payload)
                status_ok = getattr(
                    connector._stream_module.AckMessage,
                    "STATUS_OK",
                    "ok",
                )
                return status_ok, "OK"

        self._client.register_callback_handler(
            self._stream_module.ChatbotMessage.TOPIC,
            _MessageHandler(),
        )

    async def stop(self) -> None:
        await self._http.aclose()

    async def handle_callback(self, callback_payload) -> None:
        data = self._parse_data(callback_payload)

        session_webhook = str(data.get("sessionWebhook") or "").strip()
        if not session_webhook:
            return

        sender_id = str(data.get("senderStaffId") or data.get("senderId") or "").strip()
        if not sender_id:
            return

        message = self._extract_message(data)
        user_text = message.text.strip()
        if not user_text and not message.attachments:
            return

        conversation_key = str(data.get("conversationId") or sender_id).strip()
        if user_text.lower() in {command.lower() for command in NEW_SESSION_COMMANDS}:
            self._session_ids[conversation_key] = self._new_session_id(conversation_key)
            await self.send_text(
                session_webhook=session_webhook,
                text="✨ 已开启新会话，之前的上下文已清空。",
            )
            return

        session_id = self._session_ids.get(conversation_key)
        if not session_id:
            session_id = self._new_session_id(conversation_key)
            self._session_ids[conversation_key] = session_id

        result = await self._bridge.run(
            agent_id=self._config.default_agent_id or "aworld",
            session_id=session_id,
            text=message.text,
        )
        await self.send_text(
            session_webhook=session_webhook,
            text=result.text or "（空响应）",
        )

    async def send_text(self, *, session_webhook: str, text: str) -> None:
        response = await self._http.post(
            session_webhook,
            json={"msgtype": "text", "text": {"content": text}},
        )
        response.raise_for_status()

    @staticmethod
    def _parse_data(raw) -> dict:
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @staticmethod
    def _new_session_id(conversation_key: str) -> str:
        return f"dingtalk_{conversation_key}_{uuid4().hex[:8]}"

    @staticmethod
    def _extract_message(data: dict) -> ExtractedMessage:
        text_data = data.get("text")
        if isinstance(text_data, dict):
            text = str(text_data.get("content") or "")
        else:
            text = str(data.get("content") or "")
        return ExtractedMessage(text=text, attachments=[])

    @staticmethod
    def _required_env(name: str | None) -> str:
        key = (name or "").strip()
        value = os.getenv(key, "").strip()
        if not value:
            raise ValueError(f"Missing required env var: {name}")
        return value
