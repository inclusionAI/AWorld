## Agent Training

AWorld Training bridges AWorld Agents with external training frameworks (e.g., Reinforcement Learning libraries). It is framework-agnostic, enabling you to bring AWorld Agents or Swarms into your preferred training environment.

![Architecture Diagram](../../readme_assets/train_env_agent_architecture.png)

---

## 1. Environment Host Setup (Locally)

First you need to deploy an environment to host the tools your Agent will use (via MCP servers). Or you can just use your training machines.

### Machine Sizing Tips

- **Capacity planning**: roughly 2C4G per concurrent worker
- **Example**: concurrency=8 → plan for ~16C and ~32G

### Prepare Repository and Environment Variables

```bash
# Clone AWorld
git clone git@github.com:inclusionAI/AWorld.git
cd /path/to/AWorld

# Copy .env template for MCP servers
cp ./env/gaia-mcp-server/mcp_servers/.env_template \
   ./env/gaia-mcp-server/mcp_servers/.env
```

Edit `./env/gaia-mcp-server/mcp_servers/.env` and configure tokens for the tools you need:

```
JINA_API_KEY=<YOUR_JINA_API_KEY>
TAVILY_API_KEY=<YOUR_TAVILY_API_KEY>
GOOGLE_API_KEY=<YOUR_GOOGLE_API_KEY>
GOOGLE_CSE_ID=<YOUR_GOOGLE_CSE_ID>
DATALAB_API_KEY=<YOUR_DATALAB_API_KEY>
E2B_API_KEY=<YOUR_E2B_API_KEY>

MCP_LLM_BASE_URL=<YOUR_MCP_LLM_BASE_URL>
MCP_LLM_MODEL_NAME=<YOUR_MCP_LLM_MODEL_NAME>
MCP_LLM_API_KEY=<YOUR_MCP_LLM_API_KEY>

BROWSERUSE_LLM_BASE_URL=${MCP_LLM_BASE_URL}
BROWSERUSE_LLM_MODEL_NAME=${MCP_LLM_MODEL_NAME}
BROWSERUSE_LLM_API_KEY=${MCP_LLM_API_KEY}
CODE_LLM_BASE_URL=${MCP_LLM_BASE_URL}
CODE_LLM_MODEL_NAME=${MCP_LLM_MODEL_NAME}
CODE_LLM_API_KEY=${MCP_LLM_API_KEY}
THINK_LLM_BASE_URL=${MCP_LLM_BASE_URL}
THINK_LLM_MODEL_NAME=${MCP_LLM_MODEL_NAME}
THINK_LLM_API_KEY=${MCP_LLM_API_KEY}
GUARD_LLM_BASE_URL=${MCP_LLM_BASE_URL}
GUARD_LLM_MODEL_NAME=${MCP_LLM_MODEL_NAME}
GUARD_LLM_API_KEY=${MCP_LLM_API_KEY}
AUDIO_LLM_BASE_URL=${MCP_LLM_BASE_URL}
AUDIO_LLM_MODEL_NAME=${MCP_LLM_MODEL_NAME}
AUDIO_LLM_API_KEY=${MCP_LLM_API_KEY}
IMAGE_LLM_BASE_URL=${MCP_LLM_BASE_URL}
IMAGE_LLM_MODEL_NAME=${MCP_LLM_MODEL_NAME}
IMAGE_LLM_API_KEY=${MCP_LLM_API_KEY}
VIDEO_LLM_BASE_URL=${MCP_LLM_BASE_URL}
VIDEO_LLM_MODEL_NAME=${MCP_LLM_MODEL_NAME}
VIDEO_LLM_API_KEY=${MCP_LLM_API_KEY}
```

### Start the MCP Server

```bash
cd /path/to/Aworld
# use --docker_dir to specify the docker directory to build (e.g., gaia-mcp-server)
python -m env.train_env --docker_dir=gaia-mcp-server
```

Once started, you will see connection details:

```bash
{
  "ip": "1xx.1xx.x.xx",
  "port": 8000,
  "token": "eyJhbGciOi...rYmQ"
}
```

You will need `ip`, `port`, and `token` in Part 2.

Kubernetes deployment instructions will be provided in future updates.

---

## 2. Training Cluster Setup

The training cluster runs your training framework and your Agent or Swarm.
The following steps will guide you through the setup process.

### 1) Provide MCP Connection to the Agent

Export the connection details obtained from Part 1:

