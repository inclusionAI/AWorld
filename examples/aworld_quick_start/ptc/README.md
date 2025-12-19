# Programmatic Tool Calling (PTC)

## The Problem Every Agent Developer Faces

**You want your agent to query weather for 6 cities. What happens?**

### Without PTC: The Context Overflow Nightmare

```
Task: Query weather for 6 cities (Beijing, Nanjing, Hangzhou, Guangzhou, Shenzhen, Urumqi)

Step 1: Call browser_navigate() → 18,987 tokens in context
Step 2: Call browser_type("北京") → 18,189 tokens in context  
Step 3: Call browser_snapshot() → 25,672 tokens in context
Step 4: Extract Beijing weather data
Step 5: Call browser_navigate() again → More tokens...
Step 6: Call browser_type("南京") → Even more tokens...
...

⚠️ After 2 cities: Context is 70% full (140k/200k tokens)
❌ Agent gives up: "Due to context limits, I can only provide estimates for remaining cities"
```

**Result**: Only 2/6 cities completed. Agent had to **guess** the weather for the other 4 cities.

### With PTC: One Script, All Cities, Perfect Results

```python
# Agent generates this code and executes it once
cities = ["北京", "南京", "杭州", "广州", "深圳", "乌鲁木齐"]
results = {}

for city in cities:
    await browser_navigate(url=f"https://weather.com.cn/.../{city_code}")
    snapshot = await browser_snapshot()
    weather = extract_weather(snapshot)  # Process in code
    results[city] = weather

return results  # Only final results go back to LLM
```

**Result**: All 6 cities completed. Context usage: **38.5%** (77k/200k tokens).

## Why This Matters

| Problem | Without PTC | With PTC |
|---------|-------------|----------|
| **Context Overflow** | ❌ 70% full after 2 cities | ✅ 38.5% after all 6 cities |
| **Task Completion** | ❌ 33% (2/6 cities) | ✅ 100% (6/6 cities) |
| **Token Cost** | 💸 874,631 tokens | 💰 390,630 tokens (**55% savings**) |
| **Reliability** | ❌ Must estimate/guess | ✅ Real data for all cities |

**The Magic**: Tools are injected as Python functions. All processing happens in a sandbox. Only final results return to the LLM context.


## How to Use

### Enable PTC

Specify which tools can be used in PTC scripts when creating your agent:

```python
agent = Agent(
    name="my_agent",
    mcp_config=MCP_CONFIG,
    mcp_servers=["ms-playwright"],
    ptc_tools=["browser_navigate", "browser_snapshot", "browser_type"]  # Enable PTC for these tools
)
```

## Core Idea

Instead of calling tools one by one (each response fills your context), PTC lets you:

1. **Generate Python code** that calls tools programmatically
2. **Execute the code in a sandbox** where tools are available as functions
3. **Process everything in code** - filter, aggregate, transform
4. **Return only the final result** - keeping your context clean

This is inspired by [Anthropic's Programmatic Tool Calling](https://www.anthropic.com/engineering/advanced-tool-use) technology.

## How It Works: Visual Comparison

```
┌─────────────────────────────────────────────────────────┐
│  WITHOUT PTC: Every tool response fills your context    │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  LLM Context                                            │
│  ┌──────────────────────────────────────────────────┐  │
│  │ Tool Call 1 → 18k tokens                         │  │
│  │ Tool Call 2 → 18k tokens                         │  │
│  │ Tool Call 3 → 25k tokens                          │  │
│  │ ...                                                │  │
│  │ ⚠️ 70% Full → Can't continue                      │  │
│  └──────────────────────────────────────────────────┘  │
│                                                          │
│  ❌ Context overflow after 2 cities                      │
│  ❌ Must guess remaining cities                          │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  WITH PTC: Tools execute in code, only results return   │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  LLM Context                                            │
│  ┌──────────────────────────────────────────────────┐  │
│  │ Generate code → Execute → Final result          │  │
│  │ ✅ 38.5% Used                                    │  │
│  └──────────────────────────────────────────────────┘  │
│                    │                                    │
│                    ▼                                    │
│  Sandbox (Isolated)                                     │
│  ┌──────────────────────────────────────────────────┐  │
│  │ # Tools injected as Python functions            │  │
│  │ for city in cities:                             │  │
│  │   data = await browser_snapshot()  # In sandbox │  │
│  │   process(data)                   # In sandbox │  │
│  │ return summary                    # Only this   │  │
│  └──────────────────────────────────────────────────┘  │
│                                                          │
│  ✅ All 6 cities processed in sandbox                    │
│  ✅ Only final summary returns to context                │
└─────────────────────────────────────────────────────────┘
```

**Key Insight**: Tools become Python functions. All processing happens in the sandbox. Your context stays clean.

## Real Results

**From the weather query example (6 cities):**

| Metric | Without PTC | With PTC |
|--------|-------------|----------|
| **Token Cost** | 874k tokens | 390k tokens (**55% less**) |
| **Context Usage** | 70% full | 38.5% used |
| **Cities Completed** | 2/6 (33%) | 6/6 (100%) |
| **Result Quality** | Had to guess 4 cities | Real data for all cities |

**Bottom line**: PTC saves 55% on costs, completes 100% of tasks, and keeps your context clean.


## Best Practices

1. **Always explore first**: Use regular tool calls to understand data structures before writing PTC code
2. **Handle different return types**: Tools might return `list`, `dict`, or `string` - always check!
3. **Process in code**: Filter, aggregate, and transform data in the sandbox, not in context
4. **Return summaries**: Only send final processed results back to the LLM

## Learn More

- **Implementation**: See `aworld/experimental/ptc/` for the code
- **Examples**: Check `examples/ptc/` for usage examples
- **Inspiration**: Based on [Anthropic's Programmatic Tool Calling](https://www.anthropic.com/engineering/advanced-tool-use)

