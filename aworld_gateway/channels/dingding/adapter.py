from __future__ import annotations

from aworld_gateway.channels.base import ChannelAdapter, ChannelMetadata


class DingdingChannelAdapter(ChannelAdapter):
    @classmethod
    def metadata(cls) -> ChannelMetadata:
        return ChannelMetadata(name="dingding", implemented=False)

    def start(self) -> None:
        raise NotImplementedError("Dingding channel adapter is not implemented yet.")
