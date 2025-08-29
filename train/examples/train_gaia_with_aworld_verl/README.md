# Example: AWorld GAIA Agent + VeRL

## Installation
### Set 

### 1. Set up environment
We recommend using either conda or venv to create a new virtual environment.\
Python 3.10 is recommended.

### 2. Install mcp env
#### 2.1 Local Docker Deployment
##### Prerequisites
Ensure Docker and Docker Compose are properly installed and operational:
```bash
# Verify Docker installation
docker --version
docker compose --version

# Verify Docker daemon is running
docker ps
docker compose ps
```

**Step 1: Configure Environment and Prepare Gaia Dataset**

1. Copy the environment template and configure your settings:

```bash
cd {path/to/AWorld}/env
cp ./gaia-mcp-server/mcp_servers/.env_template ./gaia-mcp-server/mcp_servers/.env
```

Edit `./gaia-mcp-server/mcp_servers/.env` with your specific configuration values.
**Step 2: Launch VirtualPC MCP Server locally**

```bash
sh run-local.sh
```

Monitor the terminal output for any errors during startup.

Then you use the following configuration to connect to the VirtualPC MCP Server:

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

**Note**: The Bearer token above is for local testing only. The `MCP_SERVERS` header specifies the MCP server scope for your current connection, which should be a subset of server names defined in `gaia-mcp-server/mcp_servers/mcp_config.py`.\
If you want to deploy mcp servers on Kubernetes cluster, please refer to the documentation ([env/README.md § 2.2 Kubernetes Cluster Deployment](../../../env/README.md#22-kubernetes-cluster-deployment)).

### 3. Install Aworld
    ```bash
    pip install aworld
    ```
### 4. Install verl
    ```bash
    pip install verl==0.5.0
    ```

## Quick Start
```bash
cd {path/to/AWorld}/train/examples/train_gaia_with_aworld_verl
```
### 1. Init env and build your agent
The example code is already provided in `custom_agent_loop.py`.

**Step 1: Configure Tool Environment**

First, configure the `TOOL_ENV_CONFIG` dictionary. The parameters for this configuration should come from the VirtualPC MCP Server connection details provided in section "[2. Install mcp env](#2-install-mcp-env)".

```python
# Use the connection details from the VirtualPC MCP Server
# "url" -> "url"
# "headers.Authorization" -> "authorization"
# "headers.MCP_SERVERS" -> "mcp_servers"
TOOL_ENV_CONFIG = {
    "url": "http://localhost:8000/mcp",
    "authorization": "Bearer <YOUR_TOKEN>",
    "mcp_servers": "readweb-server,browser-server",
}
```
**Note**: Please replace `<YOUR_TOKEN>` with the actual Bearer Token you obtained above.

**Step 2: Develop Agent and Pass Configuration**

Next, in your agent development logic, pass the `TOOL_ENV_CONFIG` to the agent's parameters. The `get_agent_tool_env_and_servers` function will process it and return the appropriate `mcp_config` and `mcp_servers` for the `Agent`.

```python
class GaiaAgentLoop(AworldAgentLoop):
    def build_agents(self, model_name: str = "", base_url: str = "", api_key: str = "") -> Union[Agent, Swarm]:
        # Process the tool config
        tool_env_config, tool_servers = get_agent_tool_env_and_servers(TOOL_ENV_CONFIG)
        
        # Pass the config to the Agent
        return Agent(
            conf=AgentConfig(
                llm_model_name=model_name,
                llm_base_url=base_url,
                llm_api_key=api_key,
                llm_provider="openai",
            ),
            name="gaia_super_agent",
            system_prompt=GAIA_SYSTEM_PROMPT,

            # MCP tool configuration for the agent
            mcp_config=tool_env_config,
            mcp_servers = tool_servers,
        )
```
- Edit `agent.yaml` if needed. By default it points to `custom_agent_loop.GaiaAgentLoop`.


### 2. Run training
```bash
bash run.sh
```


## Advanced: Customize `run.sh` 
Before starting the training, you can modify the configuration in `run.sh`:
- **reward_fn_file_path** and **reward_fn_name**: Point to your reward function file and exported function name (e.g., `gaia_reward_func`).
- **agent_loop_config_path** and **AGENT_TOOL_CONFIG_PATH**: Paths to your agent loop config (`agent.yaml`) and tool config (`tool.yaml`). Note `AGENT_TOOL_CONFIG_PATH` is exported as an environment variable.
- **dummy_tool_config_path** (optional): Set to enable auto tool choice.

Example:
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
