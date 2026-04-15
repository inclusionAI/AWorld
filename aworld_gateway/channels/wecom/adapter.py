from __future__ import annotations

from aworld_gateway.channels.base import ChannelAdapter, ChannelMetadata


class WecomChannelAdapter(ChannelAdapter):
    @classmethod
    def metadata(cls) -> ChannelMetadata:
        return ChannelMetadata(name="wecom", implemented=False)

    def start(self) -> None:
        raise NotImplementedError("WeCom channel adapter is not implemented yet.")
