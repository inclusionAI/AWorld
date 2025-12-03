from aworld.core.context.amni import ApplicationContext
from aworld.core.context.amni.prompt.neurons import Neuron, neuron_factory


PTC_NEURON_PROMPT = """
<ptc_guide>
## Programmatic Tool Calling (PTC)

**PTC** allows you to orchestrate multiple tool calls through Python code execution instead of individual API round-trips.

### Core Idea

Instead of calling tools one by one and processing results in natural language, you should:
1. **Generate Python code** that calls tools programmatically
2. Wrap this code in a **single tool call** to the `PTC` tool (action name: `execute_ptc_code`)
   - Put the full Python script into the `code` field of the `PTC` tool call
3. Let the `PTC` tool execute the script, which:
   - Calls other tools (e.g., `read_file`) inside the code
   - Processes intermediate results in code (filter, aggregate, transform)
   - Returns only the final processed output

This keeps intermediate results out of your context window, saving tokens and reducing reasoning cycles, and makes it explicit that PTC work is done **via the `PTC` tool's tool_calls**, not by executing code directly in the chat.

### Identifying PTC-Compatible Tools

Tools that can be used in PTC scripts are marked with `[allow_code_execution]` at the beginning of their description. When you see this marker, you can use that tool within a Python script.

### When to Use PTC

**IMPORTANT: Do NOT write PTC code immediately!**

Before writing PTC code, you must:
1. **Explore first**: Use regular tool calls (NOT PTC) to understand:
   - What data structure each tool returns (dict, list, string, etc.)
   - What fields/keys are available in the response
   - How the website/page structure looks (for browser tools)
   - What selectors/patterns work reliably
2. **Then write PTC**: Only after you've seen 2-3 real examples of tool responses, write PTC code

Use PTC when you need to:
- Process large files/datasets where only summaries/patterns are needed
- Chain multiple tool calls with complex data transformations
- Filter, aggregate, or combine results from multiple tools
- Perform iterative operations on tool results

**Common mistake**: Writing PTC code that assumes `snapshot.get('content')` when `browser_snapshot()` actually returns a list. Always check the actual return type first!

### Example

**Step 1: First explore (use regular tool calls, NOT PTC)**
```
Call: read_file(path="large_log.txt")
Observe: Returns a string with file content
```

**Step 2: Then write PTC code (after understanding the data structure)**
```python
# Instead of loading entire file into context, process it programmatically
log_data = await read_file(path="large_log.txt")

# ALWAYS normalize tool result - handle different return types
if isinstance(log_data, list):
    # Many MCP tools return list content, join them into a single string
    log_text = "\\n".join(str(item) for item in log_data)
elif isinstance(log_data, dict):
    # Some tools return dict, extract content field or convert to string
    log_text = log_data.get('content', str(log_data)) if isinstance(log_data.get('content'), str) else str(log_data)
else:
    log_text = str(log_data) if log_data is not None else ""

# Process in code - extract only error patterns
errors = []
for line in log_text.split('\\n'):
    if "ERROR" in line:
        errors.append(extract_error_info(line))

# Return only the summary, not the entire log
result = {
    "error_count": len(errors),
    "errors": errors[:50],  # truncate if needed
}
```

**For browser tools example:**
```python
# First explore: browser_snapshot() returns a LIST, not a dict!
# So DON'T do: snapshot.get('content')  ❌
# Instead do:
snapshot = await browser_snapshot()

# Handle list return type
if isinstance(snapshot, list):
    # snapshot is a list, process each item or join them
    snapshot_text = "\\n".join(str(item) for item in snapshot)
elif isinstance(snapshot, dict):
    # If it's a dict, extract content
    snapshot_text = snapshot.get('content', str(snapshot))
else:
    snapshot_text = str(snapshot)
```

### Best Practices

- **Explore first**: Always use regular tool calls to understand return types before writing PTC code
- **Normalize results**: Always check and handle different return types (list, dict, string) - never assume!
- **Filter early**: Process and filter data in code before returning
- **Return summaries**: Return processed results, not raw intermediate data
- **Use explicit logic**: Write clear conditional logic and loops
- **Handle errors**: Include error handling for robust execution

### Common Pitfalls to Avoid

1. **Assuming return types**: Don't write `snapshot.get('content')` without first checking if `browser_snapshot()` returns a dict or list
2. **Skipping exploration**: Don't write PTC code for websites you haven't actually visited and inspected
3. **Hardcoding selectors**: Don't assume CSS selectors work without testing them first
4. **Ignoring data structure**: Always normalize tool results - they might be list, dict, or string
</ptc_guide>
"""
PTC_NEURON_NAME = "ptc"
@neuron_factory.register(name=PTC_NEURON_NAME, desc="PTC Neuron", prio=3)
class PtcNeuron(Neuron):


    async def desc(self, context: ApplicationContext, namespace: str = None, **kwargs) -> str:
        return PTC_NEURON_PROMPT

