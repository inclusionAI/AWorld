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
