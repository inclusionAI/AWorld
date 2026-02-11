<div align="center">

# AWorld：为你的领域打造智能体

</div>

<h4 align="center">

*「AI 的下一站，是你的专业能力」*

[![Twitter Follow][twitter-image]][twitter-url]
[![WeChat QR Code][wechat-image]][wechat-url]
[![Discord][discord-image]][discord-url]
[![License: MIT][license-image]][license-url]
[![DeepWiki][deepwiki-image]][deepwiki-url]
[![Tutorial][tutorial-image]][tutorial-url]
<!-- [![arXiv][arxiv-image]][arxiv-url] -->
<!-- [![Playground][playground-image]][playground-url] -->

</h4>

<h4 align="center">

[English](./README.md) |
[自动化](#your-journey-with-aworld-cli) |
[手动构建](#total-control-manually-crafting-agent-systems) |
[演进](#evolution) |
[参与贡献](#contributing) |

<!-- [经验与样本](#experience-to-samples) |
[训练](#training) | -->

</h4>

---

<p align="justify">
通用 AI 再强，也会撞上「语境之墙」——这堵墙由细粒度工作流、领域数据和长期积累的直觉砌成，构成了你的专业世界。从科研、金融到复杂工程，通用模型翻不过这道墙，也说不了你的「行话」。

AWorld 的论点是：AI 的真正扩展，来自让像你这样的专家在这堵墙上开一扇门。

AWorld-CLI 就是为此设计的平台。我们提供一套基础「配方」，让你把知识和洞察注入一支支自主智能体，从通用承诺走向在你领域里精准可用的应用。
</p>


![](./readme_assets/aworld_loop.png)

> 💡 更多信息请访问[官网](https://www.aworldagents.com/)，或体验在线[环境](https://www.aworldagents.com/environments)与[智能体](https://playground.aworldagents.com/)。 


<a id="your-journey-with-aworld-cli"></a>
# 开启你的 AWorld-CLI 之旅
从深思熟虑到可进化的自主智能体，从你指尖开始。


## 安装与激活

在 AWorld/aworld-cli 下创建 .env，配置 AWorld Agent 及其所创建智能体的基础模型，例如：
```bash
LLM_MODEL_NAME="your_model_name, Claude-Sonnet-4 or above suggested"
LLM_PROVIDER="openai"
LLM_API_KEY="your_model_api_key"
LLM_BASE_URL="your_model_base_url"
```

**安装并进入 AWorld-CLI：**
```bash
git clone https://github.com/inclusionAI/AWorld && cd AWorld

conda create -n aworld_env python=3.11 -y && conda activate aworld_env 

pip install -e . && cd aworld-cli && pip install -e .

aworld-cli
```


## 创建智能体
<p align="justify">
用自然语言描述任务，即可一键搭好智能体骨架；AWorld-CLI 负责样板代码，你专注逻辑即可。
</p>


<!-- ![](./readme_assets/aworld_cli_text2agent.png) -->
***让 AWorld Agent 为你构建智能体***
![](./readme_assets/aworld_cli_demo_step1.gif)

<p align="justify">
该命令会生成可直接运行的智能体文件，以我们精选的 Verified Skills 为底座，并挂载全局配置，生成后即可执行。

智能体一旦生成，会持久保存在 ~/.agents 目录，可重复使用。
</p>


### Verified Skills：自动化创建智能体的「基因库」
<div align="justify">
Verified Skills 不仅是模板集合，更是经过验证的专家能力池。
</div>

<br>

<p align="justify">
自动化创建新智能体时，AWorld-CLI 不会从零开始，而是智能引用这些久经考验的 Skills（见<a href="#evolution">演进</a>），以确保其稳健性，同时也会从您位于 ~/agents 文件夹中的自定义 Skills 中学习。这种双重继承机制，确保了每个智能体不仅从诞生之初就稳定可靠，适应您的特定需求。
</p>

<table style="width: 100%; border-collapse: collapse; table-layout: fixed;">
  <colgroup>
    <col style="width: 40%;">
    <col style="width: 60%;">
  </colgroup>
  <thead>
    <tr>
      <th style="text-align: left; border-bottom: 2px solid #ddd; padding: 8px;">技能</th>
      <th style="text-align: left; border-bottom: 2px solid #ddd; padding: 8px;">描述</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td style="padding: 8px; vertical-align: top;">🧠 DeepSearch 智能体</td>
      <td style="padding: 8px; vertical-align: top;">对指定主题进行全面、多源的研究，并整合生成一份结构化的报告。</td>
    </tr>
    <tr>
      <td style="padding: 8px; vertical-align: top;">🚀 PPT 智能体</td>
      <td style="padding: 8px; vertical-align: top;">根据文档、大纲或数据，创建精美的演示文稿。</td>
    </tr>
  </tbody>
</table>


## 运行智能体
<p align="justify">
向 AWorld Agent 发出指令，让它用你刚创建的智能体执行任务；每次调用、动作与观测都会写入详细轨迹日志，保存在本地目录。
</p>


<!-- ![](./readme_assets/aworld_cli_run_task.png) -->
***让新创建的智能体为你工作***
![](./readme_assets/aworld_cli_demo_step2.gif)

## 进化智能体
<p align="justify">
若智能体的表现未达预期，你可以用多种方式迭代改进它。

**手动进化**
<p align="justify">
你是专家。直接打开生成的智能体 Python 文件，按需调整提示词、逻辑或工具使用，完全可控。
</p>

**一颗赛艇：AI 辅助进化**
<p align="justify">
这里才是 AWorld-CLI 的亮点！用自然语言描述你想要的改动，AWorld Agent 会把任务交给预置的 Optimizer Agent，作为你的 AI 结对程序员，一起调优智能体。
</p>


***AI 辅助进化示意图***
![](./readme_assets/mas_meta_learning_v2.png) 


***优化你的智能体***
![](./readme_assets/aworld_cli_demo_step3.gif)


***让优化后的智能体为你做更复杂的工作***
![](./readme_assets/aworld_cli_demo_step4.gif)

**愿景：自进化**
<p align="justify">
未来形态：无需你写具体提示，系统根据奖励信号（如校验失败、偏离某 Verified Skill）自动发现次优表现，触发自主优化循环，让智能体在评估驱动下自进化，减少持续人工干预。
</p>

优化满意后，智能体会持久保存在 ~/.agents，可重复使用。
</p>


<a id="total-control-manually-crafting-agent-systems"></a>
# 完全掌控：手动构建智能体系统
<p align="justify">
在 AWorld 中，智能体即「模型 + 工具」。但真实场景常需多智能体协作。为此，AWorld 提供灵活构建路径，让你手动搭建复杂多智能体系统。
</p>

1. 端到端设计自动化工作流 [文档](https://inclusionai.github.io/AWorld/Quickstart/workflow_construction/)

2. 启动支持 MCP 的智能体 [文档](https://inclusionai.github.io/AWorld/Quickstart/agent_construction/)

3. 编排多智能体系统 (MAS) [文档](https://inclusionai.github.io/AWorld/Quickstart/multi-agent_system_construction/)


想直接体验？在 AWorld [Playground](https://playground.aworldagents.com/) 加载预置 DeepResearch 团队，查看源码并端到端运行。

# MAS演练场: 即刻运行，亲眼见证

在 AWorld [Playground](https://playground.aworldagents.com/) 启动官方 DeepResearch 团队，实时观摩 AI 协作。你可以检视其源码、运行全过程，并从中获取灵感。

![](./readme_assets/playground_gaiateam.gif)

**从用户到创造者：让你的智能体登上舞台！**
准备好构建你自己的智能体了吗？使用 aworld-cli 将你的专业知识铸造成一个强大的智能体，并将其核心能力定义在 skill.md 文件中。

想让你的作品登上这个舞台？只需提交一个 Pull Request，将你的 skill.md 添加至：
AWorld/examples/Custom_Skills/

我们会在这里展示最出色的社区智能体，让你的杰作大放异彩，赋能整个社区！


<!-- 
<a id="experience-to-samples"></a>
# 从经验到样本
<p align="justify">
放心迭代。运行时为每次任务记录完整历史（每次 LLM 调用、动作与奖励），可用于审计表现并生成高质量训练样本。
</p>
[文档](https://inclusionai.github.io/AWorld/Training/Trajectory/)


<a id="training"></a>
# 模型训练
<p align="justify">
当智能体能在环境中自由运行后，AWorld 用两种互补的训练模式形成闭环、持续提升。可接入主流 LLM 训练框架（如 AReal、Swift、Verl、Slime 等），在运行时中直接更新模型参数；适配器轻量，同一环境与智能体代码可在不同训练器间复用。
</p>
[文档](https://inclusionai.github.io/AWorld/Training/Trainer/)

> 💡 可参考[真实案例](./train/examples/train_gaia_with_aworld_verl/main.py)，内含完整智能体训练配置。 -->


<a id="evolution"></a>
# 演进
<p align="justify">
AWorld 的目标是扛住复杂度，让你专注创新。本节展示基于 AWorld 构建的前沿多智能体成果，向 AGI 迈进。
</p>


#### 智能体评测

<table style="width: 100%; border-collapse: collapse; table-layout: fixed;">
  <thead>
    <tr>
      <th style="width: 30%; text-align: left; border-bottom: 2px solid #ddd; padding: 8px;">类别</th>
      <th style="width: 20%; text-align: left; border-bottom: 2px solid #ddd; padding: 8px;">成果</th>
      <th style="width: 20%; text-align: left; border-bottom: 2px solid #ddd; padding: 8px;">表现</th>
      <th style="width: 25%; text-align: left; border-bottom: 2px solid #ddd; padding: 8px;">关键创新</th>
      <th style="width: 5%; text-align: left; border-bottom: 2px solid #ddd; padding: 8px;">日期</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td style="padding: 8px; vertical-align: top;">🤖 Agent
        <br>
        <a href="https://playground.aworldagents.com/" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/Try-Online-9B59B6?style=flat-square" alt="Try Online">
        </a>
      </td>
      <td style="padding: 8px; vertical-align: top;">
        <strong>GAIA Benchmark <br>Excellence</strong>
        <br>
        <a href="https://huggingface.co/spaces/gaia-benchmark/leaderboard" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/GAIA-Leaderboard-blue" alt="GAIA">
        </a>
      </td>
      <td style="padding: 8px; vertical-align: top;">
        Pass@1: <strong>67.89</strong> <br>
        Pass@3: <strong>83.49</strong>
        <br> (109 tasks)
        <a href="./examples/gaia/README_GUARD.md" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/Code-README-green" alt="Code">
        </a>
      </td>
      <td style="padding: 8px; vertical-align: top;">
        Multi-agent system <br>stability & orchestration
        <br>
        <a href="https://arxiv.org/abs/2508.09889" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/Paper-arXiv-red" alt="Paper">
        </a>
      </td>
      <td style="padding: 8px; vertical-align: top;">2025/08/06</td>
    </tr>
    <tr>
      <td style="padding: 8px; vertical-align: top;">🧠 Reasoning</td>
      <td style="padding: 8px; vertical-align: top;">
        <strong>IMO 2025 <br>Problem Solving</strong>
        <br>
        <a href="https://www.imo-official.org/year_info.aspx?year=2025" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/IMO-2025-blue" alt="IMO">
        </a>
      </td>
      <td style="padding: 8px; vertical-align: top;">
        <strong>5/6</strong> problems <br>solved in 6 hours
        <br>
        <a href="examples/imo/README.md" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/Code-README-green" alt="Code">
        </a>
      </td>
      <td style="padding: 8px; vertical-align: top;">Multi-agent collaboration <br>beats solo models</td>
      <td style="padding: 8px; vertical-align: top;">2025/07/25</td>
    </tr>
    <tr>
      <td style="padding: 8px; vertical-align: top;">🖼️ Multi-Modal</td>
      <td style="padding: 8px; vertical-align: top;">
        <strong>OSWorld <br>Rank 1st</strong>
        <br>
        <a href="https://os-world.github.io/" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/OSWorld-Leaderboard-green" alt="OSWorld">
        </a>
      </td>
      <td style="padding: 8px; vertical-align: top;">
        <strong>58.0%</strong> <br> Success Rate
        <br>
        <a href="examples/osworld/README.md" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/Code-README-green" alt="Code">
        </a>
      </td>
      <td style="padding: 8px; vertical-align: top;">The more tools the better?</td>
      <td style="padding: 8px; vertical-align: top;">2025/09/18</td>
    </tr>
    <tr>
      <td style="padding: 8px; vertical-align: top;">🖼️ Multi-Modal</td>
      <td style="padding: 8px; vertical-align: top;">
        <strong>VisualWebArena Rank 1st in September</strong>
        <br>
        <a href="https://docs.google.com/spreadsheets/d/1M801lEpBbKSNwP-vDBkC_pF7LdyGU1f_ufZb_NWNBZQ/edit?gid=2044883967#gid=2044883967" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/VWA-Leaderboard-green" alt="VWA">
        </a>
      </td>
      <td style="padding: 8px; vertical-align: top;">
        <strong>36.5%</strong> <br> Success Rate
        <br>
        <a href="examples/visualwebarena/README.md" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/Code-README-green" alt="Code">
        </a>
      </td>
      <td style="padding: 8px; vertical-align: top;">Automated tool generation <br>
        <a href="https://arxiv.org/pdf/2509.21072" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/Paper-arXiv-red" alt="Paper"></td>
      <td style="padding: 8px; vertical-align: top;">2025/09/25</td>
    </tr>
    <tr>
      <td style="padding: 8px; vertical-align: top;">🔍 Deep-Search</td>
      <td style="padding: 8px; vertical-align: top;">
        <strong>Xbench Excellence</strong>
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
          AWorld has its own context engine: Amni.
      </td>
      <td style="padding: 8px; vertical-align: top;">2025/10/23</td>
    </tr>
  </tbody>
</table>

#### 数据合成

1. **FunReason-MT Technical Report: Overcoming the Complexity Barrier in Multi-Turn Function Calling** arxiv, 2025. [paper](https://arxiv.org/abs/2510.24645), [code](https://github.com/inclusionAI/AWorld-RL), [model](https://huggingface.co/Bingguang/FunReason-MT), [dataset](https://huggingface.co/datasets/Bingguang/FunReason-MT)

    *Zengzhuang Xu, Bingguang Hao, Zechuan Wang, Yuntao Wen, Maolin Wang, etc.*
   
2. **From Failure to Mastery: Generating Hard Samples for Tool-use Agents** arxiv, 2026. [paper](https://arxiv.org/abs/2601.01498), [code](https://github.com/inclusionAI/AWorld-RL), [model](https://huggingface.co/Bingguang/FunReason-MT), [dataset](https://huggingface.co/datasets/Bingguang/FunReason-MT)

    *Bingguang Hao, Zengzhuang Xu, Yuntao Wen, Xinyi Xu, Yang Liu, etc.*


#### 模型训练

1. **AWorld: Orchestrating the Training Recipe for Agentic AI.** arxiv, 2025. [paper](https://arxiv.org/abs/2508.20404), [code](https://github.com/inclusionAI/AWorld/tree/main/train), [model](https://huggingface.co/inclusionAI/Qwen3-32B-AWorld)

    *Chengyue Yu, Siyuan Lu, Chenyi Zhuang, Dong Wang, Qintong Wu, etc.*

2. **FunReason: Enhancing Large Language Models' Function Calling via Self-Refinement Multiscale Loss and Automated Data Refinement.** arxiv, 2025. [paper](https://arxiv.org/abs/2505.20192), [model](https://huggingface.co/Bingguang/FunReason)

    *Bingguang Hao, Maolin Wang, Zengzhuang Xu, Cunyin Peng, etc.*

3. **Exploring Superior Function Calls via Reinforcement Learning.** arxiv, 2025. [paper](https://arxiv.org/abs/2508.05118), [code](https://github.com/BingguangHao/RLFC)

    *Bingguang Hao, Maolin Wang, Zengzhuang Xu, Yicheng Chen, etc.*

4. **RAG-R1 : Incentivize the Search and Reasoning Capabilities of LLMs through Multi-query Parallelism.** arxiv, 2025. [paper](https://arxiv.org/abs/2507.02962), [code](https://github.com/inclusionAI/AgenticLearning), [model](https://huggingface.co/collections/endertzw/rag-r1-68481d7694b3fca8b809aa29)

    *Zhiwen Tan, Jiaming Huang, Qintong Wu, Hongxuan Zhang, Chenyi Zhuang, Jinjie Gu*

5. **V2P: From Background Suppression to Center Peaking for Robust GUI Grounding Task.** arxiv, 2025. [paper](https://arxiv.org/abs/2508.13634), [code](https://github.com/inclusionAI/AgenticLearning/tree/main/V2P)

    *Jikai Chen, Long Chen, Dong Wang, Leilei Gan, Chenyi Zhuang, Jinjie Gu*

6. **Don't Just Fine-tune the Agent, Tune the Environment** arxiv, 2025. [paper](https://arxiv.org/abs/2510.10197)

    *Siyuan Lu, Zechuan Wang, Hongxuan Zhang, Qintong Wu, Leilei Gan, Chenyi Zhuang, etc.*


#### 元学习

1. **Profile-Aware Maneuvering: A Dynamic Multi-Agent System for Robust GAIA Problem Solving by AWorld.** arxiv, 2025. [paper](https://arxiv.org/abs/2508.09889), [code](https://github.com/inclusionAI/AWorld/blob/main/examples/gaia/README_GUARD.md)

    *Zhitian Xie, Qintong Wu, Chengyue Yu, Chenyi Zhuang, Jinjie Gu*

2. **Recon-Act: A Self-Evolving Multi-Agent Browser-Use System via Web Reconnaissance, Tool Generation, and Task Execution.** arxiv, 2025. [paper](https://arxiv.org/pdf/2509.21072), [code](https://github.com/inclusionAI/AWorld/tree/main/examples/visualwebarena)

    *Kaiwen He, Zhiwei Wang, Chenyi Zhuang, Jinjie Gu*

</p>


<a id="contributing"></a>
# 参与贡献
<p align="justify">
我们的愿景包括：拓展 AI for Science & Business、深化自进化能力、扩充社区贡献的 Skills 库。

我们欢迎开发者、研究者与领域专家加入——无论是改进框架，还是贡献你所在领域的 Skill，都很有价值。

学术引用或联系我们，请使用以下 BibTeX：
</p>

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
[twitter-url]: https://x.com/AWorldAgents
[wechat-url]: https://raw.githubusercontent.com/inclusionAI/AWorld/main/readme_assets/aworld_wechat.png
[arxiv-url]: https://arxiv.org/abs/2508.20404
[tutorial-url]: https://inclusionai.github.io/AWorld/
[playground-url]: https://playground.aworldagents.com/

<!-- funreason links -->
[funreason-code-url]: https://github.com/BingguangHao/FunReason
[funreason-model-url]: https://huggingface.co/Bingguang/FunReason
[funreason-paper-url]: https://arxiv.org/pdf/2505.20192

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
