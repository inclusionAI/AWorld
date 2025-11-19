---
name: hypercode_forge
description: 🚀 HyperCode Forge - Competitive compression engine for MCP workflows
tool_list: {"terminal-server": ["execute_command"], "filesystem-server": ["read_file"], "ms-playwright": []}
active: False
---

## 🎯 What is HyperCode Forge?

HyperCode Forge is a pattern for optimizing MCP tool usage: **combine multiple MCP tool calls into a single Python script and complete them in one shot within the code execution environment**.


### 💡 Core Idea

Traditional approach (Direct Tool Calls):
```
LLM → Tool Call 1 → Result 1 → LLM → Tool Call 2 → Result 2 → LLM → ...
```
- ❌ Every tool invocation must pass through the LLM
- ❌ Intermediate results consume a large number of context tokens
- ❌ High round-trip latency and low efficiency

Code Mode approach:
```
LLM → Generate Python code → Execution environment completes all tool calls in one run → Return the final result
```
- ✅ Only one LLM interaction needed
- ✅ Intermediate results are handled in the execution environment, consuming no context
- ✅ Loops, conditionals, and other programming constructs are available
- ✅ Token usage drops by 98.7% (per Anthropic case study)


## 🧬 HyperCode Forge Pattern

HyperCode Forge distills the whole “write code to drive tools” mindset into a repeatable playbook that compresses the time, tokens, and human back-and-forth needed to win automation tasks.
If you select this pattern, You need collection full information about MCP tool call params and generate code. Such you can use browser see the webpage structure and next use this pattern fill the form.

With HyperCode Forge, you can:
- **Compression mindset**: Collapse scattered tool invocations into a single script so the agent spends once, executes once, and delivers once.
- **Adaptive batching**: Use loops, conditionals, and local caching to process large datasets without round-tripping through the LLM.
- **Execution-native debugging**: Address intermediate issues inside the Python runtime, keeping noisy logs away from the LLM context window.
- **Strategic elasticity**: Scale from five tool calls to five hundred by changing loop parameters instead of rewriting prompts.

When a competitor still handholds each MCP call, HyperCode Forge already sealed the outcome with a compact, reproducible code artifact.

## 📝 Suitable Scenarios

### ✅ Highly suitable scenarios

1. **Multi-step MCP tool invocations**
   - Require invoking several tools in sequence
   - Intermediate results are large (documents, datasets, etc.)
   - Example: Cross-system data synchronization, batch operations

2. **Data filtering and transformation**
   - Retrieve large volumes of data from a source
   - Need filtering, aggregation, or transformation
   - Example: Extracting rows meeting certain conditions from a 10,000-row spreadsheet

3. **Loops and branching logic**
   - Need to poll and wait for a status
   - Need to iterate through a list to perform operations
   - Example: Waiting for deployment completion notifications, batch updates to records

4. **Form filling and automation**
   - Need to populate multiple fields on the same page
   - Steps are clear and predictable
   - Example: Booking systems, registration forms

### ❌ Unsuitable scenarios
- Need to adjust strategy in real time based on each step’s outcome
- Steps are highly uncertain
- Single, simple tool calls
- Search By Google、Baidu、 etc..

## Notes
1. Code Mode supports Python scripts only; other languages are not supported.
2. Ensure the generated code is based on the latest data.

## 📊 Efficiency Comparison

### Traditional approach example
```
Task: Read meeting notes from Google Drive and add them to Salesforce

Step 1: TOOL CALL gdrive.getDocument(documentId: "abc123")
        → Returns 50,000-token meeting notes (loaded into context)
        
Step 2: TOOL CALL salesforce.updateRecord(...)
        → Requires writing the 50,000 tokens back in
        
Total: ~150,000 tokens
```

### Code Mode approach
```python
# Generated code
import gdrive
import salesforce

# Processed in the execution environment without consuming LLM context
transcript = gdrive.getDocument(documentId="abc123")
salesforce.updateRecord(
    objectType="SalesMeeting",
    recordId="00Q5f000001abcXYZ",
    data={"Notes": transcript}
)
print("✅ Salesforce record updated")

Total: ~2,000 tokens (code only)
Savings: 98.7%
```

## How to call MCP (Playwright example)

```python

import asyncio

from aworld.sandbox import Sandbox

mcp_servers = ["ms-playwright"]
mcp_config = {
    "mcpServers": {
        "ms-playwright": {
            "command": "npx",
            "args": [
                "@playwright/mcp@latest",
                "--no-sandbox",
                "--cdp-endpoint=http://localhost:9222"
            ],
            "env": {
                "PLAYWRIGHT_TIMEOUT": "120000",
                "SESSION_REQUEST_CONNECT_TIMEOUT": "120",
            },
        }
    }
}


async def call_ctrip_flight():
    sandbox = Sandbox(
        mcp_servers=mcp_servers,
        mcp_config=mcp_config,
    )

    result = await sandbox.mcpservers.call_tool([
        {
            "tool_name": "ms-playwright",
            "action_name": "browser_click",
            "params": {"element": "International · Hong Kong/Macau/Taiwan flights option (国际·港澳台机票选项)", "ref": "e92"}
        }
    ])
    print(f"browser_click -> {result}")


    result = await sandbox.mcpservers.call_tool([
        {
            "tool_name": "ms-playwright",
            "action_name": "browser_click",
            "params": {"element": "One-way option (单程选项)", "ref": "e327"}
        }
    ])
    print(f"browser_click one-way option -> {result}")


    result = await sandbox.mcpservers.call_tool([
        {
            "tool_name": "ms-playwright",
            "action_name": "browser_click",
            "params": {"element": "Destination input field (目的地输入框)", "ref": "e344"}
        }
    ])
    print(f"browser_click destination input -> {result}")


    result = await sandbox.mcpservers.call_tool([
        {
            "tool_name": "ms-playwright",
            "action_name": "browser_click",
            "params": {"element": "Enter country/region/city/airport (输入国家/地区/城市/机场)", "ref": "e340"}
        }
    ])



if __name__ == '__main__':
    asyncio.run(call_ctrip_flight())
```

- **HTTP headers**: When generating Python network requests, add default headers such as `User-Agent` and timeouts to avoid server rejection.
  ```python
  import urllib.request

  url = "xxx"
  req = urllib.request.Request(
      url,
      headers={"User-Agent": "Mozilla/5.0"}
  )
  with urllib.request.urlopen(req, timeout=30) as resp:
      content = resp.read()
  ```
