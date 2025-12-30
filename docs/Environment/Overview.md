## Introduction
Environment is a core concept in the AWorld framework, providing agents with a secure and isolated tool execution environment. Environment uniformly manages MCP (Model Context Protocol) servers, enabling agents to seamlessly use various external tools and services.

In the AWorld framework, `Sandbox` is a client implementation of Environment, providing a concrete implementation of the Environment interface. Through the `Sandbox` class, you can create and manage Environment instances.

### Key Features
+ ✅ **MCP Server Management**: Unified management of multiple MCP server connections
+ ✅ **Automatic Tool Discovery**: Automatically discover and register MCP tools
+ ✅ **Connection Pool Management**: Intelligently cache server connections to improve performance
+ ✅ **Registry Integration**: Support for local and remote tool registries
+ ✅ **Progress Callbacks**: Support for displaying execution progress for long-running tools
+ ✅ **Automatic Parameter Injection**: Automatically inject session_id, task_id, and other parameters from context

---

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

---

## Core Concepts
### Environment
Environment is a core concept in the AWorld framework, representing a tool execution environment. Environment provides the infrastructure for tool execution, with each Environment instance managing a group of MCP servers and providing a unified tool access interface for agents.

### Sandbox (Client Implementation of Environment)
`Sandbox` is a client implementation class of Environment. In code, you create and use Environment instances through the `Sandbox` class:

```python
from aworld.sandbox import Sandbox

# Sandbox is a client implementation of Environment
sandbox = Sandbox(mcp_config={...})
```

Although the code uses the `Sandbox` class, conceptually, you are creating an Environment instance. `Sandbox` provides a concrete implementation of the Environment interface, including:

+ MCP server connection management
+ Tool discovery and invocation
+ Resource cleanup and other functions

### MCP Servers
MCP (Model Context Protocol) servers provide tool services. The following types are supported:

+ **stdio**: Standard input/output (local processes)
+ **sse**: Server-Sent Events (HTTP SSE)
+ **streamable-http**: Streaming HTTP (supports progress callbacks)
+ **api**: RESTful API (simple HTTP calls)
+ **function_tool**: Python function tools

### Tools
Tools are executable functions provided by MCP servers. Tool naming format: `server_name__tool_name`

Example: `gaia-mcp__doc_txt__list_supported_formats_for_txt`

---

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

---

## Frequently Asked Questions
### Q1: How to view all tools supported by Environment?
```python
tools = await sandbox.list_tools()
for tool in tools:
    print(tool["function"]["name"])
```

### Q2: What to do when tool calls fail?
```python
# Check tool call results
result = await sandbox.call_tool(action_list=[{
    "tool_name": "server_name",
    "action_name": "tool_name",
    "params": {}
}])
for r in result:
    if not r.success:
        print(f"Tool {r.tool_name} call failed: {r.content}")
```

### Q3: How to set timeout?
```python
sandbox = Sandbox(
    timeout=10000,  # 10 seconds
    mcp_config={
        "mcpServers": {
            "server_name": {
                "type": "streamable-http",
                "url": "https://...",
                "timeout": 6000,  # 6 seconds
                "sse_read_timeout": 6000
            }
        }
    }
)
```

### Q4: How to disable a server?
```python
mcp_config = {
    "mcpServers": {
        "server_name": {
            "disabled": True,  # Disable this server
            ...
        }
    }
}
```

### Q5: How to find tools from the registry?
```python
# Method 1: Search by tool name
sandbox = Sandbox(
    tools=["tool1", "tool2"],
    registry_url="https://registry.example.com"
)

# Method 2: Search by server name
sandbox = Sandbox(
    mcp_servers=["server1", "server2"],
    registry_url="https://registry.example.com"
)
```

---

