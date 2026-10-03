# AWorld 1.0：复用原 LLM Provider 与待解耦项

决策日期：2026-10-01。用户明确要求先继续使用原 provider，将耦合问题记录下来；本轮不重构这些依赖，也不修改 README。

## 当前边界

新 Agent 继续依赖 `complete(ModelRequest) -> AssistantMessage`。`ProviderModel` 只转换消息、工具 schema 和响应，调用已有 provider 的 `acompletion()`。CLI 默认构造原 `aworld.models.openai_provider.OpenAIProvider` 的 async SDK 路径。请求准备、SDK、TCP keepalive 和模型通信重试均复用原实现；适配层没有第二套 HTTP 客户端或外层重试循环。

默认 `max_retries=3`（首次请求之外最多重试 3 次），可通过 CLI `--max-retries` 设置。原 `AWORLD_SELF_EVOLVE_DISABLE_PROVIDER_RETRIES=1` 仍会将 SDK 重试关闭。本轮保留原行为。重试只发生在未完成的模型请求内，已完成的工具执行不回放；Session/Run 的取消与总预算仍约束调用。

## 已确认的耦合（后续处理）

| 耦合 | 当前代码证据 | 影响 |
| --- | --- | --- |
| 旧 Context | `core/llm_provider.py` 导入 `core/context/base.py` | provider 的导入会加载旧 Context、checkpoint、轨迹与调用 journal 定义，即使新 Session 不使用它们 |
| Context Compiler | provider/base 导入 compiler；`_prepare_chat_completion_request()` 支持 candidate、lowering、attribution、cache receipt | 模型请求准备与旧上下文治理契约相互依赖；当前适配层不传入旧 Context/candidate |
| Tokenizer / Memory 辅助 | compiler 依赖 `memory/tool_call_compaction.py`，后者导入 `models/utils.py` 及两个 tokenizer | provider 导入带来 numpy/tiktoken 和两个本地词表；这不代表新 Session 开启旧 Memory |
| 缓存 | `models/prompt_cache.py` 与 provider 的 cache lowering / request preparation | 缓存策略与通信实现混合；本轮不启用新的缓存策略，也不修改原行为 |
| 日志 / Trace | `logs/util.py`、`trace/__init__.py`、调用记录 | 导入会初始化日志，并带入 instrumentation、metrics、OpenTelemetry/FastAPI；日志级别与敏感响应治理后续统一处理 |
| 自动安装辅助 | tokenizer 调用 `utils.import_package()`；HTTP handler 构造时调用 `import_package("aiohttp")` | 缺失依赖可能触发安装。新版默认使用 async SDK 路径，进入旧导入链前预检依赖，缺失时提示显式安装 `aworld[llm]` |

## 当前取舍与验收

为忠实复用原 provider，wheel 明确包含其实际导入依赖与本地 tokenizer 资源，`llm` extra 声明相关第三方依赖。基础 kernel/demo 不加载 provider，默认第三方依赖仍为空。CLI 独立包继续依赖同版本的 aworld。旧源码不删除。

验证应覆盖：真实原 provider 实例、SDK 请求断线重试且工具只执行一次、永久错误不重试、Run 取消、严格拒绝不完整模型输出，以及脱离源码目录安装 wheel 后的实际模型/工具往返。

后续在独立变更中将通信、请求准备、上下文治理与观测职责逐步分离。当前不以替换 SDK 或另写 HTTP 重试作为解耦方案。
