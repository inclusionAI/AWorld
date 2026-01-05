
## Quick Start
### Installation
The Environment module is included in the AWorld framework and does not require separate installation.

```bash
pip install aworld
```

### Minimal Example
```python
import asyncio
from aworld.sandbox import Sandbox  # Sandbox is a client implementation of Environment

# Configure MCP servers
mcp_config = {
    "mcpServers": {
        "gaia-mcp": {
            "type": "streamable-http",
            "url": "https://mcp.example.com/mcp",
            "headers": {
                "Authorization": "Bearer YOUR_TOKEN",
                "MCP_SERVERS": "server1,server2"
            },
            "timeout": 6000,
            "sse_read_timeout": 6000
        }
    }
}

async def main():
    # Create an Environment instance through Sandbox (Environment's client)
    sandbox = Sandbox(mcp_config=mcp_config)
    
    # List available tools
    tools = await sandbox.list_tools()
    print(f"Number of available tools: {len(tools)}")
    
    # Call a tool
    result = await sandbox.call_tool(action_list=[{
        "tool_name": "gaia-mcp",
        "action_name": "tool_name",
        "params": {}
    }])
    print(f"Tool execution result: {result}")
    
    # Clean up resources
    await sandbox.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
```


## Basic Operations
### Creating an Environment
In AWorld, you create Environment instances through the `Sandbox` class (Environment's client implementation):

```python
from aworld.sandbox import Sandbox

# Method 1: Using mcp_config
# Sandbox is a client implementation of Environment
sandbox = Sandbox(
    mcp_config={
        "mcpServers": {
            "server1": {
                "type": "streamable-http",
                "url": "https://...",
                "headers": {...}
            }
        }
    }
)

# Method 2: Specify server names
sandbox = Sandbox(
    mcp_servers=["server1", "server2"],
    mcp_config={
        "mcpServers": {
            "server1": {...},
            "server2": {...}
        }
    }
)

# Method 3: Using tool names (lookup from registry)
sandbox = Sandbox(
    tools=["tool1", "tool2"],
    registry_url="https://registry.example.com"
)

# Method 4: Specify sandbox_id
sandbox = Sandbox(
    sandbox_id="my-sandbox-id",
    mcp_config={
        "mcpServers": {
            "server_name": {...}
        }
    }
)
```

### Listing Tools
```python
# Get all available tools
tools = await sandbox.list_tools()

# Tool format example
[
    {
        "type": "function",
        "function": {
            "name": "server_name__tool_name",
            "description": "Tool description",
            "parameters": {
                "type": "object",
                "properties": {
                    "param1": {
                        "type": "string",
                        "description": "Parameter description"
                    }
                },
                "required": ["param1"]
            }
        }
    }
]
```

### Calling Tools
```python
# Single tool call
result = await sandbox.call_tool(action_list=[{
    "tool_name": "server_name",
    "action_name": "tool_name",
    "params": {
        "param1": "value1"
    }
}])

# Multiple tool calls (parallel)
results = await sandbox.call_tool(action_list=[
    {
        "tool_name": "server1",
        "action_name": "tool1",
        "params": {}
    },
    {
        "tool_name": "server2",
        "action_name": "tool2",
        "params": {}
    }
])

# Result format
[
    ActionResult(
        tool_name="server_name",
        action_name="tool_name",
        content="Execution result",
        success=True,
        metadata={...}
    )
]
```

### Getting Environment Information
```python
info = sandbox.get_info()
# {
#     "sandbox_id": "...",
#     "status": "Running",
#     "metadata": {...},
#     "env_type": 1
# }
```

### Cleaning Up Resources
```python
# Manual cleanup
await sandbox.cleanup()

# Automatic cleanup (when object is destroyed)
# Environment will automatically clean up in __del__
```

---

## Advanced Features
### Registry Integration
#### Local Registry
```python
# Use a local JSON file as the registry
sandbox = Sandbox(
    tools=["tool1", "tool2"],
    registry_url="~/workspace/registry.json"
)

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

#### Remote Registry
```python
# Use a remote registry
sandbox = Sandbox(
    tools=["tool1", "tool2"],
    registry_url="https://registry.example.com"
)

# Registry API
# POST /api/v1/registry/search
{
    "entity_type": "tool",
    "status": "active",
    "tools": ["tool1", "tool2"]  # Optional
}
```

### Custom Environment Tools
```python
import os

# Set environment variables
os.environ["CUSTOM_ENV_URL"] = "https://custom.example.com"
os.environ["CUSTOM_ENV_TOKEN"] = "your_token"
os.environ["CUSTOM_ENV_IMAGE_VERSION"] = "v1.0.0"

# Use custom environment tools
sandbox = Sandbox(
    custom_env_tools={
        "custom_server": {
            "type": "streamable-http",
            "url": "...",
            "headers": {...}
        }
    }
)
```

### Blacklisted Tools
```python
# Disable specific tools
sandbox = Sandbox(
    mcp_config={
        "mcpServers": {
            "server_name": {...}
        }
    },
    black_tool_actions={
        "server_name": ["tool1", "tool2"]  # Disable these tools
    }
)
```

### Skill Configuration
```python
# Configure skill system
sandbox = Sandbox(
    mcp_config={
        "mcpServers": {
            "server_name": {...}
        }
    },
    skill_configs={
        "skill1": {
            "tools": ["tool1", "tool2"],
            "description": "Skill description"
        }
    }
)
```

### Progress Callbacks
```python
# Receive progress updates during tool calls
# Progress messages are automatically sent to the event bus
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

