# 示例：AWorld GAIA Agent + VeRL

[English](./README.md)

## 安装
### 1. 环境准备
建议使用 conda 或 venv 创建新的虚拟环境。\
    推荐使用 Python 3.10。
### 2. 安装 MCP 环境
#### 2.1 本地 Docker 部署
##### 先决条件
确保已正确安装并可正常使用 Docker 与 Docker Compose：
```bash
# 验证 Docker 安装
docker --version
docker compose --version

# 验证 Docker 守护进程运行状态
docker ps
docker compose ps
```

**步骤 1：配置环境并准备 Gaia 数据集**

1. 复制环境模板并进行配置：

```bash
cd {path/to/AWorld}/env
cp ./gaia-mcp-server/mcp_servers/.env_template ./gaia-mcp-server/mcp_servers/.env
```

编辑 `./gaia-mcp-server/mcp_servers/.env`，填入你的本地配置。

**步骤 2：本地启动 VirtualPC MCP Server**

```bash
sh run-local.sh
```

监控终端输出，查看启动过程中是否有任何错误。

然后就可以使用以下配置连接到 VirtualPC MCP Server：

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

**注意**：上述 Bearer token 仅用于本地测试。`MCP_SERVERS` 头部指定了当前连接的 MCP 服务器范围，应该是 `gaia-mcp-server/mcp_servers/mcp_config.py` 中定义的服务器名称的子集。\
如果想在Kubernetes 集群部署MCP Server，请参考文档([env/README.md § 2.2 Kubernetes Cluster Deployment](../../../env/README.md#22-kubernetes-cluster-deployment))。

### 3. 安装 AWorld
```bash
pip install aworld
```
### 4. 安装 VeRL
```bash
pip install verl==0.5.0
```

## 快速开始
```bash
cd {path/to/AWorld}/train/examples/train_gaia_with_aworld_verl
```
### 1. 初始化工具环境并构建你的 Agent
示例代码已在 `custom_agent_loop.py` 中提供。

**步骤 1: 配置工具环境**

首先，配置 `TOOL_ENV_CONFIG` 。此配置的参数应来自于“[2. 安装 MCP 环境](#2-安装-mcp-环境)”章节中提供的 VirtualPC MCP Server 连接详细信息。

```python
# 使用 VirtualPC MCP Server 的连接信息
# "url" -> "url"
# "headers.Authorization" -> "authorization"
# "headers.MCP_SERVERS" -> "mcp_servers"
TOOL_ENV_CONFIG = {
    "url": "http://localhost:8000/mcp",
    "authorization": "Bearer <YOUR_TOKEN>",
    "mcp_servers": "readweb-server,browser-server",
}
```
**注意**：请将 `<YOUR_TOKEN>` 替换为你在安装-mcp-环境的步骤2中获取的实际 Bearer Token。

**步骤 2: 开发 Agent 并传入配置**

接下来，在你的 Agent 开发逻辑中，将 `TOOL_ENV_CONFIG` 传递给 Agent 的参数。用`get_agent_tool_env_and_servers` 函数处理后，为 `Agent` 返回合适的 `mcp_config` 和 `mcp_servers`。

```python
class GaiaAgentLoop(AworldAgentLoop):
    def build_agents(self, model_name: str = "", base_url: str = "", api_key: str = "") -> Union[Agent, Swarm]:
        # 处理工具配置
        tool_env_config, tool_servers = get_agent_tool_env_and_servers(TOOL_ENV_CONFIG)
        
        # 将配置传递给 Agent
        return Agent(
            conf=AgentConfig(
                llm_model_name=model_name,
                llm_base_url=base_url,
                llm_api_key=api_key,
                llm_provider="openai",
            ),
            name="gaia_super_agent",
            system_prompt="",

            # Agent 的 MCP 工具配置
            mcp_config=tool_env_config,
            mcp_servers = tool_servers,
        )
```
- 如有需要，编辑 `agent.yaml`。agent默认指向 `custom_agent_loop.GaiaAgentLoop`。

### 2. 运行训练
```bash
bash run.sh
```

## 进阶：自定义 `run.sh`

在启动训练前，可在 `run.sh` 中修改配置：
- **reward_fn_file_path** 与 **reward_fn_name**：reward计算函数文件路径与导出函数名（例如 `gaia_reward_func`）。
- **agent_loop_config_path** 与 **AGENT_TOOL_CONFIG_PATH**：Agent loop 配置（`agent.yaml`）与工具配置（`tool.yaml`）的路径。注意 `AGENT_TOOL_CONFIG_PATH` 通过环境变量导出。
- **dummy_tool_config_path**（可选）：设置后可启用自动工具选择（auto tool choice）。

示例：
```bash
# =================== custom ===================
path_to_train="/abs/path/to/AWorld/train"

reward_fn_name=gaia_reward_func
reward_fn_file_path=${path_to_train}/examples/train_gaia_with_aworld_verl/metrics/gaia_reward_function.py

# Agent config
agent_loop_config_path=${path_to_train}/examples/train_gaia_with_aworld_verl/agent.yaml
export AGENT_TOOL_CONFIG_PATH=${path_to_train}/examples/train_gaia_with_aworld_verl/configs/tool.yaml

# Optional: enable auto_tool_choice with a dummy tool config
dummy_tool_config_path=${path_to_train}/examples/train_gaia_with_aworld_verl/configs/dummy_tool_config.yaml
```

