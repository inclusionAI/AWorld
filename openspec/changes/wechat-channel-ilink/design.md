## Context

当前 `aworld_gateway` 已经形成了两层边界：

- 公共控制平面：
  - `config`
  - `registry`
  - `runtime`
  - `router`
  - `status`
- channel 本地实现：
  - `telegram` 采用较薄的 webhook + final-text 适配
  - `dingding` 已经演化成 channel-local subsystem

从参考实现 `/Users/wuman/Documents/workspace/hermes-agent/gateway/platforms/weixin.py` 和 `/Users/wuman/Documents/workspace/hermes-agent/gateway/platforms/wecom.py` 来看，个人微信与企业微信的差异非常大：

- `weixin`:
  - `getupdates` 长轮询
  - 每个 peer 需要缓存最新 `context_token`
  - 出站回复通常要回传该 token
  - 可选 QR 登录与账号恢复
  - 平台不支持消息编辑，发送侧采用 final-only
- `wecom`:
  - WebSocket 订阅和 callback
  - `req_id` 关联响应
  - 群聊主动发送受限
  - media upload 分块流程

因此，本次 change 不应尝试定义一个同时覆盖 `weixin` 和 `wecom` 的统一微信实现模型。正确边界是：

- `wechat` 独立作为个人微信 channel
- `wecom` 继续保留为企业微信 channel
- 二者只在必要处共享极小 helper

## Goals / Non-Goals

**Goals**

- 为 gateway 增加一个可运行的 `wechat` channel。
- 明确 `wechat` 的公共命名和 `weixin / iLink` 的协议来源之间的关系。
- 在不扩大回归面的前提下复用现有 gateway 控制平面。
- 第一阶段跑通文本消息闭环、长轮询、`context_token` 缓存与回传。
- 用 OpenSpec 固化 `wechat` 与 `wecom` 的边界，避免后续实现把两者重新揉成一个 channel。

**Non-Goals**

- 不把 `wechat` 和 `wecom` 合并成一个统一 `wx` channel。
- 不把 `wechat` channel 直接内嵌进 `aworld-cli acp` 的 stdio host 进程。
- 不在第一阶段实现全局流式 `GatewayRouter` 重构。
- 不在第一阶段承诺完整媒体上传/下载闭环。
- 不在第一阶段要求 QR 登录 CLI 命令面。
- 不在本 change 中顺手完成 `wecom` 的完整实现。

## Decisions

### Decision: Public channel naming uses `wechat`, while protocol details remain `weixin / iLink`

新增 channel 的公共 id、目录名和 operator-facing 配置名统一使用 `wechat`，而底层协议说明保留为 `weixin / iLink Bot`。

Why:

- `wechat` / `wecom` 对 operator 更直观。
- 公共层避免把具体 provider 实现名写死。
- 后续若个人微信还有其他接入方式，不需要重命名 channel 抽象。

Rejected alternatives:

- 公共 channel id 直接使用 `weixin`
  Rejected，因为会把协议实现名固化到公共抽象层。
- 用一个总的 `wx` channel 再在内部细分
  Rejected，因为公共抽象会比真实能力边界更模糊。

### Decision: `wechat` and `wecom` remain independent channels

虽然两者都属于微信生态，但它们在连接方式、发送限制、会话模型和账号形态上都不同，必须保持两个独立 channel。

Why:

- `weixin` 是 poll + token state 模型。
- `wecom` 是 websocket + correlated response 模型。
- 把两者合并会让主 connector 和 state model 充满条件分支。

Rejected alternatives:

- 做一个统一的“微信 family”主实现
  Rejected，因为共享面过小，不值得引入更高层的抽象复杂度。

### Decision: Happy ACP and `wechat` coexist through two separate processes

当用户同时希望通过 Happy ACP 和 `wechat` 访问同一套 AWorld 能力时，宿主方式固定为两个并行进程：

- `aworld-cli acp`
- `aworld-cli gateway server`

二者可以共享同一套 agent 定义、同一台 worker host 和同一个仓库/workspace，但不应合并成单个混合宿主进程。

Why:

- `aworld-cli acp` 的 `stdout` 必须保持 JSON-RPC/NDJSON 协议纯净。
- `wechat` 的 long-poll lifecycle 与 ACP session lifecycle 是两类不同宿主问题。
- 将二者混在一个进程里会放大故障域并增加日志/生命周期耦合。

Rejected alternatives:

- 在 `aworld-cli acp` 进程内直接启动 `wechat` channel
  Rejected，因为这会把 stdio backend host 和 channel runtime 混成一个失败域，并增加协议输出污染风险。

### Decision: `wechat` uses a channel-local subsystem instead of a thin adapter

`wechat` 不采用 `telegram` 那种薄 adapter 结构，而是参考 `dingding` 的边界，在 `aworld_gateway/channels/wechat/` 下收敛实现复杂度。

推荐模块拆分：

- `adapter.py`
- `connector.py`
- `account_store.py`
- `context_token_store.py`
- `transform.py`
- `media.py`
- 可选 `login.py`

Why:

- `weixin.py` 的核心复杂度在长轮询和 token 状态，而不是单个 API 调用。
- 这些状态不应污染 `aworld_gateway/router.py` 或 `runtime.py`。

Rejected alternatives:

- 先把实现塞进一个单文件 adapter
  Rejected，因为后续媒体、登录和 typing 很快会让文件失控。
- 直接照搬 `telegram` 的 `handle_update -> router -> send`
  Rejected，因为会丢失 `context_token` 和账号状态的主导地位。

