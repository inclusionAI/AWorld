# Agent Configuration Reference

Complete reference for configuring agents in AWorld framework.

## AgentConfig

```python
from aworld.config import AgentConfig, ModelConfig

agent_config = AgentConfig(
    llm_config=ModelConfig(...),  # Required: LLM configuration
    use_vision=False,              # Optional: Enable vision capabilities
    # ... other optional parameters
)
```

### ModelConfig Parameters

#### Required Parameters (from environment)

```python
ModelConfig(
    llm_model_name=os.environ.get("LLM_MODEL_NAME"),      # Model identifier
    llm_provider=os.environ.get("LLM_PROVIDER"),          # Provider name
    llm_api_key=os.environ.get("LLM_API_KEY"),            # API key
    llm_base_url=os.environ.get("LLM_BASE_URL"),          # Base URL for API
)
```

#### Optional Parameters

```python
ModelConfig(
    # Temperature (0.0 - 2.0)
    # Lower values: More deterministic, focused
    # Higher values: More creative, diverse
    llm_temperature=0.6,  # Default: varies by use case
    
    # Max completion tokens
    params={"max_completion_tokens": 40960},  # Default: depends on model
    
    # Other model-specific parameters
    # ... provider-specific options
)
```

### Temperature Guidelines

- **Orchestrator/Coordination Agents**: `0.1 - 0.3` (more deterministic)
- **Creative/Content Agents**: `0.6 - 0.9` (more creative)
- **Analysis/Planning Agents**: `0.4 - 0.7` (balanced)
- **Code Generation Agents**: `0.1 - 0.3` (precise)

## Agent Class Parameters

```python
from aworld.agents.llm_agent import Agent

agent = Agent(
    name="agent_name",              # Required: Unique agent identifier
    desc="Agent description",       # Required: Agent purpose description
    conf=agent_config,              # Required: AgentConfig instance
    system_prompt="...",            # Required: System prompt for agent behavior
    mcp_servers=[...],              # Optional: List of MCP server names
    mcp_config={...},               # Optional: MCP server configuration
    black_tool_actions={...}        # Optional: Disable specific tool actions
)
```

### MCP Server Configuration

```python
mcp_config = {
    "mcpServers": {
        "server-name": {
            "type": "stdio",        # Connection type: "stdio" or "http"
            "command": "npx",       # Command to run
            "args": [               # Command arguments
                "-y",
                "@modelcontextprotocol/server-package"
            ],
            # HTTP type example:
            # "url": "http://localhost:3000",
            # "headers": {"Authorization": "Bearer token"}
        }
    }
}
```

### Common MCP Servers

- `ms-playwright` - Browser automation
- `filesystem-server` - File system operations
- `terminal-server` - Command execution
- `amnicontext-server` - Context management
- `document_server` - Document processing
- `image_server` - Image processing

### Black Tool Actions

Disable specific tool actions for security or control:

```python
black_tool_actions = {
    "server-name": [
        "dangerous_action",
        "read_sensitive_file"
    ]
}
```

## Swarm Configuration

### TeamSwarm

```python
from aworld.core.agent.swarm import TeamSwarm

swarm = TeamSwarm(
    orchestrator_agent,    # Required: First agent is the leader
    worker_agent_1,        # Required: Worker agent
    worker_agent_2,        # Optional: Additional workers
    max_steps=30,          # Optional: Maximum execution steps
    # ... other optional parameters
)
```

### Swarm

```python
from aworld.core.agent.swarm import Swarm

swarm = Swarm(
    agent_1,               # Required: First agent in workflow
    agent_2,               # Required: Second agent in workflow
    agent_3,               # Optional: Additional agents
    max_steps=30,          # Optional: Maximum execution steps
    # ... other optional parameters
)
```

### Common Swarm Parameters

- `max_steps`: Maximum number of execution steps (default: 0 = unlimited)
- `topology`: Custom topology definition (advanced)
- `root_agent`: Root agent for custom topologies (advanced)
- `build_type`: GraphBuildType enum (usually auto-detected)

## Context Configuration

```python
from aworld.core.context.amni.config import (
    AmniConfigFactory,
    AmniConfigLevel,
    ContextEnvConfig
)

context_config = AmniConfigFactory.create(
    AmniConfigLevel.NAVIGATOR,  # Context level
    debug_mode=True,             # Enable debug logging
    neuron_names=["task"],       # Context neurons to use
    env_config=ContextEnvConfig(
        env_type="local",        # "local" or "remote"
        enabled_file_share=False,
        env_config={             # Environment-specific config
            # Remote example:
            # "URL": "http://mcp.example.com",
            # "TOKEN": "your-token",
            # "IMAGE_VERSION": "latest"
        }
    )
)
```

### Context Levels

- `AmniConfigLevel.NAVIGATOR`: Full context with navigation capabilities
- `AmniConfigLevel.BASIC`: Basic context support
- Default: Basic context

## Agent Team Registration

```python
from aworldappinfra.core.registry import agent_team
from aworldappinfra.ui.ui_template import build_markdown_ui

@agent_team(
    name="TeamName",                    # Required: Team identifier
    desc="Team description",            # Required: Team purpose
    context_config=context_config,      # Required: Context configuration
    ui=build_markdown_ui,               # Optional: UI template function
    metadata={                          # Optional: Additional metadata
        "version": "1.0.0",
        "creator": "your-name",
        "create_time": "2025-01-01"
    }
)
async def build_swarm(context: ApplicationContext) -> Swarm:
    # ... swarm building logic
    return swarm
```

## Example: Complete Configuration

```python
import os
from aworld.config import AgentConfig, ModelConfig
from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import Swarm
from aworld.core.context.amni import ApplicationContext, AmniConfigFactory
from aworldappinfra.core.registry import agent_team

# 1. Create agent configuration
agent_config = AgentConfig(
    llm_config=ModelConfig(
        llm_temperature=0.6,
        llm_model_name=os.environ.get("LLM_MODEL_NAME"),
        llm_provider=os.environ.get("LLM_PROVIDER"),
        llm_api_key=os.environ.get("LLM_API_KEY"),
        llm_base_url=os.environ.get("LLM_BASE_URL"),
        params={"max_completion_tokens": 40960}
    ),
    use_vision=False
)

# 2. Create agents
agent1 = Agent(
    name="agent1",
    desc="First agent in workflow",
    conf=agent_config,
    system_prompt="You are agent 1...",
)

agent2 = Agent(
    name="agent2",
    desc="Second agent in workflow",
    conf=agent_config,
    system_prompt="You are agent 2...",
    mcp_servers=["filesystem-server"],
    mcp_config={
        "mcpServers": {
            "filesystem-server": {
                "type": "stdio",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", "~/workspace"]
            }
        }
    }
)

# 3. Create swarm
@agent_team(
    name="MySwarm",
    desc="Example swarm",
    context_config=AmniConfigFactory.create(debug_mode=True),
    metadata={"version": "1.0.0"}
)
async def build_swarm(context: ApplicationContext) -> Swarm:
    return Swarm(agent1, agent2, max_steps=30)
```

