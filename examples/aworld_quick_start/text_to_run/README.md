# Text-to-Swarm Guide

## Overview

This example demonstrates how to use natural language to automatically generate multi-agent systems. The `text_to_swarm` API leverage **SwarmComposerAgent** to convert natural language descriptions into executable agent teams.

## Key Concepts

**`text_to_swarm`**: Generate a **reusable** Swarm from natural language
   - Returns a Swarm instance that can be used for multiple different tasks
   - Best for scenarios where you need the same agent team for different queries
   - Most efficient for repeated execution


**Benefits**:
- ✅ Swarm is generated only once (saves time and costs)
- ✅ Same agent team structure for consistent execution

## Quick Start

### Prerequisites

1. Copy `.env_template` to `.env` in the `examples/aworld_quick_start/` directory
2. Fill in the required environment variables:
   ```bash
   LLM_PROVIDER=openai
   LLM_MODEL_NAME=gpt-4o
   LLM_API_KEY=your-api-key
   LLM_BASE_URL=https://api.openai.com/v1
   
   # Optional: for MCP tool examples
   TAVILY_API_KEY=your-tavily-key
   ```

### Run the Examples

```bash
# From the project root
python examples/aworld_quick_start/text_to_run/run.py
```

By default, only Example 1 runs. Uncomment other examples in `main()` to run them.

## Examples Explained

### Example 1: Basic text_to_swarm Usage

**Scenario**: Create a reusable stock analysis team.

**Code**:
```python
# Generate swarm once
swarm = await Runners.text_to_swarm(
    query="Create a stock analysis team with data collector, analyst, and risk assessor"
)
```

**Key Learning**: Swarm reuse is efficient for repeated execution with the same team structure.

---

### Example 2: text_to_swarm with Predefined Agents

**Scenario**: Provide a custom agent with special capabilities (e.g., Tavily MCP for web search).

**Code**:
```python
# Create specialized agent
search_agent = Agent(
    name="WebSearchAgent",
    mcp_servers=["tavily-mcp"],
    mcp_config={...}
)

# SwarmComposerAgent decides whether to use it
swarm = await Runners.text_to_swarm(
    query="Create research team that searches web",
    available_agents={"web_search_agent": search_agent}
)
```

**Key Learning**: You can inject pre-configured agents with specific tools/MCPs.

---

### Example 3: text_to_swarm with Available Tools

**Scenario**: Limit which tools the generated agents can use.

**Code**:
```python
swarm = await Runners.text_to_swarm(
    query="Create data analysis team",
    available_tools=['calculator', 'python_repl']  # Only these tools
)
```

**Key Learning**: Control tool availability for security or resource management.

## API Reference

### text_to_swarm

```python
async def text_to_swarm(
    query: str,
    *,
    swarm_composer_agent: 'SwarmComposerAgent' = None,
    skills_path: Union[str, Path] = None,
    available_agents: Dict[str, BaseAgent] = None,
    available_tools: List[str] = None,
    mcp_config: Dict[str, Any] = None,
    context_config: Optional[AmniContextConfig] = None,
    **swarm_overrides
) -> Swarm
```

**Parameters**:
- `query`: Natural language description of the team structure or task requirements
- `swarm_composer_agent`: Custom SwarmComposerAgent (optional, uses default if None)
- `skills_path`: Path to skills directory for scanning available skills
- `available_agents`: Dict of predefined agents `{agent_id: agent_instance}`
- `available_tools`: List of available tool names
- `mcp_config`: Global MCP server configurations
- `**swarm_overrides`: Override swarm configs (max_steps, event_driven, etc.)

**Returns**: Reusable Swarm instance


## Behind the Scenes

### What text_to_swarm Does

1. **Planning Phase**: SwarmComposerAgent analyzes the query
2. **YAML Generation**: Generates complete agent + swarm configuration
3. **Swarm Building**: Parses YAML and constructs Swarm instance
4. **Returns**: Reusable Swarm ready for Task creation

