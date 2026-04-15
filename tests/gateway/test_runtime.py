from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.config import GatewayConfig
from aworld_gateway.runtime import GatewayRuntime


def test_runtime_start_reports_running_when_no_channels_enabled():
    runtime = GatewayRuntime(config=GatewayConfig())

    runtime.start()

    assert runtime.status() == {
        "state": "running",
        "channels": {
            "telegram": "disabled",
            "web": "disabled",
            "dingding": "disabled",
            "feishu": "disabled",
            "wecom": "disabled",
        },
    }


def test_runtime_start_degrades_when_enabled_channel_is_not_implemented():
    config = GatewayConfig()
    config.channels.web.enabled = True
    runtime = GatewayRuntime(config=config)

    runtime.start()

    assert runtime.status() == {
        "state": "degraded",
        "channels": {
            "telegram": "disabled",
            "web": "not_implemented",
            "dingding": "disabled",
            "feishu": "disabled",
            "wecom": "disabled",
        },
    }
