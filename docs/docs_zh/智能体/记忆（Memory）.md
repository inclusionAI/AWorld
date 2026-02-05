AWorld Memory 是一个专为多智能体系统设计的统一记忆管理框架。它旨在帮助智能体存储、检索和处理信息，从而实现跨会话的持续学习、个性化交互和经验进化。

## 1. 核心概念
AWorld 将记忆划分为两个主要维度：**短期记忆 (Short-Term Memory)** 和 **长期记忆 (Long-Term Memory)**。

### 1.1 短期记忆 (Short-Term Memory)
用于存储智能体在当前任务或会话中的即时交互记录。

+ **存储对象**: `MemoryHumanMessage` (用户输入), `MemoryAIMessage` (AI 回复), `MemoryToolMessage` (工具调用结果), `MemorySystemMessage` (系统指令)。
+ **存储引擎**: 支持多种后端，默认为 `InMemoryMemoryStore`，生产环境推荐使用 `PostgresMemoryStore`。
+ **上下文管理**: 为了防止上下文溢出，提供多种自动压缩策略：
    - **裁剪策略 (Trimming)**: 仅保留最近的 N 轮对话。
    - **固定步长摘要 (Fixed Step Summary)**: 每 N 条消息生成一次摘要，并移除已被摘要的消息。
    - **长度触发摘要 (Length-based Summary)**: 当消息总长度超过阈值时触发摘要。
    - **单轮摘要 (Single Round Summary)**: 对过长的单条消息进行即时压缩。

### 1.2 长期记忆 (Long-Term Memory)
用于存储具有持久价值的信息，支持跨会话检索。

+ **UserProfile (用户画像)**:
    - 结构化存储用户的偏好、职业、技术栈、沟通风格等。
    - **示例**: `key="preferences.technical", value={"preferred_languages": ["Python"]}`。
+ **AgentExperience (智能体经验)**:
    - 记录智能体在解决复杂问题时的成功路径和技能。
    - **结构**: `skill` (技能名称), `actions` (动作序列), `outcome` (执行结果)。
+ **Fact (事实沉淀)**:
    - 从对话中提取的具体事实信息（如：用户住在上海）。
+ **ConversationSummary (历史会话总结)**:
    - 对已完成会话的高层级总结，作为未来交互的背景参考。

---

## 2. 快速开始
### 2.1 初始化 Memory 环境
推荐使用 `MemoryFactory` 进行全局初始化。

```python
from aworld.memory.main import MemoryFactory
from aworld.core.memory import MemoryConfig, MemoryLLMConfig

# 初始化 Memory 工厂
MemoryFactory.init(
    config=MemoryConfig(
        provider="aworld", # 核心引擎
        llm_config=MemoryLLMConfig(
            provider="openai",
            model_name="gpt-4o",
            api_key="your_api_key"
        )
    )
)

# 获取实例
memory = MemoryFactory.instance()
```

### 2.2 在智能体中使用记忆
通过 `AgentMemoryConfig` 深度定制每个 Agent 的记忆行为。

```python
from aworld.agents.llm_agent import Agent
from aworld.core.memory import AgentMemoryConfig, LongTermConfig

# 1. 配置记忆行为
memory_config = AgentMemoryConfig(
    enable_summary=True,        # 启用自动摘要
    trim_rounds=20,             # 保留最近20轮
    enable_long_term=True,      # 启用长期记忆
    long_term_config=LongTermConfig.create_simple_config(
        enable_user_profiles=True,
        enable_agent_experiences=True
    )
)

# 2. 创建智能体
agent = Agent(
    name="MemoryAgent",
    agent_memory_config=memory_config,
    # ... 其他配置
)
```

---

## 3. 基本操作
### 3.1 核心操作接口 (CRUD)
通过 `MemoryFactory.instance()` 拿到实例后，可以进行以下核心操作：

#### 1. 添加记录 (`add`)
用于将新的消息或记忆项存入系统。在 AWorld 中，添加记录通常会触发后续的自动摘要或长期记忆提取逻辑。

```python
from aworld.memory.models import MemoryHumanMessage, MessageMetadata

# 准备元数据
metadata = MessageMetadata(
    user_id="u_001",
    session_id="s_123",
    agent_id="a_999",
    task_id="t_456"
)

# 创建消息对象
message = MemoryHumanMessage(content="你好", metadata=metadata)

# 添加记忆
# agent_memory_config 可选，用于触发该 Agent 特有的摘要策略
await memory.add(
    message, 
    agent_memory_config=context.get_config().get_agent_memory_config(namespace="a_999")
)
```

#### 2. 删除记录 (`delete`)
根据 ID 删除指定的记忆项（通常为逻辑删除）。

```python
# 根据 memory_id 删除
await memory.delete("mem_uuid_12345")
```

