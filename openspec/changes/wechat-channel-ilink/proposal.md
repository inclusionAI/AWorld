## Why

`aworld_gateway` 当前已经有 `telegram` 和 `dingding` 两类 channel，但还没有个人微信能力。仓库里虽然保留了 `wecom` 占位 channel，实际个人微信与企业微信并不是同一种接入问题：

- 个人微信在参考实现中走的是 Weixin iLink Bot 协议，核心是 `getupdates` 长轮询、`context_token` 回传、账号恢复和可选 QR 登录。
- 企业微信在参考实现中走的是 WeCom AI Bot WebSocket 协议，核心是订阅握手、`req_id` 关联响应、群聊限制和上传会话。

因此，这次扩展不能把两者合并成一个泛化但模糊的“微信 channel”。需要先把个人微信能力独立落成一个 `wechat` channel，并明确与 `wecom` 的边界。

另外，参考 `hermes-agent` 的 `weixin.py` 可以确认一个关键事实：虽然运行时可产生流式文本，WeChat 平台侧并不支持消息编辑，实际发送路径采用 `send-final-only` 回退。因此本次第一阶段没有必要先把 `GatewayRouter` 改造成全局流式框架。

## What Changes

- 新增一个独立的 `wechat` channel，用于对接个人微信 / Weixin iLink Bot。
- 明确 `wechat` 与 `wecom` 是两个独立 channel：
  - `wechat` 负责个人微信
  - `wecom` 负责企业微信
  - 二者可以共享少量 helper，但不共享主 connector / state model
- 明确当用户同时需要 Happy ACP 访问与 `wechat` channel 访问时，采用两个并行进程满足该诉求：
  - `aworld-cli acp`
  - `aworld-cli gateway server`
  - 不要求把 `wechat` channel 直接内嵌进 ACP stdio host 进程
- 在 gateway 公共层新增 `WechatChannelConfig`，并把 `wechat` 接入：
  - `aworld_gateway/config/models.py`
  - `aworld_gateway/config/__init__.py`
  - `aworld_gateway/config/loader.py`
  - `aworld_gateway/registry.py`
  - `aworld_gateway/runtime.py`
- 在 `aworld_gateway/channels/wechat/` 下实现 channel-local subsystem，而不是套用 `telegram` 式薄适配器。
- 第一阶段保持现有 `GatewayRouter` 的单次入站 -> 最终文本回复 contract，不引入全局流式 router 改造。

## Capabilities

### New Capabilities

- `wechat-channel`: Aworld gateway 可以作为个人微信 channel 运行，接收文本消息并返回最终文本回复。

### Modified Capabilities

- `gateway-channel-runtime`: gateway runtime、registry、status 和 config 能识别并管理新的 `wechat` channel。

## Impact

- Affected code:
  - `aworld_gateway/config/`
  - `aworld_gateway/registry.py`
  - `aworld_gateway/runtime.py`
  - `aworld_gateway/channels/wechat/`
  - `tests/gateway/`
- Affected behavior:
  - `gateway status` / `channels list` 会出现 `wechat`
  - `wechat` enabled 且配置完整时可启动长轮询 connector
  - 文本消息可通过现有 router 进入默认 Aworld Agent 并返回最终文本回复
- Constraints preserved:
  - `wechat` 与 `wecom` 保持独立 channel
  - Happy ACP 与 `wechat` 并存时采用两个进程，而不是单进程混合宿主
  - 第一阶段不改全局 `GatewayRouter` 为流式框架
  - 第一阶段不要求完整媒体闭环或 QR 登录命令面
