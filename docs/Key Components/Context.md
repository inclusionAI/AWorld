

# AWorld Context Engineering
> **First Principle**: The performance bottleneck of large models lies not in parameter count, but in the "quality of context" they perceive. Despite expanding context windows, research has found a "**Context Rot**" phenomenon: as the number of tokens increases, the model's ability to accurately recall information decreases. Context engineering is the connector between an Agent's "digital brain" and the real world, and it is the **natural evolution of Prompt Engineering** — shifting from simple instruction writing to the dynamic curation and maintenance of the optimal token set during the LLM's reasoning process.
>

---

## 1. Core Challenge: Why Context Engineering?
In long sequences and complex Agent tasks, simply increasing the window size does not solve all problems; instead, it brings the following challenges:

1. **Context Rot**: LLMs have an "attention budget." Due to the limitations of the Transformer architecture, excessively long contexts lead to a significant decline in the model's ability to extract information from the middle (Lost in the middle).
2. **Noise & Entropy**: Massive raw tool outputs and redundant logs drown out key instructions.
3. **State Hallucination**: Stale information in long-term memory conflicts with the latest state in the real-time Workspace, leading to decision bias.
4. **RAG Semantic Loss**: Traditional document chunking destroys context integrity, resulting in retrieved fragments that lack interpretability.

---

## 2. Context Anatomy
AWorld deconstructs the context load into three core components to achieve fine-grained management:

### 2.1 Guidance Context
Defines the Agent's basic reasoning mode and behavioral boundaries:

+ **System Instructions**: Roles, capabilities, and constraints.
+ **Tool Definitions**: API schema descriptions (supports dynamic loading via MCP).
+ **Few-Shot Examples**: Guides the model to follow specific reasoning chains.

### 2.2 Evidential Data
Substantive basis for Agent reasoning:

+ **Long-Term Memory**: User preferences, factual knowledge, cross-session experiences.
+ **External Knowledge**: Document fragments retrieved via RAG.
+ **Tool/Sub-Agent Outputs**: Intermediate results during task execution.

### 2.3 Immediate Information
Places the Agent within the current interaction flow:

+ **Dialogue History**: Round-by-round records to maintain coherence.
+ **Scratchpad**: Records intermediate reasoning processes.
+ **User Prompt**: The specific query currently needing resolution.

---

## 3. Core Architecture: AmniContext
AWorld is based on the **AmniContext** framework, countering context rot through **active control** rather than passive stacking.

### 3.1 Design Philosophy (A-M-N-I)
+ **A (Ant)**: Carries the engineering heritage of Ant Group in distributed architecture and large-scale collaboration.
+ **M (Mind)**: Simulates the interaction between human working memory and long/short-term memory.
+ **N (Neuro)**: Mimics neural network information indexing and association.
+ **I (Intelligence)**: Drives high-quality intelligent decision-making.

### 3.2 Hierarchical Structure and Memory Model
Context supports **tree-based hierarchical referencing**, achieving information sharing and isolation through an upward backtracing mechanism:

| Memory Layer | Storage Location | Function |
| :--- | :--- | :--- |
| **Working Memory** | Memory / TaskState | Real-time decision making, stores current step KV data |
| **Short Memory** | Checkpoint / Memory | Maintains task continuity, includes dialogue history and trajectories |
| **Long Memory** | Vector DB / UserProfile | Cross-session personalization, stores user profiles and factual knowledge |


---

## 4. Basic Usage
Basic usage covers the core operations required to build standard Agent tasks.

### 4.1 Initialization and Creation
Before starting, middlewares need to be initialized to enable memory and retrieval functions.

