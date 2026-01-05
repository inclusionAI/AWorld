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
