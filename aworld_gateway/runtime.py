from __future__ import annotations

import copy

from aworld_gateway.channels.base import ChannelAdapter
from aworld_gateway.config import DingdingChannelConfig
from aworld_gateway.config import GatewayConfig
from aworld_gateway.config import WechatChannelConfig
from aworld_gateway.config import WecomChannelConfig
from aworld_gateway.logging import get_gateway_logger
from aworld_gateway.registry import ChannelRegistry

logger = get_gateway_logger("runtime")


class GatewayRuntime:
    def __init__(
        self,
        *,
        config: GatewayConfig,
        registry: ChannelRegistry | None = None,
        router: object | None = None,
        artifact_service: object | None = None,
    ) -> None:
        self._config = config
        self._registry = registry or ChannelRegistry()
        self._router = router
        self._artifact_service = artifact_service
        self._channel_states = self._build_base_channel_states()
        self._state = self._derive_runtime_state(self._channel_states)
        self._started_channels: dict[str, ChannelAdapter] = {}

    async def start(self) -> None:
        await self._stop_started_adapters()
        self._channel_states = self._build_base_channel_states()
        logger.info("Gateway runtime start requested")

        for channel_name, channel_state in self._channel_states.items():
            if not channel_state["enabled"]:
                continue
            if not channel_state["configured"]:
                logger.info(
                    "Gateway channel skipped "
                    f"channel={channel_name} reason=not_configured"
                )
                continue
            if not channel_state["implemented"]:
                channel_state["state"] = "degraded"
                logger.warning(
                    "Gateway channel degraded "
                    f"channel={channel_name} reason=not_implemented"
                )
                continue

            channel_config = getattr(self._config.channels, channel_name, None)
            if (
                channel_name == "dingding"
                and isinstance(channel_config, DingdingChannelConfig)
                and not channel_config.default_agent_id
            ):
                channel_config.default_agent_id = self._config.default_agent_id
            if (
                channel_name == "wechat"
                and isinstance(channel_config, WechatChannelConfig)
                and not channel_config.default_agent_id
            ):
                channel_config.default_agent_id = self._config.default_agent_id
            if (
                channel_name == "wecom"
                and isinstance(channel_config, WecomChannelConfig)
                and not channel_config.default_agent_id
            ):
                channel_config.default_agent_id = self._config.default_agent_id
            adapter = self._registry.build_adapter(
                channel_name,
                channel_config,
                router=self._router,
                artifact_service=self._artifact_service,
            )
            if adapter is None:
                channel_state["state"] = "degraded"
                channel_state["error"] = "Channel adapter is not available."
                logger.warning(
                    "Gateway channel degraded "
                    f"channel={channel_name} reason=adapter_unavailable"
                )
                continue

            try:
                logger.info(f"Gateway channel starting channel={channel_name}")
                await adapter.start()
                self._started_channels[channel_name] = adapter
                channel_state["running"] = True
                channel_state["state"] = "running"
                channel_state["error"] = None
                logger.info(f"Gateway channel started channel={channel_name}")
            except Exception as exc:
                channel_state["running"] = False
                channel_state["state"] = "degraded"
                channel_state["error"] = str(exc)
                logger.exception(
                    f"Gateway channel failed channel={channel_name} error={exc}"
                )

        self._state = self._derive_runtime_state(self._channel_states)
        logger.info(f"Gateway runtime state={self._state}")

    async def stop(self) -> None:
        logger.info("Gateway runtime stop requested")
        await self._stop_started_adapters()
        self._channel_states = self._build_base_channel_states()
        self._state = self._derive_runtime_state(self._channel_states)
        logger.info(f"Gateway runtime stopped state={self._state}")

    def status(self) -> dict[str, object]:
        return copy.deepcopy(
            {
                "state": self._state,
                "channels": self._channel_states,
            }
        )

    def get_started_channel(self, channel_name: str) -> ChannelAdapter | None:
        return self._started_channels.get(channel_name)

    async def _stop_started_adapters(self) -> None:
        for channel_name, adapter in self._started_channels.items():
            logger.info(f"Gateway channel stopping channel={channel_name}")
            await adapter.stop()
            logger.info(f"Gateway channel stopped channel={channel_name}")
        self._started_channels = {}

    def _build_base_channel_states(self) -> dict[str, dict[str, object]]:
        channel_states: dict[str, dict[str, object]] = {}

        for channel_name in self._registry.list_channels():
            channel_meta = self._registry.get_meta(channel_name)
            if channel_meta is None:
                continue

            channel_config = getattr(self._config.channels, channel_name, None)
            channel_enabled = bool(channel_config and channel_config.enabled)
            channel_implemented = bool(channel_meta["implemented"])
            channel_configured = self._registry.is_configured(channel_name, channel_config)
            channel_error = None
            if channel_enabled and not channel_configured:
                channel_error = "Channel is enabled but not configured enough to start."
            elif channel_enabled and channel_configured and not channel_implemented:
                channel_error = "Channel is registered but not implemented."

            channel_states[channel_name] = self._make_channel_state(
                enabled=channel_enabled,
                configured=channel_configured,
                implemented=channel_implemented,
                running=False,
                degraded=channel_enabled
                and (not channel_configured or not channel_implemented),
                error=channel_error,
            )

        return channel_states

    @staticmethod
    def _make_channel_state(
        *,
        enabled: bool,
        configured: bool,
        implemented: bool,
        running: bool,
        degraded: bool,
        error: str | None,
    ) -> dict[str, object]:
        if degraded:
            state = "degraded"
        elif running:
            state = "running"
        elif enabled and configured and implemented:
            state = "configured"
        else:
            state = "registered"

        return {
            "enabled": enabled,
            "configured": configured,
            "implemented": implemented,
            "running": running,
            "state": state,
            "error": error,
        }

    @staticmethod
    def _derive_runtime_state(channel_states: dict[str, dict[str, object]]) -> str:
        if any(
            channel_state["enabled"]
            and (not channel_state["configured"] or channel_state["state"] == "degraded")
            for channel_state in channel_states.values()
        ):
            return "degraded"
        if any(channel_state["running"] for channel_state in channel_states.values()):
            return "running"
        if any(
            channel_state["enabled"]
            and channel_state["configured"]
            and channel_state["implemented"]
            for channel_state in channel_states.values()
        ):
            return "configured"
        return "registered"
