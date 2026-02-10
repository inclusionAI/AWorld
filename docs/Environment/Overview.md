## Introduction

Environment is a core concept in the AWorld framework, providing agents with a secure and isolated tool execution environment. Environment uniformly manages MCP (Model Context Protocol) servers, enabling agents to seamlessly use various external tools and services.

In the AWorld framework, `Sandbox` is a client implementation of Environment, providing a concrete implementation of the Environment interface. Through the `Sandbox` class, you can create and manage Environment instances.

### Key Features

+ ✅ **MCP Server Management**: Unified management of multiple MCP server connections
+ ✅ **Automatic Tool Discovery**: Automatically discover and register MCP tools
+ ✅ **Connection Pool Management**: Intelligently cache server connections to improve performance
+ ✅ **Registry Integration**: Support for local and remote tool registries
+ ✅ **Streaming Response**: Support for streaming tool responses, including progress callbacks
+ ✅ **Environment Context Capability**: Automatically inject user-defined context parameters into tool calls
+ ✅ **Agent Runtime Support**: Support for running Agents in Environment
