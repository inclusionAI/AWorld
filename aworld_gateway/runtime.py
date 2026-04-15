from __future__ import annotations

import copy

from aworld_gateway.channels.base import ChannelAdapter
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
        self._started_channels: dict[str, ChannelAdapter] = {}

    async def start(self) -> None:
        await self._stop_started_adapters()
        self._state = "running"
        self._channel_states = {}

        for channel_name in self._registry.list_channels():
            channel_meta = self._registry.get_meta(channel_name)
            if channel_meta is None:
                continue
            channel_config = getattr(self._config.channels, channel_name, None)
            channel_enabled = bool(channel_config and channel_config.enabled)
            channel_implemented = bool(channel_meta["implemented"])

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

            adapter = self._registry.build_adapter(channel_name, channel_config)
            if adapter is None:
                self._channel_states[channel_name] = {
                    "enabled": True,
                    "implemented": True,
                    "state": "degraded",
                }
                self._state = "degraded"
                continue

            try:
                await adapter.start()
                self._started_channels[channel_name] = adapter
                self._channel_states[channel_name] = {
                    "enabled": True,
                    "implemented": True,
                    "state": "registered",
                }
            except NotImplementedError:
                self._channel_states[channel_name] = {
                    "enabled": True,
                    "implemented": True,
                    "state": "degraded",
                }
                self._state = "degraded"

    async def stop(self) -> None:
        await self._stop_started_adapters()
        reconciled_states: dict[str, dict[str, object]] = {}
        for channel_name in self._registry.list_channels():
            channel_meta = self._registry.get_meta(channel_name)
            if channel_meta is None:
                continue
            channel_config = getattr(self._config.channels, channel_name, None)
            channel_enabled = bool(channel_config and channel_config.enabled)
            reconciled_states[channel_name] = {
                "enabled": channel_enabled,
                "implemented": bool(channel_meta["implemented"]),
                "state": "stopped" if channel_enabled else "disabled",
            }

        self._channel_states = reconciled_states
        self._state = "stopped"

    def status(self) -> dict[str, object]:
        return copy.deepcopy(
            {
                "state": self._state,
                "channels": self._channel_states,
            }
        )

    async def _stop_started_adapters(self) -> None:
        for adapter in self._started_channels.values():
            await adapter.stop()
        self._started_channels = {}
