<div align="center">

# AWorld：为你的世界打造的智能体工坊

</div>

<h4 align="center">

*「AI 的下一个前沿，是你的专业领域」*

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
[自动化](#使用-aworld-cli-自动化创建) |
[手动](#完全掌控手动构建智能体系统) |
[演进](#evolution) |
[参与贡献](#contributing) |


</h4>

---

<p align="justify">
通用 AI 再强大，也有一道「语境之墙」——它由你领域里细碎的工作流、专属数据和来之不易的直觉砌成。从科研、金融分析到复杂工程，通用模型翻不过这道墙，也说不了你的「行话」。

越来越多共识指出：AI 智能体的真正力量，不仅来自核心模型，更来自协调其工具、记忆与执行循环的**智能体缰绳（Agent Harness）**。AWorld 的立场与此一致：只有当像你这样的专家能为自己的领域打造专属的 Harness、在这道墙上开一扇门时，真正的 AI 规模化才会发生。

AWorld 及其 CLI 模式正是为此设计的平台。我们提供基础配方，让你——作为专家——把知识与洞察注入一支支自主智能体，从而从「通用承诺」走向在你世界中精准落地的、稳健的应用。
</p>


# 使用 AWorld-CLI 开启你的旅程
从想法到可演进、可自主运行的智能体，一切从你的指尖开始。


## 安装与激活

一次安装，全局配置，随处运行。

**安装 AWorld-CLI**
```bash
git clone https://github.com/inclusionAI/AWorld && cd AWorld

conda create -n aworld_env python=3.11 -y && conda activate aworld_env 

pip install -e . && cd aworld-cli && pip install -e .
```


**配置与启动**

```bash
cd 你的工作目录

aworld-cli --config
```

配置完成后，在终端输入 `aworld-cli` 即可开始使用。

你也可以在工作目录下创建 `.env` 文件来配置模型与 API。详见 [环境配置](./README_env_config.md)。


## 使用 AWorld-CLI 自动化创建
<p align="justify">
AWorld-CLI 不止于简单脚手架。它作为中枢大脑——AWorld Agent——协调一组专职子智能体，自主地构建、评估乃至演进其他智能体。

这套多智能体系统协同工作，将你的想法变为现实：
</p>

<table>
<colgroup>
<col style="width: 400px">
<col>
</colgroup>
<thead>
<tr><th>智能体名称</th><th>角色与核心职能</th></tr>
</thead>
<tbody>
<tr><td>👑 AWorld Agent</td><td><strong>编排者</strong>：中枢大脑，理解用户目标、制定计划并将任务分派给合适的子智能体，从头到尾管理整个工作流。</td></tr>
<tr><td>🧑‍💻 Developer</td><td><strong>构建者</strong>：负责编写、调试与重构代码（如适用的 HTML）的主工匠。</td></tr>
<tr><td>🧐 Evaluator</td><td><strong>评判者</strong>：质量保障专家。根据由 Skill 定义的客观标准（如 UI 分数）评估 Developer 的输出，为演进循环提供关键反馈。</td></tr>
</tbody>
</table>

### 演进循环：构建 → 评估 → 演进

假设你提出：*「帮我做一个英语单词学习小应用，UI 质量分数要高于 0.9。」*

*   **Developer 构建**：`Developer` 智能体分析需求并编写代码（如 HTML/JS），使用 **CAST**——我们专为代码分析与压缩设计的工具集，让智能体能以手术级精度阅读和修改复杂仓库。
*   **Evaluator 评判**：`Evaluator` 智能体使用经过验证的 Skill（如专业 UI 评估 Skill）检查输出。
*   **循环精修**：若分数未达目标（如 0.7），AWorld 会指示 Developer 根据 Evaluator 指出的具体问题修复。该循环持续直到满足你的标准。

***观看自演进循环演示***

[![在 YouTube 观看](https://img.shields.io/badge/观看-自演进循环-red?style=for-the-badge&logo=youtube)](https://youtube.com/shorts/F7INIq5HG1g?feature=share)


### 无评估则无演进
<div align="justify">
智能体要进步，必须先知道「好」长什么样。Skill 就是答案——它们是可复用、可验证的领域知识模块。


AWorld 引入<b>共享 Skill 体系</b>，让你的知识直接驱动整个演进循环：
<ul>
    <li><b>对 Evaluator</b>：Skill（如<i>法律合同审查、UI 美学评分、金融风险评估</i>）提供具体指标，教 Evaluator 如何自主评判表现并发现缺陷。</li>
    <li><b>对 Developer</b>：Developer 也可直接调用这些 Skill 作为工具，提升自身能力，确保产出从一开始就符合高标准。</li>
</ul>

由此形成强协同：Evaluator 设定目标，Developer 用同一套知识库去达成。
</div>

*   **官方库**：位于 `AWorld/aworld-skills` 的优质、已验证 Skill 集合（如 UI 美学评估），开箱即用。
*   **把你的专长变成 Skill**：这是 AWorld 拓展新领域的方式。将你的知识编码成 Skill 并放入 `~/.aworld/skills`，CLI 会自动索引，立即升级你的 Developer 与 Evaluator。**你的贡献是解锁新智能体能力、惠及所有人的关键。**


### 你才是终极评判者
<p align="justify">
Evaluator 自动化精修循环，但你的直觉仍是天花板。AWorld 旨在放大你的专长，而非取代它。
</p>

<p align="justify">
你可以在任意阶段介入。对生成的视频、应用或报告给出自然语言反馈。AWorld Agent 将你的批评视为高优先级约束，把你的主观品味或细微要求解读为对 Developer 的直接指令。无论是来自 Skill 的自动分数，还是你的人工指导，精准反馈驱动精准演进。
</p>


### CAST：强大的文件管理工具
<p align="justify">
LLM 智能体在真实代码仓库中常常失败，因为被代码复杂度和有限上下文窗口压垮。为此我们构建了 <b>CAST</b>——让智能体以超高效理解与修改大型代码库的专用引擎。
</p>

<p align="justify">
CAST 不是让智能体读扁平文本，而是提供压缩的、层次化的代码理解，相当于给智能体一套建筑蓝图而非一堆砖块。它实现：
</p>

*   **层次化导航**：智能体可从高层架构（逻辑/骨架层）瞬间下钻到具体实现，理解代码意图而不迷失细节。
*   **近乎无限的上下文**：CAST 智能压缩代码，只喂给智能体相关信息，有效打破上下文窗口限制，使其能对海量代码库进行推理。
*   **精准代码修改**：在理解代码结构与依赖后，Developer 可做精确修改、应用复杂补丁并以 pinpoint 精度重构，避免「盲目」文本替换的常见错误。


# 完全掌控：手动构建智能体系统

<p align="justify">
在 AWorld 中，一个智能体即「模型 + 工具」。但现实问题往往需要不止一个智能体。AWorld 通过灵活构建路径给你完全控制权，让你手动打造复杂的多智能体协作系统。
</p>

1. 灵活的多智能体编排、丰富环境沙箱、完整可观测性追踪 [文档](https://inclusionai.github.io/AWorld/Get%20Start/Core%20Capabilities/)

2. 并行任务运行时、流式响应 [文档](https://inclusionai.github.io/AWorld/Get%20Start/Parallel%20Tasks/)

3. 人在回路（HITL）[文档](https://inclusionai.github.io/AWorld/Get%20Start/HITL/)


# Evolution（演进）
<p align="justify">
AWorld 的使命是承担复杂性，让你专注创新。本节展示基于 AWorld 构建的尖端多智能体系统，向 AGI 迈进。
</p>


#### 智能体基准

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
      <td style="padding: 8px; vertical-align: top;">🤖 Agent
        <br>
        <a href="https://playground.aworldagents.com/" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/Try-Online-9B59B6?style=flat-square" alt="Try Online">
        </a>
      </td>
      <td style="padding: 8px; vertical-align: top;">
        <strong>GAIA 基准 <br>卓越表现</strong>
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
        多智能体系统 <br>稳定性与编排
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
        <strong>IMO 2025 <br>解题</strong>
        <br>
        <a href="https://www.imo-official.org/year_info.aspx?year=2025" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/IMO-2025-blue" alt="IMO">
        </a>
      </td>
      <td style="padding: 8px; vertical-align: top;">
        <strong>5/6</strong> 题 <br>6 小时内解决
        <br>
        <a href="examples/imo/README.md" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/Code-README-green" alt="Code">
        </a>
      </td>
      <td style="padding: 8px; vertical-align: top;">多智能体协作 <br>优于单模型</td>
      <td style="padding: 8px; vertical-align: top;">2025/07/25</td>
    </tr>
    <tr>
      <td style="padding: 8px; vertical-align: top;">🖼️ Multi-Modal</td>
      <td style="padding: 8px; vertical-align: top;">
        <strong>OSWorld <br>榜首</strong>
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
      <td style="padding: 8px; vertical-align: top;">工具越多越好？</td>
      <td style="padding: 8px; vertical-align: top;">2025/09/18</td>
    </tr>
    <tr>
      <td style="padding: 8px; vertical-align: top;">🖼️ Multi-Modal</td>
      <td style="padding: 8px; vertical-align: top;">
        <strong>VisualWebArena 九月榜首</strong>
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
      <td style="padding: 8px; vertical-align: top;">🔍 Deep-Search</td>
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
          AWorld 自有上下文引擎：Amni。
      </td>
      <td style="padding: 8px; vertical-align: top;">2025/10/23</td>
    </tr>
  </tbody>
</table>

#### 数据与综合

1. **FunReason-MT 技术报告：克服多轮函数调用中的复杂度壁垒** arxiv, 2025. [论文](https://arxiv.org/abs/2510.24645), [代码](https://github.com/inclusionAI/AWorld-RL), [模型](https://huggingface.co/Bingguang/FunReason-MT), [数据集](https://huggingface.co/datasets/Bingguang/FunReason-MT)

    *Zengzhuang Xu, Bingguang Hao, Zechuan Wang, Yuntao Wen, Maolin Wang, etc.*
   
2. **From Failure to Mastery: Generating Hard Samples for Tool-use Agents** arxiv, 2026. [论文](https://arxiv.org/abs/2601.01498), [代码](https://github.com/inclusionAI/AWorld-RL), [模型](https://huggingface.co/Bingguang/FunReason-MT), [数据集](https://huggingface.co/datasets/Bingguang/FunReason-MT)

    *Bingguang Hao, Zengzhuang Xu, Yuntao Wen, Xinyi Xu, Yang Liu, etc.*


#### 模型训练

1. **AWorld: Orchestrating the Training Recipe for Agentic AI.** arxiv, 2025. [论文](https://arxiv.org/abs/2508.20404), [代码](https://github.com/inclusionAI/AWorld/tree/main/train), [模型](https://huggingface.co/inclusionAI/Qwen3-32B-AWorld)

    *Chengyue Yu, Siyuan Lu, Chenyi Zhuang, Dong Wang, Qintong Wu, etc.*

2. **FunReason: Enhancing Large Language Models' Function Calling via Self-Refinement Multiscale Loss and Automated Data Refinement.** arxiv, 2025. [论文](https://arxiv.org/abs/2505.20192), [模型](https://huggingface.co/Bingguang/FunReason)

    *Bingguang Hao, Maolin Wang, Zengzhuang Xu, Cunyin Peng, etc.*

3. **Exploring Superior Function Calls via Reinforcement Learning.** arxiv, 2025. [论文](https://arxiv.org/abs/2508.05118), [代码](https://github.com/BingguangHao/RLFC)

    *Bingguang Hao, Maolin Wang, Zengzhuang Xu, Yicheng Chen, etc.*

4. **RAG-R1 : Incentivize the Search and Reasoning Capabilities of LLMs through Multi-query Parallelism.** arxiv, 2025. [论文](https://arxiv.org/abs/2507.02962), [代码](https://github.com/inclusionAI/AgenticLearning), [模型](https://huggingface.co/collections/endertzw/rag-r1-68481d7694b3fca8b809aa29)

    *Zhiwen Tan, Jiaming Huang, Qintong Wu, Hongxuan Zhang, Chenyi Zhuang, Jinjie Gu*

5. **V2P: From Background Suppression to Center Peaking for Robust GUI Grounding Task.** arxiv, 2025. [论文](https://arxiv.org/abs/2508.13634), [代码](https://github.com/inclusionAI/AgenticLearning/tree/main/V2P)

    *Jikai Chen, Long Chen, Dong Wang, Leilei Gan, Chenyi Zhuang, Jinjie Gu*

6. **Don't Just Fine-tune the Agent, Tune the Environment** arxiv, 2025. [论文](https://arxiv.org/abs/2510.10197)

    *Siyuan Lu, Zechuan Wang, Hongxuan Zhang, Qintong Wu, Leilei Gan, Chenyi Zhuang, etc.*


#### 元学习与系统

1. **Profile-Aware Maneuvering: A Dynamic Multi-Agent System for Robust GAIA Problem Solving by AWorld.** arxiv, 2025. [论文](https://arxiv.org/abs/2508.09889), [代码](https://github.com/inclusionAI/AWorld/blob/main/examples/gaia/README_GUARD.md)

    *Zhitian Xie, Qintong Wu, Chengyue Yu, Chenyi Zhuang, Jinjie Gu*

2. **Recon-Act: A Self-Evolving Multi-Agent Browser-Use System via Web Reconnaissance, Tool Generation, and Task Execution.** arxiv, 2025. [论文](https://arxiv.org/pdf/2509.21072), [代码](https://github.com/inclusionAI/AWorld/tree/main/examples/visualwebarena)

    *Kaiwen He, Zhiwei Wang, Chenyi Zhuang, Jinjie Gu*

</p>


# Contributing（参与贡献）
<p align="justify">
我们的路线图包括扩展 AI for Science & Business 计划、深化自演进能力，以及壮大社区贡献的 Skill 库。

我们热烈欢迎开发者、研究者和领域专家加入。无论你是增强框架还是贡献本领域的 Skill，你的工作都有价值。

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

<!-- # Star History
![](https://api.star-history.com/svg?repos=inclusionAI/AWorld&type=Date) -->


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
