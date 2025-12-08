<div align="center">

# AWorld：丰富的环境、高效的智能体、持续的进化

</div>

<h4 align="center">

*“自我意识：最难的问题不在于在局限内求解，而在于发现自身的局限”*

[![Twitter Follow][twitter-image]][twitter-url]
[![WeChat QR Code][wechat-image]][wechat-url]
[![Discord][discord-image]][discord-url]
[![License: MIT][license-image]][license-url]
[![DeepWiki][deepwiki-image]][deepwiki-url]
[![arXiv][arxiv-image]][arxiv-url]
[![Tutorial][tutorial-image]][tutorial-url]
[![Playground][playground-image]][playground-url]

</h4>

<h4 align="center">

[English](./README.md) |
[安装](#安装) |
[环境](#复杂环境在线访问) |
[智能体](#高效的智能体构建) |
[经验](#经验到样本) |
[训练](#训练) |
[架构](#架构设计原则) |
[演进](#演进) |
[贡献](#贡献) |

</h4>

**AWorld (Agent World)** 构建智能体（Agent）及其运行的丰富环境，旨在拓展 AI 能力的前沿并实现持续进化。本项目提供了 Agentic Learning（智能体学习）的基础配方：[环境访问](#复杂环境在线访问)、[智能体构建](#高效的智能体构建)、[经验获取](#经验到样本) 和 [模型训练](#训练)。AWorld 的强大之处在于，智能体可以利用这些相同的组件来自动提升自己。

![](./readme_assets/aworld_loop.png)

> 💡 访问我们的 [主页](https://www.aworldagents.com/) 了解更多详情，或者尝试我们的在线 [环境](https://www.aworldagents.com/environments) 和 [智能体](https://playground.aworldagents.com/)。


# 安装
> [!TIP]
> Python>=3.11
```bash
git clone https://github.com/inclusionAI/AWorld && cd AWorld

pip install -e .
```

# 复杂环境在线访问
配置丰富的环境并非易事——依赖包冲突、API 需要密钥、并发需要扩展、网路配置等。我们通过三种访问模式让这一切变得轻松无痛：
1. 使用我们默认的托管设置（针对有使用成本的工具，我们提供有限免费额度）。
2. 自带 API 密钥以获得无限制次数工具使用（即将推出）。
3. 拉取我们的 Docker 镜像并在您自己的基础设施上部署运行（即将推出）。

```python
import os
import asyncio
from aworld.sandbox import Sandbox

INVITATION_CODE = os.environ.get("INVITATION_CODE", "")

mcp_config = {
    "mcpServers": {
        "gaia_server": {
            "type": "streamable-http",
            "url": "https://playground.aworldagents.com/environments/mcp",
            "timeout": 600,
            "sse_read_timeout": 600,
            "headers": {
                "ENV_CODE": "gaia",
                "Authorization": f"Bearer {INVITATION_CODE}",
            }
        }
    }
}

async def _list_tools():
    sand_box = Sandbox(mcp_config=mcp_config, mcp_servers=["gaia_server"])
    return await sand_box.mcpservers.list_tools()

if __name__ == "__main__":
    tools = asyncio.run(_list_tools())
    print(tools)
```

![](./readme_assets/how_to_access_env.gif)

# 高效的智能体构建
在 AWorld 中，智能体被简洁的定义成一个工具增强的模型。要启动一个智能体，您只需要：
1. 一个模型服务（对于训练，vLLM/SGLang服务效果就很好）
2. 一个可调用的在线环境（使用我们的托管选项或接入您自己的 MCP 工具链）
就是这样——无需繁重的脚手架代码。

```python
from aworld.agents.llm_agent import Agent
from aworld.runner import Runners

# 详情请参阅上一节
mcp_config = {...}

searcher = Agent(
    name="Search Agent",
    system_prompt="You specialize at searching.",
    mcp_config=mcp_config
)

if __name__ == "__main__":
    result = Runners.sync_run(
        input="Use google search tool to answer the question: the news about AI today.",
        agent=searcher
    )
    print(f"answer: {result.answer}")
```

记得先配置您的 LLM 凭证。
```bash
# 设置 LLM 凭证
export LLM_MODEL_NAME="gpt-4"
export LLM_API_KEY="your-api-key-here"
export LLM_BASE_URL="https://api.openai.com/v1"
```

## 复杂智能体系统构建

现实世界的问题通常需要构建复杂的智能体系统。AWorld 为您提供了灵活的构建模式：
1. 设计端到端的自动化工作流 [文档](https://inclusionai.github.io/AWorld/Quickstart/workflow_construction/)
2. 构建支持 MCP 的智能体 [文档](https://inclusionai.github.io/AWorld/Quickstart/agent_construction/)
3. 编排多智能体系统 (MAS) [文档](https://inclusionai.github.io/AWorld/Quickstart/multi-agent_system_construction/)

想看实际效果？可在 AWorld [Playground](https://playground.aworldagents.com/) 中加载我们预构建的 DeepResearch 智能体系统，检查源代码，并端到端运行它。
![](./readme_assets/playground_gaiateam.gif)


# 经验到样本
我们的运行时（Runtime）会捕获离线和在线运行中的每一个步骤。每个任务都会产生一条完整的轨迹——包含每一次 LLM 调用、动作和奖励——因此您可以用于样本合成、性能评估、并高置信地进行迭代。

## 完整的任务轨迹
任务是通过许多次 LLM 调用展开的。框架会捕获每一步，为您提供完整的轨迹。

```python
import asyncio
from aworld.runner import Runners
from aworld.core.task import Task
from aworld.logs.util import logger
import json

# 智能体构建请参考上一节
searcher = Agent(...)

if __name__ == "__main__":
    async def test_complete_trajectory():
        task = Task(
            input="Use google search tool to answer the question: the news about AI today.",
            agent=searcher
        )

        responses = await Runners.run_task(task)
        resp = responses[task.id]
        logger.info(f"task answer: {resp.answer}")
        logger.info(f"task trajectory: {json.dumps(resp.trajectory, ensure_ascii=False)}")
    asyncio.run(test_complete_trajectory())
```

## 单步内省 (Single-Step Introspection)
需要更精细的控制？调用 `step()` 来逐次检查动作/响应数据对。这允许您在训练期间注入中间奖励，从而实现更丰富、更灵活的学习信号。

```python
import os
import asyncio
from aworld.runner import Runners
from aworld.core.task import Task
from aworld.logs.util import logger
import json
from aworld.config import TaskConfig, TaskRunMode

# 智能体构建请参考上一节
searcher = Agent(...)

if __name__ == "__main__":
    async def test_single_step_introspection():
        task = Task(
            input="Use google search tool to answer the question: the news about AI today.",
            agent=searcher,
            conf=TaskConfig(
                resp_carry_context=True,
                run_mode=TaskRunMode.INTERACTIVE
            )
        )

        trajectory_log = os.path.join(os.path.dirname(__file__), "trajectory_log.txt")
        is_finished = False
        step = 1
        while not is_finished:
            with open(trajectory_log, "a", encoding="utf-8") as traj_file:
                is_finished, observation, response = await Runners.step(task)
                traj_file.write(f"Step {step}\n")
                traj_file.write(json.dumps(response.trajectory, ensure_ascii=False, indent=2))
                traj_file.write("\n\n")
                step += 1
    asyncio.run(test_single_step_introspection())
```


# 训练
一旦智能体能够在环境中探索，AWorld 能通过两种互补的训练模式形成进化的闭环，推动持续改进。

## 模型训练
将任何主流 LLM 训练框架——AReal、Swift、Verl、Slime 等——接入运行时，直接更新模型参数。适配器非常轻量，因此您可以在不同的训练器之间复用相同的环境和智能体代码。

```python
from datasets import load_dataset
from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig

from train.trainer.agent_trainer import AgentTrainer
from train.examples.train_gaia_with_aworld_verl.metrics.gaia_reward_function import gaia_reward_func


# 详情请参阅上一节
mcp_config = {...}

# 配置智能体使用 Verl 作为模型服务（自动适配推理格式）
agent_config = AgentConfig(
    llm_provider="verl"
)
searcher = Agent(
    name="Search Agent",
    system_prompt="You specialize at searching.",
    mcp_config=mcp_config,
    conf=agent_config
)

train_dataset = load_dataset("", split="train")
test_dataset = load_dataset("", split="test")

trainer = AgentTrainer(
    agent=agent,
    config=custom_train_config,
    reward_func=gaia_reward_func,
    train_dataset=train_dataset,
    test_dataset=test_dataset
)

trainer.train()
```
> 💡 查看 [真实案例](./train/examples/train_gaia_with_aworld_verl/main.py)，其中包含运行智能体训练所需的完整训练配置。

## 元学习 (Meta-Learning)
除了更新模型权重之外，您还可以对整个智能体系统进行元学习。启动特定角色的智能体，让它们针对目标智能体进行更新、重写提示词、优化工作流或调整策略，然后迭代团队（如下图所示）。

![](./readme_assets/mas_meta_learning.png)

# 架构设计原则
本框架旨在具有高度适应性，使研究人员和开发人员能够跨多个领域进行探索和创新，从而提升多智能体系统的能力和应用。

## 概念与框架
| 概念 | 描述 |
| :-------------------------------------- | ------------ |
| [`agent`](./aworld/core/agent/base.py)  | 定义基础类、描述、输出解析以及多智能体协作（swarm）逻辑，用于定义、管理和编排 AWorld 系统中的智能体。 |
| [`runner`](./aworld/runners)            | 包含管理智能体在环境中的执行循环的运行器类，处理剧集回放（episode rollouts）和并行训练/评估工作流。   |
| [`task`](./aworld/core/task.py)         | 定义基础任务类，封装了智能体交互所需的环境目标、必要工具和终止条件。  |
| [`swarm`](./aworld/core/agent/swarm.py) | 实现 SwarmAgent 类，通过去中心化策略管理多智能体协调和涌现的群体行为。 |
| [`sandbox`](./aworld/sandbox)           | 提供带有可配置场景的受控运行时，用于快速原型设计和验证智能体行为。 |
| [`tools`](./aworld/tools)               | 提供灵活的框架，用于定义、适配和执行 AWorld 系统中的智能体-环境交互工具。 |
| [`context`](./aworld/core/context)      | 为 AWorld 智能体提供全面的上下文管理系统，实现完整的状态跟踪、配置管理、提示词优化、多任务状态处理以及贯穿智能体生命周期的动态提示词模板。  |
| [`memory`](./aworld/memory)             | 为智能体实现可扩展的记忆系统，支持短期和长期记忆、摘要、检索、嵌入（embeddings）和集成。|
| [`trace`](./aworld/trace)               | 为 AWorld 提供可观测的追踪框架，支持分布式追踪、上下文传播、Span 管理，并与流行框架和协议集成，以监控和分析智能体、工具及任务的执行。|


## 特性
| 智能体构建                    | 拓扑编排                                                                                     | 环境                           |
|:------------------------------|:-----------------------------------------------------------------------------------------|:-------------------------------|
| ✅ 集成 MCP 服务               | ✅ 封装的运行时                                                                                 | ✅ 运行时状态管理               |
| ✅ 支持多模型提供商              | ✅ 灵活的 MAS 模式                                                                             | ✅ 高并发支持                   |
| ✅ 高度自定义构建                  | ✅ 清晰的状态追踪                                                                                | ✅ 分布式训练                   |
| ✅ [支持智能体技能](https://github.com/inclusionAI/AWorld/tree/main/examples/skill_agent)  | ✅ [支持交互式终端](https://github.com/inclusionAI/AWorld/tree/main/examples/aworld_cli_demo) 🚀 |       |


# 演进
我们的使命：把复杂繁琐的任务留给 AWorld，您来负责创新。本节展示了利用 AWorld 开发的几个创新项目，以证明框架本身的有效性。

#### 智能体打榜

<table style="width: 100%; border-collapse: collapse; table-layout: fixed;">
  <thead>
    <tr>
      <th style="width: 30%; text-align: left; border-bottom: 2px solid #ddd; padding: 8px;">类别</th>
      <th style="width: 20%; text-align: left; border-bottom: 2px solid #ddd; padding: 8px;">成就</th>
      <th style="width: 20%; text-align: left; border-bottom: 2px solid #ddd; padding: 8px;">表现</th>
      <th style="width: 25%; text-align: left; border-bottom: 2px solid #ddd; padding: 8px;">关键创新</th>
      <th style="width: 5%; text-align: left; border-bottom: 2px solid #ddd; padding: 8px;">日期</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td style="padding: 8px; vertical-align: top;">🤖 智能体
        <br>
        <a href="https://playground.aworldagents.com/" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/Try-Online-9B59B6?style=flat-square" alt="Try Online">
        </a>
      </td>
      <td style="padding: 8px; vertical-align: top;">
        <strong>GAIA Benchmark <br>卓越表现</strong>
        <br>
        <a href="https://huggingface.co/spaces/gaia-benchmark/leaderboard" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/GAIA-Leaderboard-blue" alt="GAIA">
        </a>
      </td>
      <td style="padding: 8px; vertical-align: top;">
        Pass@1: <strong>67.89</strong> <br>
        Pass@3: <strong>83.49</strong>
        <br> (109 任务)
        <a href="./examples/gaia/README_GUARD.md" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/Code-README-green" alt="Code">
        </a>
      </td>
      <td style="padding: 8px; vertical-align: top;">
        多智能体系统 <br>稳定性与编排
        <br>
        <a href="https://arxiv.org/abs/2508.09889" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/Paper-arXiv-red" alt="Paper">
        </a>
      </td>
      <td style="padding: 8px; vertical-align: top;">2025/08/06</td>
    </tr>
    <tr>
      <td style="padding: 8px; vertical-align: top;">🧠 推理</td>
      <td style="padding: 8px; vertical-align: top;">
        <strong>IMO 2025 <br>解题</strong>
        <br>
        <a href="https://www.imo-official.org/year_info.aspx?year=2025" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/IMO-2025-blue" alt="IMO">
        </a>
      </td>
      <td style="padding: 8px; vertical-align: top;">
        6小时内解决 <br><strong>5/6</strong> 道题
        <br>
        <a href="examples/imo/README.md" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/Code-README-green" alt="Code">
        </a>
      </td>
      <td style="padding: 8px; vertical-align: top;">多智能体协作 <br>优于单个模型</td>
      <td style="padding: 8px; vertical-align: top;">2025/07/25</td>
    </tr>
    <tr>
      <td style="padding: 8px; vertical-align: top;">🖼️ 多模态</td>
      <td style="padding: 8px; vertical-align: top;">
        <strong>OSWorld <br>排名第一</strong>
        <br>
        <a href="https://os-world.github.io/" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/OSWorld-Leaderboard-green" alt="OSWorld">
        </a>
      </td>
      <td style="padding: 8px; vertical-align: top;">
        <strong>58.0%</strong> <br> 成功率
        <br>
        <a href="examples/osworld/README.md" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/Code-README-green" alt="Code">
        </a>
      </td>
      <td style="padding: 8px; vertical-align: top;">工具越多越好吗？</td>
      <td style="padding: 8px; vertical-align: top;">2025/09/18</td>
    </tr>
    <tr>
      <td style="padding: 8px; vertical-align: top;">🖼️ 多模态</td>
      <td style="padding: 8px; vertical-align: top;">
        <strong>VisualWebArena 九月排名第一</strong>
        <br>
        <a href="https://docs.google.com/spreadsheets/d/1M801lEpBbKSNwP-vDBkC_pF7LdyGU1f_ufZb_NWNBZQ/edit?gid=2044883967#gid=2044883967" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/VWA-Leaderboard-green" alt="VWA">
        </a>
      </td>
      <td style="padding: 8px; vertical-align: top;">
        <strong>36.5%</strong> <br> 成功率
        <br>
        <a href="examples/visualwebarena/README.md" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/Code-README-green" alt="Code">
        </a>
      </td>
      <td style="padding: 8px; vertical-align: top;">自动化工具生成 <br>
        <a href="https://arxiv.org/pdf/2509.21072" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/Paper-arXiv-red" alt="Paper"></td>
      <td style="padding: 8px; vertical-align: top;">2025/09/25</td>
    </tr>
    <tr>
      <td style="padding: 8px; vertical-align: top;">🔍 深度搜索</td>
      <td style="padding: 8px; vertical-align: top;">
        <strong>Xbench 卓越表现</strong>
        <br>
        <a href="https://xbench.org/" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/xbench-Leaderboard-green" alt="xbench">
        </a>
      </td>
      <td style="padding: 8px; vertical-align: top;">
        Pass@1: 51 <br> Pass@3: 61
        <br>
        <a href="examples/xbench/README.md" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/Code-README-green" alt="Code">
        </a>
      </td>
      <td style="padding: 8px; vertical-align: top;">
          AWorld 拥有自己的上下文引擎：Amni。
      </td>
      <td style="padding: 8px; vertical-align: top;">2025/10/23</td>
    </tr>
  </tbody>
</table>

#### 数据合成 (Data Synthesis)

1. **FunReason-MT Technical Report: Overcoming the Complexity Barrier in Multi-Turn Function Calling** arxiv, 2025. [论文](https://arxiv.org/abs/2510.24645), [代码](https://github.com/inclusionAI/AWorld-RL), [模型](https://huggingface.co/Bingguang/FunReason-MT), [数据集](https://huggingface.co/datasets/Bingguang/FunReason-MT)

    *Zengzhuang Xu, Bingguang Hao, Zechuan Wang, Yuntao Wen, Maolin Wang, 等*


#### 模型训练 (Model Training)

1. **AWorld: Orchestrating the Training Recipe for Agentic AI.** arxiv, 2025. [论文](https://arxiv.org/abs/2508.20404), [代码](https://github.com/inclusionAI/AWorld/tree/main/train), [模型](https://huggingface.co/inclusionAI/Qwen3-32B-AWorld)

    *Chengyue Yu, Siyuan Lu, Chenyi Zhuang, Dong Wang, Qintong Wu, 等*

2. **FunReason: Enhancing Large Language Models' Function Calling via Self-Refinement Multiscale Loss and Automated Data Refinement.** arxiv, 2025. [论文](https://arxiv.org/abs/2505.20192), [模型](https://huggingface.co/Bingguang/FunReason)

    *Bingguang Hao, Maolin Wang, Zengzhuang Xu, Cunyin Peng, 等*

3. **Exploring Superior Function Calls via Reinforcement Learning.** arxiv, 2025. [论文](https://arxiv.org/abs/2508.05118), [代码](https://github.com/BingguangHao/RLFC)

    *Bingguang Hao, Maolin Wang, Zengzhuang Xu, Yicheng Chen, 等*

4. **RAG-R1 : Incentivize the Search and Reasoning Capabilities of LLMs through Multi-query Parallelism.** arxiv, 2025. [论文](https://arxiv.org/abs/2507.02962), [代码](https://github.com/inclusionAI/AgenticLearning), [模型](https://huggingface.co/collections/endertzw/rag-r1-68481d7694b3fca8b809aa29)

    *Zhiwen Tan, Jiaming Huang, Qintong Wu, Hongxuan Zhang, Chenyi Zhuang, Jinjie Gu*

5. **V2P: From Background Suppression to Center Peaking for Robust GUI Grounding Task.** arxiv, 2025. [论文](https://arxiv.org/abs/2508.13634), [代码](https://github.com/inclusionAI/AgenticLearning/tree/main/V2P)

    *Jikai Chen, Long Chen, Dong Wang, Leilei Gan, Chenyi Zhuang, Jinjie Gu*

6. **Don’t Just Fine-tune the Agent, Tune the Environment** arxiv, 2025. [论文](https://arxiv.org/abs/2510.10197)

    *Siyuan Lu, Zechuan Wang, Hongxuan Zhang, Qintong Wu, Leilei Gan, Chenyi Zhuang, 等*


#### 元学习 (Meta Learning)

1. **Profile-Aware Maneuvering: A Dynamic Multi-Agent System for Robust GAIA Problem Solving by AWorld.** arxiv, 2025. [论文](https://arxiv.org/abs/2508.09889), [代码](https://github.com/inclusionAI/AWorld/blob/main/examples/gaia/README_GUARD.md)

    *Zhitian Xie, Qintong Wu, Chengyue Yu, Chenyi Zhuang, Jinjie Gu*

2. **Recon-Act: A Self-Evolving Multi-Agent Browser-Use System via Web Reconnaissance, Tool Generation, and Task Execution.** arxiv, 2025. [论文](https://arxiv.org/pdf/2509.21072), [代码](https://github.com/inclusionAI/AWorld/tree/main/examples/visualwebarena)

    *Kaiwen He, Zhiwei Wang, Chenyi Zhuang, Jinjie Gu*


# 贡献
我们热烈欢迎开发者加入我们，共同构建和改进 AWorld！无论您是想增强框架功能、修复 Bug 还是添加新特性，您的贡献对我们都非常宝贵。

如需学术引用或希望联系我们，请使用以下 BibTeX 条目：

```bibtex
@misc{yu2025aworldorchestratingtrainingrecipe,
      title={AWorld: Orchestrating the Training Recipe for Agentic AI}, 
      author={Chengyue Yu and Siyuan Lu and Chenyi Zhuang and Dong Wang and Qintong Wu and Zongyue Li and Runsheng Gan and Chunfeng Wang and Siqi Hou and Gaochi Huang and Wenlong Yan and Lifeng Hong and Aohui Xue and Yanfeng Wang and Jinjie Gu and David Tsai and Tao Lin},
      year={2025},
      eprint={2508.20404},
      archivePrefix={arXiv},
      primaryClass={cs.AI},
      url={https://arxiv.org/abs/2508.20404}, 
}
```

# Star History
![](https://api.star-history.com/svg?repos=inclusionAI/AWorld&type=Date)

<!-- resource section start -->
<!-- image links -->
[arxiv-image]: https://img.shields.io/badge/Paper-arXiv-B31B1B?style=for-the-badge&logo=arxiv&logoColor=white
[blog-image]: https://img.shields.io/badge/Blog-Coming%20Soon-FF5722?style=for-the-badge&logo=blogger&logoColor=white
[deepwiki-image]: https://img.shields.io/badge/DeepWiki-Explore-blueviolet?style=for-the-badge&logo=wikipedia&logoColor=white
[discord-image]: https://img.shields.io/badge/Discord-Join%20us-blue?style=for-the-badge&logo=discord&logoColor=white
[github-code-image]: https://img.shields.io/badge/Code-GitHub-181717?style=for-the-badge&logo=github&logoColor=white
[huggingface-dataset-image]: https://img.shields.io/badge/Dataset-Coming%20Soon-007ACC?style=for-the-badge&logo=dataset&logoColor=white
[huggingface-model-image]: https://img.shields.io/badge/Model-Hugging%20Face-FF6B6B?style=for-the-badge&logo=huggingface&logoColor=white
[license-image]: https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge
[twitter-image]: https://img.shields.io/badge/Twitter-Follow%20us-1DA1F2?style=for-the-badge&logo=twitter&logoColor=white
[wechat-image]: https://img.shields.io/badge/WeChat-Add%20us-green?style=for-the-badge&logo=wechat&logoColor=white
[tutorial-image]: https://img.shields.io/badge/Tutorial-Get%20Started-FF6B35?style=for-the-badge&logo=book&logoColor=white
[playground-image]: https://img.shields.io/badge/Playground-Try%20Online-9B59B6?style=for-the-badge&logo=book&logoColor=white

<!-- aworld links -->
[deepwiki-url]: https://deepwiki.com/inclusionAI/AWorld
[discord-url]: https://discord.gg/b4Asj2ynMw
[license-url]: https://opensource.org/licenses/MIT
[twitter-url]: https://x.com/InclusionAI666
[wechat-url]: https://raw.githubusercontent.com/inclusionAI/AWorld/main/readme_assets/aworld_wechat.png
[arxiv-url]: https://arxiv.org/abs/2508.
[tutorial-url]: https://inclusionai.github.io/AWorld/
[playground-url]: https://playground.aworldagents.com/

<!-- funreason links -->
[funreason-code-url]: https://github.com/BingguangHao/FunReason
[funreason-model-url]: https://huggingface.co/Bingguang/FunReason
[funreason-paper-url]: https://arxiv.org/pdf/2505.20192
<!-- [funreason-dataset-url]: https://github.com/BingguangHao/FunReason -->
<!-- [funreason-blog-url]: https://github.com/BingguangHao/FunReason -->

<!-- deepsearch links -->
[deepsearch-code-url]: https://github.com/inclusionAI/AgenticLearning
[deepsearch-dataset-url]: https://github.com/inclusionAI/AgenticLearning
[deepsearch-model-url]: https://huggingface.co/collections/endertzw/rag-r1-68481d7694b3fca8b809aa29
[deepsearch-paper-url]: https://arxiv.org/abs/2507.02962

<!-- badge -->
[MAS]: https://img.shields.io/badge/Mutli--Agent-System-EEE1CE
[IMO]: https://img.shields.io/badge/IMO-299D8F
[BFCL]: https://img.shields.io/badge/BFCL-8AB07D
[GAIA]: https://img.shields.io/badge/GAIA-E66F51
[Runtime]: https://img.shields.io/badge/AWorld-Runtime-287271
[Leaderboard]: https://img.shields.io/badge/Leaderboard-FFE6B7
[Benchmark]: https://img.shields.io/badge/Benchmark-FFE6B7
[Cloud-Native]: https://img.shields.io/badge/Cloud--Native-B19CD7
[Forward]: https://img.shields.io/badge/Forward-4A90E2
[Backward]: https://img.shields.io/badge/Backward-7B68EE
[Code]: https://img.shields.io/badge/Code-FF6B6B
[Paper]: https://img.shields.io/badge/Paper-4ECDC4


<!-- resource section end -->