### Generated YAML Structure

```yaml
agents:
  - id: agent_1
    name: DataCollector
    system_prompt: "You collect data..."
    type: skill  # or 'predefined'
    skill_name: search_skill
  
  - id: agent_2
    name: Analyst
    system_prompt: "You analyze data..."

swarm:
  type: workflow
  order: [agent_1, agent_2]
```

### SwarmComposerAgent Decision Making

The SwarmComposerAgent intelligently decides:
- **How many agents** are needed
- **What roles** each agent should have
- **Which tools/skills** to assign
- **What topology** to use (workflow, handoff, team)
- **Whether to use** predefined agents or create new ones

## Performance Considerations

### Swarm Generation Cost

- **text_to_swarm**: 1 SwarmComposerAgent call (generates YAML)
- **Swarm parsing**: Negligible overhead

### Swarm Reuse Benefits

For 10 queries with the same team:
- **Without reuse**: 10 SwarmComposerAgent calls
- **With reuse**: 1 SwarmComposerAgent call + 9 Task creations
- **Savings**: ~90% planning cost reduction

### Recommendation

- ✅ Use swarm reuse for batch processing
- ✅ Use swarm reuse for similar query types
- ⚠️ Generate new swarm if requirements change significantly

## Troubleshooting

### Issue: SwarmComposerAgent generates unexpected team structure

**Solution**: Provide more specific query description
```python
# Too vague
query = "Help me with data"

# Better
query = "Create a team with: 1) web searcher, 2) data analyst, 3) report writer"
```

---

### Issue: Predefined agents not used

**Solution**: Ensure agent description matches query requirements
```python
# Make sure agent desc clearly states its capabilities
agent = Agent(
    name="SearchAgent",
    desc="Web search specialist using Tavily API"  # Clear description
)
```


## Advanced Usage

### Custom SwarmComposerAgent

Create a SwarmComposerAgent with custom prompts or model:

```python
from aworld.agents.swarm_composer_agent import SwarmComposerAgent
from aworld.config import ModelConfig

custom_composer = SwarmComposerAgent(
    model_config=ModelConfig(
        llm_model_name="gpt-4o",
        llm_temperature=0.0
    ),
    # Add custom system prompt if needed
)

swarm = await Runners.text_to_swarm(
    query="Create team",
    swarm_composer_agent=custom_composer
)
```

### Global MCP Configuration

Share MCP servers across all agents:

```python
mcp_config = {
    "mcpServers": {
        "shared-mcp": {
            "command": "npx",
            "args": ["-y", "some-mcp@1.0.0"],
            "env": {"API_KEY": "..."}
        }
    }
}

swarm = await Runners.text_to_swarm(
    query="Create team",
    mcp_config=mcp_config  # All agents can access this MCP
)
```

### Context Configuration

Provide custom context configuration for task execution:

```python
from aworld.core.context.amni.config import AmniContextConfig

context_config = AmniContextConfig(
    max_turns=20,
    memory_type="redis"
)

task, results = await Runners.text_to_run(
    query="Complex task",
    context_config=context_config
)
```

## Comparison with Other Approaches

### vs. Manual Agent Definition

| Aspect | Manual | text_to_swarm |
|--------|--------|---------------|
| Flexibility | ✅ Full control | ⚠️ Depends on SwarmComposerAgent |
| Speed | ❌ Slow to write | ✅ Fast prototyping |
| Maintenance | ❌ High effort | ✅ Low effort |
| Best for | Production systems | Rapid development, experimentation |

### vs. YAML Configuration

| Aspect | YAML Config | text_to_swarm |
|--------|-------------|---------------|
| Readability | ✅ Structured | ✅ Natural language |
| Version control | ✅ Easy to track | ⚠️ Generated output |
| Dynamic generation | ❌ Static | ✅ Dynamic based on query |
| Best for | Stable workflows | Adaptive workflows |
