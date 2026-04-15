from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.channels.base import ChannelAdapter
from aworld_gateway.config import PlaceholderChannelConfig, TelegramChannelConfig
from aworld_gateway.registry import ChannelRegistry


def test_list_channels_reports_phase_one_builtins_with_implementation_flags():
    summary = ChannelRegistry().list_channels()

    assert summary["telegram"]["implemented"] is True
    assert summary["web"]["implemented"] is False
    assert summary["dingding"]["implemented"] is False
    assert summary["feishu"]["implemented"] is False
    assert summary["wecom"]["implemented"] is False


def test_registry_exposes_metadata_and_adapter_class_paths():
    registry = ChannelRegistry()
    telegram_meta = registry.get_meta("telegram")
    web_meta = registry.get_meta("web")

    assert telegram_meta is not None
    assert telegram_meta["implemented"] is True
    telegram_adapter_cls = registry.get_adapter_class("telegram")
    assert telegram_adapter_cls is not None
    assert issubclass(telegram_adapter_cls, ChannelAdapter)

    assert web_meta is not None
    assert web_meta["implemented"] is False
    web_adapter_cls = registry.get_adapter_class("web")
    assert web_adapter_cls is not None
    assert issubclass(web_adapter_cls, ChannelAdapter)


def test_registry_validates_channel_configuration(monkeypatch):
    registry = ChannelRegistry()

    monkeypatch.delenv("AWORLD_TELEGRAM_BOT_TOKEN", raising=False)

    assert registry.is_configured("web", PlaceholderChannelConfig()) is True
    assert registry.is_configured("telegram", TelegramChannelConfig()) is False
    assert registry.is_configured("unknown", PlaceholderChannelConfig()) is False
    assert (
        registry.is_configured(
            "telegram",
            TelegramChannelConfig(bot_token="telegram-token"),
        )
        is True
    )

    monkeypatch.setenv("CUSTOM_TELEGRAM_TOKEN", "env-token")
    assert (
        registry.is_configured(
            "telegram",
            TelegramChannelConfig(bot_token_env="CUSTOM_TELEGRAM_TOKEN"),
        )
        is True
    )