#### 3. 获取最近历史 (`get_last_n`)
这是最常用的接口，用于在构建 LLM Prompt 时获取最近的对话上下文。它会自动处理 `init` (系统/初始信息)、`message` (对话) 和 `summary` (摘要) 的合并。

```python
# 获取最近 10 轮历史
# filters 必须包含 agent_id, session_id, task_id 以确保隔离
filters = {
    "agent_id": "a_999",
    "session_id": "s_123",
    "task_id": "t_456"
}

histories = memory.get_last_n(
    last_rounds=10, 
    filters=filters,
    agent_memory_config=agent_memory_config # 传入配置以支持更复杂的合并逻辑
)
```

#### 4. 获取所有记录 (`get_all`)
根据过滤器获取匹配的所有记忆项。

```python
# 获取当前任务下的所有消息记录
all_messages = memory.get_all(filters={
    "task_id": "t_456",
    "memory_type": "message"
})
```

### 3.2 记忆隔离级别与检索过滤器
在通过 `MemoryFactory.instance()` 拿到实例后，最重要的一步是确定记忆的**隔离级别 (History Scope)**。这决定了智能体能够“回想起”多大范围的历史信息。

#### 1. 隔离级别 (History Scope)
在 `AgentMemoryConfig` 中通过 `history_scope` 参数配置，支持以下三种级别：

| 级别 | 参数值 | 说明 | 适用场景 |
| --- | --- | --- | --- |
| **任务级 (默认)** | `task` | 仅检索当前 `task_id` 下的消息。 | 独立的任务执行，不需要历史背景。 |
| **会话级** | `session` | 检索相同 `session_id` 下的所有消息（可能跨多个 Task）。 | 连续的多轮对话，由多个子任务组成。 |
| **用户级** | `user` | 检索该 `user_id` 下在该 Agent 上的所有历史记录。 | 长期个性化助手，需要记住用户的习惯。 |


#### 2. 构建检索过滤器
在调用 `get_last_n` 或 `search` 时，框架会根据配置的 Scope 自动构建过滤器。手动操作示例：

```python
# 逻辑参考 aworld/agents/llm_agent.py
def build_filters(self, context: Context):
    filters = {"agent_id": self.id()}
    config = context.get_agent_memory_config(self.id())
    
    # 核心逻辑：根据 history_scope 确定过滤维度
    scope = config.history_scope or "task"
    if scope == "user":
        filters["user_id"] = context.user_id # 或从 task 中获取
    elif scope == "session":
        filters["session_id"] = context.get_task().session_id
    else:
        filters["task_id"] = context.get_task().id
    return filters

# 使用过滤器获取最近历史
histories = memory.get_last_n(
    last_rounds=config.history_rounds, 
    filters=build_filters(context)
)
```

### 3.3 检索长期记忆
```python
# 检索用户画像
profiles = await memory.retrival_user_profile(
    user_id="u_001", 
    user_input="我想写点 Python 代码"
)

# 检索相关经验
experiences = await memory.retrival_agent_experience(
    agent_id="a_999",
    user_input="处理 API 频率限制报错"
)
```

---

## 4. 长期记忆抽取 (Long-term Extraction)
长期记忆抽取是 AWorld 实现“智能体进化”的核心机制。它通过分析短期对话历史，自动沉淀具有持久价值的知识。

### 4.1 触发机制 (Triggering)
长期记忆的抽取不是每轮都发生的，而是由 `DefaultMemoryOrchestrator` 根据配置触发：

+ **消息计数触发**: 当短期记忆中的消息数量达到 `message_count_threshold`（默认 10 条）时触发。
+ **重要性触发**: 监控消息中的关键词（如 "error", "success", "完成"），一旦匹配则触发。
+ **强制触发**: 在调用 `trigger_short_term_memory_to_long_term` 时设置 `force=True`。

### 4.2 抽取流程 (Extraction Flow)
AWorld 的长期记忆抽取是一个**全自动**的过程，由系统在后台静默执行。

1. **任务创建**: 编排器（Orchestrator）根据 `history_scope` 自动收集相关的消息流。
2. **异步处理**: 如果启用了 `enable_background_processing`，抽取任务将在后台异步执行，不影响主对话的响应速度。
3. **LLM 提取 (Gungnir)**: 
    - **全自动识别**: 系统会自动识别对话中的关键信息并进行结构化。
    - **支持 Prompt 自定义**: 虽然抽取是自动触发的，但开发者可以根据业务需求自定义抽取的 Prompt 模板。通过修改 `ExtractionConfig` 中的 `user_profile_extraction_prompt` 或 `agent_experience_extraction_prompt`，可以精确控制 LLM 关注的信息类型和输出格式。
4. **结构化存储**: 提取结果被转换为 `UserProfile` 或 `AgentExperience` 对象，并持久化到 `MemoryStore` 和向量数据库中。

