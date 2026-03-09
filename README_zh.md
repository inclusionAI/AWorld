<div align="center">

# AWorld：为你的世界打造的智能体驾驭框架

</div>

<h4 align="center">

*「AI 的下一个前沿，是你的专业能力」*

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
[自动化](#你的-aworld-cli-之旅) |
[手动](#完全掌控手动打造智能体系统) |
[演进](#演进循环构建--评估--演进) |
[参与贡献](#参与贡献) |


</h4>

---

<p align="justify">
通用 AI 常常会撞上「上下文之墙」——那些定义<em>你</em>世界的细微数据、工作流与直觉。智能体的真正力量不仅来自模型本身，更来自其<strong>智能体驾驭框架（Agent Harness）</strong>：协调工具、记忆、上下文与执行的整体框架。

这就是<strong>AWorld 理念</strong>：仅有一个强大的驾驭框架还不够。只有当像你这样的专家将宝贵知识嵌入其中，真正在墙上打开那扇门，AI 的规模化才会被解锁。

AWorld 正是为此而设计的平台。我们提供一套完整、久经考验的 Harness 作为「配方」，让作为专家的你将知识锻造成一支自主智能体舰队。我们一起超越 AI 的泛化承诺，打造稳健、精准、精通<em>你</em>所在领域的应用。
</p>

# 从专业能力到产品

看看当专家知识被编码成可复用的 **Skill（技能）** 时会发生什么。下面展示的成果均由 AWorld 智能体编排完成，体现了我们的核心规模化定律：社区贡献的专业能力越多，整个生态就越强大。

这是今天已经能做到的。想象一下，有了*你的*专业能力，我们还能一起构建什么。

<table>
<colgroup>
  <col style="width:15%">
  <col style="width:40%">
  <col style="width:22%">
  <col style="width:23%">
</colgroup>
<thead>
<tr>
  <th>能力</th>
  <th>专业能力</th>
  <th>效果演示</th>
  <th>配方</th>
</tr>
</thead>
<tbody>
<tr>
  <td>创建应用</td>
  <td>• 由基座模型自动创建<br>• 由 <a href="aworld-skills/app_evaluator/SKILL.md">UI 评估 Skill</a> 自动评估</td>
  <td style="width:22%"><img src="readme_assets/aworld_cli_app_create.gif" alt="应用创建演示" width="270"></td>
  <td><a href="docs/Recipe/miniapp_build_recipe.md">查看配方</a></td>
</tr>
<tr>
  <td>创建视频</td>
  <td>• 由 <a href="https://www.skillhub.club/skills/remotion-dev-remotion-remotion">Remotion Skill</a> 自动创建<br>• 人工评估</td>
  <td style="width:22%"><img src="readme_assets/aworld_cli_intro_fast.gif" alt="视频创建演示" width="270"></td>
  <td><a href="docs/Recipe/video_create_recipe.md">查看配方</a></td>
</tr>
</tbody>
</table>


# 你的 AWorld-CLI 之旅
从想法到可演进、自主智能体的旅程，从你的指尖开始。


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

配置完成后，在终端输入 `aworld-cli` 即可开始你的旅程。

你也可以在「工作目录」下创建 `.env` 文件，配置模型与 API 等。详见 [环境配置](./README_env_config.md)。


## 用 AWorld-CLI 自动化创建
<p align="justify">
AWorld-CLI 不止于简单脚手架。它作为中央大脑——AWorld Agent，协调一组专业子智能体，自主地构建、评估甚至演进其他智能体。

这套多智能体系统协同工作，将你的想法变为现实：
</p>

<table>
<thead>
<tr><th style="white-space:nowrap">智能体名称</th><th>角色与核心职能</th></tr>
</thead>
<tbody>
<tr><td style="white-space:nowrap">👑 AWorld Agent</td><td><strong>编排者</strong>：中央大脑，理解用户目标、制定计划并将任务分派给合适的子智能体，从始至终管理整个工作流。</td></tr>
<tr><td style="white-space:nowrap">🧑‍💻 Developer</td><td><strong>构建者</strong>：负责编写、调试与重构代码的工匠。</td></tr>
<tr><td style="white-space:nowrap">🧐 Evaluator</td><td><strong>评判者</strong>：质量保障专家，根据客观标准评估 Developer 的输出，为演进循环提供关键反馈。</td></tr>
</tbody>
</table>

### 演进循环：构建 → 评估 → 演进

假设你提出：*「帮我做一个英语单词学习小程序，UI 质量分数要高于 0.9。」*

*   **Developer 构建**：`Developer` 分析需求并编写代码（如 HTML），使用 [CAST](#cast-征服代码复杂度）。
*   **Evaluator 评判**：`Evaluator` 使用 [我们验证过的 Skill](aworld-skills/app_evaluator/SKILL.md) 检查输出。
*   **循环精进**：若分数低于目标（如 0.9），AWorld 会指示 Developer 根据 Evaluator 指出的具体问题修复。循环持续直到满足你的标准。

***📹 观看自演进循环实战***

<p align="center">
  <video src="https://github.com/user-attachments/assets/ff56195e-e117-4d33-b709-9a2144680abd" 
         poster="readme_assets/evolution_loop_poster.png" 
         width="80%" controls style="max-width: 80%;">
  </video>
</p>


### 无评估则无演进

<p align="justify">
智能体要进步，必须先理解什么是「好」。评估是我们自主演进循环的核心，但也是复杂挑战：从有清晰指标的<strong>客观</strong>任务（如解数学题），到需要人类偏好的<strong>主观</strong>判断（如评判 UI 美观度）。现实中的演进还受限于庞大代码库、有限上下文窗口以及需要精确迭代。
</p>
<p align="justify">
AWorld 提供完整基础设施来解决这些问题。我们的系统被设计为同时驾驭两种评估场景，将你的专业能力转化为驱动智能体贯穿整个演进循环的决定性力量。
</p>

#### CAST：征服代码复杂度
<p align="justify">
智能体常因代码复杂度而失败。我们构建了 <strong>CAST</strong>（Code Abstract Syntax Tree，代码抽象语法树）来解决这一问题。CAST 不再让智能体面对扁平文本，而是提供代码的「架构蓝图」，从而支持：
</p>

*   **层级导航**：快速理解代码结构与意图，不被实现细节淹没。
*   **近乎无限的上下文**：智能压缩代码，只向智能体提供相关信息，突破上下文窗口限制。
*   **精准代码修改**：在完整依赖感知下做精确修改，避免「盲目」文本替换带来的错误。

#### 你的专业能力即评判标准
<p align="justify">
CAST 提供「改变」的技术能力，而你的知识提供「方向」。AWorld 的<strong>共享 Skill 体系</strong>让你的专业能力成为质量的终极度量。
</p>

<p align="justify">
<strong>自动评估</strong>：<code>Evaluator</code> 智能体评判表现并指出缺陷，为 <code>Developer</code> 智能体设定清晰、客观的目标。这形成强大协同：Evaluator 设定目标，Developer 用同一套知识去达成。
</p>

<p align="justify">
<strong>人工评估</strong>：对于需要主观判断的任务，你的直觉就是天花板。你是终极评判者。在任何阶段用自然语言给出反馈，AWorld 智能体会将其解读为下一轮演进的高优先级指令。
</p>

<p align="justify">
无论是你贡献的 Skill 给出的自动分数，还是你直接的人工指导，在 AWorld 中，精确反馈驱动精确演进。
</p>


# 久经考验的 Harness：基准表现优异
<p align="justify">
以下在竞争性基准上的领先排名，不仅是智能体成就，更是对 AWorld <strong>Harness</strong> 的直接验证。它们证明我们稳健、久经考验的基础设施，为构建一流 AI 提供了必要基石。


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
      <td style="padding: 8px; vertical-align: top;">🤖 智能体
        <br>
        <a href="https://playground.aworldagents.com/" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/Try-Online-9B59B6?style=flat-square" alt="在线体验">
        </a>
      </td>
      <td style="padding: 8px; vertical-align: top;">
        <strong>GAIA 基准<br>优异</strong>
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
          <img src="https://img.shields.io/badge/Code-README-green" alt="代码">
        </a>
      </td>
      <td style="padding: 8px; vertical-align: top;">
        多智能体系统<br>稳定性与编排
        <br>
        <a href="https://arxiv.org/abs/2508.09889" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/Paper-arXiv-red" alt="论文">
        </a>
      </td>
      <td style="padding: 8px; vertical-align: top;">2025/08/06</td>
    </tr>
    <tr>
      <td style="padding: 8px; vertical-align: top;">🧠 推理</td>
      <td style="padding: 8px; vertical-align: top;">
        <strong>IMO 2025<br>解题</strong>
        <br>
        <a href="https://www.imo-official.org/year_info.aspx?year=2025" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/IMO-2025-blue" alt="IMO">
        </a>
      </td>
      <td style="padding: 8px; vertical-align: top;">
        <strong>5/6</strong> 题<br>6 小时内解决
        <br>
        <a href="examples/imo/README.md" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/Code-README-green" alt="代码">
        </a>
      </td>
      <td style="padding: 8px; vertical-align: top;">多智能体协作<br>优于单模型</td>
      <td style="padding: 8px; vertical-align: top;">2025/07/25</td>
    </tr>
    <tr>
      <td style="padding: 8px; vertical-align: top;">🖼️ 多模态</td>
      <td style="padding: 8px; vertical-align: top;">
        <strong>OSWorld<br>第一名</strong>
        <br>
        <a href="https://os-world.github.io/" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/OSWorld-Leaderboard-green" alt="OSWorld">
        </a>
      </td>
      <td style="padding: 8px; vertical-align: top;">
        <strong>58.0%</strong> <br> 成功率
        <br>
        <a href="examples/osworld/README.md" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/Code-README-green" alt="代码">
        </a>
      </td>
      <td style="padding: 8px; vertical-align: top;">工具越多越好？</td>
      <td style="padding: 8px; vertical-align: top;">2025/09/18</td>
    </tr>
    <tr>
      <td style="padding: 8px; vertical-align: top;">🖼️ 多模态</td>
      <td style="padding: 8px; vertical-align: top;">
        <strong>VisualWebArena 九月第一名</strong>
        <br>
        <a href="https://docs.google.com/spreadsheets/d/1M801lEpBbKSNwP-vDBkC_pF7LdyGU1f_ufZb_NWNBZQ/edit?gid=2044883967#gid=2044883967" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/VWA-Leaderboard-green" alt="VWA">
        </a>
      </td>
      <td style="padding: 8px; vertical-align: top;">
        <strong>36.5%</strong> <br> 成功率
        <br>
        <a href="examples/visualwebarena/README.md" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/Code-README-green" alt="代码">
        </a>
      </td>
      <td style="padding: 8px; vertical-align: top;">自动化工具生成<br>
        <a href="https://arxiv.org/pdf/2509.21072" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/Paper-arXiv-red" alt="论文"></td>
      <td style="padding: 8px; vertical-align: top;">2025/09/25</td>
    </tr>
    <tr>
      <td style="padding: 8px; vertical-align: top;">🔍 深度搜索</td>
      <td style="padding: 8px; vertical-align: top;">
        <strong>Xbench 优异</strong>
        <br>
        <a href="https://xbench.org/" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/xbench-Leaderboard-green" alt="xbench">
        </a>
      </td>
      <td style="padding: 8px; vertical-align: top;">
        Pass@1: 51 <br> Pass@3: 61
        <br>
        <a href="examples/xbench/README.md" target="_blank" style="text-decoration: none;">
          <img src="https://img.shields.io/badge/Code-README-green" alt="代码">
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

    *Zengzhuang Xu, Bingguang Hao, Zechuan Wang, Yuntao Wen, Maolin Wang, 等*
   
2. **From Failure to Mastery: Generating Hard Samples for Tool-use Agents** arxiv, 2026. [论文](https://arxiv.org/abs/2601.01498), [代码](https://github.com/inclusionAI/AWorld-RL), [模型](https://huggingface.co/Bingguang/FunReason-MT), [数据集](https://huggingface.co/datasets/Bingguang/FunReason-MT)

    *Bingguang Hao, Zengzhuang Xu, Yuntao Wen, Xinyi Xu, Yang Liu, 等*


#### 模型训练

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

6. **Don't Just Fine-tune the Agent, Tune the Environment** arxiv, 2025. [论文](https://arxiv.org/abs/2510.10197)

    *Siyuan Lu, Zechuan Wang, Hongxuan Zhang, Qintong Wu, Leilei Gan, Chenyi Zhuang, 等*


#### 元学习

1. **Profile-Aware Maneuvering: A Dynamic Multi-Agent System for Robust GAIA Problem Solving by AWorld.** arxiv, 2025. [论文](https://arxiv.org/abs/2508.09889), [代码](https://github.com/inclusionAI/AWorld/blob/main/examples/gaia/README_GUARD.md)

    *Zhitian Xie, Qintong Wu, Chengyue Yu, Chenyi Zhuang, Jinjie Gu*

2. **Recon-Act: A Self-Evolving Multi-Agent Browser-Use System via Web Reconnaissance, Tool Generation, and Task Execution.** arxiv, 2025. [论文](https://arxiv.org/pdf/2509.21072), [代码](https://github.com/inclusionAI/AWorld/tree/main/examples/visualwebarena)

    *Kaiwen He, Zhiwei Wang, Chenyi Zhuang, Jinjie Gu*

</p>


# 参与贡献
<p align="justify">
我们的路线图包括扩展 AI for Science & Business 计划、深化自演进能力，以及丰富社区贡献的 Skill 库。

我们热烈欢迎开发者、研究人员和领域专家加入。无论你是增强框架，还是贡献你所在领域的 Skill，你的工作都有价值。

学术引用或希望联系我们，请使用以下 BibTeX：
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
