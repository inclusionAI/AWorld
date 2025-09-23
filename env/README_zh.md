<div align="center">

# VirtualPC MCP Server

*一个基于 Docker 的统一 MCP 工具运行时，具备实时 UI 可视化、分布式架构及高可扩展性*

[![License: MIT][license-image]][license-url]

</div>

<div align="center">

[English](./README.md) | [快速入门](#快速入门) | [开发指南](#开发指南) | [参与贡献](#参与贡献)

</div>

---

## 1. 项目概述

VirtualPC MCP Server 是一个统一的 MCP 工具运行时环境，专为 AI 代理提供统一、隔离且可扩展的执行环境而设计。它构建于 Docker 之上，提供了一致的 MCP 工具运行能力，集成了实时可视化界面。

### 1.1 核心特性

- **统一的 MCP 工具运行环境**：确保在多个 MCP Server 实例上提供一致且标准的工具执行环境。
- **实时 UI 可视化**：通过直观的用户界面，实时监控和可视化 Agent 的 MCP 操作。
- **分布式架构**：原生支持本地 Docker 及 Kubernetes 集群部署，满足不同规模的应用场景。
- **可扩展的运行时**：采用模块化设计，允许开发者无缝集成新的 MCP 工具服务，增强系统功能。

## 2. 快速入门

本项目依赖 Docker 进行部署。

#### 环境要求

请确保您的环境中已正确安装并运行 Docker 和 Docker Compose：

```bash
# 验证 Docker 版本
docker --version
docker compose --version

# 确认 Docker 守护进程正在运行
docker ps
docker compose ps
```

**步骤 1：环境配置项 与 Gaia 数据集准备**

1.  复制环境配置文件模板，并根据您的需求进行修改：

    ```bash
    cp ./gaia-mcp-server/mcp_servers/.env_template ./gaia-mcp-server/mcp_servers/.env
    ```

    请编辑 `./gaia-mcp-server/mcp_servers/.env` 文件，填入您的具体配置信息。

2.  从 Hugging Face 下载 [gaia_dataset](https://huggingface.co/datasets/gaia-benchmark/GAIA) 数据集，并将其存放至 `./gaia-mcp-server/docker/gaia_dataset` 目录下。

**步骤 2：启动 VirtualPC MCP Server**

```bash
sh run-local.sh
```

请监控终端输出，确保启动过程中没有错误信息。

**步骤 3：连接到 VirtualPC MCP Server**

使用以下配置信息连接到您的 VirtualPC MCP Server 实例：

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

## 3. 开发指南

### 3.1 添加自定义 MCP 工具

**步骤 1：开发 MCP 工具 (可选)**

如果您需要开发自定义的 MCP 工具并将其集成到 VirtualPC MCP Server 中，请在 `gaia-mcp-server/mcp_servers` 目录下创建您的项目，并实现工具代码。项目结构可参考 [hello_world](./gaia-mcp-server/mcp_servers/hello_world/) 示例。

项目规范：

1.  项目依赖应通过 `pyproject.toml` 文件进行管理，以便于 Docker 镜像的构建。

**步骤 2：注册 MCP 工具**

将您开发的或第三方的 MCP 工具注册到 VirtualPC MCP Server。

编辑 [MCP 工具注册文件](./gaia-mcp-server/mcp_servers/mcp_config.py)：

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

**步骤 3：更新 MCP 工具模式 (Schema)**

> **重要提示**：VirtualPC MCP Server 使用预生成的工具模式数据来响应 `list_tools()` 函数的调用。因此，在修改 MCP 服务器配置后，您必须更新 [mcp_tool_schema.json](./gaia-mcp-server/mcp_servers/mcp_tool_schema.json) 文件。

我们提供了一个 Python 脚本 [build_mcp_tool_schema.py](./gaia-mcp-server/mcp_servers/build_mcp_tool_schema.py) 来帮助您更新 `mcp_tool_schema.json`。在运行此脚本前，请确保 MCP 服务器的 [.env](./gaia-mcp-server/mcp_servers/.env) 文件已正确配置。

```bash
cd ./gaia-mcp-server/mcp_servers/
pip install mcp
python build_mcp_tool_schema.py
```

**步骤 4：构建 Docker 镜像并部署**

完成以上步骤后，请重新构建 Docker 镜像并部署服务。

## 4. 参与贡献

我们非常欢迎社区的贡献！如果您希望参与本项目，请遵循我们的贡献指南。我们鼓励您通过以下方式参与：

-   **报告问题**：发现 Bug 或有功能建议，请通过 [Issues](https://github.com/your-repo/issues) 提交。
-   **代码贡献**：请遵循标准的 Fork & Pull Request 流程。我们推荐您在提交前遵循项目既有的代码风格和标准。
-   **文档完善**：如果您发现文档中有任何遗漏或错误，欢迎提交修正。

## 5. 参考信息

### 致谢

-   **Magentic-UI 项目**：本项目集成了 [magentic-ui](https://github.com/microsoft/magentic-ui) 项目中的 Docker Browser 源代码。在此，我们向 magentic-ui 项目团队的卓越工作表示诚挚的感谢。

### 相关项目

-   [模型上下文协议 (MCP)](https://modelcontextprotocol.io/)
-   [Magentic-UI](https://github.com/microsoft/magentic-ui)
-   [Debian](https://www.debian.org/)

---

<div align="center">

**VirtualPC MCP Server** - 为 AI Agent 提供强大且可扩展的运行时环境

[license-image]: https://img.shields.io/badge/License-MIT-yellow.svg
[license-url]: https://opensource.org/licenses/MIT

</div>