```python
from aworld.core.context.amni import ApplicationContext, TaskInput, init_middlewares
from aworld.core.context.amni.config import AmniConfigFactory, AmniConfigLevel

# 1. Initialize middlewares (Enable SQLite memory backend and retriever)
init_middlewares()

# 2. Create task input
task_input = TaskInput(
    user_id="user_001",
    session_id="session_001",
    task_id="task_001",
    task_content="Analyze AI trends in 2024"
)

# 3. Create configuration (PILOT/COPILOT/NAVIGATOR)
# PILOT level provides basic context management
config = AmniConfigFactory.create(level=AmniConfigLevel.PILOT)

# 4. Create context from input (Asynchronous)
context = await ApplicationContext.from_input(task_input, context_config=config)
```

### 4.2 Neurons & Assembly
The core philosophy of AWorld is: **Treat every structured piece of information in the Prompt as a "Neuron"**. Context is no longer a disorganized pile of text but a "digital brain" dynamically assembled from multiple sensory-capable neurons.

#### 1. Context Processing Pipeline: Op -> Neuron
The flow of information follows a pipeline model of **"Processing (Op) -> Expression (Neuron)"**:

+ **Context Op**: Responsible for extracting, cleaning, and structuring information from raw inputs (e.g., tool execution results, dialogue history).
+ **Neuron**: Responsible for presenting the processed information to the model in the optimal format (e.g., XML) within the Prompt.

#### 2. Variable Access and Auto-Assembly
Context provides a simple KV interface and supports dynamic referencing in Prompt templates via `{{xxx}}` syntax.

```python
# Store runtime variables (Working Memory)
context.put("focus_area", "LLM Context Window")

# Retrieve variables (Supports automatic upward backtracing to parent context)
area = context.get("focus_area")
```

#### 3. Customization
Developers can easily extend custom neurons and processing operations based on specific business scenarios.

**Custom Neuron Example:**

```python
from aworld.core.context.amni.prompt.neurons import Neuron, neuron_factory

@neuron_factory.register(name="user_preference", desc="Custom user preference neuron")
class UserPreferenceNeuron(Neuron):
    async def format(self, context: ApplicationContext, **kwargs) -> str:
        pref = context.get("user_pref")
        return f"<user_pref>{pref}</user_pref>"
```

**Custom Processing Operation (Op) Example:**

```python
from aworld.core.context.amni.processor.op import BaseOp, memory_op

@memory_op("extract_pref")
class ExtractPreferenceOp(BaseOp):
    async def run(self, context: ApplicationContext):
        # Logic: Analyze and extract preferences from dialogue, store in KV
        context.put("user_pref", "High Precision")
```

#### 4. Advanced Prompt Assembly Example
`PromptService` supports referencing various built-in and custom neurons:

```python
template = """
# Task and Environment
Goal: {{task_input}}
Active Working Directory: {{working_dir}}

# Cognitive Neurons
Key Facts: {{facts}}
User Preference: {{user_preference}}  # Reference custom neuron

# History Backtrace
Root Task Goal: {{root.task_input}}
"""

# Execute formatting; the system will automatically call the format method of each neuron
prompt = await context.prompt_service.async_format(template)
```

### 4.3 Knowledge Usage
Knowledge refers to unstructured or semi-structured data (Artifacts) stored in the Workspace, which can be indexed and referenced through context.

#### Adding Knowledge
```python
from aworld.output import Artifact

# 1. Create artifact (e.g., a research report)
artifact = Artifact(
    id="report_001",
    content="AWorld uses distributed context management...",
    metadata={"type": "text", "source": "research"}
)

# 2. Add knowledge to context (Automatic indexing)
await context.add_knowledge(artifact)
```

#### Referencing Knowledge in Prompt
You can inject knowledge content directly into the Prompt using specific path syntax:

```python
# Reference full content
prompt_with_content = await context.prompt_service.async_format(
    "Refer to report content: {{knowledge/report_001/content}}"
)

# Reference auto-generated summary (Requires corresponding configuration)
prompt_with_summary = await context.prompt_service.async_format(
    "Report summary: {{knowledge/report_001/summary}}"
)
```

---

## 5. Advanced Usage
Advanced usage is suitable for multi-agent collaboration, ultra-long tasks, and automated cognitive processing.

