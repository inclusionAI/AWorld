> 大模型的性能瓶颈不在于参数量，而在于其感知到的“上下文质量”。尽管上下文窗口在不断扩大，但研究发现存在“**上下文腐蚀 (Context Rot)**”现象：随着 Token 数量增加，模型准确召回信息的能力会下降。上下文工程是 Agent 的“数字大脑”与现实世界之间的连接器，是 **Prompt Engineering 的自然演进** —— 从单纯的指令编写转向对 LLM 推理过程中最优 Token 集合的动态策划与维护。
>

---

## 1. 核心挑战：为什么需要上下文工程？
在长序列和复杂 Agent 任务中，单纯增加窗口大小并不能解决所有问题，反而带来了以下挑战：

1. **上下文腐蚀 (Context Rot)**: LLM 存在“注意力预算”。由于 Transformer 架构的局限，过长的上下文会导致模型在中间部分的信息提取能力显著下降（Lost in the middle）。
2. **信息熵爆炸 (Noise & Entropy)**: 大量的工具原始输出、冗余日志淹没了关键指令。
3. **状态幻觉 (State Hallucination)**: 长期记忆中的陈旧信息与实时 Workspace 中的最新状态发生冲突，导致决策偏差。
4. **RAG 语义丢失**: 传统的文档分块（Chunking）破坏了上下文的完整性，导致检索出的片段缺乏解释性。

---

## 2. 上下文的组成 (Context Anatomy)
AWorld 将上下文负载拆解为三大核心组件，实现精细化管理：

### 2.1 指导推理的上下文 (Guidance)
定义 Agent 的基本推理模式 and 行为边界：

+ **系统指令 (System Instructions)**: 角色、能力与约束。
+ **工具定义 (Tool Definitions)**: API 架构描述（支持 MCP 动态加载）。
+ **少样本示例 (Few-Shot)**: 引导模型遵循特定思考链路。

### 2.2 事实与证据数据 (Evidential)
Agent 推理的实质性依据：

+ **长期记忆 (Long-Term)**: 用户偏好、事实知识、跨会话经验。
+ **外部知识 (External)**: 通过 RAG 检索到的文档片段。
+ **工具/子 Agent 输出**: 任务执行过程中的中间结果。

### 2.3 即时对话信息 (Immediate)
将 Agent 置于当前交互流中：

+ **对话历史 (History)**: 维持连贯性的回合记录。
+ **草稿板 (Scratchpad)**: 记录中间推理过程。
+ **用户提示 (User Prompt)**: 当前需要解决的具体查询。

---

## 3. 核心架构：AmniContext
AWorld 基于 **AmniContext** 框架，通过**主动控制**而非被级堆叠来对抗上下文腐化。

### 3.1 设计哲学 (A-M-N-I)
+ **A (Ant)**: 承载蚂蚁集团在分布式架构与大规模协作方面的工程沉淀。
+ **M (Mind)**: 模拟人类的工作记忆与长短期记忆交互。
+ **N (Neuro)**: 模仿神经网络的信息索引与关联。
+ **I (Intelligence)**: 驱动高质量的智能决策。

### 3.2 层次化结构与记忆模型
上下文支持 **树状分级引用**，通过向上回溯机制实现信息共享与隔离：

| 记忆层级 | 存储位置 | 作用 |
| :--- | :--- | :--- |
| **Working Memory** | 内存 / TaskState | 实时决策，存储当前步骤的 KV 数据 |
| **Short Memory** | Checkpoint / Memory | 维持任务连续性，包含对话历史与轨迹 |
| **Long Memory** | 向量库 / UserProfile | 跨会话个性化，存储用户画像与事实知识 |


---

## 4. 基本用法 (Basic Usage)
基本用法涵盖了构建标准智能体任务所需的核心操作。

### 4.1 初始化与创建
在开始之前，需要初始化中间件以启用记忆和检索功能。

```python
from aworld.core.context.amni import ApplicationContext, TaskInput, init_middlewares
from aworld.core.context.amni.config import AmniConfigFactory, AmniConfigLevel

# 1. 初始化中间件 (启用 SQLite 记忆后端与检索器)
init_middlewares()

# 2. 创建任务输入
task_input = TaskInput(
    user_id="user_001",
    session_id="session_001",
    task_id="task_001",
    task_content="分析 2024 年 AI 趋势"
)

# 3. 创建配置 (PILOT/COPILOT/NAVIGATOR)
# PILOT 等级提供基础的上下文管理
config = AmniConfigFactory.create(level=AmniConfigLevel.PILOT)

# 4. 从输入创建上下文 (异步)
context = await ApplicationContext.from_input(task_input, context_config=config)
```

