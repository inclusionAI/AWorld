from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.channels.base import ChannelAdapter
from aworld_gateway.config import (
    DingdingChannelConfig,
    PlaceholderChannelConfig,
    TelegramChannelConfig,
    WechatChannelConfig,
    WecomChannelConfig,
)
from aworld_gateway.registry import ChannelRegistry


def test_list_channels_reports_phase_one_builtins_with_implementation_flags():
    summary = ChannelRegistry().list_channels()

    assert summary["telegram"]["implemented"] is True
    assert summary["web"]["implemented"] is False
    assert summary["dingding"]["implemented"] is True
    assert summary["wechat"]["implemented"] is True
    assert summary["feishu"]["implemented"] is False
    assert summary["wecom"]["implemented"] is True


def test_registry_exposes_metadata_and_adapter_class_paths():
    registry = ChannelRegistry()
    telegram_meta = registry.get_meta("telegram")
    dingding_meta = registry.get_meta("dingding")
    wechat_meta = registry.get_meta("wechat")
    wecom_meta = registry.get_meta("wecom")
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

    assert dingding_meta is not None
    assert dingding_meta["implemented"] is True
    dingding_adapter_cls = registry.get_adapter_class("dingding")
    assert dingding_adapter_cls is not None
    assert issubclass(dingding_adapter_cls, ChannelAdapter)
    assert dingding_adapter_cls.metadata().implemented is True

    assert wechat_meta is not None
    assert wechat_meta["implemented"] is True
    wechat_adapter_cls = registry.get_adapter_class("wechat")
    assert wechat_adapter_cls is not None
    assert issubclass(wechat_adapter_cls, ChannelAdapter)
    assert wechat_adapter_cls.metadata().implemented is True

    assert wecom_meta is not None
    assert wecom_meta["implemented"] is True
    wecom_adapter_cls = registry.get_adapter_class("wecom")
    assert wecom_adapter_cls is not None
    assert issubclass(wecom_adapter_cls, ChannelAdapter)
    assert wecom_adapter_cls.metadata().implemented is True


def test_registry_validates_channel_configuration(monkeypatch):
    registry = ChannelRegistry()

    monkeypatch.delenv("AWORLD_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("AWORLD_DINGTALK_CLIENT_ID", raising=False)
    monkeypatch.delenv("AWORLD_DINGTALK_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("AWORLD_WECHAT_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("AWORLD_WECHAT_TOKEN", raising=False)
    monkeypatch.delenv("AWORLD_WECOM_BOT_ID", raising=False)
    monkeypatch.delenv("AWORLD_WECOM_SECRET", raising=False)

    assert registry.is_configured("web", PlaceholderChannelConfig()) is True
    assert registry.is_configured("telegram", TelegramChannelConfig()) is False
    assert registry.is_configured("dingding", DingdingChannelConfig()) is False
    assert registry.is_configured("wechat", WechatChannelConfig()) is False
    assert registry.is_configured("wecom", WecomChannelConfig()) is False
    assert registry.is_configured("unknown", PlaceholderChannelConfig()) is False

    monkeypatch.setenv("CUSTOM_TELEGRAM_TOKEN", "env-token")
    assert (
        registry.is_configured(
            "telegram",
            TelegramChannelConfig(bot_token_env="CUSTOM_TELEGRAM_TOKEN"),
        )
        is True
    )

    monkeypatch.setenv("AWORLD_DINGTALK_CLIENT_ID", "ding-client")
    assert registry.is_configured("dingding", DingdingChannelConfig()) is False

    monkeypatch.setenv("AWORLD_DINGTALK_CLIENT_SECRET", "ding-secret")
    assert registry.is_configured("dingding", DingdingChannelConfig()) is True

    monkeypatch.setenv("AWORLD_WECHAT_ACCOUNT_ID", "wechat-account")
    assert registry.is_configured("wechat", WechatChannelConfig()) is False

    monkeypatch.setenv("AWORLD_WECHAT_TOKEN", "wechat-token")
    assert registry.is_configured("wechat", WechatChannelConfig()) is True

    monkeypatch.setenv("AWORLD_WECOM_BOT_ID", "wecom-bot")
    assert registry.is_configured("wecom", WecomChannelConfig()) is False

    monkeypatch.setenv("AWORLD_WECOM_SECRET", "wecom-secret")
    assert registry.is_configured("wecom", WecomChannelConfig()) is True
