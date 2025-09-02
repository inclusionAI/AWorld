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
首先，您需要为智能体的工具创建一个运行环境。选择一台机器（也可以是训练机），创建一个 `.env` 文件来为需要token验证的工具进行配置：

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

接下来，运行启动脚本，在机器本地启动 MCP 服务器：

```bash
sh start_env.sh
```

MCP 服务器成功启动后，它将输出连接详细信息：
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
您需要从此输出中获取 URL 和令牌。将它们导出为环境变量或添加到您的 `.env` 文件中，以便您的智能体可以连接到工具服务器：
```bash
# 导出为环境变量
export MCP_SERVER_URL=http://<ip>:<port>/mcp
export MCP_SERVER_TOKEN=<tokenid>

# 或者添加到 `.env` 文件中
# echo "MCP_SERVER_URL=http://<ip>:<port>/mcp" >> .env
# echo "MCP_SERVER_TOKEN=<tokenid>" >> .env
```

有关在 Kubernetes 上部署环境的说明，请参阅 [`../env/README.md`](../env/README.md)。


### 2. 创建智能体或智能体集群
环境准备就绪后，下一步是在您选择的训练框架的循环中定义您的自定义智能体。对于 VeRL，这是通过实现一个自定义的 `AgentLoop` 来完成的。

例如，`GaiaAgentLoop` 继承自 `AworldAgentLoop` 并实现了 `build_agents` 方法。

```python
from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig

from train.adapter.verl.aworld_agent_loop import AworldAgentLoop
from train.adapter.verl.common import get_agent_tool_env_and_servers

class GaiaAgentLoop(AworldAgentLoop):
    def build_agents(self):
        # 从环境中获取环境配置和服务器。
        # 注意：您必须先按照步骤1中的说明启动 MCP 服务器
        # 并在您的环境变量中设置 URL 和令牌。
        gaia_env_config, gaia_env_servers = get_agent_tool_env_and_servers()

        return Agent(
            conf=AgentConfig(
                # verl会启动llm服务并动态分配服务地址，需要从服务管理器中获取服务地址
                llm_base_url=self.get_llm_server_address(),
                llm_model_name=self.get_llm_server_model_name(),
            ),
            name="gaia_super_agent",
            system_prompt="YOUR SYSTEM PROMPT",

            # 智能体的 MCP 工具配置
            mcp_config=gaia_env_config,
            mcp_servers=gaia_env_servers,
        )
```

下图展示了智能体（Agent）和环境（Environment）的整体架构及两者间的交互关系：

![架构示意图](../readme_assets/train_env_agent_architecture.png)


### 3. 运行训练
在运行训练之前，请在 `agent.yaml` 中指定您的自定义 `AgentLoop`：

```yaml
# 在 agent.yaml 中
- name: gaia_agent
  _target_: train.examples.train_gaia_with_aworld_verl.custom_agent_loop.GaiaAgentLoop
```

最后，运行训练脚本。该脚本通常是基于 VeRL 示例的 `run.sh` 文件。
```bash
bash run.sh
```
此脚本处理由 VeRL 编排的AgentLoop、奖励计算函数和训练流程。
有关 `run.sh` 中的参数设置，请参阅 [VeRL 文档](https://verl.readthedocs.io/en/latest/examples/config.html)。

一个完整的、可运行的示例，包括为 `GaiaAgentLoop` 定制的 `run.sh` 脚本，可在 [`./examples/train_gaia_with_aworld_verl/`](./examples/train_gaia_with_aworld_verl/) 中找到。

## 进阶教程

### 如何创建复杂的多智能体集群 (Swarm)
除了单个智能体，您还可以训练一个多智能体集群。只需让您的 `build_agents` 方法（或等效的设置函数）返回一个 `Swarm` 对象而不是单个 `Agent` 对象即可。AWorld 和训练适配器将处理剩下的部分。

```python
# 在自定义的AgentLoop中
def build_agents(self, ...) -> Union[Agent, Swarm]:
    # ... 创建多个agent
    agent_to_be_train = Agent(
      conf=AgentConfig(
          # 对于要训练的agent，llm_base_url和llm_model_name是从verl启动的服务中获取的
          llm_base_url=self.get_llm_server_address(),
          llm_model_name=self.get_llm_server_model_name(),
      ),
    )

    plan_agent = Agent(
      conf=AgentConfig(
          # 在此提供openai兼容的llm服务的地址、api_key和模型名称
          llm_base_url="<your llm base url>",
          llm_api_key="<your llm api key>",
          llm_model_name="<your llm model name>",
      ),
    )
    
    exe_agent = Agent(
      conf=AgentConfig(
          # 在此提供openai兼容的llm服务的地址、api_key和模型名称
          llm_base_url="<your llm base url>",
          llm_api_key="<your llm api key>",
          llm_model_name="<your llm model name>",
      ),
    )
    
    sum_agent = Agent(
      conf=AgentConfig(
          # 在此提供openai兼容的llm服务的地址、api_key和模型名称
          llm_base_url="<your llm base url>",
          llm_api_key="<your llm api key>",
          llm_model_name="<your llm model name>",
      ),
    )

    # 返回由以上定义的智能体组成的Swarm
    return Swarm(
        agent_to_be_train, plan_agent, exe_agent, sum_agent,
        # ... 其他Swarm配置
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


