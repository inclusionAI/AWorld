## Context

AWorld 当前的 `amni` context 体系已经提供了成熟的内容治理能力：

- AWORLD.md / workspace instruction 注入
- relevant memory recall
- summary 与 history trim
- tool result offload
- neuron-based system prompt augmentation

这些能力的设计重点是“内容生产”和“上下文压缩”，而不是 prompt cache 友好的请求装配。现有主链路会在 `system_prompt_augment_op` 中将系统提示与增强内容直接拼接成一个大的 system prompt 字符串，再交由 provider 发送。

这种实现对一般对话是足够的，但对于 Anthropic prompt caching、OpenAI cached tokens 以及未来其他 provider 的 prefix-cache 类能力，会暴露出三个结构性缺口：

- 缺少 stable / dynamic prompt boundary。
- 缺少 provider-neutral 的 cache-aware 中间表示。
- 缺少 provider-native 可选 lowering 机制。

## Goals / Non-Goals

**Goals**

- 在不重写 `amni` 内容生产逻辑的前提下，新增基于 provider/strategy 注入的 cache-friendly prompt assembly 能力。
- 把 stable prefix / dynamic suffix 结构从字符串拼接中显式提出来。
- 让 context cache 能力作为 AWorld 的通用高级特性存在，而不是 Anthropic 专属实现。
- 支持双层配置：agent 和 model 都能控制启用/禁用。
- 默认可启用，但用户可显式关闭。
- 让 provider-native cache 成为可选增强层，而不是主流程依赖。
- 首期让 Anthropic 成为第一个真正消费该能力的 provider。

**Non-Goals**

- 不重写 `amni` 的 neurons、memory recall、summary、tool offload 主逻辑。
- 不把 provider-specific 字段直接引入 `amni` 公共对象。
- 不在首期实现 fork / subagent / resume 的全链路 cache-stable 前缀复制。
- 不在首期引入完整 context usage analytics、auto-compact 或 duplicate read 分析。
- 不把 stable prefix 作为普通 system message 写回 history/memory。

## Decisions

### Decision: `amni` remains the context content producer, not the provider-aware cache layer

`amni` 继续负责回答“哪些内容应该进入上下文”，包括：

- AWORLD.md / workspace instruction
- relevant memory
- summary
- history
- policy / skills / neuron augmentation

新增能力不会替换这层逻辑，而是在其下游增加 request-time assembly。

Why:

- `amni` 已经承担 context 内容治理职责，强行把 provider cache 逻辑塞进去会扩大回归面。
- 内容生产与请求投递属于不同职责边界。

### Decision: Do not introduce a parallel `AmniContext`; inject a `PromptAssemblyProvider` beneath `amni`

本次扩展不引入新的平行 context backend，也不通过替换 `AmniContext` 的方式接入新能力。新增能力应通过 `amni` 下游可注入的 `PromptAssemblyProvider` 或等价 strategy 来实现。

推荐形态：

- `DefaultPromptAssemblyProvider`
  - 尽量贴近当前 system prompt 拼接行为
- `CacheAwarePromptAssemblyProvider`
  - 负责 stable / dynamic 分段、stable hash 复用和 `PromptAssemblyPlan` 生成

Why:

- 当前 `amni` 的主要扩展点是 processor / op / neuron 机制，而不是完整 context backend provider 替换。
- 如果引入平行 `AmniContext`，长期很容易演化为两套漂移的 context 系统。
- 在 `amni` 之下注入 assembly provider，可以最大化复用现有能力并降低回归面。

### Decision: Introduce a provider-neutral `PromptAssemblyPlan`

新增 `PromptAssemblyProvider` 输出 provider-neutral 的 `PromptAssemblyPlan`。该对象仅表达 prompt 的结构和稳定性，而不表达任何厂商专有字段。

`PromptAssemblyPlan` 包含：

- stable system sections
- dynamic system sections
- conversation messages
- tool stability hint
- stable hash / metadata

Why:

- AWorld 当前统一的是 OpenAI-style message 语义，不足以表达 cache-aware prompt 结构。
- 如果直接让 provider 从原始字符串中猜 stable/dynamic 边界，会导致职责模糊且难以测试。

### Decision: Stable prefix uses hash-based reuse, but only at request time

stable prefix 采用基于内容 hash 的自动复用策略：

- 每次请求都重新组装 stable sections 的候选内容
- hash 相同则可复用已计算结果
- hash 变化则重建

但 stable prefix 不写入 history/memory，也不作为普通 system message 持久化。

Why:

- 这样可以把“会话历史”与“可缓存前缀”彻底解耦。
- 可以避免历史持久化反向污染 assembly 逻辑。

### Decision: The feature is advanced-but-default-on, with explicit opt-out

该能力属于高级增强特性，但默认可以开启。用户必须可以在 agent/model 两层显式关闭。

实际启用条件为：

- `AgentContextConfig.context_cache.enabled == True`
- `ModelConfig.context_cache.enabled == True`
- provider path not explicitly disabled

任一层关闭，则回退为普通请求装配。

Why:

