<div align="center">

# VirtualPC MCP Server

*A unified, Docker-based MCP tool runtime with real-time UI visualization, distributed architecture, and high extensibility.*

[![License: MIT][license-image]][license-url]

</div>

<div align="center">

[中文版](./README_zh.md) | [Quickstart](#quickstart) | [Development Guide](#development-guide) | [Contributing](#contributing)

</div>

---

## 1. Overview

VirtualPC MCP Server is a comprehensive Model Context Protocol (MCP) tool runtime environment designed to provide a unified, isolated, and scalable execution environment for AI agents. Built on Docker, it offers a consistent MCP tool runtime and integrates a real-time visualization interface.

### 1.1 Core Features

- **Unified MCP Tool Runtime**: Ensures a consistent and standardized tool execution environment across multiple MCP Server instances.
- **Real-Time UI Visualization**: Monitor and visualize agent MCP operations in real-time through an intuitive user interface.
- **Distributed Architecture**: Natively supports both local Docker and Kubernetes cluster deployments to suit various application scales.
- **Extensible Runtime**: Features a modular design that allows developers to seamlessly integrate new MCP tool services, enhancing system functionality.

## 2. Quickstart

This project is deployed using Docker.

#### Prerequisites

Ensure that Docker and Docker Compose are correctly installed and running in your environment:

```bash
# Verify Docker versions
docker --version
docker compose --version

# Confirm the Docker daemon is running
docker ps
docker compose ps
```

**Step 1: Configure Environment and Prepare Gaia Dataset**

1.  Copy the environment configuration file template and modify it according to your needs:

    ```bash
    cp ./gaia-mcp-server/mcp_servers/.env_template ./gaia-mcp-server/mcp_servers/.env
    ```

    Edit the `./gaia-mcp-server/mcp_servers/.env` file and fill in your specific configuration details.

2.  Download the [gaia_dataset](https://huggingface.co/datasets/gaia-benchmark/GAIA) from Hugging Face and place it in the `./gaia-mcp-server/docker/gaia_dataset` directory.

**Step 2: Launch VirtualPC MCP Server**

```bash
sh run-local.sh
```

Monitor the terminal output to ensure there are no errors during startup.

**Step 3: Connect to VirtualPC MCP Server**

Use the following configuration to connect to your VirtualPC MCP Server instance:

```json
{
    "virtualpc-mcp-server": {
        "type": "streamable-http",
        "url": "http://localhost:8000/mcp",
        "timeout": 6000,
        "sse_read_timeout": 6000,
        "client_session_timeout_seconds": 6000
    }
}
```

## 3. Development Guide

### 3.1 Adding Custom MCP Tools

**Step 1: Develop an MCP Tool (Optional)**

If you need to develop a custom MCP tool and integrate it into the VirtualPC MCP Server, create your project directory under `gaia-mcp-server/mcp_servers` and implement the tool's code. You can refer to the [hello_world](./gaia-mcp-server/mcp_servers/hello_world/) directory for an example project structure.

Project requirements:

1.  Project dependencies should be managed via a `pyproject.toml` file to facilitate Docker image builds.

**Step 2: Register the MCP Tool**

Register your custom or third-party MCP tool with the VirtualPC MCP Server.

Edit the [MCP tool registration file](./gaia-mcp-server/mcp_servers/mcp_config.py):

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

**Step 3: Update the MCP Tool Schema**

> **Important**: The VirtualPC MCP Server uses pre-generated tool schema data to respond to calls from the `list_tools()` function. Therefore, you must update the [mcp_tool_schema.json](./gaia-mcp-server/mcp_servers/mcp_tool_schema.json) file after modifying the MCP server configuration.

We provide a Python script, [build_mcp_tool_schema.py](./gaia-mcp-server/mcp_servers/build_mcp_tool_schema.py), to help you update `mcp_tool_schema.json`. Before running this script, ensure that the MCP server's [.env](./gaia-mcp-server/mcp_servers/.env) file is configured correctly.

```bash
cd ./gaia-mcp-server/mcp_servers/
pip install mcp
python build_mcp_tool_schema.py
```

**Step 4: Build the Docker Image and Deploy**

After completing the steps above, rebuild the Docker image and deploy the service.

## 4. Contributing

We warmly welcome contributions from the community! If you wish to get involved with this project, please follow our contribution guidelines. We encourage you to participate in the following ways:

-   **Reporting Issues**: If you find a bug or have a feature request, please submit it via [Issues](https://github.com/your-repo/issues).
-   **Code Contributions**: Please follow the standard Fork & Pull Request workflow. We recommend adhering to the project's existing code style and standards.
-   **Improving Documentation**: If you notice any omissions or errors in the documentation, we welcome your corrections.

## 5. References

### Acknowledgments

-   **Magentic-UI Project**: This project incorporates the Docker Browser source code from the [magentic-ui](https://github.com/microsoft/magentic-ui) project. We extend our sincere gratitude to the magentic-ui project team for their excellent work.

### Related Projects

-   [Model Context Protocol (MCP)](https://modelcontextprotocol.io/)
-   [Magentic-UI](https://github.com/microsoft/magentic-ui)
-   [Debian](https://www.debian.org/)

---

<div align="center">

**VirtualPC MCP Server** - Empowering AI agents with a powerful and extensible runtime environment

[license-image]: https://img.shields.io/badge/License-MIT-yellow.svg
[license-url]: https://opensource.org/licenses/MIT

</div>

