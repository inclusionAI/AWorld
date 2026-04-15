from __future__ import annotations

from aworld_gateway.config import GatewayConfig
from aworld_gateway.registry import ChannelRegistry


class GatewayRuntime:
    def __init__(
        self,
        *,
        config: GatewayConfig,
        registry: ChannelRegistry | None = None,
        router: object | None = None,
    ) -> None:
        self._config = config
        self._registry = registry or ChannelRegistry()
        self._router = router
        self._state = "created"
        self._channel_states: dict[str, dict[str, object]] = {}

    async def start(self) -> None:
        self._state = "running"
        self._channel_states = {}

        for channel_name, channel_info in self._registry.list_channels().items():
            channel_enabled = bool(getattr(self._config.channels, channel_name).enabled)
            channel_implemented = bool(channel_info["implemented"])

            if not channel_enabled:
                self._channel_states[channel_name] = {
                    "enabled": False,
                    "implemented": channel_implemented,
                    "state": "disabled",
                }
                continue

            if not channel_implemented:
                self._channel_states[channel_name] = {
                    "enabled": True,
                    "implemented": False,
                    "state": "degraded",
                }
                self._state = "degraded"
                continue

            self._channel_states[channel_name] = {
                "enabled": True,
                "implemented": True,
                "state": "registered",
            }

    async def stop(self) -> None:
        self._state = "stopped"

    def status(self) -> dict[str, object]:
        return {
            "state": self._state,
            "channels": dict(self._channel_states),
        }
