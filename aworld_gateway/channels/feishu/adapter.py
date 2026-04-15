from __future__ import annotations

from aworld_gateway.channels.base import ChannelAdapter, ChannelMetadata
from aworld_gateway.types import OutboundEnvelope


class FeishuChannelAdapter(ChannelAdapter):
    @classmethod
    def metadata(cls) -> ChannelMetadata:
        return ChannelMetadata(name="feishu", implemented=False)

    async def start(self) -> None:
        raise NotImplementedError("Feishu channel adapter is not implemented yet.")

    async def stop(self) -> None:
        return None

    async def send(self, envelope: OutboundEnvelope):
        raise NotImplementedError("Feishu channel adapter is not implemented yet.")
