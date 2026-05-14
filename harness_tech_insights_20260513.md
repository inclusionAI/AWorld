---
title: AI Agent Harness 工程关键技术洞察
date: 2026-05-13
tags: [AI-Agent, Harness-Engineering, 技术洞察]
source: 基于本地Obsidian知识库harness相关文献的综合分析
---

# AI Agent Harness 工程关键技术洞察

> 基于知识库中10+篇核心文献的深度分析，包括AutoHarness论文、Cursor官方博客、LangChain实践等

## 🎯 核心发现：Harness才是AI Agent的真正产品

### 1. 范式转移：Agent = Model + Harness

**关键数据证明：**
- 同一模型（Claude Opus 4.5）在不同Harness下性能差异：**42% vs 78%**（CORE-Bench，+36%提升）
- LangChain编码Agent仅通过Harness调整：**52.8% → 66.5%**（TerminalBench 2.0，+13.7%）
- Devin PR合并率：2024年34% → 2025年67%（+33%，主要归功于Harness改进）

**核心认知：**
- 模型是可替换的商品，Harness才是差异化的核心竞争力
- 如果你的Harness随着模型改进而变得更复杂，说明设计方向错了
- 正确的Harness应该随着模型能力提升而**简化**

**类比：** 模型是引擎，Harness是整车。没人会单独买引擎。

---

## 🔬 技术突破：AutoHarness自动合成代码约束

### Google DeepMind的创新方法

**核心思想：** 让LLM自己生成Code Harness来约束自己的行为

**三种Harness模式：**

1. **动作过滤器（Action Filter）**
   - Harness生成所有合法候选动作
   - 模型从中选择
   - 适用场景：规则明确、动作空间有限

2. **动作校验器（Action Verifier）** ⭐ 论文重点
   - 模型先提出动作
   - Harness验证合法性
   - 非法则带警告让模型重新生成
   - 适用场景：复杂规则、需要灵活性

3. **策略即代码（Policy-as-Code）**
   - 完全用代码实现策略
   - 决策时不需要LLM
   - 成本极低，性能最高
   - 适用场景：规则完全可编码

**迭代优化流程：**
```
初始代码生成 → 环境反馈 → Critic分析 → Refiner优化 → 循环迭代
```

**树搜索策略：**
- 每个节点 = 一个Harness版本
- Thompson Sampling平衡探索/利用
- 在145个TextArena游戏上消除所有非法动作

**性能突破：**
- Gemini-2.5-Flash + AutoHarness > Gemini-2.5-Pro（裸模型）
- 小模型 + 定制Harness 击败通用大模型

---

## 🏗️ 核心设计模式：渐进式披露（Progressive Disclosure）

### 最被低估但最有效的模式

**数据证明：**
- Claude-Mem：静态加载25,000 tokens效率0.8%，渐进式披露955 tokens效率100%（**26x提升**）
- Cursor延迟工具加载：减少**46.9% token使用**
- Vercel案例：移除80%工具后，延迟从724s降至141s（-80.5%），Agent从失败变为成功

**理论基础：** Liu et al. (TACL 2024) 证明LLM性能呈**U型曲线**
- 信息在开头/结尾：性能最佳
- 信息在中间：性能下降15-47%

**实现策略：**

| 系统 | 策略 | 效果 |
|------|------|------|
| Claude Code | 技能按需加载（Skill.md模式） | 减少初始上下文 |
| Cursor | MCP工具延迟加载，仅提供工具名称 | -46.9% tokens |
| Manus | 分层操作空间（L1原子→L2 Bash→L3动态脚本） | 避免工具过载 |
| SWE-Agent | 观察压缩：最后5条完整，早期压缩为单行 | 保持上下文清晰 |

**Dex Horthy的"40%法则"：**
- 当模型输入超过容量40%时，进入"愚蠢区域"
- 信噪比下降 → 注意力分散 → 推理错误

---

## 🎛️ 控制论视角：Harness Engineering is Cybernetics

### Harness的本质是控制论系统