### 4.2 神经元理念与 Prompt 组装 (Neurons & Assembly)
AWorld 的核心理念是：**把 Prompt 中的每一块结构化信息都视为一个“神经元 (Neuron)”**。上下文不再是杂乱无章的文本堆砌，而是由多个具备感知能力的神经元动态组装而成的“数字大脑”。

#### 1. 上下文加工流水线：Op -> Neuron
信息的流动遵循 **“加工 (Op) -> 表达 (Neuron)”** 的管道模型：

+ **Context Op (操作)**: 负责从原始输入（如工具执行结果、对话历史）中提取、清洗和结构化信息。
+ **Neuron (神经元)**: 负责将加工后的信息以最优的格式（如 XML）在 Prompt 中呈现给模型。

#### 2. 变量存取与自动组装
上下文提供简单的 KV 接口，并支持在 Prompt 模板中通过 `{{xxx}}` 语法动态引用。

```python
# 存储运行时变量 (Working Memory)
context.put("focus_area", "LLM Context Window")

# 获取变量 (支持自动向上回溯父级上下文)
area = context.get("focus_area")
```

#### 3. 用户自定义能力 (Customization)
开发者可以根据特定业务场景，轻松扩展自定义的神经元和处理操作。

**自定义神经元示例：**

```python
from aworld.core.context.amni.prompt.neurons import Neuron, neuron_factory

@neuron_factory.register(name="user_preference", desc="自定义用户偏好神经元")
class UserPreferenceNeuron(Neuron):
    async def format(self, context: ApplicationContext, **kwargs) -> str:
        pref = context.get("user_pref")
        return f"<user_pref>{pref}</user_pref>"
```

**自定义加工操作 (Op) 示例：**

```python
from aworld.core.context.amni.processor.op import BaseOp, memory_op

@memory_op("extract_pref")
class ExtractPreferenceOp(BaseOp):
    async def run(self, context: ApplicationContext):
        # 逻辑：从对话中分析并提取偏好，存入 KV 存储
        context.put("user_pref", "High Precision")
```

#### 4. Prompt 组装进阶示例
`PromptService` 支持引用内置与自定义的各类神经元：

```python
template = """
# 任务与环境
目标: {{task_input}}
活跃工作目录: {{working_dir}}

# 认知神经元
关键事实: {{facts}}
用户偏好: {{user_preference}}  # 引用自定义神经元

# 历史回溯
根任务目标: {{root.task_input}}
"""

# 执行格式化，系统会自动调用对应神经元的 format 方法
prompt = await context.prompt_service.async_format(template)
```

### 4.3 知识管理与使用 (Knowledge Usage)
Knowledge 指存储在 Workspace 中的非结构化或半结构化数据（Artifacts），可通过上下文进行索引和引用。

#### 添加知识
```python
from aworld.output import Artifact

# 1. 创建工件 (例如一个研究报告)
artifact = Artifact(
    id="report_001",
    content="AWorld 使用分布式上下文管理...",
    metadata={"type": "text", "source": "research"}
)

# 2. 将知识添加到上下文 (自动进行索引)
await context.add_knowledge(artifact)
```

#### 在 Prompt 中引用知识
你可以通过特定的路径语法直接在 Prompt 中注入知识内容：

```python
# 引用完整内容
prompt_with_content = await context.prompt_service.async_format(
    "参考报告内容：{{knowledge/report_001/content}}"
)

# 引用自动生成的摘要 (需开启相应配置)
prompt_with_summary = await context.prompt_service.async_format(
    "报告摘要：{{knowledge/report_001/summary}}"
)
```

---

## 5. 高级用法 (Advanced Usage)
高级用法适用于多智能体协作、超长任务以及自动化认知加工。

### 5.1 上下文卸载 (Context Offloading)
针对巨量工具输出（如海量网页内容），系统会自动或手动将其从上下文卸载到 Workspace。

```python
# 1. 自动卸载配置 (在 COPILOT/NAVIGATOR 等级默认开启)
config = AmniConfigFactory.create(
    level=AmniConfigLevel.COPILOT,
    tool_result_offload=True,
    tool_result_length_threshold=30000  # 超过 30k 字符自动卸载
)

# 2. 手动执行卸载
await context.offload_by_workspace(artifacts)
```

