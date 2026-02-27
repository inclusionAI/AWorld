# Context Offload：Tool Result 落盘与访问

## 1. 目标

- 将 `tool_result` 大内容 offload 到 workspace（文件/知识库），在上下文中只保留「文件对应的地址/引用」。
- 通过统一约定（如 `context_type = "tool_result"`）支持按类型查询与访问。

## 2. 实现方式：直接保存 tool_result（不提取 artifacts）

**原则**：不再从 tool_result 中解析、提取多个 artifacts，而是**把整份 tool_result 当作一个 artifact 直接写入 workspace**。

### 2.1 流程

1. **入口**：`ToolResultOffloadOp`（`tool_result_process_op.py`）
   - 判断是否需要 offload：`_need_offload()`（白名单、`metadata.offload`、或 token 超阈值）。
   - 需要时：`_offload_tool_result()` → **用当前 tool_result 构造一个 Artifact** → 调用 `context.offload_by_workspace(artifacts=[artifact], biz_id=tool_call_id)`。
   - 用 offload 返回的「替换内容」写回 `tool_result.content`，再转成 memory。

2. **构造单个 Artifact（不调用 extract_artifacts_from_toolresult）**
   - **artifact_id**：使用 `tool_call_id` 或 `f"tool_result_{tool_call_id}"`，保证与本次 tool call 一一对应、可按 biz_id 查询。
   - **content**：`tool_result.content` 的序列化结果。若已是 `str` 则直接使用；否则用 `json.dumps(tool_result.content)` 等转为字符串存储。
   - **artifact_type**：`ArtifactType.TEXT`（或按需 `ArtifactType.JSON`）。
   - **metadata**：必须包含：
     - `context_type = "tool_result"`
     - `biz_id = tool_call_id`
     - `agent_id`、`task_id`、`session_id`
     - `tool_name`、`action_name`（来自 `tool_result`），便于列表与检索。

3. **落盘与替换内容**：`KnowledgeService.offload_by_workspace()`（`knowledge_service.py`）
   - 收到**单个 artifact**（即本次 tool_result 对应的一份内容）。
   - 为 artifact 设置 `metadata["biz_id"] = biz_id`（与上面构造时一致）。
   - 通过 `add_knowledge_list()` 写入 workspace。
   - **返回给上下文的「地址/替换内容」**：
     - 若 **content 长度 < 40K**：直接返回整段 content（小结果仍可直接在上下文中展示）。
    - 否则：返回 `<knowledge_list>` 形式的索引与摘要，内含该 artifact 的 id 及摘要信息，模型后续可以通过 `get_knowledge_by_id(artifact_id)` / `get_knowledge_by_lines(artifact_id, start_line, end_line)` / `grep_knowledge(artifact_id, pattern, ...)` 等接口按需拉取或检索完整内容。

### 2.2 「地址」的形态

- **小结果**：替换为完整 content（< 40K 时仍内联在上下文中）。
- **大结果**：替换为「单个 knowledge 的索引描述 + artifact_id 引用」，访问方式：`get_knowledge_by_id(artifact_id)`、`get_knowledge_by_lines`、`grep_knowledge` 等现有 knowledge 接口。

### 2.3 与旧方案的区别

| 项目       | 旧方案（已废弃）           | 新方案（当前）                     |
|------------|----------------------------|------------------------------------|
| 数据来源   | 从 tool_result 提取多个 artifact | 整份 tool_result 对应 1 个 artifact |
| 依赖       | `extract_artifacts_from_toolresult` | 不再依赖，op 内直接构造 Artifact   |
| 存储粒度   | 可能多 artifact / tool_call_id     | 1 artifact / tool_call_id，一一对应 |
| context_type | 需在 op 里对每个 artifact 设置   | 构造时即设 `context_type = "tool_result"` |

---

## 3. 访问约定与可选扩展

### 3.1 存储与查询约定

- **存储约定**：每个 offload 的 tool result 对应 workspace 中的一个 knowledge artifact；`metadata.context_type = "tool_result"`，`metadata.biz_id = tool_call_id`。
- **按 context_type 查询**：`query_artifacts(search_filter={"context_type": "tool_result"})`，可叠加 `task_id`、`session_id`、`biz_id` 缩小范围。
- **按 artifact 细读**：`get_knowledge_by_id(artifact_id)`、`get_knowledge_by_lines`、`grep_knowledge` 等现有接口，无需改接口。

### 3.2 可选：get_tool_result_offload_info

- 与 `get_actions_info` 类似，提供「当前有哪些已落盘的 tool result」的概览：
  - `get_tool_result_offload_info(namespace, task_id=None, session_id=None)`
  - 内部：`query_artifacts(search_filter={"context_type": "tool_result", ...})`，返回 artifact_id、biz_id、tool_name、action_name 等简要信息。
- Prompt 中可说明：通过该接口查看列表，用 `get_knowledge_by_id(artifact_id)` 查看详情。

### 3.3 日志与可观测

- offload 路径打日志时带上 `context_type`、`biz_id`（即 tool_call_id），便于排查某次 tool call 的 result 是否被正确落盘并可被查询。

---

## 4. 小结

| 项目         | 说明 |
|--------------|------|
| 实现方式     | **直接保存 tool_result**：整份内容构造为 1 个 Artifact，不提取 artifacts |
| 落盘位置     | workspace（与现有 knowledge 同一套存储） |
| 替换内容     | 小结果（< 40K）：原文；大结果：`<knowledge_list>` + 该 artifact 索引与摘要 |
| 访问方式     | `get_knowledge_by_id(artifact_id)` / `get_knowledge_by_lines` / `grep_knowledge` 等 |
| 类型约定     | 构造 artifact 时即设 `metadata["context_type"] = "tool_result"` |
| 关联键       | `artifact_id` 与 `biz_id` = `tool_call_id` 对应，可配合 `task_id`、`session_id` 筛选 |
| 可选扩展     | `get_tool_result_offload_info` 按 context_type 列出已落盘 tool result，引导按需拉取 |

完成上述实现后，即可：**将 tool_result 直接作为单个 artifact 落盘、用知识索引/地址替换原文、并通过 context_type = "tool_result" 进行访问与扩展**。
