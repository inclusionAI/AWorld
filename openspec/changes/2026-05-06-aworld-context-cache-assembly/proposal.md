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

- 在 `amni` 现有 context 内容生产能力之上，新增一个可注入的 `PromptAssemblyProvider` 抽象。
- 默认 provider 保持接近当前字符串拼装行为；cache-aware provider 负责稳定前缀 / 动态后缀分段和 request-time stable hash 复用策略。
- 新增双层配置入口：
  - `AgentContextConfig.context_cache`
  - `ModelConfig.context_cache`
- 规定实际生效规则为：
  - agent 侧启用
  - model 侧启用
  - provider 路径未显式禁用
- 明确 stable prefix 不进入普通 history / memory，不再作为持久化的 system message 保存。
- 明确每次 LLM call 的真实 request snapshot 必须以 append-only 的调用记录保存在 runtime context 中，而不是覆写到单个 `context_info["llm_input"]` 字段。
- 新增 provider capability / lowerer 抽象：
  - 默认 provider 路径可消费 assembly provider 产物并降级为普通请求
  - Anthropic 首期实现 provider-native cache lowering
- 将 tools 视为 stable section 的语义成员，但 tools 的 wire-format 序列化仍由具体 provider 负责。
- 明确不引入平行的 `AmniContext` 或替代性的 context backend；新能力通过 `amni` 下游的 provider/strategy 注入。
- 统一扩展 token usage schema，在已有 `prompt_tokens / completion_tokens / total_tokens` 之外新增：
  - `cache_hit_tokens`
  - `cache_write_tokens`
- 要求任务完成日志和相关 hook payload 能输出 cache hit / write token 统计，便于观察 prompt cache 收益。
- 要求 `trajectory.log` 优先记录每次 LLM call 的真实 request messages 快照，而不是仅依赖 memory 事后重建；`trajectory.log` 不承载 cache token usage。

## Capabilities

### New Capabilities

- `context-cache-assembly`: AWorld 可以通过可注入的 `PromptAssemblyProvider` 在 request-time 构造 cache-friendly 的 prompt assembly plan，并在 provider 不支持时安全降级。
- `provider-prompt-cache-lowering`: provider 可以按自身能力选择是否将 assembly plan 翻译为原生 prompt cache 请求。

### Modified Capabilities

- `amni-context-management`: `amni` 继续负责 context 内容生产，但不再默认承担 stable prefix 的持久化职责，也不需要被替换为平行 context backend。
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
  - 当 provider 返回原生 cache usage 时，AWorld 会统一汇总并在任务完成日志中输出 `cache_hit_tokens` 和 `cache_write_tokens`。
  - 当同一条 message 内发生多次 LLM call 时，AWorld 会在 runtime context 中保留多条调用记录，避免 `llm_input` / `llm_output` / `llm_call_start_time` 被后一次调用覆盖。
  - `trajectory.log` 在可用时会使用这些逐次调用快照作为 prompt 真相源；只有缺失快照时才允许退回 memory reconstruction。
- Constraints preserved:
  - 不重写 `amni` 的 neurons、summary、recall、offload 主逻辑。
  - 不把 Anthropic 专有字段泄漏到 `amni` 公共对象。
  - 不把 stable prefix 写回普通 history/memory。
  - 不要求首期实现 fork/subagent/resume 的全链路 cache-stable 复制。
