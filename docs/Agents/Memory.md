AWorld Memory is a unified memory management framework designed for multi-agent systems. It aims to help agents store, retrieve, and process information, enabling continuous learning, personalized interaction, and experiential evolution across sessions.

## 1. Core Concepts
AWorld divides memory into two primary dimensions: **Short-Term Memory (STM)** and **Long-Term Memory (LTM)**.

### 1.1 Short-Term Memory (STM)
Used to store immediate interaction records of the agent in the current task or session.

+ **Storage Objects**: `MemoryHumanMessage` (User input), `MemoryAIMessage` (AI response), `MemoryToolMessage` (Tool execution results), `MemorySystemMessage` (System instructions).
+ **Storage Engines**: Supports multiple backends, defaulting to `InMemoryMemoryStore`. `PostgresMemoryStore` is recommended for production environments.
+ **Context Management**: To prevent context overflow, several automatic compression strategies are provided:
    - **Trimming**: Keeps only the last N rounds of conversation.
    - **Fixed Step Summary**: Generates a summary every N messages and removes the summarized messages.
    - **Length-based Summary**: Triggers summarization when the total message length exceeds a threshold.
    - **Single Round Summary**: Compresses individual messages that are excessively long.

### 1.2 Long-Term Memory (LTM)
Used to store information with persistent value, supporting cross-session retrieval.

+ **UserProfile**:
    - Structured storage of user preferences, profession, tech stack, communication style, etc.
    - **Example**: `key="preferences.technical", value={"preferred_languages": ["Python"]}`.
+ **AgentExperience**:
    - Records the successful paths and skills of the agent in solving complex problems.
    - **Structure**: `skill` (Skill name), `actions` (Action sequence), `outcome` (Execution result).
+ **Fact**:
    - Concrete factual information extracted from conversations (e.g., "User lives in Shanghai").
+ **ConversationSummary**:
    - High-level summaries of completed sessions, serving as background reference for future interactions.

---

## 2. Quick Start
### 2.1 Initializing the Memory Environment
It is recommended to use `MemoryFactory` for global initialization.

```python
from aworld.memory.main import MemoryFactory
from aworld.core.memory import MemoryConfig, MemoryLLMConfig

# Initialize Memory Factory
MemoryFactory.init(
    config=MemoryConfig(
        provider="aworld", # Core engine
        llm_config=MemoryLLMConfig(
            provider="openai",
            model_name="gpt-4o",
            api_key="your_api_key"
        )
    )
)

# Get Instance
memory = MemoryFactory.instance()
```

### 2.2 Using Memory in Agents
Deeply customize the memory behavior of each agent through `AgentMemoryConfig`.

```python
from aworld.agents.llm_agent import Agent
from aworld.core.memory import AgentMemoryConfig, LongTermConfig

# 1. Configure Memory Behavior
memory_config = AgentMemoryConfig(
    enable_summary=True,        # Enable automatic summarization
    trim_rounds=20,             # Keep the last 20 rounds
    enable_long_term=True,      # Enable long-term memory
    long_term_config=LongTermConfig.create_simple_config(
        enable_user_profiles=True,
        enable_agent_experiences=True
    )
)

# 2. Create Agent
agent = Agent(
    name="MemoryAgent",
    agent_memory_config=memory_config,
    # ... other configurations
)
```

---

## 3. Basic Operations
### 3.1 Core Operation Interface (CRUD)
After obtaining an instance via `MemoryFactory.instance()`, you can perform the following core operations:

#### 1. Add Record (`add`)
Used to store new messages or memory items into the system. In AWorld, adding a record usually triggers subsequent automatic summarization or long-term memory extraction logic.

```python
from aworld.memory.models import MemoryHumanMessage, MessageMetadata

# Prepare metadata
metadata = MessageMetadata(
    user_id="u_001",
    session_id="s_123",
    agent_id="a_999",
    task_id="t_456"
)

# Create message object
message = MemoryHumanMessage(content="Hello", metadata=metadata)

# Add memory
# agent_memory_config is optional, used to trigger specific summarization strategies for that agent
await memory.add(
    message, 
    agent_memory_config=context.get_config().get_agent_memory_config(namespace="a_999")
)
```

#### 2. Delete Record (`delete`)
Deletes a specified memory item based on its ID (usually a logical deletion).

```python
# Delete by memory_id
await memory.delete("mem_uuid_12345")
```

