from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class StrictConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GatewayServerConfig(StrictConfigModel):
    host: str = "127.0.0.1"
    port: int = 18888


class BaseChannelConfig(StrictConfigModel):
    enabled: bool = False
    default_agent_id: Optional[str] = None


class TelegramChannelConfig(BaseChannelConfig):
    bot_token: Optional[str] = None
    bot_token_env: Optional[str] = "AWORLD_TELEGRAM_BOT_TOKEN"
    webhook_path: str = "/webhooks/telegram"


class PlaceholderChannelConfig(BaseChannelConfig):
    implemented: bool = Field(default=False, exclude=True)


class ChannelConfigMap(StrictConfigModel):
    web: PlaceholderChannelConfig = Field(default_factory=PlaceholderChannelConfig)
    telegram: TelegramChannelConfig = Field(default_factory=TelegramChannelConfig)
    dingding: PlaceholderChannelConfig = Field(default_factory=PlaceholderChannelConfig)
    feishu: PlaceholderChannelConfig = Field(default_factory=PlaceholderChannelConfig)
    wecom: PlaceholderChannelConfig = Field(default_factory=PlaceholderChannelConfig)


class RouteRule(StrictConfigModel):
    channel: Optional[str] = None
    account_id: Optional[str] = None
    conversation_type: Optional[Literal["dm", "group", "web"]] = None
    conversation_id: Optional[str] = None
    sender_id: Optional[str] = None
    agent_id: str


class GatewayConfig(StrictConfigModel):
    default_agent_id: str = "aworld"
    gateway: GatewayServerConfig = Field(default_factory=GatewayServerConfig)
    channels: ChannelConfigMap = Field(default_factory=ChannelConfigMap)
    routes: List[RouteRule] = Field(default_factory=list)
