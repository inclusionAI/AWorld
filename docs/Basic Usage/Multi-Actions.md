Here is the English version of the **Agent Multi-actions** documentation, maintaining the style and tone of the `Streaming Response.md` guide.

---

# Agent Multi-actions

In complex task scenarios, an Agent may need to perform multiple operations within a single reasoning step. **AWorld** supports **Multi-actions**, allowing the Agent to output multiple `tool_calls` in one LLM response and execute them efficiently in parallel.

### Overview
A **Multi-actions** scenario occurs when the LLM returns a list containing more than one `tool_call`. Each `tool_call` is treated as an independent `Action`.

The AWorld Runtime automatically identifies these actions and applies the optimal execution strategy based on their type (e.g., a basic function or another sub-agent), typically running them **concurrently** to minimize total latency.

### Action Types
In a Multi-actions context, actions generally fall into two categories:

| Type | Description | Execution Logic |
| :--- | :--- | :--- |
| **Function Tool** | Basic functions or API calls. | Handled by `DefaultToolHandler`, invoking the tool's `do_step` method. |
| **Agent as Tool** | Calling another Agent as if it were a tool. | Handled by `GroupHandler`, supporting independent sub-agent contexts and reasoning. |

### Execution Mechanism
AWorld uses the `GroupHandler` to orchestrate the execution of multiple actions:

1. **Identification & Classification**: The system splits the `tool_calls` list into "Standard Tools" and "Sub-Agents."
2. **Parallel Scheduling**:
    *   **Sub-Agents**: Initiates multiple asynchronous tasks via `_parallel_exec_agents_actions`. Each sub-agent runs in its own environment.
    *   **Function Tools**: Batched and sent to the tool handler for processing.
3. **Result Aggregation**: Once all actions complete, the `DefaultGroupHandler` collects all `ActionResult` or `Observation` data and merges them back into the main Agent's memory and context.

### Quick Reference: Handling Multi-action Events
When using the `streaming_run` interface, you can detect Multi-actions via the `ToolMessage`:

```python
from aworld.core.event.base import ToolMessage
from aworld.core.common import ActionModel

async for msg in Runners.streaming_run(input="Search for weather in London and Paris simultaneously", agent=agent):
    if isinstance(msg, ToolMessage):
        # payload is a list of ActionModel objects
        actions = msg.payload
        if len(actions) > 1:
            print(f"\n[Multi-actions Detected]: Executing {len(actions)} tasks in parallel...")
            for action in actions:
                print(f" - Executing: {action.tool_name} (params: {action.params})")
```

### Deep Dive: Group Execution
For **Agent as Tool** scenarios, AWorld provides advanced management features (implemented in `aworld/runners/handler/group.py`):

*   **Isolation**: Each sub-agent is executed as a copy, ensuring that concurrent operations do not interfere with each other's state.
*   **Context Merging**: Upon completion, the `trajectory` and `context` of sub-agents are merged into the parent task, allowing the main Agent to stay informed about all sub-steps.
*   **State Tracking**: A `Group` node is created in the `RuntimeStateManager`, allowing users to track the overall status of the parallel execution block.

### Best Practices
1. **Leverage Parallelism**: Encourage the model to output Multi-actions for independent tasks (e.g., fetching data from multiple sources) to significantly improve performance.
2. **Thread Safety**: Ensure your `BaseTool` implementations (see `aworld/core/tool/base.py`) are asynchronous or thread-safe to handle concurrent execution.
3. **Token Management**: Be mindful that multiple tool results increase the context size. Ensure your model's `max_tokens` limit is sufficient to accommodate the aggregated results.