- 默认开启有利于让更优的 prompt 结构尽早成为默认行为。
- 双层配置允许 agent 级控制和模型级兜底禁用同时存在。

### Decision: Provider-native cache lowering is optional and capability-driven

provider-native cache 不是主流程前提，而是 provider 能力驱动的可选增强层。

- 默认 provider 路径必须支持把 `PromptAssemblyPlan` 折叠回普通请求。
- Anthropic 首期实现 native lowering。
- 其他 provider 首期可以完全忽略 native cache hint。

Why:

- 这样不会把普通请求路径和某个厂商的特化实现绑死。
- 也能保留未来对 OpenAI 或其他 provider 的扩展空间。

### Decision: Cache usage tokens are normalized into the common AWorld usage schema

当 provider 返回原生 prompt cache 统计时，AWorld 应将其映射为统一 usage 字段，而不是直接把厂商字段名泄漏到上层日志、history 或 hook payload。

统一字段：

- `cache_hit_tokens`
- `cache_write_tokens`

首期映射规则：

- Anthropic
  - `cache_read_input_tokens -> cache_hit_tokens`
  - `cache_creation_input_tokens -> cache_write_tokens`

Why:

- 这样日志、history、hook payload 都可以消费统一语义。
- 也为后续接入 OpenAI 或其他 provider 的 cache usage 统计保留统一出口。

### Decision: Task-completion logging should surface cache hit and write tokens when available

像 `main task ... finished` 这类任务完成日志，应和现有 token usage 一起输出 cache hit / write token 统计。

Why:

- prompt cache 的收益如果只存在 provider 原始响应里，运维和调试价值有限。
- 输出到任务完成日志可以最低成本地验证 cache 命中效果。

### Decision: Real request snapshots MUST be captured per LLM call in append-only runtime records

每次 LLM call 的真实请求快照必须以 append-only 记录写入 runtime context，而不是继续使用单值 `llm_input` / `llm_output` / `llm_call_start_time` 作为唯一真相源。

推荐形态：

- `context_info["llm_calls"] = list[LlmCallRecord]`
- 每条 record 至少包含：
  - `call_id`
  - `step_id`
  - `agent_id`
  - `started_at`
  - `request.messages`
- 旧字段 `llm_input` / `llm_output` / `llm_call_start_time` 可保留为“最后一次调用别名”以兼容现有读路径

Why:

- 同一条 message 可能触发多次 LLM call，单值字段天然会发生覆盖。
- `ContextState` 是分层 KV；如果未来改成 list 但直接原地修改继承对象，也可能污染父 context。
- append-only 调用记录可以同时支撑 observability、trajectory snapshot fidelity 和后续 provider 级调试。

### Decision: `trajectory.log` should prefer per-call request snapshots, not post-hoc reconstructed memory

`trajectory.log` 的 prompt 快照应优先来自逐次捕获的 `llm_calls[*].request.messages`，而不是默认从 memory 进行事后重建。

首期约束：

- trajectory 不要求承载 `cache_hit_tokens` / `cache_write_tokens`
- trajectory 关注的是“真实发给模型的 request messages”
- 仅当调用快照缺失时，才允许退回 memory reconstruction 作为兼容 fallback

Why:

- memory reconstruction 无法保证与真实请求完全一致，特别是在引入 cache-aware assembly 后更容易漂移。
- trajectory 训练/调试价值取决于 prompt snapshot fidelity，而不是 token observability。

### Decision: Tools are represented as a stable semantic section, but serialized by providers

assembly 层只表达 tools 属于 stable section 的语义，不定义 tools 的最终 wire format。

- `PromptAssemblyPlan` 可以包含 tools hint / fingerprint
- 具体 tools schema 序列化仍由 provider 决定

Why:

- 不同 provider 对 tools 的请求格式差异明显。
- 但从 cache 语义上，tools 通常是稳定部分，不能完全丢出 assembly 视野。

### Decision: Stable and dynamic content classification is fixed for phase 1

Phase 1 的默认分类规则如下：

Stable:

- base system rules
- AWORLD.md / workspace instruction
- stable skill / policy descriptions
- tools semantic section

Dynamic:

- relevant memory recall
- conversation history
- summary
- current task-related prompt injection

Why:

- 这组分类最接近当前内容实际变化频率。
- 可以在不改变上游内容生产逻辑的前提下，先得到最直接的 cache 友好收益。

## Architecture

### Layer 1: Content production

现有 `amni` 负责生成可被 prompt 消费的内容：

- `system_prompt_augment_op`
- neurons
- memory recall / summary
- tool result offload

这一层不关心 provider cache，也不需要理解 provider-specific 降级语义。

### Layer 2: Prompt assembly provider

新增可注入的 `PromptAssemblyProvider`，负责：

- 接收上游 system prompt 与 augment 内容
- 将内容拆分为 stable / dynamic sections
- 生成 `PromptAssemblyPlan`
- 计算 stable hash
- 维护 request-time runtime state

这一层仍然完全 provider-neutral。默认 provider 保持接近当前行为，cache-aware provider 才开启结构化分段。

### Layer 3: Provider lowering

provider capability / lowerer 负责：

