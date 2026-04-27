## Context

企业微信 `wecom` 与个人微信 `wechat` 的协议模型不同：

- `wechat`：poll + `context_token`
- `wecom`：WebSocket + `req_id`

因此 `wecom` 必须保持独立 channel 与独立 connector。

## Decisions

### Decision: `wecom` 保持 channel-local WebSocket 生命周期与协议状态

当前范围：

- WebSocket 建链与订阅握手
- 入站文本回调到 `InboundEnvelope`
- `req_id` 关联回复
- 主动文本发送
- DM / group 基础策略
- 应用层心跳
- 断线后的 connector-local 重连
- 入站图片/文件缓存
- 出站本地媒体上传与媒体类型/大小限制

Deferred：

- 群级细粒度 sender allowlist

### Decision: `wecom` 继续沿用 final-text router contract

`GatewayRouter` 不改。`wecom` connector 负责：

- callback -> `InboundEnvelope`
- `OutboundEnvelope` -> markdown/text send
- channel-local attachment intent -> native media upload/send + follow-up markdown

### Decision: 测试使用 connector-local transport abstraction

为避免测试直接依赖真实 WebSocket，connector 引入本地 transport 抽象：

- `send_json(payload)`
- `receive_json()`
- `close()`
- `closed`

默认实现由 `aiohttp` 提供，测试用 fake transport 注入。

## File Map

- `aworld_gateway/config/models.py`
- `aworld_gateway/config/__init__.py`
- `aworld_gateway/config/loader.py`
- `aworld_gateway/registry.py`
- `aworld_gateway/runtime.py`
- `aworld_gateway/channels/wecom/adapter.py`
- `aworld_gateway/channels/wecom/connector.py`
- `tests/gateway/test_wecom_config.py`
- `tests/gateway/test_wecom_adapter.py`
- `tests/gateway/test_wecom_connector.py`
- updates to existing registry/runtime/config-loader tests
