This document introduces how to use the Environment client (Sandbox) to create and manage Environment instances, and perform basic tool operations. If you are not familiar with the basic concepts of Environment, we recommend reading the Overview document first.

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
In AWorld, you create Environment instances through the `Sandbox` class (Environment's client implementation). Builder pattern is recommended:

```python
from aworld.sandbox import Sandbox

# Method 1: Using mcp_config (Builder pattern)
sandbox = Sandbox().mcp_config({
    "mcpServers": {
        "server1": {
            "type": "streamable-http",
            "url": "https://...",
            "headers": {...}
        }
    }
}).build()

# Method 2: Specify server names (Builder pattern)
sandbox = Sandbox().mcp_servers(["server1", "server2"]).mcp_config({
    "mcpServers": {
        "server1": {...},
        "server2": {...}
    }
}).build()

# Method 3: Using tool names (lookup from registry, Builder pattern)
sandbox = Sandbox().tools(["tool1", "tool2"]).registry_url("https://registry.example.com").build()

# Method 4: Specify sandbox_id (Builder pattern)
sandbox = Sandbox().sandbox_id("my-sandbox-id").mcp_config({
    "mcpServers": {
        "server_name": {...}
    }
}).build()

# Method 5: Chain multiple options (Builder pattern)
sandbox = (Sandbox()
    .mcp_config({...})
    .streaming(True)  # Enable streaming, see Advanced Capabilities
    .env_content({"user_id": "user123"})  # Configure environment context, see Advanced Capabilities
    .agents({"my_agent": "/path/to/agents"})  # Configure agents, see Advanced Capabilities
    .build()
)

# Note: Direct constructor is also supported (backward compatible)
sandbox = Sandbox(mcp_config={...})
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
#     "metadata": {...}
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
# Builder pattern
sandbox = Sandbox().timeout(10000).mcp_config({
    "mcpServers": {
        "server_name": {
            "type": "streamable-http",
            "url": "https://...",
            "timeout": 6000,  # 6 seconds
            "sse_read_timeout": 6000
        }
    }
}).build()
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
# Method 1: Search by tool name (Builder pattern)
sandbox = Sandbox().tools(["tool1", "tool2"]).registry_url("https://registry.example.com").build()

# Method 2: Search by server name (Builder pattern)
sandbox = Sandbox().mcp_servers(["server1", "server2"]).registry_url("https://registry.example.com").build()
```

### Q6: How to use environment context capability?
See the "Environment Context Capability" section in the Advanced Capabilities document.

### Q7: How to configure agents to run in Environment?
See the "Agent Runtime Support" section in the Advanced Capabilities document.

---

## Best Practices
### 1. Reuse Environment Instances
```python
# ✅ Recommended: Create once, use multiple times (Builder pattern)
sandbox = Sandbox().mcp_config(mcp_config).build()
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
    sandbox = Sandbox().mcp_config(mcp_config).build()
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
# Set appropriate timeouts based on tool execution time (Builder pattern)
sandbox = Sandbox().timeout(30000).mcp_config({
    "mcpServers": {
        "server_name": {
            "type": "streamable-http",
            "url": "https://...",
            "timeout": 60000,  # 60 seconds
            "sse_read_timeout": 120000  # 120 seconds
        }
    }
}).build()
```

### 4. Use Registry to Manage Tools
```python
# Recommended: Use registry to centrally manage tool configurations (Builder pattern)
sandbox = Sandbox().tools(["tool1", "tool2"]).registry_url("https://registry.example.com").build()
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
    # Create an Environment instance through Sandbox (Environment's client) (Builder pattern)
    sandbox = Sandbox().mcp_config(gaia_mcp_config).build()
    
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

# Create an Environment instance through Sandbox (Environment's client) (Builder pattern)
sandbox = Sandbox().mcp_config({...}).build()

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

