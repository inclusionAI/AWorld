AWorld delivers a vibrant set of capabilities for building AI agents. At the architectural level, it offers four core pillars:

<h3 id="XkxNd">1. Flexible Multi-Agent Orchestration</h3>
One of AWorld’s most distinctive features is its built-in support for multi-agent collaboration, enabling flexible orchestration of Multi-Agent Systems (MAS) with diverse topologies and interaction patterns.

**Key Features:**

+ Supports multiple collaboration paradigms: **Workflow**, **Handoff**, **Team**, and more
+ Enables the construction of both **DAG** (Directed Acyclic Graph) and **DCG** (Directed Cyclic Graph) agent topologies
+ Support infinite nesting of topology
+ Accommodates various structural layouts: star, tree, mesh, ring, and other network topologies

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
# create agent
researcher = Agent(name="研究员", conf=agent_config, system_prompt="你是一个专业的研究员...")
writer = Agent(name="作家", conf=agent_config, system_prompt="你是一个专业的内容创作者...")

# build collaboration topology
swarm = Swarm(
    researcher, writer
)
```

<h3 id="2.-rich-environment-sandbox">2. Rich Environment Sandbox</h3>
AWorld provides a standardized tool abstraction layer that transforms complex real-world environments into agent-friendly, Gym-style or API-style interfaces. It supports both local and remote tools and integrates seamlessly with tools implementing the **Model Context Protocol (MCP)**.

**Key Features:**

+ Unified tool description and interface
+ Sandboxed execution environment with strong isolation
+ Built-in error handling and timeout controls
+ Seamless integration with MCP-compliant servers

```python
from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig, ModelConfig

# config
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
    name="tool_agent", 
    conf=agent_config, 
    mcp_servers=mcp_servers, 
    mcp_config=mcp_config
)
```

<h3 id="3.-closed-loop-self-evolution-training">3. Closed-Loop Self-Evolution Training</h3>
This is AWorld’s most innovative capability. Beyond just building and running agents, AWorld collects high-quality execution trajectories and provides a complete pipeline for training and continuous evolution.

**Key Features:**

+ Framework-agnostic design
+ Integration with third-party RL training frameworks (e.g., **VeRL**, **AReaL**)
+ Full support for tool-based environments in training
+ One-command, code-driven training launch

```python
from train.trainer.agent_trainer import AgentTrainer

# define dataset
train_dataset, test_dataset = "None or string or code reference"
# define agent
agent = Agent(...)
# train config
custom_train_config = "string or json"
# define reward
reward_func = "None or string or code reference"
# create trainer instance
trainer = AgentTrainer(agent=agent,
                       config=custom_train_config,
                       reward_func=reward_func,
                       train_dataset=train_dataset,
                       test_dataset=test_dataset)
trainer.train()
```

<h3 id="bb24ea02">4. Comprehensive Observability Tracing</h3>
AWorld includes a full-featured tracing framework that supports distributed tracing, context propagation, and span management—capturing every tool call and token consumption across multi-step reasoning chains.

**Key Features:**

+ Distributed tracing support
+ Integration with mainstream observability frameworks and protocols
+ Real-time monitoring of agents, tools, and task execution
+ Performance profiling and bottleneck identification

```python
import aworld.trace as trace
from aworld.trace.config import ObservabilityConfig

# use trace, call before start task
trace.configure(ObservabilityConfig(trace_server_enabled=True))

...

# asyncio monitor
asyncio_monitor = AsyncioMonitor(detect_duration_second=1, shot_file_name=False)
asyncio_monitor.start()

# execute task
execute_your_task()

asyncio_monitor.stop()
```

<h3 id="additional-key-capabilities">Additional Key Capabilities</h3>
<h4 id="task-runtime">**Task Runtime**</h4>
AWorld offers a flexible and powerful task execution runtime, supporting diverse execution modes through multiple types of Runners.

**Key Features:**

+ Event-driven 
+ Extensible runtime architecture (Runner, Handler, Callback, Hooks)
+ Support for multiple compute backends (local, Spark, Ray, etc.)
+ End-to-end execution trajectory tracking

```python
from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig, RunConfig, EngineName
from aworld.runner import Runners

# create agent
agent = Agent(
    name="analysis_agent",
    conf=AgentConfig(
        llm_model_name="gpt-4",
        llm_api_key="your-api-key",
        llm_base_url='available url'
    ),
    system_prompt="..."
)

# sync execute task
result = Runners.sync_run(
    input="analysis AI agent development trend",
    agent=agent,
    run_conf=RunConfig(
        # engine name
        engine_name=EngineName.LOCAL,
        worker_num=len(tasks),
        reuse_process=True
    )
)

print(f"answer: {result.answer}, trajectory: {result.trajectory}")
```

<h4 id="context-management">**Context Management**</h4>
AWorld’s context management system provides agents with comprehensive state tracking, configuration control, and prompt optimization.

**Key Features:**

+ Full state tracking and recovery
+ Intelligent prompt management and optimization
+ Task context penetration and isolation
+ Multi-layered task state management

```python
from aworld.core.context.base import Context
from aworld.core.task import Task

# create context
context = Context()
context.set_task(Task(input="分析最新的AI发展趋势"))
context.post_init()

# isolate based on task ID
context.task_id

# merging task context
context.merge_context(other_context)
# merging subtask context
context.merge_sub_context(sub_task_context)
```

<h4 id="memory-system">**Memory System**</h4>
AWorld includes an extensible memory module that supports short-term and long-term memory, summarization, retrieval, and embedding.

**Key Features:**

+ **Short-term memory**: Fast access to recent interactions
+ **Long-term memory**: Persistent storage of critical information
+ **Flexible backend support**: Compatible with multiple vector databases
+ **Automatic summarization strategies**: Optimizes performance and context window usage

```python
from aworld.config import AgentMemoryConfig
from aworld.memory.main import MemoryFactory
from aworld.memory.models import MemoryHumanMessage, MessageMetadata

# create instance
MemoryFactory.init()
memory = MemoryFactory.instance()
metadata = MessageMetadata(
    user_id="zues",
    session_id="session#foo",
    task_id="zues:session#foo:task#1",
    agent_id="super_agent",
    agent_name="super_agent"
)

# add memory
await memory.add(
    MemoryHumanMessage(content="what is memory", metadata=metadata), 
    agent_memory_config=AgentMemoryConfig()
)
```

<h4 id="multi-model-support">**Multi-Model Support**</h4>
AWorld’s **Models** module offers a powerful and flexible LLM interface, enabling seamless integration of various large language model providers and effortless switching between them.

**Key Features:**

+ Unified API across all models
+ Tool-style invocation pattern
+ Support for synchronous, asynchronous, and streaming modes
+ Customizable and extensible architecture

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
# sync
print(call_llm_model(llm, messages))

# async
# print(await acall_llm_model(llm, messages))

# async stream
# async for chunk in acall_llm_model_stream(llm, messages):
#     print(chunk)
```

