<div align="center">

# VirtualPC MCP Server (Incubating)

*A unified MCP tool runtime environment based on Debian with session-level environment isolation, environment state persistence, real-time UI visualization, distributed architecture, and extensibility*

[![License: MIT][license-image]][license-url]

</div>

<div align="center">

[中文版](./README_zh.md) | [Quick Start](#quick-start) | [Development](#development) | [Contributing](#contributing)

</div>

---

## 1. Overview

VirtualPC MCP Server is a comprehensive MCP (Model Context Protocol) tool runtime environment designed to provide a unified, isolated, and scalable execution environment for AI agents. Built on Debian, it offers session-level environment isolation, persistent state management across multiple sessions, and real-time visualization capabilities.

### 1.1 Features

- **Session-Level Environment Isolation**: Each MCP session runs in its own isolated environment
- **Multi-Session State Persistence**: Maintains environment state across multiple MCP sessions
- **Real-Time UI Visualization**: Live monitoring and visualization of Agent MCP operations
- **Distributed Architecture**: Supports both local Docker and Kubernetes cluster deployments
- **Extensible Runtime**: Modular design allowing easy integration of new MCP tool servers
- **Production-Ready**: Suitable for both development/demo and production/RL training scenarios

## 2. Quick Start

We support both local Docker deployment (ideal for demos and debugging) and Kubernetes cluster deployment (recommended for production and RL training).

### 2.1 Local Docker Deployment

#### Prerequisites

Ensure Docker and Docker Compose are properly installed and running:

```bash
# Verify Docker installation
docker --version
docker compose --version

# Check if Docker daemon is running
docker ps
docker compose ps
```

**Step 1: Generate Environment Configuration**

Copy the environment template and configure your settings:

```bash
cp ./gaia-mcp-server/mcp_servers/.env_template ./gaia-mcp-server/mcp_servers/.env
```

Edit `./gaia-mcp-server/mcp_servers/.env` with your specific configuration values.

**Step 2: Launch VirtualPC MCP Server**

```bash
sh run-local.sh
```

Monitor the terminal output for any errors during startup.

**Step 3: Connect to VirtualPC MCP Server**

Use the following configuration to connect to the VirtualPC MCP Server:

```json
{
    "virtualpc-mcp-server": {
        "type": "streamable-http",
        "url": "http://localhost:8000/mcp",
        "headers": {
            "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhcHAiOiJsb2NhbF9kZWJ1ZyIsInZlcnNpb24iOjEsInRpbWUiOjE3NTYzOTUzNzIuMTg0MDc0NH0.SALKn1dxEzsdX82-e3jAJANAo_kE4NO4192Epw5rYmQ",
            "MCP_SERVERS": "readweb-server,browser-server"
        },
        "timeout": 6000,
        "sse_read_timeout": 6000,
        "client_session_timeout_seconds": 6000
    }
}
```

**Note**: The Bearer token above is for local testing only. The `MCP_SERVERS` header specifies the MCP server scope for your current connection, which should be a subset of server names defined in `gaia-mcp-server/mcp_servers/mcp_config.py`.

### 2.2 Kubernetes Cluster Deployment

For production deployments and RL training scenarios, we recommend using Kubernetes cluster deployment. Detailed instructions will be provided in future updates.

## 3. Development

### 3.1 Adding Your Own MCP Tool to VirtualPC MCP Server

**Step 1: Develop MCP Tool (Optional)**

If you need to develop your own MCP Tool and register it with VirtualPC MCP Server, create your MCP Tool project directory under `gaia-mcp-server/mcp_servers` and write the MCP Tool code. Refer to the [hello_world](./gaia-mcp-server/mcp_servers/hello_world/) directory for the project structure.

Project specifications:

1. Use `pyproject.toml` to manage project dependencies for Docker image building

**Step 2: Register MCP Tool**

Register your developed MCP Tool or third-party MCP Tool with VirtualPC MCP Server.

Edit the [MCP Tool registration file](./gaia-mcp-server/mcp_servers/mcp_config.py):

```python
"STDIO_SERVER_DEMO": {
    "type": "stdio",
    "command": "python",
    "args": ["-m", "hello_world.main"],
    "cwd": "hello_world",
},
"{SSE/STREAMABLE-HTTP_SERVER_NAME}": {
    "type": "sse/streamable-http",
    "url": "{URL for sse/streamable-http mcp server}",
    "headers": {
        "Authorization": f"Bearer {token}"
    }
},
```

**Step 3: Update MCP Tool Schema**

> **Important**: VirtualPC MCP Server uses pre-generated tool schema data for the `list_tools()` function, so you must update [mcp_tool_schema.json](./gaia-mcp-server/mcp_servers/mcp_tool_schema.json) after modifying the MCP server configuration.

We provide a Python script [build_mcp_tool_schema.py](./gaia-mcp-server/mcp_servers/build_mcp_tool_schema.py) to update `mcp_tool_schema.json`. Before running this script, ensure the MCP server [.env](./gaia-mcp-server/mcp_servers/.env) file is correctly configured.

```bash
cd ./gaia-mcp-server/mcp_servers/
pip install mcp
python build_mcp_tool_schema.py
```

**Step 4: Build Docker Image and Run Service**

After completing the above steps, build the Docker image and start the service.

## 4. Contributing

We welcome contributions from the community! Please refer to our contributing guidelines for:

- Code style and standards
- Pull request process
- Issue reporting
- Development setup instructions

## 5. References

### Acknowledgments

- **Magentic-UI Project**: We have incorporated Docker Browser source code from the [magentic-ui](https://github.com/microsoft/magentic-ui) project. Special thanks to the magentic-ui project team for their excellent work.

### Related Projects

- [Model Context Protocol (MCP)](https://modelcontextprotocol.io/)
- [Magentic-UI](https://github.com/microsoft/magentic-ui)
- [Debian](https://www.debian.org/)

---

<div align="center">

**VirtualPC MCP Server** - Empowering AI agents with robust, scalable runtime environments

[license-image]: https://img.shields.io/badge/License-MIT-yellow.svg
[license-url]: https://opensource.org/licenses/MIT

</div>

