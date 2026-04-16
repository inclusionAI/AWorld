from __future__ import annotations

import inspect
import os
from collections import OrderedDict
from dataclasses import dataclass
from typing import TypeAlias

from aworld_gateway.channels.base import ChannelAdapter, ChannelMetadata
from aworld_gateway.channels.dingding.adapter import DingdingChannelAdapter
from aworld_gateway.channels.feishu.adapter import FeishuChannelAdapter
from aworld_gateway.channels.telegram.adapter import TelegramChannelAdapter
from aworld_gateway.channels.web.adapter import WebChannelAdapter
from aworld_gateway.channels.wecom.adapter import WecomChannelAdapter
from aworld_gateway.config import (
    BaseChannelConfig,
    DingdingChannelConfig,
    TelegramChannelConfig,
)

ChannelMetaSummary: TypeAlias = dict[str, object]

@dataclass(frozen=True)
class ChannelRegistration:
    metadata: ChannelMetadata
    label: str
    adapter_class: type[ChannelAdapter] | None = None


class ChannelRegistry:
    def __init__(self) -> None:
        self._registrations: OrderedDict[str, ChannelRegistration] = OrderedDict(
            {
                "telegram": ChannelRegistration(
                    metadata=ChannelMetadata(name="telegram", implemented=True),
                    label="Telegram",
                    adapter_class=TelegramChannelAdapter,
                ),
                "web": ChannelRegistration(
                    metadata=ChannelMetadata(name="web", implemented=False),
                    label="Web",
                    adapter_class=WebChannelAdapter,
                ),
                "dingding": ChannelRegistration(
                    metadata=ChannelMetadata(name="dingding", implemented=True),
                    label="DingTalk",
                    adapter_class=DingdingChannelAdapter,
                ),
                "feishu": ChannelRegistration(
                    metadata=ChannelMetadata(name="feishu", implemented=False),
                    label="Feishu",
                    adapter_class=FeishuChannelAdapter,
                ),
                "wecom": ChannelRegistration(
                    metadata=ChannelMetadata(name="wecom", implemented=False),
                    label="WeCom",
                    adapter_class=WecomChannelAdapter,
                ),
            }
        )

    def list_channels(self) -> dict[str, ChannelMetaSummary]:
        return {
            channel_id: self._meta_summary(registration)
            for channel_id, registration in self._registrations.items()
        }

    def get_meta(self, channel_id: str) -> ChannelMetaSummary | None:
        registration = self._registrations.get(channel_id)
        if registration is None:
            return None
        return self._meta_summary(registration)

    def get_adapter_class(self, channel_id: str) -> type[ChannelAdapter] | None:
        registration = self._registrations.get(channel_id)
        if registration is None:
            return None
        return registration.adapter_class

    def build_adapter(
        self,
        channel_id: str,
        config: BaseChannelConfig,
        *,
        router: object | None = None,
    ) -> ChannelAdapter | None:
        adapter_class = self.get_adapter_class(channel_id)
        if adapter_class is None:
            return None
        init_params = inspect.signature(adapter_class.__init__).parameters
        if router is not None and "router" in init_params:
            return adapter_class(config, router=router)
        return adapter_class(config)

    def is_configured(
        self,
        channel_id: str,
        config: BaseChannelConfig | None,
    ) -> bool:
        registration = self._registrations.get(channel_id)
        if registration is None or config is None:
            return False

        if channel_id == "telegram":
            if not isinstance(config, TelegramChannelConfig):
                return False
            if not config.bot_token_env:
                return False
            return bool(os.getenv(config.bot_token_env))

        if channel_id == "dingding":
            if not isinstance(config, DingdingChannelConfig):
                return False
            if not config.client_id_env or not config.client_secret_env:
                return False
            return bool(os.getenv(config.client_id_env)) and bool(
                os.getenv(config.client_secret_env)
            )

        return True

    @staticmethod
    def _meta_summary(registration: ChannelRegistration) -> ChannelMetaSummary:
        return {
            "label": registration.label,
            "implemented": registration.metadata.implemented,
        }
