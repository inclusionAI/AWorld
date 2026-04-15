from __future__ import annotations

from aworld_gateway.config import GatewayConfig
from aworld_gateway.registry import ChannelRegistry


class GatewayRuntime:
    def __init__(
        self,
        *,
        config: GatewayConfig,
        registry: ChannelRegistry | None = None,
    ) -> None:
        self._config = config
        self._registry = registry or ChannelRegistry()
        self._state = "created"
        self._channel_states: dict[str, str] = {}

    def start(self) -> None:
        self._state = "running"
        self._channel_states = {}

        for channel_info in self._registry.list_channels():
            channel_name = str(channel_info["name"])
            channel_enabled = bool(getattr(self._config.channels, channel_name).enabled)
            channel_implemented = bool(channel_info["implemented"])

            if not channel_enabled:
                self._channel_states[channel_name] = "disabled"
                continue

            if not channel_implemented:
                self._channel_states[channel_name] = "not_implemented"
                self._state = "degraded"
                continue

            self._channel_states[channel_name] = "enabled"

    def status(self) -> dict[str, object]:
        return {
            "state": self._state,
            "channels": dict(self._channel_states),
        }