### 5.2 任务树与规划模式 (Hierarchical Tasks & Planning)
AmniContext 的核心优势在于对 **Planning 模式** 的原生支持。它通过树状任务结构（Task Tree），将复杂的宏观目标拆解为可执行的微观原子任务，并自动管理其间的上下文流动。

#### 核心能力：多任务分解与并行
+ **递归拆解 (Recursive Decomposition)**: Agent 可以根据主任务目标动态生成 N 个子任务，每个子任务拥有独立的 `SubContext`。
+ **上下文继承 (Inheritance)**: 子上下文自动继承父级的 `kv_store` 和环境配置，确保子任务在执行时具备全局视野（Contextual Awareness）。
+ **状态聚合 (Consolidation)**: 当子任务完成后，通过 `merge_sub_context` 将结果、事实和产生的 Token 消耗异步合并回主上下文。

#### 示例：规划模式下的任务生命周期
```python
# 1. 构建子上下文 (由 Planner Agent 触发)
# 系统会自动建立父子节点的引用关系，并克隆必要的运行环境
sub_context = await context.build_sub_context(
    sub_task_content="调研长文本模型技术细节",
    sub_task_id="sub_task_001",
    task_type="normal" # 支持 normal 或 background (后台异步)
)

# 2. 精准上下文隔离与回溯
# 子任务只关注自己的 input，但可以通过 {{parent.xxx}} 获取主任务的背景
parent_goal = await sub_context.prompt_service.async_format("{{parent.task_input}}")

# 3. 任务执行与状态合并
# 任务完成后，子任务产生的 Facts、KV 变量会自动更新到父任务中
context.merge_sub_context(sub_context)
```

#### 并行执行支撑
在分布式场景下，AmniContext 支持多个子任务在不同的执行引擎中**并行运行**。每个节点通过 `snapshot` 和 `restore` 保持上下文的一致性，最终在根节点实现全局状态的终态聚合。这使得 AWorld 能够高效处理复杂的研究调研、代码库重构等需要大规模分工的任务。

### 5.3 多智能体隔离 (Agent Isolation)
为每个 Agent 建立独立的私有状态空间，防止变量污染。

```python
# 1. 初始化 Agent 私有状态
await context.build_agents_state([web_agent, analyst_agent])

# 2. 在指定命名空间存取
context.put("search_history", ["link1", "link2"], namespace=web_agent.id())
```

### 5.4 环境集成与自由空间 (Freedom Space)
`FreedomSpaceService` 为 Agent 提供了一个隔离的“自由空间”（工作目录），其核心能力是实现**持久化存储与执行环境（Sandbox/Docker）之间的文件系统共享**。

#### 核心特性
+ **文件系统映射**: Agent 在自由空间中操作的文件会自动映射到执行环境中的 `env_mount_path`。
+ **透明存取**: Agent 编写的代码或生成的工件，在物理存储层（本地/OSS）与逻辑执行层（Docker）之间是透明同步的。
+ **持久化保证**: 即使执行环境销毁，自由空间中的数据依然保留在 Workspace 中，支持跨任务恢复。

#### 示例：配置与使用
```python
# 1. 配置环境共享参数
config = AmniConfigFactory.create(level=AmniConfigLevel.COPILOT)
config.env_config.env_type = "remote" # 启用远程/沙盒模式
config.env_config.env_mount_path = "/workspace" # 执行环境内的挂载点

# 2. 向自由空间添加文件并获取环境路径
success, env_abs_path, content = await context.freedom_space_service.add_file(
    filename="data_process.py",
    content="import os\nprint(os.getcwd())",
    mime_type="text/x-python"
)
# env_abs_path 将返回 "/workspace/data_process.py"

# 3. 在代码执行器中使用
# Agent 可以直接在 sandbox 中执行该路径下的文件
```

#### 路径变量引用
在 Prompt 模板中，可以使用以下变量精准引用环境信息（请确保变量名与代码实现一致）：

+ `{{working_dir_env_mounted_path}}`: 获取自由空间在执行环境中的绝对挂载路径。
+ `{{working_dir}}`: 返回自由空间中所有已挂载文件的格式化列表（XML 格式，包含文件名与绝对路径）。
+ `{{current_working_directory}}`: 获取当前进程的操作系统运行目录。

### 5.5 自动化认知加工 (Autonomous Cognition)
在 `NAVIGATOR` 等级下，系统将全面开启基于“认知闭环”的自动驾驶模式，实现 **System Prompt 自动增强** 与 **工具动态注入**：

