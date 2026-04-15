from __future__ import annotations

from aworld_gateway.channels.base import ChannelAdapter, ChannelMetadata


class WebChannelAdapter(ChannelAdapter):
    @classmethod
    def metadata(cls) -> ChannelMetadata:
        return ChannelMetadata(name="web", implemented=False)

    def start(self) -> None:
        raise NotImplementedError("Web channel adapter is not implemented yet.")