**三个核心要素：**
1. **传感器（Sensor）：** 测试、lint、架构约束检查
2. **执行器（Actuator）：** 模型生成的代码/修改
3. **反馈回路（Feedback Loop）：** 持续验证→修正→再验证

**历史类比：**
- James Watt离心调速器（1780s）：感知转速 → 自动调节阀门
- Kubernetes控制器：观察实际状态 → 与期望状态对比 → 调和差异
- Harness Engineering：设计环境和约束 → Agent生成代码 → 自动验证和修正

**角色转变：**
```
工人：转阀门 → 设计调速器
工程师：重启服务 → 编写K8s spec
开发者：写代码 → 设计Harness
```

**关键实践：**
- OpenAI团队曾每周五花20%时间清理"AI垃圾代码"
- 直到他们**将标准编码到Harness中**
- 你无法用未经校准的Agent清理混乱，必须先**将你的判断变成机器可读的**

---

## 🏭 主流系统的Harness设计对比

### Claude Code
- **循环架构：** 模型控制循环（while tool_call）
- **工具集：** ~18个基础工具（Bash、Glob、Grep、Read/Write/Edit、TodoWrite等）
- **信息分层：** 6层（组织策略、CLAUDE.md、用户设置、MEMORY.md、会话历史、Git状态）
- **渐进式披露：** 技能按需加载（Skill.md模式）
- **关键机制：** TodoWrite作为进度锚点，防止长期任务"迷失方向"

### Cursor
- **文件为中心：** 一切映射到文件（支持搜索、分组、版本控制）
- **模型特化：** 针对每个前沿模型调整工具集和提示
- **延迟加载：** MCP工具仅提供名称，定义按需获取（-46.9% tokens）
- **自定义嵌入：** 使用Agent会话轨迹训练嵌入模型（+12.5%准确率）
- **动态上下文：** 模型决定何时拉取过往对话、终端会话、相关工具

### Manus
- **KV-Cache优先：** 避免上下文前端变化导致缓存失效
- **逻辑掩码：** 所有工具永久加载，通过输出概率限制可用性
- **分层操作：** L1原子工具 → L2 Bash/MCP → L3动态脚本
- **迭代简化：** 5次重写，每次移除功能而非增加

### SWE-Agent
- **ACI设计：** Agent-Computer Interface（为LLM而非人类设计）
- **代码检查器：** 自动拒绝语法错误的编辑（-3%性能损失）
- **观察压缩：** 最后5条完整，早期观察压缩为单行

---

## 🛠️ 实战模式库

### 反馈回路模式

**构建-验证循环：**
```
1. 规划与探索 → 扫描代码库，制定计划
2. 构建 → 实施时考虑可验证性
3. 验证 → 运行测试，读取完整输出
4. 修复 → 分析错误，重新审视规范
```

**Ralph Loop（持续执行）：**
```
while not goal_achieved:
    拦截模型退出尝试
    清空上下文窗口
    重新注入原始提示 + 文件系统状态
    继续执行
```

**自愈循环（Replit）：**
```
生成 → 执行 → Playwright测试 → 修复 → 重新运行
```

### 上下文管理模式

**渐进式披露实现：**
- 文件系统卸载：保留路径，丢弃内容（可逆）
- 工具延迟加载：前置元数据，按需获取定义
- 技能按需激活：匹配触发词后再加载完整内容
- 观察压缩：近期完整，早期摘要

**压缩策略：**
- 工具输出：保留头尾tokens，完整输出存文件
- 会话历史：智能摘要，关键转折点保留
- 计划文件重写：将全局计划推入最近注意力范围

### 工具设计模式

**伪工具模式：**
- TodoWrite：无实际功能，强制Agent阐述和跟踪计划
- 效果：防止长期任务中"迷失方向"

**分层工具空间：**
```
L1: 原子函数调用（直接暴露给模型）
L2: 沙箱实用程序（通过Bash调用）
L3: 动态脚本（使用预装库）
```

---

## 📊 Cursor的Harness工程实践

### 上下文窗口的演进

