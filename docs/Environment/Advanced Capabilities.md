## Introduction
This document introduces the advanced capabilities of Environment, including registry integration, streaming response, environment context, Agent runtime support, tool management, and more. These features help you better manage and extend Environment capabilities.

If you are new to Environment, we recommend reading the Env Client document first to understand basic usage.

---

## Registry Integration
### Local Registry
```python
# Use a local JSON file as the registry (Builder pattern)
sandbox = Sandbox().tools(["tool1", "tool2"]).registry_url("~/workspace/registry.json").build()

# Registry file format
{
    "tool:server_name": {
        "entity_type": "tool",
        "name": "server_name",
        "description": "Server description",
        "tools": [
            {
                "name": "tool_name",
                "description": "Tool description"
            }
        ],
        "data": {
            "type": "streamable-http",
            "url": "https://...",
            "headers": {...}
        }
    }
}
```

### Remote Registry
```python
# Use a remote registry (Builder pattern)
sandbox = Sandbox().tools(["tool1", "tool2"]).registry_url("https://registry.example.com").build()

# Registry API
# POST /api/v1/registry/search
{
    "entity_type": "tool",
    "status": "active",
    "tools": ["tool1", "tool2"]  # Optional
}
```

---

## Streaming Response
The streaming response feature supports real-time return of tool results, including progress callbacks. This is useful for long-running tools.

```python
# Enable streaming (Builder pattern)
sandbox = Sandbox().mcp_config({...}).streaming(True).build()

# During tool calls, progress messages are automatically sent to the event bus
result = await sandbox.call_tool(action_list=[{
    "tool_name": "server_name",
    "action_name": "long_running_tool",
    "params": {}
}])

# Progress message format
# Message(
#     category=Constants.OUTPUT,
#     payload=Output(data="Progress information"),
#     sender="server_name__tool_name"
# )
```

---

## Environment Context Capability
The environment context feature allows you to define context parameters that are automatically injected into tool calls. This is useful for passing user information, environment configuration, etc.

```python
# Set environment context (Builder pattern)
sandbox = Sandbox().mcp_config({...}).env_content({
    "user_id": "user123",
    "workspace": "/path/to/workspace",
    "custom_config": {"key": "value"}
}).env_content_name("env_content").build()  # env_content_name is optional, defaults to "env_content"

# When calling tools, env_content parameter is automatically injected
# task_id, session_id, agent_id, etc. are also added dynamically
result = await sandbox.call_tool(
    action_list=[{
        "tool_name": "server_name",
        "action_name": "tool_name",
        "params": {
            # env_content is automatically injected, no need to pass manually
            # Final parameters will include:
            # {
            #     "user_id": "user123",
            #     "workspace": "/path/to/workspace",
            #     "custom_config": {"key": "value"},
            #     "task_id": "...",  # Automatically added from context
            #     "session_id": "...",  # Automatically added from context
            #     "agent_id": "..."  # Automatically added from event_message
            # }
        }
    }],
    context=context  # Pass context to automatically add task_id and session_id
)
```

**How it works**:
1. Environment context parameters are hidden in tool schemas (LLM cannot see them)
2. User-defined values are automatically injected during tool calls
3. Dynamic context information like `task_id`, `session_id`, `agent_id` is added automatically
4. If user manually provides parameter values, user values take priority

---

## Agent Runtime Support
Support for configuring Agents to run in Environment, allowing Agents to be called as tools by other Agents.

### Local Agents
```python
# Simple format: Directly specify agent directory path (Builder pattern)
sandbox = Sandbox().mcp_config({...}).agents({
    "local_agent": "/path/to/agents"  # Agent directory path
}).build()

# Extended format: Provide more configuration options (Builder pattern)
sandbox = Sandbox().mcp_config({...}).agents({
    "advanced_agent": {
        "location": "/path/to/agents",
        "run_mode": "local",  # Optional: "local" or "remote", default is "local"
        "env": {"KEY": "value"},  # Optional: Environment variables
        "cwd": "/path/to/workdir",  # Optional: Working directory
        "headers": {
            "SANDBOX_ENV": '{"key": "value"}'  # Optional: Pass env vars through headers (JSON string)
        }
    }
}).build()

# Using chain Builder API to configure agents
sandbox = (Sandbox()
    .mcp_config({...})
    .agents()
    .advanced_agent()
    .location("/path/to/agents")
    .run_mode("local")
    .env({"KEY": "value"})
    .build()
)
```

