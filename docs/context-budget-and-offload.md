# Context budget、AMNI offload 与历史压缩

上下文治理分层执行，各层的阈值有不同含义：

1. Tool 执行边界在结果进入历史之前处理大输出。默认 inline 预算为 4096 tokens，超出时持久化完整 artifact，向模型提供有界视图、引用和 checksum。可通过 Context 的 artifact 读取接口回读完整内容。
2. AMNI 的 `ToolResultOffloadOp` 继续按开关、白名单和长度阈值处理尚未 offload 的结果。已有可校验、可恢复 Tool boundary 的结果不重复 offload；memory compaction 也保留这个契约。
3. Adaptive checkpoint 的历史压缩清理较早的轮次，保留系统指令、原始任务、近期消息和最新完整 assistant/tool 组。它不是任意长消息的通用摘要器，也不会改写工具调用参数。
4. Final compiler 汇总模型请求并分配总输入预算，可选项按策略选择。默认 enforce/adaptive 模式下，如果扣除输出、协议和安全预留后的必需项仍超预算，模型运行层启动有界恢复，再重新编译完整请求；纯预算分配器不执行 I/O。

`context_compiler.max_item_tokens` 默认从 `10000` 改为 `null`。旧默认与上面的保护策略冲突：最新 assistant 消息含有超过 10K tokens 的工具参数时，工具结果 offload 无法缩短它，历史压缩也不能丢掉它，但 final compiler 会在总预算充足时依然拒绝发送。现在这类消息在总预算内完整保留，tool-call ID、参数和配对结果不变。

需要单项硬契约的调用方仍可显式设置正整数，例如：

```yaml
context_compiler:
  mode: enforce
  max_item_tokens: null
  default_tool_output_inline_tokens: 4096
  artifact_offload: true
  checkpoint_policy: adaptive
```

配置正整数 `max_item_tokens` 后，该限制与 owner 的 `ContextItem.token_limit` 取较小值；只设置 owner 限制时仍执行 owner 限制。显式限制下，已有纯文本 system/user/developer 无损分段逻辑继续生效；不可分割的必需项超限仍报 `required_item_token_limit_exceeded`。`null` 不代表可以超过总模型窗口。

另一个冲突发生在格式转换层：`MemoryAIMessage.to_openai_message()` 和 OpenAI provider sanitizer 原先自动调用 replay 参数压缩，把长字符串替换成只有形状/hash 的占位符。这种压缩不可回读，还会在历史策略判断消息是否受保护之前丢失内容。现在这两个转换入口仅做结构规范化，保留合法 JSON 参数的原始字符串；显式调用 replay 压缩工具的行为仍保留，非法 JSON 的既有处理也不变。

最终预算恢复只响应 `required_context_budget_exceeded`，不绕过显式单项限制、权限校验或未知协议错误。它将已完成的 assistant/tool 交互整组归档到 AMNI workspace；未完成的并行调用、系统指令和用户约束不截断。归档经原有 KnowledgeService 回读验证后才替换为引用及短预览。连续归档引用可再次合并，避免长期运行时引用本身不断撑大窗口。最多四次缩减，每次存储恢复最多 15 秒；每次都重新编译实际消息、工具目录和 trust envelope，不重新执行工具或额外调用模型。

归档内容按 512 字符分行保存，读取 recovery artifact 时每次最多四行。使用 `KNOWLEDGE__get_knowledge_by_lines` 分页取回即可，拼接数据行可恢复完整 JSON。普通知识 artifact 的读取行为不变。恢复引用与回读工具在最终编译中同时保留，不能只留下不可读取的引用。

替换映射写入任务运行状态以及 AMNI WorkingState，并保存轻量 checkpoint；下一轮 Memory 重放、Context 副本以及从 WorkingState 恢复时复用同一引用。恢复只改变动态历史，不改系统前缀、不临时扩充工具目录，checkpoint 使用 `cache_boundary=False`，保留既有 cache epoch 和原生 cache control。前面的 adaptive checkpoint、已有 AMNI 摘要/offload 配置继续按原有策略执行，恢复不引入另一轮模型摘要调用。

CLI 默认 Aworld Agent 开启 `automated_cognitive_ingestion`，提供 workspace 知识保存、列举、查询、按行读取等工具及对应提示。它不扫描或上传本机文件，也不自动开启 TODO 编排；TODO 提示受 `automated_reasoning_orchestrator` 控制。对于没有开启完整知识能力、但启用了 adaptive 恢复的 AMNI Agent，首轮只提供按行回读动作，并固定在 progressive 工具目录中。显式的工具黑名单与 instruction-only 限制仍生效。

关闭 artifact offload、使用 explicit checkpoint 策略、没有可用回读工具、存储/校验失败，或剩余不可缩减内容仍超预算时，保留明确的预算错误。知识工具开放后，工具目录会比旧版更大；部署切换时可能发生一次缓存前缀变化。测试验证前缀身份和 native cache control 的保留，实际命中率与任务质量仍需部署后衡量。

验证覆盖：默认 ModelConfig 到 provider 发送的同步、异步和 streaming 路径；真实本地 AMNI workspace 存储和分页回读；Memory 重放与 WorkingState 恢复；连续多轮超预算归档；稳定前缀、工具目录及 Anthropic 原生缓存；未完成调用、显式限制和存储故障。修改生效需要重新构建 AWorld 与 CLI wheel，并更新使用它们的 Runtime 镜像。
