from __future__ import annotations

from aworld_gateway.channels.base import ChannelMetadata


class ChannelRegistry:
    _BUILTIN_CHANNELS: tuple[ChannelMetadata, ...] = (
        ChannelMetadata(name="telegram", implemented=True),
        ChannelMetadata(name="web", implemented=False),
        ChannelMetadata(name="dingding", implemented=False),
        ChannelMetadata(name="feishu", implemented=False),
        ChannelMetadata(name="wecom", implemented=False),
    )

    def list_channels(self) -> list[dict[str, object]]:
        return [
            {"name": channel.name, "implemented": channel.implemented}
            for channel in self._BUILTIN_CHANNELS
        ]

    def get_channel(self, name: str) -> ChannelMetadata | None:
        for channel in self._BUILTIN_CHANNELS:
            if channel.name == name:
                return channel
        return None
