from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.config import GatewayConfig
from aworld_gateway.channels.dingding.adapter import DingdingChannelAdapter
from aworld_gateway.registry import ChannelRegistry
from aworld_gateway.runtime import GatewayRuntime


def test_runtime_status_is_initialized_before_start(monkeypatch):
    monkeypatch.delenv("AWORLD_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("AWORLD_DINGTALK_CLIENT_ID", raising=False)
    monkeypatch.delenv("AWORLD_DINGTALK_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("AWORLD_WECHAT_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("AWORLD_WECHAT_TOKEN", raising=False)
    monkeypatch.delenv("AWORLD_WECOM_BOT_ID", raising=False)
    monkeypatch.delenv("AWORLD_WECOM_SECRET", raising=False)

    runtime = GatewayRuntime(
        config=GatewayConfig(),
        registry=ChannelRegistry(),
        router=None,
    )

    status = runtime.status()
    assert status["state"] == "registered"
    assert status["channels"]["telegram"]["state"] == "registered"
    assert status["channels"]["dingding"]["state"] == "registered"
    assert status["channels"]["wechat"]["state"] == "registered"
    assert status["channels"]["wecom"]["state"] == "registered"
    assert status["channels"]["web"]["state"] == "registered"


def test_runtime_status_derives_pre_start_state_from_non_default_config():
    config = GatewayConfig()
    config.channels.web.enabled = True

    runtime = GatewayRuntime(
        config=config,
        registry=ChannelRegistry(),
        router=None,
    )

    status = runtime.status()
    assert status["state"] == "degraded"
    assert status["channels"]["web"]["state"] == "degraded"


def test_runtime_start_reports_registered_channels_when_no_channels_enabled(
    monkeypatch,
):
    monkeypatch.delenv("AWORLD_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("AWORLD_DINGTALK_CLIENT_ID", raising=False)
    monkeypatch.delenv("AWORLD_DINGTALK_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("AWORLD_WECHAT_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("AWORLD_WECHAT_TOKEN", raising=False)
    monkeypatch.delenv("AWORLD_WECOM_BOT_ID", raising=False)
    monkeypatch.delenv("AWORLD_WECOM_SECRET", raising=False)

    runtime = GatewayRuntime(
        config=GatewayConfig(),
        registry=ChannelRegistry(),
        router=None,
    )

    asyncio.run(runtime.start())

    status = runtime.status()
    assert status["state"] == "registered"
    assert status["channels"]["telegram"]["enabled"] is False
    assert status["channels"]["telegram"]["configured"] is False
    assert status["channels"]["telegram"]["implemented"] is True
    assert status["channels"]["telegram"]["running"] is False
    assert status["channels"]["telegram"]["state"] == "registered"
    assert status["channels"]["web"]["enabled"] is False
    assert status["channels"]["web"]["configured"] is True
    assert status["channels"]["web"]["implemented"] is False
    assert status["channels"]["web"]["running"] is False
    assert status["channels"]["web"]["state"] == "registered"
    assert status["channels"]["dingding"]["enabled"] is False
    assert status["channels"]["dingding"]["configured"] is False
    assert status["channels"]["dingding"]["implemented"] is True
    assert status["channels"]["dingding"]["running"] is False
    assert status["channels"]["dingding"]["state"] == "registered"
    assert status["channels"]["wechat"]["enabled"] is False
    assert status["channels"]["wechat"]["configured"] is False
    assert status["channels"]["wechat"]["implemented"] is True
    assert status["channels"]["wechat"]["running"] is False
    assert status["channels"]["wechat"]["state"] == "registered"
    assert status["channels"]["wecom"]["enabled"] is False
    assert status["channels"]["wecom"]["configured"] is False
    assert status["channels"]["wecom"]["implemented"] is True
    assert status["channels"]["wecom"]["running"] is False
    assert status["channels"]["wecom"]["state"] == "registered"

    asyncio.run(runtime.stop())
    stopped_status = runtime.status()
    assert stopped_status["state"] == "registered"
    assert stopped_status["channels"]["telegram"]["state"] == "registered"


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
    assert status["channels"]["web"]["configured"] is True
    assert status["channels"]["web"]["implemented"] is False
    assert status["channels"]["web"]["running"] is False
    assert status["channels"]["web"]["state"] == "degraded"


def test_runtime_start_degrades_when_enabled_dingding_is_not_configured(
    monkeypatch,
):
    monkeypatch.delenv("AWORLD_DINGTALK_CLIENT_ID", raising=False)
    monkeypatch.delenv("AWORLD_DINGTALK_CLIENT_SECRET", raising=False)

    config = GatewayConfig()
    config.channels.dingding.enabled = True
    runtime = GatewayRuntime(
        config=config,
        registry=ChannelRegistry(),
        router=None,
    )

    asyncio.run(runtime.start())

    status = runtime.status()
    assert status["state"] == "degraded"
    assert status["channels"]["dingding"]["enabled"] is True
    assert status["channels"]["dingding"]["configured"] is False
    assert status["channels"]["dingding"]["implemented"] is True
    assert status["channels"]["dingding"]["running"] is False
    assert status["channels"]["dingding"]["state"] == "degraded"
    assert (
        status["channels"]["dingding"]["error"]
        == "Channel is enabled but not configured enough to start."
    )


def test_runtime_start_degrades_when_enabled_wechat_is_not_configured(
    monkeypatch,
):
    monkeypatch.delenv("AWORLD_WECHAT_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("AWORLD_WECHAT_TOKEN", raising=False)

    config = GatewayConfig()
    config.channels.wechat.enabled = True
    runtime = GatewayRuntime(
        config=config,
        registry=ChannelRegistry(),
        router=None,
    )

    asyncio.run(runtime.start())

    status = runtime.status()
    assert status["state"] == "degraded"
    assert status["channels"]["wechat"]["enabled"] is True
    assert status["channels"]["wechat"]["configured"] is False
    assert status["channels"]["wechat"]["implemented"] is True
    assert status["channels"]["wechat"]["running"] is False
    assert status["channels"]["wechat"]["state"] == "degraded"
    assert (
        status["channels"]["wechat"]["error"]
        == "Channel is enabled but not configured enough to start."
    )


def test_runtime_start_degrades_when_enabled_wecom_is_not_configured(
    monkeypatch,
):
    monkeypatch.delenv("AWORLD_WECOM_BOT_ID", raising=False)
    monkeypatch.delenv("AWORLD_WECOM_SECRET", raising=False)

    config = GatewayConfig()
    config.channels.wecom.enabled = True
    runtime = GatewayRuntime(
        config=config,
        registry=ChannelRegistry(),
        router=None,
    )

    asyncio.run(runtime.start())

    status = runtime.status()
    assert status["state"] == "degraded"
    assert status["channels"]["wecom"]["enabled"] is True
    assert status["channels"]["wecom"]["configured"] is False
    assert status["channels"]["wecom"]["implemented"] is True
    assert status["channels"]["wecom"]["running"] is False
    assert status["channels"]["wecom"]["state"] == "degraded"
    assert (
        status["channels"]["wecom"]["error"]
        == "Channel is enabled but not configured enough to start."
    )


def test_runtime_start_degrades_when_enabled_and_configured_dingding_missing_dependency(
    monkeypatch,
):
    monkeypatch.setenv("AWORLD_DINGTALK_CLIENT_ID", "ding-client")
    monkeypatch.setenv("AWORLD_DINGTALK_CLIENT_SECRET", "ding-secret")
    monkeypatch.setattr(
        DingdingChannelAdapter,
        "_import_stream_module",
        lambda self: (_ for _ in ()).throw(
            RuntimeError("Missing optional dependency 'dingtalk_stream' for DingTalk channel.")
        ),
    )

    config = GatewayConfig()
    config.channels.dingding.enabled = True
    runtime = GatewayRuntime(
        config=config,
        registry=ChannelRegistry(),
        router=None,
    )

    asyncio.run(runtime.start())

    status = runtime.status()
    assert status["state"] == "degraded"
    assert status["channels"]["dingding"]["enabled"] is True
    assert status["channels"]["dingding"]["configured"] is True
    assert status["channels"]["dingding"]["implemented"] is True
    assert status["channels"]["dingding"]["running"] is False
    assert status["channels"]["dingding"]["state"] == "degraded"
    assert (
        status["channels"]["dingding"]["error"]
        == "Missing optional dependency 'dingtalk_stream' for DingTalk channel."
    )


def test_runtime_starts_enabled_and_configured_telegram_channel(
    monkeypatch,
):
    monkeypatch.setenv("AWORLD_TELEGRAM_BOT_TOKEN", "telegram-token")

    config = GatewayConfig()
    config.channels.telegram.enabled = True
    runtime = GatewayRuntime(
        config=config,
        registry=ChannelRegistry(),
        router=None,
    )

    asyncio.run(runtime.start())

    status = runtime.status()
    assert status["state"] == "running"
    assert status["channels"]["telegram"]["enabled"] is True
    assert status["channels"]["telegram"]["configured"] is True
    assert status["channels"]["telegram"]["implemented"] is True
    assert status["channels"]["telegram"]["running"] is True
    assert status["channels"]["telegram"]["state"] == "running"
    assert status["channels"]["telegram"]["error"] is None
    assert runtime.get_started_channel("telegram") is not None

    asyncio.run(runtime.stop())
    stopped_status = runtime.status()
    assert stopped_status["state"] == "configured"
    assert stopped_status["channels"]["telegram"]["running"] is False
    assert stopped_status["channels"]["telegram"]["state"] == "configured"


def test_runtime_wires_router_into_started_telegram_adapter(monkeypatch):
    monkeypatch.setenv("AWORLD_TELEGRAM_BOT_TOKEN", "telegram-token")

    class FakeRouter:
        async def handle_inbound(self, inbound, *, channel_default_agent_id):
            return None

    config = GatewayConfig()
    config.channels.telegram.enabled = True
    router = FakeRouter()
    runtime = GatewayRuntime(
        config=config,
        registry=ChannelRegistry(),
        router=router,
    )

    asyncio.run(runtime.start())

    adapter = runtime.get_started_channel("telegram")
    assert adapter is not None
    assert getattr(adapter, "_router") is router


def test_runtime_status_returns_deep_copy():
    runtime = GatewayRuntime(
        config=GatewayConfig(),
        registry=ChannelRegistry(),
        router=None,
    )

    asyncio.run(runtime.start())
    first = runtime.status()
    first["channels"]["web"]["state"] = "tampered"
    first["channels"]["web"]["running"] = True

    second = runtime.status()
    assert second["channels"]["web"]["state"] == "registered"
    assert second["channels"]["web"]["running"] is False


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
        def __init__(self) -> None:
            super().__init__()
            self.built_configs = []

        def list_channels(self):
            return {"web": {"label": "Web", "implemented": True}}

        def get_meta(self, channel_id: str):
            if channel_id == "web":
                return {"label": "Web", "implemented": True}
            return None

        def is_configured(self, channel_id: str, config):
            return channel_id == "web"

        def build_adapter(
            self,
            channel_id: str,
            config,
            *,
            router=None,
            artifact_service=None,
        ):
            if channel_id == "web":
                self.built_configs.append(config)
                return FakeAdapter(config)
            return None

    config = GatewayConfig()
    config.channels.web.enabled = True
    registry = FakeRegistry()
    runtime = GatewayRuntime(config=config, registry=registry, router=None)

    asyncio.run(runtime.start())
    started_status = runtime.status()
    assert started_status["state"] == "running"
    assert started_status["channels"]["web"]["enabled"] is True
    assert started_status["channels"]["web"]["configured"] is True
    assert started_status["channels"]["web"]["implemented"] is True
    assert started_status["channels"]["web"]["running"] is True
    assert started_status["channels"]["web"]["state"] == "running"
    assert registry.built_configs == [config.channels.web]

    asyncio.run(runtime.stop())
    stopped_status = runtime.status()
    assert stopped_status["state"] == "configured"
    assert stopped_status["channels"]["web"]["running"] is False
    assert stopped_status["channels"]["web"]["state"] == "configured"


def test_runtime_degrades_failing_channel_without_blocking_other_channels():
    class FailingAdapter:
        def __init__(self, config) -> None:
            self.config = config

        async def start(self) -> None:
            raise RuntimeError("boom")

        async def stop(self) -> None:
            return None

    class RunningAdapter:
        def __init__(self, config) -> None:
            self.config = config
            self.started = False

        async def start(self) -> None:
            self.started = True

        async def stop(self) -> None:
            return None

    class FakeRegistry(ChannelRegistry):
        def list_channels(self):
            return {
                "web": {"label": "Web", "implemented": True},
                "feishu": {"label": "Feishu", "implemented": True},
            }

        def get_meta(self, channel_id: str):
            if channel_id in {"web", "feishu"}:
                return {"label": channel_id.title(), "implemented": True}
            return None

        def is_configured(self, channel_id: str, config):
            return channel_id in {"web", "feishu"}

        def build_adapter(
            self,
            channel_id: str,
            config,
            *,
            router=None,
            artifact_service=None,
        ):
            if channel_id == "web":
                return FailingAdapter(config)
            if channel_id == "feishu":
                return RunningAdapter(config)
            return None

    config = GatewayConfig()
    config.channels.web.enabled = True
    config.channels.feishu.enabled = True
    runtime = GatewayRuntime(config=config, registry=FakeRegistry(), router=None)

    asyncio.run(runtime.start())

    status = runtime.status()
    assert status["state"] == "degraded"
    assert status["channels"]["web"]["running"] is False
    assert status["channels"]["web"]["state"] == "degraded"
    assert status["channels"]["web"]["error"] == "boom"
    assert status["channels"]["feishu"]["running"] is True
    assert status["channels"]["feishu"]["state"] == "running"


def test_runtime_passes_artifact_service_to_registry_builder():
    class FakeAdapter:
        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    class FakeRegistry(ChannelRegistry):
        def __init__(self) -> None:
            super().__init__()
            self.captured_artifact_service = None

        def list_channels(self):
            return {"web": {"label": "Web", "implemented": True}}

        def get_meta(self, channel_id: str):
            if channel_id == "web":
                return {"label": "Web", "implemented": True}
            return None

        def is_configured(self, channel_id: str, config):
            return channel_id == "web"

        def build_adapter(
            self,
            channel_id: str,
            config,
            *,
            router=None,
            artifact_service=None,
        ):
            if channel_id != "web":
                return None
            self.captured_artifact_service = artifact_service
            return FakeAdapter()

    config = GatewayConfig()
    config.channels.web.enabled = True
    registry = FakeRegistry()
    shared_artifact_service = object()
    runtime = GatewayRuntime(
        config=config,
        registry=registry,
        router=None,
        artifact_service=shared_artifact_service,
    )

    asyncio.run(runtime.start())

    assert registry.captured_artifact_service is shared_artifact_service


def test_runtime_inherits_gateway_default_agent_for_dingding_channel():
    class FakeAdapter:
        def __init__(self, config) -> None:
            self.config = config

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    class FakeRegistry(ChannelRegistry):
        def __init__(self) -> None:
            super().__init__()
            self.built_config = None

        def list_channels(self):
            return {"dingding": {"label": "DingTalk", "implemented": True}}

        def get_meta(self, channel_id: str):
            if channel_id == "dingding":
                return {"label": "DingTalk", "implemented": True}
            return None

        def is_configured(self, channel_id: str, config):
            return channel_id == "dingding"

        def build_adapter(
            self,
            channel_id: str,
            config,
            *,
            router=None,
            artifact_service=None,
        ):
            if channel_id != "dingding":
                return None
            self.built_config = config
            return FakeAdapter(config)

    config = GatewayConfig()
    config.default_agent_id = "Aworld"
    config.channels.dingding.enabled = True
    config.channels.dingding.default_agent_id = None
    registry = FakeRegistry()
    runtime = GatewayRuntime(config=config, registry=registry, router=None)

    asyncio.run(runtime.start())

    assert registry.built_config is config.channels.dingding
    assert registry.built_config.default_agent_id == "Aworld"