### Decision: Phase 1 keeps the current non-streaming `GatewayRouter` contract

第一阶段继续沿用现有 `InboundEnvelope -> GatewayRouter -> OutboundEnvelope(final text)` contract，不先改造全局 streaming router。

Why:

- `hermes-agent` 的 `weixin.py` 明确设置 `SUPPORTS_MESSAGE_EDITING = False`。
- 注释已说明 WeChat 平台侧发送要走 `send-final-only` 回退。
- 因此先做文本闭环与 token 状态管理，收益远高于提前重构全局流式框架。

Rejected alternatives:

- 先为 `wechat` 引入 channel-specific 流式 router
  Rejected，因为第一阶段目标只是对齐参考实现中的 final-only 平台发送特性。
- 借 `wechat` 一次性推动整个 gateway 流式化
  Rejected，因为会把局部 channel 扩展变成全局架构改造。

### Decision: Phase 1 scope is limited to text delivery and required state caches

第一阶段只冻结下列能力：

- 文本消息入站
- `getupdates` 长轮询
- 基础 dedup
- `context_token` 更新与回传
- account/token/base_url 恢复
- 文本分段发送
- gateway config / registry / runtime / status 接入

Deferred to later stages:

- 入站图片/文件/语音下载
- 出站媒体上传
- typing ticket
- QR 登录命令面
- 更完整的 markdown / 富文本策略

Why:

- 这能最小化实现切片，并对齐当前 branch 的核心目标。
- 媒体与登录都依赖更多平台细节，适合在文本闭环稳定后继续推进。

## Configuration Model

建议新增 `WechatChannelConfig`，第一阶段只包含最小字段：

- `enabled`
- `default_agent_id`
- `account_id_env`
- `token_env`
- `base_url_env`
- `cdn_base_url_env`
- `dm_policy`
- `group_policy`
- `allow_from`
- `group_allow_from`
- `split_multiline_messages`

`account_id / token / base_url` 的运行态恢复继续由 channel-local store 管理，不直接把持久状态写入 gateway config。

## Phase Plan

### Phase 1

- 文本收发闭环
- 长轮询 connector
- `context_token` store
- account restore
- gateway runtime / registry / config / status 接入
- 基础测试覆盖

### Phase 2

- 入站媒体下载
- 出站媒体上传
- typing ticket
- 更精细文本拆分和富文本兼容

当前这个 change 在不改 `GatewayRouter` 的前提下，进一步冻结了一个最小可行的 Phase 2 方案：

- 入站图片/文件/视频/语音继续由 `wechat` connector 自己下载到 channel-local cache。
- `InboundEnvelope` 不新增媒体字段，而是通过 `metadata.attachments` 和附加到 `text` 的 attachment prompt 暴露本地缓存路径。
- 出站不消费通用 `metadata.attachments`，避免和 router 当前的 metadata 回传行为形成“收到什么就发回什么”的回环。
- 出站媒体能力以显式本地引用为边界：markdown image、markdown link、`file://` / `attachment://` / `MEDIA:` 形式的引用会被解释为“发送附件”意图，其余普通文本不做自动文件提取。
- 所有 `full_url` 型入站下载都必须经过 WeChat CDN allowlist 校验，避免 connector 退化成通用 URL 抓取器。

### Phase 3

- QR 登录
- 运维命令面
- 更复杂的群聊策略
- 如有需要，再单独推进 `wecom` 实现 change

## File Map

Planned primary files:

- `aworld_gateway/channels/wechat/__init__.py`
- `aworld_gateway/channels/wechat/adapter.py`
- `aworld_gateway/channels/wechat/connector.py`
- `aworld_gateway/channels/wechat/account_store.py`
- `aworld_gateway/channels/wechat/context_token_store.py`
- `aworld_gateway/channels/wechat/transform.py`
- `aworld_gateway/channels/wechat/media.py`
- `aworld_gateway/config/models.py`
- `aworld_gateway/config/__init__.py`
- `aworld_gateway/config/loader.py`
- `aworld_gateway/registry.py`
- `aworld_gateway/runtime.py`
- `tests/gateway/test_wechat_config.py`
- `tests/gateway/test_wechat_adapter.py`
- `tests/gateway/test_wechat_connector.py`
- updates to existing `tests/gateway/test_registry.py` and `tests/gateway/test_runtime.py`

## Risks / Trade-offs

- [Config surface grows before full media support exists] -> 将 Phase 1 config 限定为文本闭环必需字段，避免预先暴露过多未实现选项。
- [`wechat` and `wecom` may still want helper reuse later] -> 允许后续抽 helper，但当前不提前引入 family-level base class。
- [Long-poll connector adds stateful failure modes] -> 将重试、dedup、token store 都限定在 `channels/wechat/` 内，不向 gateway 公共层泄漏。
- [Later streaming work may revisit router shape] -> 在 design 中明确这是后续 change，而不是 Phase 1 的隐含依赖。
- [Users may expect ACP and `wechat` to co-host in one process] -> 在 design 中显式冻结为双进程方案，避免后续实现误入混合宿主。

## Migration Plan

1. 在 gateway config/registry/runtime 中注册 `wechat`。
2. 建立 `channels/wechat/` 子系统骨架。
3. 先实现文本闭环与 `context_token` 状态管理。
4. 补齐 gateway 测试和 `wechat` 子系统测试。
5. 完成后再决定是否开启 Phase 2 媒体能力。
