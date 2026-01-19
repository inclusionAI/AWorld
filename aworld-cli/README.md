# AWorld CLI

Command-line interface for interacting with AWorld agents.

## Installation

```bash
# Install dependencies with uv
uv sync

# Or install with pip
pip install -e .
```

## Quick Start

### Interactive Mode

```bash
# Start interactive CLI (automatically loads built-in Aworld agent)
aworld-cli
```

### List Available Agents

```bash
aworld-cli list
```

### Run Tasks Directly

```bash
# Execute a task with built-in Aworld agent
aworld-cli --task "Your task here" --agent Aworld

# Limit number of runs
aworld-cli --task "Your task" --agent Aworld --max-runs 5

# Limit cost
aworld-cli --task "Your task" --agent Aworld --max-cost 10.00

# Limit duration
aworld-cli --task "Your task" --agent Aworld --max-duration 2h
```

### Use Custom Agents

```bash
# Specify agent directory
aworld-cli --agent-dir ./my_agents list

# Execute task with custom agent
aworld-cli --agent-dir ./my_agents --task "Your task" --agent MyAgent
```

### Use Remote Backend

```bash
# Connect to remote backend
aworld-cli --remote-backend http://localhost:8000 list
```

## Create Custom Agent

Use the `@agent` decorator to register an agent:

```python
from aworld_cli.core.agent_registry import agent
from aworld.core.agent.swarm import Swarm
from aworld.agents.llm_agent import Agent

@agent(
    name="MyAgent",
    desc="My agent description"
)
def build_my_swarm() -> Swarm:
    agent = Agent(...)
    return Swarm(agent)
```

Place the file in the directory specified by `LOCAL_AGENTS_DIR` or use `--agent-dir` parameter.

## Environment Variables

- `LOCAL_AGENTS_DIR`: Local agent directories (semicolon-separated)
- `REMOTE_AGENTS_BACKEND`: Remote backend URLs (semicolon-separated)
- `SKILLS_PATH`: Skill source paths (local directories or GitHub URLs, semicolon-separated)
- `AWORLD_DISABLE_CONSOLE_LOG`: Disable console logging (set to 'true')

## More Help

```bash
# Show help
aworld-cli --help

# Show Chinese help
aworld-cli --zh

# Show usage examples
aworld-cli --examples
```
