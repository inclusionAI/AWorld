# AWorld CLI Quick Start

Minimal example for creating and using AI Agents with `aworld-cli`.

## Quick Start

### 1. Setup Environment

```bash
cp env.template .env
# Edit .env and fill in your API keys
```

### 2. Run CLI

```bash
# Interactive mode
aworld-cli

# List agents
aworld-cli list

# Run a task
aworld-cli --task "Your task" --agent MyAgent
```

## Examples

- `agents/simple_agent.py` - Basic single agent
- `agents/skill_agent.py` - Agent with skills and MCP tools
- `agents/pe_team_agent.py` - Multi-agent system
- `agents/document_agent.md` - Markdown agent example
- `agents/hilp.py` - Human in the loop agent example

## Create Your Agent

### Python Agent

Create `agents/my_agent.py`:

```python
from aworld_cli.core.agent_registry import agent
from aworld.core.agent.swarm import Swarm
from aworld.agents.llm_agent import Agent

@agent(name="MyAgent", desc="My agent description")
def build_swarm():
    agent = Agent(name="my_agent", desc="My agent")
    return Swarm(agent)
```

### Markdown Agent

Create `agents/my_agent.md`:

```markdown
---
name: MyAgent
description: My agent description
---
```