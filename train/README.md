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
First, you need to create a training environment that agents can interact with. 
When creating an environment, some tools may require you to configure authentication credentials. This can be done by setting environment variables (we recommend managing them in a `.env` file).

For example, to run the GAIA task, you need to set the following variable:
```bash
export GOOGLE_API_KEY={YOUR_GOOGLE_API_KEY}
```

Then use `train_env` utility to create your training environment and get environment configs for agents.
```python
from train import train_env

# For local tool environment
gaia_env = train_env.create("GAIA", mode="local")

# The 'gaia_env' object now holds the connection configuration for the MCP server,
# which can be passed to your agent.
# For distributed environment creation, please refer to env/README.md.
```

### 2. Create an Agent or Swarm
Next, define your agent. This is a standard AWorld agent. The key is to pass the environment configuration you created in the previous step to the agent's `mcp_config`.

```python
from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig

# Assuming 'gaia_env' contains {'mcp_config': {...}, 'mcp_servers': '...'}
gaia_agent = Agent(
    conf=AgentConfig(
        llm_model_name="your-model-name",
        llm_base_url="your-llm-base-url",
        llm_api_key="your-llm-api-key",
        llm_provider="openai",
    ),
    name="gaia_super_agent",
    system_prompt="You are a helpful AI assistant.",

    # Pass the MCP tool configuration from the environment
    mcp_config=gaia_env.get("mcp_config"),
    mcp_servers=gaia_env.get("mcp_servers"),
)
```

### 3. Run Training
With the environment and agent ready, the next step is to integrate them into your chosen training framework's loop. For VeRL, this is done by implementing a custom `AgentLoop`.

You can inherit from the base `AworldAgentLoop` and implement the `build_agents` method. This is where you create the environment and agent, and link them together.

<details>
<summary>Click to expand example code</summary>

```python
# In your custom_agent_loop.py
class GaiaAgentLoop(AworldAgentLoop):
  def build_agents(self, ...):
      # Create the environment
      train_env = TranEnv()
      gaia_env = train_env.create_env(name="GAIA", mode="local")

      # Create and return the agent, passing in the env config
      return Agent(
          ...,
          mcp_config=gaia_env.get("mcp_config"),
          mcp_servers=gaia_env.get("mcp_servers"),
      )
```

</details>

Next, you must specify your custom `AgentLoop` in the `agent.yaml` configuration file to tell the trainer which loop to use.

```yaml
# In agent.yaml
- name: gaia_agent
  _target_: train.examples.train_gaia_with_aworld_verl.custom_agent_loop.GaiaAgentLoop
```

Finally, run the training script:
```bash
cd ./examples/train_gaia_with_aworld_verl
bash run.sh
```
This script handles the training loop, reward calculation, and agent updates, orchestrated by VeRL.
Please refer to the [VeRL documentation](https://verl.readthedocs.io/en/latest/examples/config.html) for parameter settings in `run.sh`.

### A Complete Example

For a full, runnable code example, please refer to the example at [`./examples/train_gaia_with_aworld_verl/`](./examples/train_gaia_with_aworld_verl/).

## Advanced Tutorial

### How to Create a Complex Swarm
Instead of a single agent, you can also train a multi-agent swarm. Simply have your `build_agents` method (or equivalent setup function) return a `Swarm` object instead of a single `Agent`. AWorld and the training adapter will handle the rest.

```python
# In your AgentLoop or setup file
def build_agents(self, ...) -> Union[Agent, Swarm]:
    # ... (create individual agents)
    planner_agent = ...
    worker_agent_1 = ...
    worker_agent_2 = ...

    # Return a Swarm composed of your agents
    return Swarm(
        planner_agent, worker_agent_1, worker_agent_2,
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
