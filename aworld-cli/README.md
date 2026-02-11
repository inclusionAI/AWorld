# AWorld CLI

AWorld CLI is a command-line tool for interacting with AWorld agents.

## Features

- **Interactive CLI**: Rich terminal interface for agent interaction
- **Agent Discovery**: Automatic discovery of agents using `@agent` decorator
- **Built-in Agents**: Automatically loads built-in agents from `inner_plugins/*/agents` directories (no configuration required)
- **Multiple Sources**: Support for local and remote agents
- **Streaming Output**: Real-time streaming of agent responses
- **Agent Priority**: Built-in agents → Local agents → Remote agents


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


## Command-Line Interface

### Interactive Mode

```bash
# Start interactive mode (automatically loads built-in Aworld agent)
aworld-cli
```

### List Agents

```bash
# List all available agents (including built-in agents)
aworld-cli list

# Example output:
# 📦 Loading built-in agents from: .../inner_plugins/smllc/agents
# 📚 Loaded 2 global skill(s): text2agent, optimizer
# 
#                                                                   Available Agents
#╭────────┬─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┬─────────╮
#│ Name   │ Description                                                                                                                     │ Address │
#├────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼─────────┤
#│ Aworld │ Aworld is a versatile AI assistant that can execute tasks directly or delegate to specialized agent teams. Use when you need:   │ list    │
#│        │ (1) General-purpose task execution, (2) Complex multi-step problem solving, (3) Coordination of specialized agent teams, (4)    │         │
#│        │ Adaptive task handling that switches between direct execution and team delegation                                               │         │
#╰────────┴─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┴─────────╯
```

### Direct Run Mode

```bash
# Run a task with built-in Aworld agent
aworld-cli --task "Your task here" --agent Aworld --max-runs 5

# Use custom agents alongside built-in agents
aworld-cli --agent-dir ./my_agents --task "Your task" --agent MyAgent

# Use remote agents
aworld-cli --remote-backend http://localhost:8000 --task "Your task" --agent RemoteAgent
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



## Agent Loading Priority

1. 📦 **Built-in Agents** (`inner_plugins/*/agents`) - Always loaded first (no configuration required)
   - Only loads `agents` directories from each plugin
   - Skills are managed separately by `skill_registry`
2. 📂 **Local Agents** (`LOCAL_AGENTS_DIR` or `--agent-dir`) - User-configured local agents
3. 🌐 **Remote Agents** (`REMOTE_AGENTS_BACKEND` or `--remote-backend`) - Remote backend agents

**Built-in Agents:**
- **Aworld**: A versatile AI assistant that can execute tasks directly or delegate to specialized agent teams
  - Location: `inner_plugins/smllc/agents/`
  - Supports direct execution with MCP tools and skills
  - Can delegate complex tasks to agent teams
  - Includes agent creation skills


## Environment Variables

- `LOCAL_AGENTS_DIR`: Semicolon-separated list of local agent directories (in addition to built-in agents)
- `REMOTE_AGENTS_BACKEND`: Semicolon-separated list of remote backend URLs
- `SKILLS_PATH`: Semicolon-separated list of skill sources (local directories or GitHub URLs)
  - Example: `SKILLS_PATH=./skills;https://github.com/user/repo;../custom-skills`
- `SKILLS_DIR`: Single skills directory (legacy, for backward compatibility)
- `SKILLS_CACHE_DIR`: Custom cache directory for GitHub skill repositories (default: ~/.aworld/skills)
- `AWORLD_DISABLE_CONSOLE_LOG`: Disable console logging (set to 'true')

**Note:** Built-in agents from `inner_plugins/*/agents` directories are always loaded automatically, regardless of environment variable configuration. Only the `agents` subdirectories are scanned to avoid loading unnecessary files.



## More Help

```bash
# Show help
aworld-cli --help

# Show Chinese help
aworld-cli --zh

# Show usage examples
aworld-cli --examples
```
