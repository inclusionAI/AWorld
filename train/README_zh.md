<div align="center">

# AWorld Train

*为使用 AWorld 构建的智能体，提供与外部 RL/训练框架对接的、与框架无关的适配层、可运行示例与通用工具*

[![License: MIT][license-image]][license-url]

</div>

---

AWorld Train 为 AWorld 智能体生态系统和各种外部训练框架（如强化学习库）之间提供了一座桥梁。它被设计为框架无关的，可以在你喜欢的训练环境中使用AWorld 智能体。

## 安装

推荐使用 Python>=3.10。

```bash
# 安装 AWorld
pip install aworld

# 安装特定框架的依赖（以 VeRL 为例）
pip install verl==0.5.0
```

## 快速开始

使用外部框架训练一个 AWorld 智能体只需 3 个步骤。

我们将以 GAIA 智能体和 VeRL 框架为例。

### 1. 创建环境
首先，您需要创建一个智能体可以与之交互的训练环境。
创建环境时，某些工具可能需要您配置身份验证凭据。这可以通过设置环境变量来完成（建议在 `.env` 文件中管理它们）。

例如，要运行 GAIA 任务，需要设置以下变量：
```bash
export GOOGLE_API_KEY={YOUR_GOOGLE_API_KEY}
```

然后使用 `train_env` 工具来创建训练环境，并为智能体获取环境配置。
```python
from train.train_env import TrainEnv

gaia_env = TrainEnv()
# 针对本地工具环境
gaia_env = gaia_env.create_env(name="GAIA", mode="local")

# 'gaia_env.get_env_config()' 对象现在包含了 MCP 服务器的连接配置，
# 可以将其传递给智能体。
# 关于分布式环境的创建，请参考 env/README.md。
```

### 2. 创建智能体
接下来，定义您的智能体。这是一个标准的 AWorld Agent。将上一步中创建的环境配置传递给智能体的 `mcp_config`。

```python
from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig

# 假设 'gaia_env' 包含 {'mcp_config': {...}, 'mcp_servers': '...'}
gaia_agent = Agent(
    conf=AgentConfig(
        llm_model_name="your-model-name",
        llm_base_url="your-llm-base-url",
        llm_api_key="your-llm-api-key",
        llm_provider="openai",
    ),
    name="gaia_super_agent",
    system_prompt="You are a helpful AI assistant.",

    # 传入 MCP 工具配置
    mcp_config=gaia_env.get("mcp_config"),
    mcp_servers=gaia_env.get("mcp_servers"),
)
```

### 3. 开始训练
环境和智能体准备就绪后，下一步是将其集成到您所选训练框架的循环中。对于 VeRL，这是通过实现一个自定义的 `AgentLoop` 来完成的。

您可以继承自基础的 `AworldAgentLoop` 并实现 `build_agents` 方法。在这里您可以创建环境和智能体，并将它们连接在一起。

<details>
<summary>Click to expand example code</summary>

```python
# 在您的 custom_agent_loop.py 文件中
class GaiaAgentLoop(AworldAgentLoop):
  def build_agents(self, ...):
      # 创建环境
      gaia_env = TrainEnv()
      gaia_env.create_env(name="GAIA", mode="local")

      # 创建并返回智能体，传入环境配置
      return Agent(
          ...,
          mcp_config=gaia_env.get_env_config().get("mcp_config"),
          mcp_servers=gaia_env.get_env_config().get("mcp_servers"),
      )
```

</details>

接下来，您必须在 `agent.yaml` 配置文件中指定您的自定义 `AgentLoop`，以告知训练器使用哪个循环。

```yaml
# 在 agent.yaml 中
- name: gaia_agent
  _target_: train.examples.train_gaia_with_aworld_verl.custom_agent_loop.GaiaAgentLoop
```

最后，运行训练脚本：
```bash
cd ./examples/train_gaia_with_aworld_verl
bash run.sh
```
该脚本运行 VeRL 中的AgentLoop、奖励计算和模型训练。
关于 `run.sh` 中的参数设置，请参考 [VeRL 文档](https://verl.readthedocs.io/en/latest/examples/config.html)。

### 完整示例

要获取一个完整的、可运行的代码示例，请参考 [`./examples/train_gaia_with_aworld_verl/`](./examples/train_gaia_with_aworld_verl/) 目录下的示例。

## 进阶教程

### 如何创建复杂的多智能体集群 (Swarm)
除了单个智能体，您也可以训练一个多智能体集群（Swarm）。只需让您的 `build_agents` 方法（或等效的设置函数）返回一个 `Swarm` 对象而不是单个 `Agent` 对象即可。AWorld 和训练适配器将处理剩下的部分。

```python
# 在 自定义的AgentLoop 中
def build_agents(self, ...) -> Union[Agent, Swarm]:
    # ... (创建单个智能体)
    # create env
    train_env = TrainEnv()
    gaia_env = train_env.create_env(name="GAIA", mode="local")
    planner_agent = ...
    worker_agent_1 = ...
    worker_agent_2 = ...

    # 返回由多个智能体组成的 Swarm
    return Swarm(
        planner_agent, worker_agent_1, worker_agent_2,
        # ... 其他 swarm 配置
    )
```

### 如何集成其他训练框架
AWorld Train 被设计为可扩展的。要为新的训练框架（例如 “Swift”）添加支持，通常需要：

1.  **创建新的适配器**：在 `train/adapter/` 目录内，为您的框架创建一个新文件夹（例如 `swift/`）。
2.  **实现核心逻辑**：创建一个主类（例如 `AworldAgentTrainer`），它继承自目标框架的某个基类。这个类将负责：
    *   从框架的环境中接收任务或观察结果。
    *   运行 AWorld 智能体（`Runners.sync_run(input=input, agent=agent)`）以获取动作。
    *   将智能体的响应返回给框架。
    *   处理奖励和更新。
3.  **创建示例**：在 `train/examples/` 目录中添加一个新示例，以演示如何使用新的适配器。

可以参考现有的 `verl` 适配器（`train/adapter/verl/`）作为参考实现。

---

<div align="center">

**AWorld Train** — 让你的 AWorld 智能体快速接入主流训练框架

[license-image]: https://img.shields.io/badge/License-MIT-yellow.svg
[license-url]: https://opensource.org/licenses/MIT

</div>


