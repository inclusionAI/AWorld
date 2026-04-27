from __future__ import annotations

from aworld_gateway.channels.base import ChannelAdapter, ChannelMetadata
from aworld_gateway.channels.wechat.connector import WechatConnector
from aworld_gateway.config import WechatChannelConfig
from aworld_gateway.types import OutboundEnvelope


class WechatChannelAdapter(ChannelAdapter):
    def __init__(
        self,
        config: WechatChannelConfig | None = None,
        *,
        router: object | None = None,
        connector_cls: type[WechatConnector] = WechatConnector,
    ) -> None:
        if config is None:
            config = WechatChannelConfig()
        super().__init__(config)
        self._config = config
        self._router = router
        self._connector_cls = connector_cls
        self._connector: WechatConnector | None = None

    @classmethod
    def metadata(cls) -> ChannelMetadata:
        return ChannelMetadata(name="wechat", implemented=True)

    async def start(self) -> None:
        self._connector = self._connector_cls(
            config=self._config,
            router=self._router,
        )
        await self._connector.start()

    async def stop(self) -> None:
        if self._connector is not None:
            await self._connector.stop()

    async def send(self, envelope: OutboundEnvelope):
        if self._connector is None:
            raise RuntimeError("WeChat channel adapter is not started.")
        metadata = dict(envelope.metadata)
        outbound_attachments = self._event_attachments(envelope.events)
        if outbound_attachments:
            existing = metadata.get("outbound_attachments")
            merged = list(existing) if isinstance(existing, list) else []
            merged.extend(outbound_attachments)
            metadata["outbound_attachments"] = merged
        return await self._connector.send_text(
            chat_id=envelope.conversation_id,
            text=envelope.text,
            metadata=metadata,
        )

    @staticmethod
    def _event_attachments(events: list[dict]) -> list[dict[str, object]]:
        attachments: list[dict[str, object]] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("type") or "").strip().lower()
            if event_type not in {"image", "file", "video", "voice"}:
                continue
            raw_path = str(
                event.get("path")
                or event.get("file_path")
                or event.get("local_path")
                or ""
            ).strip()
            if not raw_path:
                continue
            attachments.append(
                {
                    "path": raw_path,
                    "type": event_type,
                    "force_file_attachment": bool(event.get("force_file_attachment"))
                    or event_type == "file",
                }
            )
        return attachments
