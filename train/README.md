<div align="center">

# AWorld Train

*Framework-agnostic training adapters, examples, and utilities for training AWorld agents with external RL/training frameworks*

[![License: MIT][license-image]][license-url]

</div>

---

AWorld Train provides a bridge between the AWorld agent ecosystem and various external training frameworks like Reinforcement Learning (RL) libraries. It is designed to be framework-agnostic, allowing you to bring your AWorld agents to your favorite training environments.

## Installation

Python>=3.10 is recommended.

```bash
# Install AWorld
pip install aworld

# Framework-specific deps (VeRL example)
pip install verl==0.5.0
```

## Quick Start

Training an AWorld agent with an external framework can be done in 3 steps.

We'll use the GAIA agent with VeRL as an example.


### 1. Create an Environment
First, you need to set up the environment where the agent's tools will run. On your chosen machine (which can be a training machine), create a `.env` file to configure authentication tokens for any required tools:

```.env
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

Next, run the startup script to launch the MCP server locally:

```bash
sh start_env.sh
```

Once the MCP server starts successfully, it will output the connection details:
```bash
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
You need to capture the URL and token from this output. Export them as environment variables or add them to your `.env` file so your agent can connect to the tool servers:
```bash
# export them as environment variables
export MCP_SERVER_URL=http://<ip>:<port>/mcp
export MCP_SERVER_TOKEN=<tokenid>

# or add them to `.env` file
# echo "MCP_SERVER_URL=http://<ip>:<port>/mcp" >> .env
# echo "MCP_SERVER_TOKEN=<tokenid>" >> .env
```

For instructions on deploying the environment on Kubernetes, please refer to [`../env/README.md`](../env/README.md).


### 2. Create an Agent or Swarm
With the environment ready, the next step is to define your custom agent in your chosen training framework's loop. For VeRL, this is done by implementing a custom `AgentLoop`.

For example, `GaiaAgentLoop` inherits from `AworldAgentLoop` and implements the `build_agents` method.

```python
from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig

from train.adapter.verl.aworld_agent_loop import AworldAgentLoop
from train.adapter.verl.common import get_agent_tool_env_and_servers

class GaiaAgentLoop(AworldAgentLoop):
    def build_agents(self):
        # Get env config and servers.
        # Note: You must start the MCP server and set the URL and token
        # in your environment variables as described in Step 1.
        gaia_env_config, gaia_env_servers = get_agent_tool_env_and_servers()

        return Agent(
            conf=AgentConfig(
                # Get the dynamic llm server address from the server manager. 
                # The llm server is launched within VeRL.
                llm_base_url=self.get_llm_server_address(),
                llm_model_name=self.get_llm_server_model_name(),
            ),
            name="gaia_super_agent",
            system_prompt="YOUR SYSTEM PROMPT",

            # MCP tool configuration for the agent
            mcp_config=gaia_env_config,
            mcp_servers=gaia_env_servers,
        )
```

The following diagram illustrates the overall architecture and the interaction between the Agent and the Environment:

![Architecture Diagram](../readme_assets/train_env_agent_architecture.png)


### 3. Run Training
Before run training, specify your custom `AgentLoop` in the `agent.yaml`:

```yaml
# In agent.yaml
- name: gaia_agent
  _target_: train.examples.train_gaia_with_aworld_verl.custom_agent_loop.GaiaAgentLoop
```

Finally, run the training script. This script is typically a `run.sh` file based on the VeRL example.
```bash
bash run.sh
```
This script handles the training loop, reward calculation, and agent updates, orchestrated by VeRL.
Please refer to the [VeRL documentation](https://verl.readthedocs.io/en/latest/examples/config.html) for parameter settings in `run.sh`.

A complete, runnable example, including a `run.sh` script tailored for `GaiaAgentLoop`, is available in [`./examples/train_gaia_with_aworld_verl/`](./examples/train_gaia_with_aworld_verl/).

## Advanced Tutorial

### How to Create a Complex Swarm
Instead of a single agent, you can also train a multi-agent swarm. Simply have your `build_agents` method (or equivalent setup function) return a `Swarm` object instead of a single `Agent`. AWorld and the training adapter will handle the rest.

```python
# In your AgentLoop or setup file
def build_agents(self, ...) -> Union[Agent, Swarm]:
    # ... (create individual agents)
    agent_to_be_train = Agent(
      conf=AgentConfig(
          # For the agent to be trained, llm_base_url and llm_model_name are obtained from the services launched by VeRL
          llm_base_url=self.get_llm_server_address(),
          llm_model_name=self.get_llm_server_model_name(),
      ),
    )

    plan_agent = Agent(
      conf=AgentConfig(
          # Provide a ready-to-use OpenAI-compatible llm service address, model name, and api_key
          llm_base_url="",
          llm_model_name="",
          llm_api_key=""
      ),
    )
    
    exe_agent = Agent(
      conf=AgentConfig(
          # Provide a ready-to-use OpenAI-compatible llm service address, model name, and api_key
          llm_base_url="",
          llm_model_name="",
          llm_api_key=""
      ),
    )
    
    sum_agent = Agent(
      conf=AgentConfig(
          # Provide a ready-to-use OpenAI-compatible llm service address, model name, and api_key
          llm_base_url="",
          llm_model_name="",
          llm_api_key=""
      ),
    )

    # Return a Swarm composed of your agents
    return Swarm(
        agent_to_be_train, plan_agent, exe_agent, sum_agent,
        # ... other swarm configuration
    )
```

### How to Integrate with Other Training Frameworks
AWorld Train is designed for extensibility. To add support for a new training framework (e.g., "Swift"), you would typically need to:

1.  **Create a new Adapter**: Inside the `train/adapter/` directory, create a new folder for your framework (e.g., `swift/`).
2.  **Implement the Core Logic**: Create a primary class (e.g., `AworldAgentTrainer`) that inherits from a base class of the target framework. This class will be responsible for:
    *   Receiving tasks or observations from the framework's environment.
    *   Run the AWorld agent (`Runners.sync_run(input=input, agent=agent)`) to get an action.
    *   Returning the agent's response back to the framework.
    *   Handling rewards and updates.
3.  **Create an Example**: Add a new example in the `train/examples/` directory to demonstrate how to use the new adapter.

You can refer to the existing `verl` adapter (`train/adapter/verl/`) as a reference implementation.

---

<div align="center">

**AWorld Train** — Bring your AWorld agents to your favorite training frameworks

[license-image]: https://img.shields.io/badge/License-MIT-yellow.svg
[license-url]: https://opensource.org/licenses/MIT

</div>
