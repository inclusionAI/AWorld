from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.config import GatewayConfig
from aworld_gateway.registry import ChannelRegistry
from aworld_gateway.runtime import GatewayRuntime


def test_runtime_start_reports_running_when_no_channels_enabled():
    runtime = GatewayRuntime(
        config=GatewayConfig(),
        registry=ChannelRegistry(),
        router=None,
    )

    asyncio.run(runtime.start())

    status = runtime.status()
    assert status["state"] == "running"
    assert status["channels"]["telegram"]["enabled"] is False
    assert status["channels"]["telegram"]["implemented"] is True
    assert status["channels"]["telegram"]["state"] == "disabled"
    assert status["channels"]["web"]["enabled"] is False
    assert status["channels"]["web"]["implemented"] is False
    assert status["channels"]["web"]["state"] == "disabled"

    asyncio.run(runtime.stop())
    assert runtime.status()["state"] == "stopped"


def test_runtime_start_degrades_when_enabled_channel_is_not_implemented():
    config = GatewayConfig()
    config.channels.web.enabled = True
    runtime = GatewayRuntime(
        config=config,
        registry=ChannelRegistry(),
        router=None,
    )

    asyncio.run(runtime.start())

    status = runtime.status()
    assert status["state"] == "degraded"
    assert status["channels"]["web"]["enabled"] is True
    assert status["channels"]["web"]["implemented"] is False
    assert status["channels"]["web"]["state"] == "degraded"


def test_runtime_degrades_for_enabled_metadata_only_channel_without_adapter():
    config = GatewayConfig()
    config.channels.telegram.enabled = True
    runtime = GatewayRuntime(
        config=config,
        registry=ChannelRegistry(),
        router=None,
    )

    asyncio.run(runtime.start())

    status = runtime.status()
    assert status["state"] == "degraded"
    assert status["channels"]["telegram"]["enabled"] is True
    assert status["channels"]["telegram"]["implemented"] is True
    assert status["channels"]["telegram"]["state"] == "degraded"


def test_runtime_status_returns_deep_copy():
    runtime = GatewayRuntime(
        config=GatewayConfig(),
        registry=ChannelRegistry(),
        router=None,
    )

    asyncio.run(runtime.start())
    first = runtime.status()
    first["channels"]["web"]["state"] = "tampered"

    second = runtime.status()
    assert second["channels"]["web"]["state"] == "disabled"


def test_runtime_uses_registry_adapter_builder_and_stop_reconciles_state():
    class FakeAdapter:
        def __init__(self, config) -> None:
            self.config = config
            self.started = False
            self.stopped = False

        @classmethod
        def metadata(cls):
            return {"name": "web", "implemented": True}

        async def start(self) -> None:
            self.started = True

        async def stop(self) -> None:
            self.stopped = True

    class FakeRegistry(ChannelRegistry):
        def list_channels(self):
            return {"web": {"label": "Web", "implemented": True}}

        def get_meta(self, channel_id: str):
            if channel_id == "web":
                return {"label": "Web", "implemented": True}
            return None

        def get_adapter_class(self, channel_id: str):
            if channel_id == "web":
                return FakeAdapter
            return None

    config = GatewayConfig()
    config.channels.web.enabled = True
    runtime = GatewayRuntime(config=config, registry=FakeRegistry(), router=None)

    asyncio.run(runtime.start())
    started_status = runtime.status()
    assert started_status["state"] == "running"
    assert started_status["channels"]["web"]["state"] == "registered"

    asyncio.run(runtime.stop())
    stopped_status = runtime.status()
    assert stopped_status["state"] == "stopped"
    assert stopped_status["channels"]["web"]["state"] == "stopped"