**2024年末（早期）：**
- 大量静态上下文预先提供
- 严格护栏：限制工具调用数量、改写文件读取请求
- 每次编辑后提供lint和类型错误信息

**2026年（现在）：**
- 减少护栏，转向动态上下文
- 模型按需获取信息
- 仍保留实用静态上下文（OS、Git状态、当前文件）

### 评估框架变更的两种方式

**1. 离线评估：**
- 自建评估套件 + 公开基准CursorBench
- 快速、标准化，支持跨时间对比
- 局限：只能近似反映真实使用

**2. 在线A/B测试：**
- 同时部署多个框架变体
- 直接指标：延迟、token效率、工具调用次数、缓存命中率
- 质量指标：
  - **代码保留率（Keep Rate）：** 固定时间后仍保留的代码比例
  - **语义满意度：** 用LM读取用户回应，判断是否满意

### 工具可靠性工程

**成果：** 今年早些时候集中冲刺，将所有工具调用可靠性提升到至少99%，很多达到99.9%

**错误分类：**
- `InvalidArguments`：模型出错
- `UnexpectedEnvironment`：上下文窗口矛盾
- `ProviderError`：外部服务中断
- `UserAborted`、`Timeout`：预期行为

**告警机制：**
- 未知错误：超过固定阈值立即告警
- 预期错误：异常检测，显著高于基线时告警
- 按工具、按模型分别计算基线

**自动化：** 每周运行配备专门技能的自动化，搜索日志找出新问题，在Linear中创建工单

### 为不同模型定制框架

**工具格式定制：**
- OpenAI模型：基于patch的格式编辑文件
- Anthropic模型：字符串替换
- 给模型不熟悉的工具 → 额外消耗reasoning token + 更多错误

**提示定制：**
- OpenAI：更偏字面理解，更精确
- Claude：更偏直觉，对不够精确的指令容忍度更高

**模型怪癖缓解：**
- 案例："上下文焦虑" - 模型上下文窗口填满后开始拒绝任务
- 解决：调整提示，减轻这种行为

**中途切换模型的挑战：**
1. 不同模型的行为、提示、工具接口各不相同
2. 缓存是提供商和模型特定的，切换导致缓存未命中
3. 解决方案：
   - 添加自定义指令，告诉模型它是中途接管
   - 引导避免调用不属于其工具集的工具
   - 建议：尽量在整个对话中保持同一模型

---

## 🔑 关键启示与行动建议

### 投资优先级

1. **80%工程精力放在Harness设计，而非模型选择**
2. **可度量原则：** 将判断、架构标准、代码品味转化为机器可读的约束
3. **验证为王：** 构建-验证循环是Agent长期成功的基础
4. **简化取胜：** 如果Harness越来越复杂，说明方向错了
5. **控制论思维：** 不要控制模型做什么，而是设计让它自我校正的环境

### 从失败模式反推设计

```
常见失败 → Harness对策
────────────────────────────────
早停/放弃 → Ralph Loop强制继续
规划混乱 → TodoWrite伪工具锚定进度
工具调用错误 → 代码检查器拒绝语法错误编辑
上下文过载 → 压缩 + 延迟加载 + 文件系统卸载
测试缺失 → PreCompletionChecklistMiddleware强制验证
时间感知差 → 时间预算警告注入
```

### 简化原则

- Manus经历5次重写，每次都**移除功能**
- Anthropic设计Claude Code使其随模型改进而**收缩**
- Replit从1个Agent增到3个，但每个变得**更简单**

---

## 🔮 行业趋势预测

### 短期（1-2年）
- Harness标准化出现（类似Kubernetes之于容器编排）
- 出现专门的Harness质量评估基准
- 模型训练与Harness设计的协同演化加速

### 中期（3-5年）
- 模型吸收部分Harness功能（规划、自验证、长期连贯性）
- Harness专注于系统级约束和领域知识编码
- AutoHarness类技术成熟，小模型+定制Harness击败通用大模型