## Best Practices
### 1. Reuse Environment Instances
```python
# ✅ Recommended: Create once, use multiple times
# Create an Environment instance through Sandbox (Environment's client)
sandbox = Sandbox(mcp_config=mcp_config)
tools = await sandbox.list_tools()
result = await sandbox.call_tool(action_list=[{
    "tool_name": "server_name",
    "action_name": "tool_name",
    "params": {}
}])
another_result = await sandbox.call_tool(action_list=[{
    "tool_name": "server_name",
    "action_name": "another_tool",
    "params": {}
}])
await sandbox.cleanup()

# ❌ Not recommended: Frequently create and destroy
for i in range(10):
    # Creating a new Environment instance in each loop is inefficient
    sandbox = Sandbox(mcp_config=mcp_config)
    result = await sandbox.call_tool(action_list=[{
        "tool_name": "server_name",
        "action_name": "tool_name",
        "params": {}
    }])
    await sandbox.cleanup()
```

### 2. Use Connection Pool
Environment automatically manages server connection pools, no need to manually manage connections.

### 3. Set Reasonable Timeouts
```python
# Set appropriate timeouts based on tool execution time
sandbox = Sandbox(
    timeout=30000,  # 30 seconds
    mcp_config={
        "mcpServers": {
            "server_name": {
                "type": "streamable-http",
                "url": "https://...",
                "timeout": 60000,  # 60 seconds
                "sse_read_timeout": 120000  # 120 seconds
            }
        }
    }
)
```

### 4. Use Registry to Manage Tools
```python
# Recommended: Use registry to centrally manage tool configurations
sandbox = Sandbox(
    tools=["tool1", "tool2"],
    registry_url="https://registry.example.com"
)
```

### 5. Error Handling
```python
try:
    result = await sandbox.call_tool(action_list=[{
        "tool_name": "server_name",
        "action_name": "tool_name",
        "params": {}
    }])
    for r in result:
        if not r.success:
            logger.error(f"Tool call failed: {r.content}")
except Exception as e:
    logger.error(f"Environment operation failed: {e}")
finally:
    await sandbox.cleanup()
```

---

## Example Code
### Complete Example: Using GAIA Environment
```python
import asyncio
import json
from aworld.sandbox import Sandbox

# GAIA MCP configuration
URL = "https://mcp.aworldagents.com/vpc-pre/mcp"
TOKEN = "YOUR_TOKEN"
IMAGE_VERSION = "gaia-20251125152015"

gaia_mcp_config = {
    "mcpServers": {
        "gaia-mcp": {
            "type": "streamable-http",
            "url": URL,
            "headers": {
                "env_name": "gaia",
                "Authorization": f"Bearer {TOKEN}",
                "MCP_SERVERS": (
                    "googlesearch,readweb-server,media-audio,media-image,"
                    "media-video,intell-code,intell-guard,doc-csv,doc-xlsx,"
                    "doc-docx,doc-pptx,doc-txt,doc-pdf,download,parxiv-server,"
                    "terminal-server,wayback-server,wiki-server"
                ),
                "IMAGE_VERSION": IMAGE_VERSION,
            },
            "timeout": 6000,
            "sse_read_timeout": 6000,
            "client_session_timeout_seconds": 6000,
        }
    }
}

async def main():
    # Create an Environment instance through Sandbox (Environment's client)
    sandbox = Sandbox(mcp_config=gaia_mcp_config)
    
    # List tools
    tools = await sandbox.list_tools()
    print(f"Number of available tools: {len(tools)}")
    
    # Example tool call
    result = await sandbox.call_tool(action_list=[{
        "tool_name": "gaia-mcp",
        "action_name": "doc_txt__list_supported_formats_for_txt",
        "params": {}
    }])
    
    print(f"Tool execution result: {json.dumps(result, ensure_ascii=False, indent=2)}")
    
    # Cleanup
    await sandbox.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
```

### Example: Integration with Agent
```python
from aworld.agents.llm_agent import Agent
from aworld.runner import Runners

# Create an Environment instance through Sandbox (Environment's client)
sandbox = Sandbox(mcp_config={...})

# Create Agent (automatically uses Environment)
agent = Agent(
    name="MyAgent",
    system_prompt="You are an assistant",
    sandbox=sandbox  # Pass Environment instance
)

# Run Agent (automatically uses tools from Environment)
result = await Runners.run(
    input="Use tools to execute tasks",
    agent=agent
)
```

