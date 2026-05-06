## Why

AWorld 目前已经具备 `amni` context 的内容治理能力，例如 AWORLD.md 注入、relevant memory recall、summary、history trim 和 tool result offload。但这些能力主要回答“什么内容应该进入上下文”，并没有把 prompt 结构设计成对 provider prompt cache 友好的形态。

当前实现存在几个具体问题：

- `system prompt` 与动态 context 注入直接拼接成单一字符串，缺少 stable prefix / dynamic suffix 的结构边界。
- `amni` 的 context 内容生产层和 provider 请求投递层之间没有统一的 cache-aware 中间抽象。
- provider 层虽然已有统一接口，但当前只统一到 OpenAI-style message 语义，并不能表达 prompt cache 所需的分段、稳定性和 cache hint。
- Anthropic 等 provider 的原生 prompt cache 能力没有可选接入面；同时，AWorld 也不应该把这类能力和某一个厂商强绑定。

因此，这次 change 需要把 AWorld 从“有 context 内容治理”升级为“具备 cache-friendly prompt assembly 能力”，并确保：

- 该能力是 AWorld 的通用高级特性，而不是 Anthropic 专属实现。
- 默认可以启用，但用户必须能通过配置关闭。
- provider-native cache 只是可选增强层，不得成为普通请求路径的前提依赖。

## What Changes

- 在 `amni` 现有 context 内容生产能力之上，新增一层 provider-neutral 的 prompt assembly 能力。
- 为 assembly 层引入稳定前缀 / 动态后缀分段模型，以及 request-time stable hash 复用策略。
- 新增双层配置入口：
  - `AgentContextConfig.context_cache`
  - `ModelConfig.context_cache`
- 规定实际生效规则为：
  - agent 侧启用
  - model 侧启用
  - provider 路径未显式禁用
- 明确 stable prefix 不进入普通 history / memory，不再作为持久化的 system message 保存。
- 新增 provider capability / lowerer 抽象：
  - 默认 provider 路径可把 assembly plan 降级为普通请求
  - Anthropic 首期实现 provider-native cache lowering
- 将 tools 视为 stable section 的语义成员，但 tools 的 wire-format 序列化仍由具体 provider 负责。

## Capabilities

### New Capabilities

- `context-cache-assembly`: AWorld 可以在 request-time 构造 cache-friendly 的 prompt assembly plan，并在 provider 不支持时安全降级。
- `provider-prompt-cache-lowering`: provider 可以按自身能力选择是否将 assembly plan 翻译为原生 prompt cache 请求。

### Modified Capabilities

- `amni-context-management`: `amni` 继续负责 context 内容生产，但不再默认承担 stable prefix 的持久化职责。
- `model-provider-adaptation`: provider 适配层从“只做消息协议转换”扩展为“可选地消费 assembly plan 并执行 native lowering”。

## Impact

- Affected code:
  - `aworld/core/context/amni/config.py`
  - `aworld/config/conf.py`
  - `aworld/core/context/amni/prompt/`
  - `aworld/core/context/amni/processor/op/system_prompt_augment_op.py`
  - `aworld/models/`
  - `tests/core/context/amni/`
  - `tests/models/`
- Affected behavior:
  - 默认情况下，context assembly 增强能力可以参与请求装配，但用户可通过 agent/model 配置显式关闭。
  - provider 不支持原生 cache 时，请求行为仍保持普通路径，不阻塞主流程。
  - Anthropic 在开启 provider-native cache 时，可以输出 cache-aware 的原生请求形态。
- Constraints preserved:
  - 不重写 `amni` 的 neurons、summary、recall、offload 主逻辑。
  - 不把 Anthropic 专有字段泄漏到 `amni` 公共对象。
  - 不把 stable prefix 写回普通 history/memory。
  - 不要求首期实现 fork/subagent/resume 的全链路 cache-stable 复制。
