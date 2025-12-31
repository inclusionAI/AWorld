这是一份参考 `Streaming Response.md` 风格编写的关于 **Agent Multi-actions (多动作执行)** 的用户文档。

---

# Agent Multi-actions (多动作执行)

在复杂的任务场景中，Agent 可能需要在单个推理步骤中同时执行多个操作。**AWorld** 支持 **Multi-actions** 特性，允许 Agent 在一次大模型（LLM）调用中输出多个 `tool_calls`，并实现高效的并发执行。

### 概览
当 Agent 调用大模型后，返回的结果中包含多个 `tool_call` 时，即触发了 **Multi-actions** 场景。每个 `tool_call` 被视为一个独立的 `Action`。

AWorld 的运行时（Runtime）会自动识别这些 Action 的类型，并根据其性质（是基础函数工具还是另一个子 Agent）采用最优的执行策略，通常是以**并行**方式运行以缩短任务总耗时。

### Action 类型
在 Multi-actions 场景中，Action 主要分为以下两类：

| 类型 | 描述 | 运行逻辑 |
| :--- | :--- | :--- |
| **Function Tool** | 基础的函数或 API 调用。 | 由 `DefaultToolHandler` 处理，调用工具的 `do_step` 逻辑。 |
| **Agent as Tool** | 将另一个 Agent 作为一个工具进行调用。 | 由 `GroupHandler` 接管，支持子 Agent 的独立上下文和推理。 |

### 执行机制
AWorld 通过 `GroupHandler` 统一调度多动作的执行，核心流程如下：

1. **识别与分类**：系统将 `tool_calls` 列表拆分为“普通工具”和“子 Agent”两组。
2. **并行调度**：
   - **子 Agent**：通过 `_parallel_exec_agents_actions` 开启多个异步任务，每个子 Agent 在自己的环境中运行。
   - **函数工具**：批量发送给工具处理器进行处理。
3. **结果聚合**：等待所有 Action 执行完毕后，`DefaultGroupHandler` 会收集所有 `ActionResult`（动作结果）或 `Observation`（观察结果），并将其合并回主 Agent 的记忆或上下文中。

### 快速参考：处理多动作结果
当你在使用 `streaming_run` 时，可以通过 `ToolMessage` 观察到多动作的触发：

```python
from aworld.core.event.base import ToolMessage
from aworld.core.common import ActionModel

async for msg in Runners.streaming_run(input="帮我同时查询北京和上海的天气", agent=agent):
    if isinstance(msg, ToolMessage):
        # payload 是一个 ActionModel 列表
        actions = msg.payload
        if len(actions) > 1:
            print(f"\n[检测到多动作]: 正在并行执行 {len(actions)} 个任务...")
            for action in actions:
                print(f" - 执行工具: {action.tool_name} (参数: {action.params})")
```

### 深度理解：Agent 组执行 (Group Execution)
对于 **Agent as Tool** 的多动作场景，AWorld 提供了更深层的管理。参考 `aworld/runners/handler/group.py` 的实现：

*   **隔离性**：每个作为工具运行的子 Agent 都会获得一份原 Agent 的拷贝，确保并发执行时状态互不干扰。
*   **上下文合并**：子 Agent 完成任务后，其产生的 `trajectory`（轨迹）和 `context`（上下文）会被合并到父任务中，确保主 Agent 能够感知到所有子步骤的细节。
*   **状态追踪**：通过 `RuntimeStateManager` 创建 `Group` 节点，用户可以追踪这一组并行任务的整体完成状态。

### 最佳实践
1. **利用并发**：对于互不依赖的操作（如同时检索多个信息源），鼓励模型输出 Multi-actions 以提升效率。
2. **工具设计**：确保你的 `BaseTool`（参考 `aworld/core/tool/base.py`）实现是线程安全或支持异步调用的，以便在 Multi-actions 场景下稳定运行。
3. **Token 限制**：注意多动作会产生更多的上下文，请确保模型的 `max_tokens` 设置能够容纳多个工具调用的返回结果。