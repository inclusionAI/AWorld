from __future__ import annotations

from aworld_gateway.channels.base import ChannelAdapter, ChannelMetadata


class FeishuChannelAdapter(ChannelAdapter):
    @classmethod
    def metadata(cls) -> ChannelMetadata:
        return ChannelMetadata(name="feishu", implemented=False)

    def start(self) -> None:
        raise NotImplementedError("Feishu channel adapter is not implemented yet.")