+ **System Prompt Augment (自动拼接)**: 系统会根据配置的 `neuron_names`（如 `task`, `working_dir`, `skills`, `basic`），自动将当前任务状态、工作目录文件清单、环境基础信息等“神经元”拼接至 Agent 的 System Prompt 中。Agent 无需显式感知这些变量，启动即可获得全量语境。
+ **Dynamic Tool Injection (工具动态注入)**: 通过 `skills` 神经元，系统会自动为 Agent 注入可选工具（Skills）的清单与使用指南。
    - **渐进式披露**: 初始仅注入工具摘要，Agent 决定需要某项技能时，通过 `active_skill` 动态加载完整的工具 Schema 与说明。
    - **自动引导**: 自动拼接 `<skills_guide>`，告知 Agent 如何管理自己的技能栈（Activate/Offload）。
+ **Automated Reasoning Orchestrator**: 开启自主推理编排。系统根据目标自动驱动推理引擎进行策略制定与任务逻辑流分解。开启此项后，系统会自动为 Agent 注入 `context_planning` 工具，使其具备自主管理任务树的能力。
+ **Automated Cognitive Ingestion**: 开启自动认知摄取。智能体在运行过程中自动摄取碎片化信息并将其转化为结构化的认知资产。开启此项后，系统会自动注入 `context_knowledge` 工具，并同步加载 `todo`（任务清单）与 `action_info`（执行历史）神经元，实现对执行过程的实时感知与沉淀。
+ **Automated Memory Recursive**: 开启递归经验循环。通过持续回顾过去并修正当前行为，实现闭环记忆检索与存储。
+ **Automated Memory Recall**: 开启自动记忆召回。系统主动感知并召回相关的历史经验，辅助决策。

---

## 6. 场景化上下文策略选择 (Scenario-based Strategies)
在不同的业务场景下，盲目堆叠上下文只会导致信息熵爆炸。应根据任务复杂度灵活选择策略：

### 6.1 场景一：简单对话与单次问答 (Simple Chat)
+ **特征**: 任务目标明确，不依赖复杂的外部文件或长历史。
+ **推荐策略**:
    - **配置级别**: `AmniConfigLevel.PILOT`
    - **核心神经元**: `basic` + `task`
    - **策略细节**: 保持较低的 `history_rounds` (如 5-10)，无需开启自动摘要或卸载。

### 6.2 场景二：复杂研究与信息检索 (Complex Research/RAG)
+ **特征**: 需要阅读大量文档、网页，检索召回率是关键。
+ **推荐策略**:
    - **配置级别**: `AmniConfigLevel.COPILOT`
    - **核心神经元**: `knowledge` + `summaries`
    - **集成实践**: 强制开启 **Contextual Retrieval**（为每个 Chunk 补全上下文），并利用 **Prompt Caching** 缓存大型参考资料。
    - **裁剪建议**: 针对检索结果使用基于相关性的 `Pruning` 策略，仅保留高分片段。

### 6.3 场景三：规划密集型任务 (Planning & Execution)
+ **特征**: 任务链路长，包含多个子任务，需要频繁调用工具并感知任务树。
+ **推荐策略**:
    - **配置级别**: `AmniConfigLevel.NAVIGATOR`
    - **核心神经元**: `task` + `todo` + `action_info`
    - **自动化能力**: 开启 `automated_reasoning_orchestrator`，让 Agent 自主管理任务状态。
    - **状态管理**: 在每个子任务节点强制执行 `context.snapshot()`，防止级联失败导致的上下文丢失。

### 6.4 场景四：代码执行与沙盒协作 (Code Sandbox)
+ **特征**: 依赖本地/远程文件系统，需要频繁读写工件。
+ **推荐策略**:
    - **配置级别**: `AmniConfigLevel.COPILOT` / `NAVIGATOR`
    - **核心神经元**: `working_dir` + `skills`
    - **环境集成**: 配置 `env_mount_path` 实现文件共享。
    - **卸载策略**: 针对海量编译日志或数据输出，强制开启 `tool_result_offload`，防止 Token 溢出。

---

## 7. 最佳实践指南 (Summary Checklist)
1. **注意力预算管理**: 始终遵循“最小必要原则”。核心指令建议通过 `{{agent_instruction}}` 显式放在 User Prompt 附近（Recency Bias 利用）。
2. **命名空间隔离**: 在多 Agent 协作时，务必使用 `namespace` 参数调用 `put/get`，防止不同 Agent 间的“记忆污染”。
3. **结构化加工流**: 优先将原始数据存储为 Artifacts，利用 `Context Op` 进行递归摘要，保持上下文的高信噪比。

