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

## Evaluator Report Example

The file `evaluator_report.example.json` shows the current stable evaluator report contract, including:

- `report_format` and `generated_at`
- normalized `metrics` and per-case `results`
- structured `gate`, `approval`, and `automation` sections

Use it together with `aworld-cli evaluator --print-report-schema` and `aworld-cli evaluator --validate-report <file>` when integrating evaluator output into scripts or CI.

## Declared Evaluator Suite Example

The file `declared_evaluator_suite.example.json` shows the workspace manifest format loaded from `.aworld/evaluators/*.json`.

Use it when you want to derive a stricter evaluator from `app-evaluator` while keeping AWorld's builtin runner, suite resolution, and report contract unchanged.

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
