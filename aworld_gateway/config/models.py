from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class StrictConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GatewayServerConfig(StrictConfigModel):
    host: str = "127.0.0.1"
    port: int = 18888
    public_base_url: str | None = None


class BaseChannelConfig(StrictConfigModel):
    enabled: bool = False
    default_agent_id: Optional[str] = None


class TelegramChannelConfig(BaseChannelConfig):
    bot_token_env: Optional[str] = "AWORLD_TELEGRAM_BOT_TOKEN"
    webhook_path: str = "/webhooks/telegram"


class DingdingChannelConfig(BaseChannelConfig):
    client_id_env: Optional[str] = "AWORLD_DINGTALK_CLIENT_ID"
    client_secret_env: Optional[str] = "AWORLD_DINGTALK_CLIENT_SECRET"
    card_template_id_env: Optional[str] = "AWORLD_DINGTALK_CARD_TEMPLATE_ID"
    enable_ai_card: bool = True
    enable_attachments: bool = True
    workspace_dir: Optional[str] = None


class WechatChannelConfig(BaseChannelConfig):
    account_id_env: Optional[str] = "AWORLD_WECHAT_ACCOUNT_ID"
    token_env: Optional[str] = "AWORLD_WECHAT_TOKEN"
    base_url_env: Optional[str] = "AWORLD_WECHAT_BASE_URL"
    cdn_base_url_env: Optional[str] = "AWORLD_WECHAT_CDN_BASE_URL"
    dm_policy: Literal["open", "allowlist", "disabled"] = "open"
    group_policy: Literal["open", "allowlist", "disabled"] = "disabled"
    allow_from: list[str] = Field(default_factory=list)
    group_allow_from: list[str] = Field(default_factory=list)
    split_multiline_messages: bool = False


class WecomChannelConfig(BaseChannelConfig):
    bot_id_env: Optional[str] = "AWORLD_WECOM_BOT_ID"
    secret_env: Optional[str] = "AWORLD_WECOM_SECRET"
    websocket_url_env: Optional[str] = "AWORLD_WECOM_WEBSOCKET_URL"
    dm_policy: Literal["open", "allowlist", "disabled"] = "open"
    group_policy: Literal["open", "allowlist", "disabled"] = "open"
    allow_from: list[str] = Field(default_factory=list)
    group_allow_from: list[str] = Field(default_factory=list)


class PlaceholderChannelConfig(BaseChannelConfig):
    implemented: bool = Field(default=False, exclude=True)


class ChannelConfigMap(StrictConfigModel):
    web: PlaceholderChannelConfig = Field(default_factory=PlaceholderChannelConfig)
    telegram: TelegramChannelConfig = Field(default_factory=TelegramChannelConfig)
    dingding: DingdingChannelConfig = Field(default_factory=DingdingChannelConfig)
    wechat: WechatChannelConfig = Field(default_factory=WechatChannelConfig)
    feishu: PlaceholderChannelConfig = Field(default_factory=PlaceholderChannelConfig)
    wecom: WecomChannelConfig = Field(default_factory=WecomChannelConfig)


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