### Remote Agents
```python
import os

# Set environment variables (required for remote agents)
os.environ["CUSTOM_ENV_URL"] = "https://custom.example.com"
os.environ["CUSTOM_ENV_TOKEN"] = "your_token"
os.environ["CUSTOM_ENV_IMAGE_VERSION"] = "v1.0.0"

# Simple format: Directly specify remote URL (Builder pattern)
sandbox = Sandbox().mcp_config({...}).agents({
    "remote_agent": "https://github.com/user/repo.git"  # Git repository URL
}).build()

# Extended format: Provide more configuration options (Builder pattern)
sandbox = Sandbox().mcp_config({...}).agents({
    "remote_agent": {
        "location": "https://github.com/user/repo.git",
        "run_mode": "remote",  # Explicitly specify remote mode
        "repo_url": "https://github.com/user/repo.git",  # Optional: If location is not a URL
        "project_path": "subdir",  # Optional: Project subdirectory
        "env": {"KEY": "value"},  # Optional: Environment variables
        "headers": {"Custom-Header": "value"}  # Optional: Custom HTTP headers
    }
}).build()
```

**Notes**:
- Local agents use the `aworld-cli serve --mcp --agent-dir` command to start
- Remote agents require `CUSTOM_ENV_URL`, `CUSTOM_ENV_TOKEN`, and `CUSTOM_ENV_IMAGE_VERSION` environment variables
- Agent configurations are automatically converted to MCP server configurations and merged into `mcp_config`

---

## Custom Environment Tools
```python
import os

# Set environment variables
os.environ["CUSTOM_ENV_URL"] = "https://custom.example.com"
os.environ["CUSTOM_ENV_TOKEN"] = "your_token"
os.environ["CUSTOM_ENV_IMAGE_VERSION"] = "v1.0.0"

# Use custom environment tools (Builder pattern)
sandbox = Sandbox().custom_env_tools({
    "custom_server": {
        "type": "streamable-http",
        "url": "...",
        "headers": {...}
    }
}).build()
```

---

## Blacklisted Tools
```python
# Disable specific tools (Builder pattern)
sandbox = Sandbox().mcp_config({
    "mcpServers": {
        "server_name": {...}
    }
}).black_tool_actions({
    "server_name": ["tool1", "tool2"]  # Disable these tools
}).build()
```

---

## Skill Configuration
```python
# Configure skill system (Builder pattern)
sandbox = Sandbox().mcp_config({
    "mcpServers": {
        "server_name": {...}
    }
}).skill_configs({
    "skill1": {
        "tools": ["tool1", "tool2"],
        "description": "Skill description"
    }
}).build()
```

---

## Tool Registration
### Registering to Remote Registry
```python
from aworld.sandbox import Sandbox

result = await Sandbox.register(
    registry_url="https://registry.example.com",
    name="my-server",
    version="1.0.0",
    description="My server description",
    data={
        "type": "streamable-http",
        "url": "https://...",
        "headers": {...}
    },
    tools=[
        {
            "name": "tool_name",
            "description": "Tool description"
        }
    ],
    token="your_registry_token"
)

# Return result
# {
#     "success": True,
#     "entity_id": "...",
#     "message": "Successfully registered my-server"
# }
```

### Registering to Local File
```python
result = await Sandbox.register(
    registry_url="~/workspace/registry.json",
    servers={
        "mcpServers": {
            "server_name": {
                "type": "streamable-http",
                "url": "https://...",
                "headers": {...}
            }
        }
    }
)

# Return result
# {
#     "success": True,
#     "file_path": "/path/to/registry.json",
#     "message": "Successfully registered 1 server(s) to local file",
#     "registered_count": 1
# }
```

