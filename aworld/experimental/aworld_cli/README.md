# AWorld CLI

AWorld CLI is a command-line tool for interacting with AWorld agents.

## Directory Structure

```
aworld_cli/
├── __init__.py          # Package initialization, exports main interfaces
├── main.py              # Command-line entry point
├── console.py           # CLI UI and interaction logic
├── core/                # Core functionality directory
│   ├── __init__.py      # Exports core modules
│   ├── loader.py        # Agent loader for scanning and loading agents
│   └── agent_registry.py  # Local agent registry and @agent decorator
├── runtime/             # Runtime environment directory
│   ├── __init__.py      # Exports all adapters
│   ├── base.py          # Base adapter class
│   ├── local.py         # Local adapter
│   ├── remote.py        # Remote adapter
│   └── mixed.py         # Mixed adapter (supports both local and remote)
├── executors/           # Executor directory
│   ├── __init__.py      # Exports executor protocol
│   ├── base.py          # Base executor protocol
│   ├── local.py         # Local executor
│   ├── remote.py        # Remote executor (supports streaming output)
│   └── continuous.py    # Continuous executor (supports limits and completion signals)
├── models/              # Data models directory
│   ├── __init__.py      # Exports model protocol
│   └── agent_info.py    # Agent information model implementation
└── utils/               # Utility functions
    └── agent_utils.py   # Agent utility functions
```

## Features

- **Interactive CLI**: Rich terminal interface for agent interaction
- **Agent Discovery**: Automatic discovery of agents using `@agent` decorator
- **Multiple Sources**: Support for local and remote agents
- **Streaming Output**: Real-time streaming of agent responses

## Extension Guide

### Adding New Adapters

1. Create a new file in the `runtime/` directory, e.g., `custom.py`
2. Inherit from `BaseAgentRuntime` and implement required methods:

```python
from aworld_cli.runtime.base import BaseAgentRuntime
from aworld_cli.models import AgentInfo
from aworld_cli.executors import AgentExecutor

class CustomRuntime(BaseAgentRuntime):
    async def _load_agents(self) -> List[AgentInfo]:
        # Implement logic to load agents
        pass
    
    async def _create_executor(self, agent: AgentInfo) -> Optional[AgentExecutor]:
        # Implement logic to create executor
        pass
    
    def _get_source_type(self) -> str:
        return "CUSTOM"
    
    def _get_source_location(self) -> str:
        return "custom://location"
```

3. Export the new runtime in `runtime/__init__.py`

### Adding New Executors

1. Create a new file in the `executors/` directory
2. Implement the `AgentExecutor` protocol:

```python
from aworld_cli.executors import AgentExecutor

class CustomExecutor(AgentExecutor):
    async def chat(self, message: str) -> str:
        # Implement chat logic
        pass
```

3. Export the new executor in `executors/__init__.py`

### Adding New Agent Information Models

1. Add a new class in `models/agent_info.py`
2. Ensure it implements the `AgentInfo` protocol (name, desc attributes)

### Using the @agent Decorator

The `@agent` decorator provides a convenient way to register agents:

```python
from aworld.experimental.aworld_cli.core.agent_registry import agent
from aworld.core.agent.swarm import Swarm
from aworld.agents.llm_agent import Agent

@agent(
    name="MyAgent",
    desc="My agent description",
    context_config=AmniConfigFactory.create(...),
    metadata={"version": "1.0.0"}
)
def build_my_swarm() -> Swarm:
    """Build and return a Swarm instance."""
    agent = Agent(...)
    return Swarm(agent)
```

The decorator automatically:
- Registers the agent with `LocalAgentRegistry`
- Supports lazy initialization (callable functions)
- Automatically wraps single `Agent` instances as `Swarm(agent)`
- Supports both sync and async build functions

## Usage Examples

### Using Local Runtime

```python
from aworld_cli.runtime.local import LocalRuntime

local_runtime = LocalRuntime()
await local_runtime.start()
```

### Using Remote Runtime

```python
from aworld_cli.runtime.remote import RemoteRuntime

remote_runtime = RemoteRuntime("http://localhost:8000")
await remote_runtime.start()
```

### Using Mixed Runtime (Recommended)

The `MixedRuntime` supports both local and remote agents from multiple sources:

```python
from aworld_cli.runtime.mixed import MixedRuntime

# Supports multiple local directories and remote backends
# Configure via environment variables:
# LOCAL_AGENTS_DIR: Semicolon-separated list of local directories
# REMOTE_AGENTS_BACKEND: Semicolon-separated list of remote backend URLs

mixed_runtime = MixedRuntime()
await mixed_runtime.start()
```

### Using Continuous Executor

```python
from aworld_cli.executors.continuous import ContinuousExecutor
from aworld_cli.models import AgentInfo

executor = ContinuousExecutor(
    agent=agent_info,
    max_runs=5,
    max_cost=10.00,
    max_duration="2h",
    completion_signal="Task completed",
    completion_threshold=3,
    non_interactive=False
)

await executor.run("Your task prompt here")
```

## Command-Line Interface

### Interactive Mode

```bash
aworld-cli
```

### List Agents

```bash
aworld-cli list
```

## Environment Variables

- `LOCAL_AGENTS_DIR`: Semicolon-separated list of local agent directories
- `REMOTE_AGENTS_BACKEND`: Semicolon-separated list of remote backend URLs
- `SKILLS_PATH`: Semicolon-separated list of skill sources (local directories or GitHub URLs)
  - Example: `SKILLS_PATH=./skills;https://github.com/user/repo;../custom-skills`
- `SKILLS_DIR`: Single skills directory (legacy, for backward compatibility)
- `SKILLS_CACHE_DIR`: Custom cache directory for GitHub skill repositories (default: ~/.aworld/skills)
- `AWORLD_DISABLE_CONSOLE_LOG`: Disable console logging (set to 'true')
