AWorld 提供Agent相关非常丰富的能力，从宏观架构上有四个核心能力。

## 灵活的多智能体编排
作为AWorld最突出的特点之一，框架内置了基础的多智能体协作机制，能够支持灵活的多智能体系统(MAS)拓扑编排，可以构建各种智能体协作模式。

核心特性：

+ 支持多种协作模式：Workflow、Handoff、Team等
+ 支持构建DAG和DCG类型的智能体协作拓扑
+ 支持拓扑的无限嵌套
+ 支持星型、树型、网状、环状等多种拓扑结构

```python
from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig
from aworld.core.agent.swarm import Swarm

agent_config = AgentConfig(
    llm_config=ModelConfig(
        llm_model_name="gpt-4",
        llm_api_key="your-api-key",
        llm_base_url='available url'
    ),
)
# 创建智能体
researcher = Agent(name="研究员", conf=agent_config, system_prompt="你是一个专业的研究员...")
writer = Agent(name="作家", conf=agent_config, system_prompt="你是一个专业的内容创作者...")

# 构建协作拓扑
swarm = Swarm(
    researcher, writer
)
```

## 丰富的环境沙箱
AWorld提供了标准化的工具抽象层，能够将复杂的现实世界环境转化为 Agent 可理解的 Gym 风格或 API 风格环境，支持本地工具和远程工具，支持MCP(Model Context Protocol)标准协议实现的工具集成。

核心特性：

+ 统一的工具描述和接口
+ 工具执行的环境沙箱隔离
+ 内置错误处理和超时控制机制
+ 与MCP服务器无缝集成

```python
from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig, ModelConfig

# 配置MCP工具
mcp_servers=["simple-calculator"],
mcp_config={
    "mcpServers": {
        "simple-calculator": {
            "type": "sse",
            "url": "http://127.0.0.1:55555/calculator/sse",
            "timeout": 5,
            "sse_read_timeout": 300
        }
    }
}

agent_config = AgentConfig(
    llm_config=ModelConfig(
        llm_model_name="gpt-4",
        llm_api_key="your-api-key",
        llm_base_url='available url'
    ),
)
agent = Agent(
    name="工具智能体", 
    conf=agent_config, 
    mcp_servers=mcp_servers, 
    mcp_config=mcp_config
)
```

## 闭环的自进化训练
这是 AWorld 最具特色的能力。AWorld不仅支持构建和运行智能体，还收集高质量轨迹用于训练，提供了完整的训练和进化能力。

核心特性：

+ 框架无关的设计
+ 与三方RL训练框架集成(VeRL, AReaL)
+ 工具环境支持
+ 代码式一键启动训练

```python
from train.trainer.agent_trainer import AgentTrainer

# 定义数据集
train_dataset, test_dataset = "None or string or code reference"
# 定义agent
agent = Agent(...)
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

## 完备的可观测追踪
AWorld提供了完整的追踪框架，支持分布式追踪、上下文传播和Span管理，可追踪多步推理中的每一步 Tool Call 和 Token 消耗。

核心特性：

+ 分布式追踪支持
+ 与主流框架和协议集成
+ 智能体、工具和任务执行监控
+ 性能分析和瓶颈识别

```python
import aworld.trace as trace
from aworld.trace.config import ObservabilityConfig

# 启动跟踪，在任务启动之前调用
trace.configure(ObservabilityConfig(trace_server_enabled=True))

...

# asyncio任务监控
asyncio_monitor = AsyncioMonitor(detect_duration_second=1, shot_file_name=False)
asyncio_monitor.start()

# 执行任务
execute_your_task()

asyncio_monitor.stop()
```

## 其他关键能力
### 任务运行时
AWorld提供了灵活强大的的任务运行时能力，通过不同类型的Runner支持多样化的执行模式。

核心特性：

+ 事件驱动的任务处理
+ 可扩展的运行时(runner, handler, callback和hooks)架构
+ 支持不同的计算运行时引擎执行
+ 完整的执行轨迹跟踪

```python
from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig, RunConfig, EngineName
from aworld.runner import Runners

# 创建智能体
agent = Agent(
    name="分析师",
    conf=AgentConfig(
        llm_model_name="gpt-4",
        llm_api_key="your-api-key",
        llm_base_url='available url'
    )
    system_prompt="你是一个专业的AI趋势分析师..."
)

# 同步执行任务
result = Runners.sync_run(
    input="分析最新的AI发展趋势",
    agent=agent,
    run_conf=RunConfig(
        # 引擎名
        engine_name=EngineName.LOCAL,
        worker_num=len(tasks),
        reuse_process=True
    )
)

print(f"分析结果: {result.answer}, 轨迹: {result.trajectory}")

```

### 上下文管理
AWorld的上下文管理系统为智能体提供了全面的状态跟踪、配置管理和提示词优化功能。

核心特性：

+ 完整的状态跟踪和恢复机制
+ 智能提示词管理和优化
+ 任务穿透和隔离
+ 多层任务状态管理

```python
from aworld.core.context.base import Context
from aworld.core.task import Task

# 创建上下文
context = Context()
context.set_task(Task(input="分析最新的AI发展趋势"))
context.post_init()

# 基于任务ID做隔离
context.task_id

# 合并任务的上下文
context.merge_context(other_context)
# 合并子任务的上下文
context.merge_sub_context(sub_task_context)

```

### 记忆能力
AWorld内置了可扩展的记忆系统，支持短期和长期记忆、摘要、检索、嵌入等功能。

核心特性：

+ 短期记忆：快速访问最近交互内容
+ 长期记忆：持久化存储关键信息
+ 灵活的后端支持：支持多种向量数据库
+ 自动摘要策略：优化性能和上下文长度

```python
from aworld.config import AgentMemoryConfig
from aworld.memory.main import MemoryFactory
from aworld.memory.models import MemoryHumanMessage, MessageMetadata

# 创建实例
MemoryFactory.init()
memory = MemoryFactory.instance()
metadata = MessageMetadata(
    user_id="zues",
    session_id="session#foo",
    task_id="zues:session#foo:task#1",
    agent_id="super_agent",
    agent_name="super_agent"
)

# 添加记忆
await memory.add(
    MemoryHumanMessage(content="what is memory", metadata=metadata), 
    agent_memory_config=AgentMemoryConfig()
)
```

### 多种模型支持
AWorld的Models模块为开发者提供了一个强大而灵活的LLM接口，可以轻松集成各种大型语言模型服务，并在不同提供商之间无缝切换。

核心特性：

+ 统一的API
+ 工具化调用
+ 支持同异步和流式
+ 可自定义扩展

```python
from aworld.config import ModelConfig
from aworld.models.llm import get_llm_model, call_llm_model, acall_llm_model, acall_llm_model_stream

llm = get_llm_model(ModelConfig(
    llm_provider=os.getenv("LLM_PROVIDER"),
    llm_model_name=os.getenv("LLM_MODEL_NAME"),
    llm_base_url=os.getenv("LLM_BASE_URL"),
    llm_api_key=os.getenv("LLM_API_KEY"),
))

query = "What is an agent?"
messages = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": query},
]
# 同步
print(call_llm_model(llm, messages))

# 异步
# print(await acall_llm_model(llm, messages))

# 流
# async for chunk in acall_llm_model_stream(llm, messages):
#     print(chunk)
```



