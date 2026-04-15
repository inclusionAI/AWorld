from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class GatewayServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765


class BaseChannelConfig(BaseModel):
    enabled: bool = False
    default_agent_id: Optional[str] = None


class TelegramChannelConfig(BaseChannelConfig):
    bot_token: Optional[str] = None


class PlaceholderChannelConfig(BaseChannelConfig):
    pass


class ChannelConfigMap(BaseModel):
    telegram: TelegramChannelConfig = Field(default_factory=TelegramChannelConfig)
    web: PlaceholderChannelConfig = Field(default_factory=PlaceholderChannelConfig)


class RouteRule(BaseModel):
    channel: str
    pattern: Optional[str] = None
    agent_id: str


class GatewayConfig(BaseModel):
    default_agent_id: str = "aworld"
    server: GatewayServerConfig = Field(default_factory=GatewayServerConfig)
    telegram: TelegramChannelConfig = Field(default_factory=TelegramChannelConfig)
    web: PlaceholderChannelConfig = Field(default_factory=PlaceholderChannelConfig)
    routes: List[RouteRule] = Field(default_factory=list)