#### 3. Get Recent History (`get_last_n`)
This is the most commonly used interface, used to retrieve the recent conversation context when building LLM Prompts. It automatically handles the merging of `init` (system/initial info), `message` (dialogue), and `summary`.

```python
# Get the last 10 rounds of history
# filters must include agent_id, session_id, task_id to ensure isolation
filters = {
    "agent_id": "a_999",
    "session_id": "s_123",
    "task_id": "t_456"
}

histories = memory.get_last_n(
    last_rounds=10, 
    filters=filters,
    agent_memory_config=agent_memory_config # Pass config to support complex merging logic
)
```

#### 4. Get All Records (`get_all`)
Retrieves all matching memory items based on a filter.

```python
# Get all message records under the current task
all_messages = memory.get_all(filters={
    "task_id": "t_456",
    "memory_type": "message"
})
```

### 3.2 Memory Isolation Levels and Retrieval Filters
After obtaining the instance, the most important step is to determine the **History Scope**. This determines the range of historical information the agent can "recall."

#### 1. Isolation Level (History Scope)
Configured via the `history_scope` parameter in `AgentMemoryConfig`. It supports three levels:

| Level | Parameter Value | Description | Use Case |
| --- | --- | --- | --- |
| **Task Level (Default)** | `task` | Retrieves only messages under the current `task_id`. | Independent task execution without historical context. |
| **Session Level** | `session` | Retrieves all messages under the same `session_id` (may span multiple tasks). | Continuous multi-round dialogue composed of sub-tasks. |
| **User Level** | `user` | Retrieves all history of that `user_id` on that specific Agent. | Long-term personal assistant needing to remember user habits. |


#### 2. Building Retrieval Filters
When calling `get_last_n` or `search`, the framework automatically builds filters based on the configured Scope. Manual operation example:

```python
# Logic reference: aworld/agents/llm_agent.py
def build_filters(self, context: Context):
    filters = {"agent_id": self.id()}
    config = context.get_agent_memory_config(self.id())
    
    # Core logic: Determine filter dimensions based on history_scope
    scope = config.history_scope or "task"
    if scope == "user":
        filters["user_id"] = context.user_id # or from task
    elif scope == "session":
        filters["session_id"] = context.get_task().session_id
    else:
        filters["task_id"] = context.get_task().id
    return filters

# Use filter to get recent history
histories = memory.get_last_n(
    last_rounds=config.history_rounds, 
    filters=build_filters(context)
)
```

### 3.3 Retrieving Long-Term Memory
```python
# Retrieve UserProfile
profiles = await memory.retrival_user_profile(
    user_id="u_001", 
    user_input="I want to write some Python code"
)

# Retrieve Related Experiences
experiences = await memory.retrival_agent_experience(
    agent_id="a_999",
    user_input="Handling API rate limit errors"
)
```

---

## 4. Long-term Extraction
Long-term extraction is the core mechanism for "Agent Evolution" in AWorld. It automatically distills knowledge with persistent value by analyzing short-term dialogue history.

### 4.1 Triggering Mechanism
Long-term memory extraction does not happen in every round; it is triggered by the `DefaultMemoryOrchestrator` based on configuration:

+ **Message Count Trigger**: Triggered when the number of messages in STM reaches the `message_count_threshold` (default 10).
+ **Importance Trigger**: Monitors keywords in messages (e.g., "error", "success", "completed") and triggers upon a match.
+ **Forced Trigger**: Triggered by setting `force=True` in `trigger_short_term_memory_to_long_term`.

### 4.2 Extraction Flow
AWorld's long-term memory extraction is a **fully automated** process executed silently in the background.

1. **Task Creation**: The Orchestrator automatically collects relevant message streams based on the `history_scope`.
2. **Asynchronous Processing**: If `enable_background_processing` is enabled, the extraction task runs in the background, not affecting the response speed of the main dialogue.
3. **LLM Extraction (Gungnir)**: 
    - **Automatic Identification**: The system automatically identifies key information in the dialogue and structures it.
    - **Support for Prompt Customization**: Although extraction is auto-triggered, developers can customize extraction Prompt templates. By modifying `user_profile_extraction_prompt` or `agent_experience_extraction_prompt` in `ExtractionConfig`, you can precisely control what information the LLM focuses on and the output format.
4. **Structured Storage**: Extraction results are converted into `UserProfile` or `AgentExperience` objects and persisted in the `MemoryStore` and vector database.