### 4.3 事实抽取 (Fact Extraction)
除了画像和经验，AWorld 还支持从工具执行结果（如搜索结果）中抽取原子化的**事实 (Fact)**。

+ **抽取场景**: 通常由 `ExtractToolFactOp` 在检测到工具返回大量信息时触发。
+ **特点**: 将复杂的信息流拆解为独立的、最小单元的知识点（如：某公司的财报数据、某个软件的发布日期）。
+ **作用**: 避免智能体在处理长文档或大量搜索结果时丢失细节，同时通过向量检索实现精准的“事实召回”。

### 4.4 相关配置
```python
from aworld.core.memory import LongTermConfig

# 深度定制抽取行为
lt_config = LongTermConfig()
lt_config.trigger.message_count_threshold = 15      # 提高触发阈值
lt_config.trigger.enable_importance_trigger = True  # 开启重要性监控

# 自定义抽取 Prompt (可选)
lt_config.extraction.user_profile_extraction_prompt = "你是一个专业的人格分析师，请从对话中提取..."
lt_config.extraction.agent_experience_extraction_prompt = "请总结智能体在本次任务中的核心技术路径..."

lt_config.extraction.enable_user_profile_extraction = True
lt_config.processing.enable_background_processing = True
```

---

## 5. 高级功能
### 5.1 向量存储与语义搜索
AWorld Memory 内置了向量化支持，能够基于语义检索最相关的历史信息。

```python
from aworld.core.memory import EmbeddingsConfig, VectorDBConfig

# 配置向量数据库 (例如 ChromaDB)
custom_config = MemoryConfig(
    embedding_config=EmbeddingsConfig(
        provider="openai",
        model_name="text-embedding-3-small"
    ),
    vector_store_config=VectorDBConfig(
        provider="chroma",
        config={"chroma_data_path": "./data/chroma"}
    )
)
```

### 5.2 语义化检索接口
`search` 方法支持混合检索：

```python
results = memory.search(
    query="用户对编程语言的偏好",
    memory_type="user_profile",
    filters={"user_id": "u_001"},
    limit=5
)
```

### 5.3 内部实现与优化参考
#### 消息格式转换 (LLM 适配)
AWorld 内部通过 `to_openai_message()` 统一将记忆项转换为模型可接受的格式：

```python
# 逻辑参考 aworld/agents/llm_agent.py
messages = []
for history in histories:
    if isinstance(history, MemoryMessage):
        # 自动处理 role, content 以及 tool_calls 等复杂结构
        messages.append(history.to_openai_message())
```

#### 冗余工具调用清理
为了保持上下文整洁并节省 Token，框架在特定时刻（如 Loop 终止或重置时）会自动清理冗余的工具调用消息：

```python
# 见 _clean_redundant_tool_call_messages 实现
# 逻辑：从后往前遍历，删除未产生结果或已失效的 tool_call 记录
memory.delete(history_item_id)
```

---

## 5. 最佳实践
1. **元数据一致性**: 始终提供完整的 `MessageMetadata`。由于 AWorld 默认使用 `task_id` 进行隔离，漏传元数据会导致智能体出现“失忆”或“记忆串扰”。
2. **隔离级别 (History Scope) 的选择策略**:
    - **使用 **`task`** (默认)**: 
        * 当任务是原子化的（如：翻译一段话、执行一个 SQL）。
        * 为了节省 Token，不希望历史干扰当前决策。
        * 并行执行多个互不相关的子任务。
    - **使用 **`session`:
        * 交互式对话（Chat）：用户会通过“刚才说的那个”来指代历史。
        * 复杂流水线：前一个 Task 的输出是后一个 Task 的背景（如：搜索 -> 整理 -> 写报告）。
        * 单次登录期间的上下文维持。
    - **使用 **`user`:
        * 个性化管家：需要记住用户的名字、偏好、职业等。
        * 长期项目追踪：跨越多天、多次登录的持续性支持。
        * 注意：级别越高，Token 消耗和检索压力越大，建议配合 `enable_summary` 使用。
3. **上下文压缩与历史传递策略**:
    - **短上下文场景**: 建议通过 `session` 级别共享历史消息。在会话长度可控时，直接传递原始消息能保持最高的交互忠实度。
    - **长上下文场景**: 
        * **Summary 策略**: 建议启用 `enable_summary`，通过定期生成的摘要替换旧消息，有效降低 Token 消耗并保留关键背景。
        * **add_knowledge 结合使用**: 对于超长信息或需要持久化的知识，建议使用 `add_knowledge` 将其沉淀为知识片段。通过“摘要（Summary）保持流式背景”+“`add_knowledge` 保持关键细节”的组合模式，实现高效的上下文管理。



---

_更多技术细节请参考源码模块：_`aworld.memory`