### 5.1 Context Offloading
For massive tool outputs (e.g., vast web content), the system will automatically or manually offload them from the context to the Workspace.

```python
# 1. Auto-offload configuration (Enabled by default at COPILOT/NAVIGATOR levels)
config = AmniConfigFactory.create(
    level=AmniConfigLevel.COPILOT,
    tool_result_offload=True,
    tool_result_length_threshold=30000  # Auto-offload if exceeds 30k characters
)

# 2. Manual offload execution
await context.offload_by_workspace(artifacts)
```

### 5.2 Hierarchical Tasks & Planning
A core advantage of AmniContext is its native support for **Planning mode**. It decomposes complex macro goals into executable micro atomic tasks through a Task Tree and automatically manages the flow of context between them.

#### Core Capabilities: Multi-task Decomposition and Parallelism
+ **Recursive Decomposition**: Agents can dynamically generate N sub-tasks based on the main task goal, each with its own `SubContext`.
+ **Inheritance**: Sub-contexts automatically inherit the parent's `kv_store` and environment configurations, ensuring sub-tasks have global Contextual Awareness during execution.
+ **Consolidation**: When a sub-task is completed, results, facts, and token consumption are asynchronously merged back into the main context via `merge_sub_context`.

#### Example: Task Lifecycle in Planning Mode
```python
# 1. Build sub-context (Triggered by Planner Agent)
# The system automatically establishes parent-child references and clones the necessary runtime environment
sub_context = await context.build_sub_context(
    sub_task_content="Investigate technical details of long-context models",
    sub_task_id="sub_task_001",
    task_type="normal" # Supports normal or background (asynchronous)
)

# 2. Precise context isolation and backtrace
# Sub-tasks only focus on their own input but can access parent background via {{parent.xxx}}
parent_goal = await sub_context.prompt_service.async_format("{{parent.task_input}}")

# 3. Task execution and state merging
# After task completion, facts and KV variables generated by the sub-task are automatically updated to the parent task
context.merge_sub_context(sub_context)
```

#### Parallel Execution Support
In distributed scenarios, AmniContext supports multiple sub-tasks **running in parallel** across different execution engines. Each node maintains context consistency through `snapshot` and `restore`, ultimately achieving global state consolidation at the root node.

### 5.3 Agent Isolation
Establishes an independent private state space for each Agent to prevent variable pollution.

```python
# 1. Initialize Agent private state
await context.build_agents_state([web_agent, analyst_agent])

# 2. Access in specific namespace
context.put("search_history", ["link1", "link2"], namespace=web_agent.id())
```

### 5.4 Environment Integration & Freedom Space
`FreedomSpaceService` provides an isolated "Freedom Space" (working directory) for the Agent. Its core capability is achieving **file system sharing between persistent storage and execution environments (Sandbox/Docker)**.

#### Core Features
+ **File System Mapping**: Files operated by the Agent in Freedom Space are automatically mapped to `env_mount_path` in the execution environment.
+ **Transparent Access**: Code written or artifacts generated by the Agent are transparently synchronized between the physical storage layer (local/OSS) and the logical execution layer (Docker).
+ **Persistence Guarantee**: Even if the execution environment is destroyed, data in the Freedom Space remains in the Workspace, supporting cross-task recovery.

#### Example: Configuration and Usage
```python
# 1. Configure environment sharing parameters
config = AmniConfigFactory.create(level=AmniConfigLevel.COPILOT)
config.env_config.env_type = "remote" # Enable remote/sandbox mode
config.env_config.env_mount_path = "/workspace" # Mount point inside the execution environment

# 2. Add file to Freedom Space and get environment path
success, env_abs_path, content = await context.freedom_space_service.add_file(
    filename="data_process.py",
    content="import os\nprint(os.getcwd())",
    mime_type="text/x-python"
)
# env_abs_path will return "/workspace/data_process.py"

# 3. Use in code executor
# The Agent can directly execute files under this path in the sandbox
```

