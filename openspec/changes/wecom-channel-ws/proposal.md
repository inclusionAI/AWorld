## Why

`aworld_gateway` 当前已经有个人微信 `wechat` channel，但企业微信 `wecom` 仍然只是占位实现。根据参考实现 `/Users/wuman/Documents/workspace/hermes-agent/gateway/platforms/wecom.py`，企业微信的接入模型与个人微信明显不同：

- `wecom` 使用 WebSocket 长连接与 `req_id` 关联响应
- 群聊发送依赖 reply-bound `req_id`
- 平台能力边界、鉴权方式、发送限制都不应复用 `wechat`

因此需要单独落一个 `wecom` channel change，而不是继续把企业微信能力塞进已有 `wechat` change。

## What Changes

- 新增 `WecomChannelConfig` 并接入 gateway control plane。
- 将 `wecom` 从占位 channel 升级为已实现 channel。
- 新增 `aworld_gateway/channels/wecom/connector.py`，实现企业微信 channel-local WebSocket 收发。
- 支持：
  - `aibot_subscribe` 鉴权握手
  - `aibot_msg_callback` 入站文本回调
  - `aibot_send_msg` 主动文本发送
  - `aibot_respond_msg` reply-bound 文本发送
  - `req_id` 关联与聊天级 fallback reply 缓存
  - DM / group 基础策略过滤
  - 入站图片/文件缓存与 metadata 挂载
  - 出站本地媒体上传、类型/大小限制、必要时降级为 `file`
  - 应用层心跳与断线重连

## Non-Goals

- 不在本 change 中抽象 `wechat` / `wecom` 共用微信 family base class。
- 不改造 `GatewayRouter` 为流式。
- 不在本 change 中实现更细粒度的群成员级 allowlist 规则。
