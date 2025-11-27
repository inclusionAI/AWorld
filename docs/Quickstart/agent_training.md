# AWorld Train

AWorld Training bridges AWorld Agents with external training frameworks (e.g., Reinforcement Learning libraries). It is framework-agnostic, enabling you to bring AWorld Agents or Swarms into your preferred training environment.
![Architecture Diagram](../imgs/train_env_agent_architecture.png)
To implement agent evolution in AWorld train, five modules need to be considered:

1. **Agent construction (` agent `):** Build the core logic, strategies, and decision-making capabilities of the agent.
2. **Tool Environment Settings (` env `):** Build (optional) and configure the tool environment used by the agent,
   define the state/action space and interaction mechanism with the agent.
3. **Prepare dataset (`dataset`):** The dataset required for Agent training.
4. **Reward function (`reward`):** Evaluate the performance of the agent and return a reward.
5. **Training Execution (`trainer`): ** Training related configurations and training frameworks used.

3, 4, 5 belongs to the adaptation integration module, which is related to the compatibility with specific training
frameworks such as Verl. AWorld train has provided support for VeRL and AReaL, making it more convenient for users to
use.

## Building a Custom Agent

Use AWorld's agent building capabilities to create agents.

```python
import os
from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig

mcp_config = {
    "mcpServers": {
        "gaia_server": {
            "type": "streamable-http",
            "url": "https://playground.aworldagents.com/environments/mcp",
            "timeout": 600,
            "sse_read_timeout": 600,
            "headers": {
                "ENV_CODE": "gaia",
                "Authorization": f'Bearer {os.environ.get("INVITATION_CODE", "")}',
            }
        }
    }
}

agent_config = AgentConfig(
    llm_provider="verl",
    top_k=80
)
agent = Agent(
    name="gaia_agent",
    desc="gaia_agent",
    system_prompt="Gaia agent system prompt",
    mcp_config=mcp_config,
    mcp_servers=["gaia_server"],
    conf=agent_config
)
```

- **Agent Construction**: For details on building single-agent or multi-agent systems, please refer to the [_Building
  and Running an Agent_](https://inclusionai.github.io/AWorld/Quickstart/agent_construction/#) and [_Building and
  Running a Multi-Agent System_](https://inclusionai.github.io/AWorld/Quickstart/multi-agent_system_construction/)
  guides.
- **MCP Tools**: If your agent requires MCP tools, you must configure the corresponding `mcp_config` file. Instructions
  can be found in the [_Building and Running an Agent_]() guide.

## Start-up Training

AWorld train is a one-click coding pattern that generally requires four items:
**agent**, **dataset**, **reward function**, and **custom training configuration**.

Note: Environment variables are independently configured and it is recommended to write them in the `.env` file.

[Gaia Training Startup Code](https://github.com/inclusionAI/AWorld/blob/main/train/examples/train_gaia_with_aworld_verl/main.py)

Simple example：

```python
from train.trainer.agent_trainer import AgentTrainer

# 定义数据集
train_dataset, test_dataset = "None or string or code reference"
# 定义agent
agent = ...
# 定义训练配置
custom_train_config = "string or json"
# 定义reward
reward_func = "None or string or code reference"
# 构建trainer实例并启动训练
trainer = AgentTrainer(agent=agent,
                       config=custom_train_config,
                       reward_func=reward_func,
                       train_dataset=train_dataset,
                       test_dataset=test_dataset)
trainer.train()
```

### Dataset

A dataset used for training intelligent agents.
It can be used as a file path or a Huggingface Dateset instance (generally secondary processing is required)
as a parameter and provided to the trainer.

### Reward Function

Define or adjust reward functions for evaluating agent behavior based on specific task objectives.
Taking training gaia as an example, the following code implements the `reward function` logic required by gaia,
code: [gaia_reward_function.py](https://github.com/inclusionAI/AWorld/blob/main/train/examples/train_gaia_with_aworld_verl/reward/gaia_reward_function.py )

Note: The Reward function is recommended as a separate file.

### Custom Training Configuration

YAML format configuration file, used to configure training related parameters based on actual situations, for defining
training parameters such as iteration times, learning rate, batch size, etc.
Configuration
example: [rpo_trainer.yaml](https://github.com/inclusionAI/AWorld/blob/main/train/examples/train_gaia_with_aworld_verl/grpo_trainer.yaml )

### Supplementary

Please pay special attention to the following core configuration items. When values are empty,
AWorld will **automatically** set them based on the user's trainer:

+ `train_files`, `val_files`: Specify the file paths for the training dataset and validation dataset, in `data`.
+ `agent_loop_config_path`: Specify the configuration file for the custom Agentloop, in `actor_rollout_def.rollout.agent`.
+ `reward_fn_file_path`: Defines the file path where the reward function is located, in `custom_deward_function`.
+ `reward_fn_name`: Specifies the name of the reward function to be used, in `custom_deward_function`.

Refer to the [VeRL documentation](https://verl.readthedocs.io/en/latest/examples/config.html) for detailed parameters.