- 声明当前 provider 是否支持 native prompt cache
- 将 `PromptAssemblyPlan` 翻译为 provider 请求
- 或在不支持时折叠回普通请求

Anthropic 首期实现 native lowering；默认 lowerer 必须始终可用。

## Planned Objects

### `ContextCacheConfig`

放在 `AgentContextConfig` 和 `ModelConfig` 两侧。

建议字段：

- `enabled: bool = True`
- `allow_provider_native_cache: bool = True`

### `PromptAssemblyProvider`

用于将 `amni` 产出的内容装配成 request-time prompt plan 的可注入 provider/strategy。

建议职责：

- 读取 system prompt 与 augment 内容
- 输出 `PromptAssemblyPlan`
- 管理 stable hash 相关 runtime state
- 在禁用增强能力时回退到默认装配路径

### `PromptSection`

统一描述一个 section。

建议字段：

- `name: str`
- `kind: Literal["system", "tools_hint"]`
- `stability: Literal["stable", "dynamic"]`
- `content: str | None`
- `hash: str | None`

### `ToolSectionHint`

表达 tools 的稳定性语义，不承载最终 schema。

建议字段：

- `stable: bool = True`
- `tool_names: list[str]`
- `tool_fingerprint: str`

### `PromptAssemblyPlan`

provider-neutral 的 request-time 中间对象。

建议字段：

- `stable_system_sections: list[PromptSection]`
- `dynamic_system_sections: list[PromptSection]`
- `conversation_messages: list[dict]`
- `tool_section: ToolSectionHint | None`
- `stable_hash: str`
- `metadata: dict[str, Any]`

### `PromptAssemblyRuntimeState`

轻量 request/session scoped 状态。

建议字段：

- `last_stable_hash`
- `last_stable_plan`
- `provider_cache_eligible: bool`

### `LlmCallRecord`

runtime context 中逐次记录一次 LLM call 的最小快照对象。

建议字段：

- `call_id: str`
- `step_id: str | None`
- `agent_id: str`
- `started_at: str`
- `request: dict[str, Any]`
- `response: dict[str, Any] | None`
- `usage: dict[str, Any] | None`

### `ProviderPromptCacheCapability`

provider 能力声明。

建议字段：

- `supports_native_prompt_cache: bool`
- `supports_system_block_cache: bool`
- `supports_message_cache: bool`
- `supports_tool_cache_hint: bool`

### `NormalizedTokenUsage`

在现有通用 usage 字段基础上扩展 cache 相关统计。

建议字段：

- `prompt_tokens: int`
- `completion_tokens: int`
- `total_tokens: int`
- `cache_hit_tokens: int`
- `cache_write_tokens: int`

## File Touch Points

首期文件落点建议如下：

- `aworld/core/context/amni/config.py`
  - 增加 `ContextCacheConfig`
- `aworld/config/conf.py`
  - 给 `ModelConfig` 增加 `context_cache`
- `aworld/core/context/amni/prompt/assembly/`
  - `plan.py`
  - `provider.py`
  - `default_provider.py`
  - `cache_aware_provider.py`
  - `state.py`
  - `hash_utils.py`
- `aworld/core/context/amni/processor/op/system_prompt_augment_op.py`
  - 做薄接线改造，调用注入的 `PromptAssemblyProvider`
- `aworld/models/`
  - `prompt_cache_capability.py`
  - `prompt_plan_lowerer.py`
  - `anthropic_provider.py`

## Testing Strategy

### 1. Assembly builder tests

- stable/dynamic 分类正确
- stable hash 稳定
- stable 内容变化时 hash 变化
- `PromptAssemblyPlan` 不含 provider-specific 字段

### 2. Config tests

- agent/model 双层配置都可解析
- 任一层关闭即回退
- 默认值为开启

### 3. Provider lowering tests

- Anthropic native lowering 生效
- provider 不支持时自动 fallback
- `allow_provider_native_cache=False` 时强制 fallback
- provider cache usage 能正确归一化为 `cache_hit_tokens` / `cache_write_tokens`

### 4. Regression tests

- AWORLD.md、relevant memory、system prompt augment 语义不回归
- 普通 provider 请求仍然可用
- `event_runner` 任务完成日志继续可用，并在有 cache usage 时带上统一 cache token 字段
- 同一条 message 内多次 LLM call 时，`llm_calls` 记录不会覆盖前一次请求快照
- `trajectory.log` 在有调用快照时优先输出真实 request messages，而不是 memory reconstruction

## Risks

- 如果 `PromptAssemblyProvider` 设计不清晰，容易重新滑回到在 `system_prompt_augment_op` 中硬编码所有装配逻辑。
- 如果 provider-lowering 接口设计不清晰，后续很容易重新把 Anthropic 特化泄漏进公共层。
- 如果 stable/dynamic 分类过于激进，可能破坏现有系统提示语义。
- 如果 `llm_calls` 采用原地 mutation 而不是 copy-on-write，可能污染继承得到的父 context runtime 状态。

## Rollout

- 第一步保证 assembly 正确并始终可 fallback。
- 第二步为 Anthropic 打通 native lowering。
- 第三步根据验证结果再决定是否扩展其他 provider。