### 长期趋势
- 工程师角色转变：从"写代码"到"设计约束系统"
- P vs NP的实践：不需要超越机器实现能力，只需超越其评估能力
- Harness Engineering成为核心竞争力（如同今天的DevOps）

### 多智能体未来

Cursor博客指出：
- AI辅助软件工程将迈向多智能体模式
- 系统学会在专业化智能体间委派：规划、快速编辑、调试各司其职
- 协同编排能力体现在Harness中，而非任何单个智能体
- **Harness工程只会变得更加重要**

---

## 📋 Harness设计检查清单

### 基础层（必备）
- [ ] 文件系统访问和版本控制（Git）
- [ ] Bash/代码执行能力
- [ ] 沙箱隔离环境
- [ ] 基础工具集（读写编辑、搜索、测试）
- [ ] 错误捕获和回传机制

### 上下文管理层
- [ ] 渐进式披露策略（工具/技能/文档）
- [ ] 工具输出压缩/卸载
- [ ] 会话历史摘要
- [ ] 监控上下文使用率（< 40%阈值）

### 验证与反馈层
- [ ] 自我验证循环（测试-修复）
- [ ] 代码检查器（语法/lint）
- [ ] 架构约束检查
- [ ] 持续执行机制（Ralph Loop或等价物）

### 知识编码层
- [ ] 架构文档（AGENTS.md/CLAUDE.md）
- [ ] 编码标准和最佳实践
- [ ] 领域特定约束
- [ ] 自定义linters和修复建议

### 可观测性层
- [ ] 完整轨迹记录（LangSmith或等价工具）
- [ ] 失败模式分析能力
- [ ] 性能指标跟踪（延迟、tokens、成本）
- [ ] A/B测试基础设施

### 高级优化层
- [ ] 模型特化配置（针对不同模型调优）
- [ ] 自定义嵌入模型（如需要）
- [ ] 动态工具组装（JIT模式）
- [ ] Agent轨迹自我分析

---

## 📚 核心文献索引

### 论文
- AutoHarness (arXiv 2603.03329) - Google DeepMind
- Yang et al. - SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering (NeurIPS 2024)
- Liu et al. - Lost in the Middle: How Language Models Use Long Contexts (TACL 2024)
- Cobbe et al. - Training Verifiers to Solve Math Word Problems (2021)

### 行业博客
- Cursor - 持续改进我们的智能体框架
- LangChain - The Anatomy of an Agent Harness
- LangChain - Improving Deep Agents with Harness Engineering
- Anthropic - Effective Harnesses for Long-Running Agents
- Cursor - Dynamic Context Discovery
- Manus - Context Engineering for AI Agents

### 开源实现
- [deepagents-cli](https://github.com/langchain-ai/deepagents/tree/main/libs/cli) - LangChain的编码Agent
- [Claude's C Compiler](https://github.com/anthropics/claudes-c-compiler) - Anthropic并行Agent案例
- [SWE-Agent](https://github.com/princeton-nlp/SWE-agent) - Princeton的ACI实现

---

## 🏷️ 关键概念

- **Progressive Disclosure（渐进式披露）：** 按需加载信息，避免上下文过载
- **Cybernetics（控制论）：** 传感器+执行器+反馈回路的系统设计
- **Context Engineering（上下文工程）：** 优化模型输入的艺术与科学
- **Self-Verification Loop（自我验证循环）：** 构建→验证→修复的持续循环
- **Ralph Loop：** 拦截退出→清空上下文→重新注入任务的持续执行机制
- **ACI（Agent-Computer Interface）：** 为LLM而非人类设计的交互界面
- **Context Rot（上下文腐烂）：** 累积错误降低模型后续决策质量
- **Lost in the Middle：** LLM对中间位置信息的注意力下降现象
- **Code-as-Policy（策略即代码）：** 完全用代码实现策略，决策时不需要LLM

---

**最重要的认知：模型是引擎，Harness是整车。没人会单独买引擎。**

*生成时间：2026-05-13*
*基于本地Obsidian知识库harness相关文献的综合分析*
