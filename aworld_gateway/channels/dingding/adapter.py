from __future__ import annotations

from aworld_gateway.channels.base import ChannelAdapter, ChannelMetadata
from aworld_gateway.channels.dingding.bridge import AworldDingdingBridge
from aworld_gateway.channels.dingding.connector import DingTalkConnector
from aworld_gateway.config import DingdingChannelConfig
from aworld_gateway.types import OutboundEnvelope


class DingdingChannelAdapter(ChannelAdapter):
    def __init__(
        self,
        config: DingdingChannelConfig | None = None,
        *,
        bridge: AworldDingdingBridge | None = None,
        connector_cls: type[DingTalkConnector] = DingTalkConnector,
    ) -> None:
        if config is None:
            config = DingdingChannelConfig()
        super().__init__(config)
        self._config = config
        self._bridge = bridge or AworldDingdingBridge()
        self._connector_cls = connector_cls
        self._connector: DingTalkConnector | None = None

    @classmethod
    def metadata(cls) -> ChannelMetadata:
        return ChannelMetadata(name="dingding", implemented=True)

    async def start(self) -> None:
        stream_module = self._import_stream_module()
        self._connector = self._connector_cls(
            config=self._config,
            bridge=self._bridge,
            stream_module=stream_module,
        )
        await self._connector.start()

    async def stop(self) -> None:
        if self._connector is not None:
            await self._connector.stop()

    async def send(self, envelope: OutboundEnvelope):
        if self._connector is None:
            raise RuntimeError("DingTalk channel adapter is not started.")
        session_webhook = str(envelope.metadata.get("session_webhook") or "").strip()
        if not session_webhook:
            raise ValueError("Missing session_webhook metadata for DingTalk send.")
        await self._connector.send_text(
            session_webhook=session_webhook,
            text=envelope.text,
        )
        return {"session_webhook": session_webhook, "text": envelope.text}

    def _import_stream_module(self):
        try:
            import dingtalk_stream
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Missing optional dependency 'dingtalk_stream' for DingTalk channel."
            ) from exc

        return dingtalk_stream