### 4.3 Fact Extraction
Besides profiles and experiences, AWorld supports extracting atomized **Facts** from tool execution results (e.g., search results).

+ **Scenario**: Usually triggered by `ExtractToolFactOp` when a tool returns a large volume of information.
+ **Characteristics**: Deconstructs complex information streams into independent, minimum units of knowledge (e.g., a company's financial data, a software's release date).
+ **Function**: Prevents the agent from losing details when processing long documents or vast search results, while enabling precise "Fact Recall" through vector retrieval.

### 4.4 Related Configuration
```python
from aworld.core.memory import LongTermConfig

# Deeply customize extraction behavior
lt_config = LongTermConfig()
lt_config.trigger.message_count_threshold = 15      # Increase trigger threshold
lt_config.trigger.enable_importance_trigger = True  # Enable importance monitoring

# Customize Extraction Prompt (Optional)
lt_config.extraction.user_profile_extraction_prompt = "You are a professional personality analyst, please extract from the conversation..."
lt_config.extraction.agent_experience_extraction_prompt = "Please summarize the core technical path of the agent in this task..."

lt_config.extraction.enable_user_profile_extraction = True
lt_config.processing.enable_background_processing = True
```

---

## 5. Advanced Features
### 5.1 Vector Storage and Semantic Search
AWorld Memory has built-in vectorization support, enabling retrieval of the most relevant historical information based on semantics.

```python
from aworld.core.memory import EmbeddingsConfig, VectorDBConfig

# Configure Vector Database (e.g., ChromaDB)
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

### 5.2 Semantic Retrieval Interface
The `search` method supports hybrid retrieval:

```python
results = memory.search(
    query="User preferences for programming languages",
    memory_type="user_profile",
    filters={"user_id": "u_001"},
    limit=5
)
```

### 5.3 Internal Implementation and Optimization Reference
#### Message Format Conversion (LLM Adaptation)
AWorld internally uses `to_openai_message()` to uniformly convert memory items into formats acceptable by models:

```python
# Logic reference: aworld/agents/llm_agent.py
messages = []
for history in histories:
    if isinstance(history, MemoryMessage):
        # Automatically handles role, content, and complex structures like tool_calls
        messages.append(history.to_openai_message())
```

#### Redundant Tool Call Cleaning
To keep the context clean and save tokens, the framework automatically cleans up redundant tool call messages at specific moments (e.g., loop termination or reset):

```python
# See _clean_redundant_tool_call_messages implementation
# Logic: Traverse backwards, deleting tool_call records that produced no results or are invalid
memory.delete(history_item_id)
```

---

## 5. Best Practices
1. **Metadata Consistency**: Always provide complete `MessageMetadata`. Since AWorld defaults to using `task_id` for isolation, missing metadata will lead to "amnesia" or "memory interference" for the agent.
2. **Choosing Isolation Level (History Scope)**:
    - **Use **`task`** (Default)**: 
        * When tasks are atomized (e.g., translating a paragraph, executing a SQL).
        * To save tokens and prevent history from interfering with current decisions.
        * Executing multiple unrelated sub-tasks in parallel.
    - **Use **`session`:
        * Interactive dialogues (Chat): Users will refer to history with "what I just said."
        * Complex pipelines: The output of a previous Task is the background for the next (e.g., Search -> Organize -> Write Report).
        * Context maintenance during a single login period.
    - **Use **`user`:
        * Personalized butler: Needs to remember the user's name, preferences, profession, etc.
        * Long-term project tracking: Continuous support spanning multiple days and logins.
        * Note: The higher the level, the greater the token consumption and retrieval pressure. It is recommended to use this with `enable_summary`.
3. **Context Compression and History Transfer Strategy**:
    - **Short Context Scenarios**: Recommended to share history messages via `session` level. When session length is controllable, passing original messages maintains the highest interaction fidelity.
    - **Long Context Scenarios**: 
        * **Summary Strategy**: Recommended to enable `enable_summary` to replace old messages with periodic summaries, effectively reducing token consumption while retaining key background.
        * **Combining with **`add_knowledge`: For ultra-long information or knowledge that needs persistence, it's recommended to use `add_knowledge` to distill it into knowledge snippets. Use the pattern of "Summary for streaming background" + "`add_knowledge` for key details" to achieve efficient context management.

---

_For more technical details, please refer to the source module: _`aworld.memory`