#### Path Variable Reference
In Prompt templates, the following variables can be used for precise environment referencing:

+ `{{working_dir_env_mounted_path}}`: Gets the absolute mount path of Freedom Space in the execution environment.
+ `{{working_dir}}`: Returns a formatted list of all mounted files in Freedom Space (XML format, including filenames and absolute paths).
+ `{{current_working_directory}}`: Gets the operating system running directory of the current process.

### 5.5 Autonomous Cognition
At the `NAVIGATOR` level, the system fully enables an "autopilot" mode based on a "cognitive closed loop," achieving **System Prompt Augment** and **Dynamic Tool Injection**:

+ **System Prompt Augment**: The system automatically concatenates "neurons" such as current task status, working directory file lists, and basic environment information into the Agent's System Prompt based on configured `neuron_names` (e.g., `task`, `working_dir`, `skills`, `basic`).
+ **Dynamic Tool Injection**: Via the `skills` neuron, the system automatically injects a list of available tools (Skills) and usage guides for the Agent.
    - **Progressive Disclosure**: Initially only tool summaries are injected. When the Agent decides it needs a specific skill, it dynamically loads the full tool Schema and instructions via `active_skill`.
    - **Automatic Guidance**: Automatically concatenates `<skills_guide>`, informing the Agent how to manage its own skill stack (Activate/Offload).
+ **Automated Reasoning Orchestrator**: Enables autonomous reasoning orchestration. The system automatically drives the reasoning engine for strategy formulation and task logic flow decomposition based on goals. Enabling this automatically injects the `context_planning` tool.
+ **Automated Cognitive Ingestion**: Enables automatic cognitive ingestion. Enabling this automatically injects the `context_knowledge` tool and simultaneously loads `todo` (task list) and `action_info` (execution history) neurons.
+ **Automated Memory Recursive**: Enables recursive experience loops.
+ **Automated Memory Recall**: Enables automatic memory recall.

---

## 6. Scenario-based Strategies
Strategies should be chosen flexibly based on task complexity:

### 6.1 Scenario 1: Simple Chat
+ **Features**: Clear goals, no reliance on complex files or long history.
+ **Recommended Strategy**:
    - **Config Level**: `AmniConfigLevel.PILOT`
    - **Core Neurons**: `basic` + `task`
    - **Details**: Maintain low `history_rounds` (e.g., 5-10); no need for auto-summary or offloading.

### 6.2 Scenario 2: Complex Research (RAG)
+ **Features**: Needs to read many documents/webpages; retrieval recall is key.
+ **Recommended Strategy**:
    - **Config Level**: `AmniConfigLevel.COPILOT`
    - **Core Neurons**: `knowledge` + `summaries`
    - **Practice**: Force **Contextual Retrieval** and utilize **Prompt Caching** for large reference materials.

### 6.3 Scenario 3: Planning Intensive Tasks
+ **Features**: Long task chains, multiple sub-tasks, frequent tool calls, needs Task Tree awareness.
+ **Recommended Strategy**:
    - **Config Level**: `AmniConfigLevel.NAVIGATOR`
    - **Core Neurons**: `task` + `todo` + `action_info`
    - **Capabilities**: Enable `automated_reasoning_orchestrator` for autonomous task management.

### 6.4 Scenario 4: Code Sandbox Collaboration
+ **Features**: Relies on file systems, frequent reads/writes of artifacts.
+ **Recommended Strategy**:
    - **Config Level**: `AmniConfigLevel.COPILOT` / `NAVIGATOR`
    - **Core Neurons**: `working_dir` + `skills`
    - **Integration**: Configure `env_mount_path` for file sharing.

---

## 7. Best Practices Guide
1. **Attention Budget Management**: Always follow the "Minimum Necessity Principle."
2. **Namespace Isolation**: In multi-agent collaboration, always use the `namespace` parameter.
3. **Structured Processing Flow**: Prioritize storing raw data as Artifacts and use `Context Op` for recursive summarization.