```bash
# Replace <ip>, <port>, and <token> with values from Part 1
export MCP_SERVER_URL=http://<ip>:<port>/mcp
export MCP_SERVER_TOKEN=<token>

# Alternatively, write them into a local .env
# echo "MCP_SERVER_URL=http://<ip>:<port>/mcp" >> .env
# echo "MCP_SERVER_TOKEN=<token>" >> .env
```

### 2) Install AWorld and Your Training Framework

```bash
# Python >= 3.10 is recommended

# Install AWorld
pip install aworld

# Framework-specific dependencies (VeRL example)
pip install verl==0.5.0
```

### 3) Define Your Agent 
```python
from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig


agent = Agent(
            name="gaia_super_agent",
            system_prompt="YOUR SYSTEM PROMPT",
    
            # LLM conf
            conf=AgentConfig(
                # LLM server parameters
                llm_base_url="<your_base_url>",
                llm_model_name="<your_model_name>",
                llm_api_key="<your_api_key>"
            ),

            # MCP tool configuration for the agent
            mcp_config={},
            mcp_servers=[],
        )
```

#### 3.1) Integrate with RL Framework (VeRL Example)

In VeRL, you implement a custom `AgentLoop`. The example below defines `GaiaAgentLoop` by extending `AworldAgentLoop` and building a single Agent configured with MCP tools.

```python
from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig

from train.adapter.verl.aworld_agent_loop import AworldAgentLoop
from train.adapter.verl.common import get_agent_tool_env_and_servers


class GaiaAgentLoop(AworldAgentLoop):
    async def build_agents(self):
        # Fetch environment configuration and MCP servers.
        # The MCP server must be running (Part 1) and
        # MCP_SERVER_URL/MCP_SERVER_TOKEN must be set.
        gaia_env_config, gaia_env_servers = get_agent_tool_env_and_servers()

        return Agent(
            conf=AgentConfig(
                # Dynamically get the LLM server from VeRL-managed services
                llm_base_url=await self.get_llm_server_address(),
                llm_model_name=await self.get_llm_server_model_name(),
                llm_api_key="dummy"
            ),
            name="gaia_super_agent",
            system_prompt="YOUR SYSTEM PROMPT",

            # MCP tool configuration for the agent
            mcp_config=gaia_env_config,
            mcp_servers=gaia_env_servers,
        )
```

### 4) Run Training (VeRL Example)

With the training cluster and agent ready, now you can run the training script.

For example, in VeRL, specify your custom `AgentLoop` in `agent.yaml`:

```yaml
# In agent.yaml
- name: gaia_agent
  _target_: train.examples.train_gaia_with_aworld_verl.custom_agent_loop.GaiaAgentLoop
```

Then start the training script (e.g., a `run.sh` in the VeRL example):

```bash
bash run.sh
```

For configuration parameters, refer to the [VeRL documentation](https://verl.readthedocs.io/en/latest/examples/config.html).

---

## 3. Advanced Tutorial

### Train a Complex Swarm

Instead of returning a single `Agent` from `build_agents`, return a `Swarm` composed of multiple agents. The adapter handles the rest.

```python
from typing import Union
from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import Swarm
from aworld.config.conf import AgentConfig


async def build_agents(self, ...) -> Union[Agent, Swarm]:
    # Agent to be trained: LLM address/model provided by VeRL
    agent_to_be_train = Agent(
        conf=AgentConfig(
            llm_base_url=await self.get_llm_server_address(),
            llm_model_name=await self.get_llm_server_model_name(),
        ),
    )

    # Supporting agents: provide ready-to-use OpenAI-compatible endpoints
    plan_agent = Agent(conf=AgentConfig(llm_base_url="", llm_model_name="", llm_api_key=""))
    exe_agent = Agent(conf=AgentConfig(llm_base_url="", llm_model_name="", llm_api_key=""))
    sum_agent = Agent(conf=AgentConfig(llm_base_url="", llm_model_name="", llm_api_key=""))

    return Swarm(
        agent_to_be_train, plan_agent, exe_agent, sum_agent,
        # ... other swarm configuration
    )
```

### Integrate with Other Training Frameworks

AWorld Train is extensible. To add a new framework (e.g., "Swift"):

1. **Create a new adapter** in `train/adapter/<framework>/`.
2. **Implement core logic** (e.g., `AworldAgentTrainer`) that:
   - Receives tasks/observations from the framework
   - Runs the AWorld Agent (`Runners.sync_run(input=input, agent=agent)`) to get an action
   - Returns the agent response to the framework
   - Handles rewards and updates
3. **Add an example** in `train/examples/` to demonstrate usage.

Use the existing `verl` adapter (`train/adapter/verl/`) as a reference.


