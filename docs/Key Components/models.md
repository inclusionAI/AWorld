A unified interface for interacting with various LLM providers through a consistent API.

### Overview
The **LLM Interface** in **AWorld** provides a standardized way to communicate with different Large Language Models. Instead of handling provider-specific SDKs and formats, you can use a single API to manage chat completions, streaming, and tool calls across multiple backends.

Key features include:
- **Unified API**: One interface for OpenAI, Anthropic, Azure, and more.
- **Sync & Async Support**: Full support for both synchronous and asynchronous operations.
- **Streaming**: Real-time token generation for responsive applications.
- **Standardized Response**: Every call returns a consistent `ModelResponse` object.
- **Tool Calls**: Built-in support for function calling and tool execution.

### Supported Providers
AWorld supports several major providers and is easily extensible for others.

| Provider | Description |
| :--- | :--- |
| `openai` | Models following the OpenAI API protocol (GPT-4o, GPT-3.5, etc.) |
| `anthropic` | Anthropic's Claude series models (Claude 3.5 Sonnet, Opus, etc.) |
| `azure_openai` | Microsoft Azure OpenAI Service integration |

### Quick Start

#### Using Low-level API
To get started, initialize a model using `get_llm_model` and use `call_llm_model` for simple completions.

```python
from aworld.models.llm import get_llm_model, call_llm_model

# 1. Initialize the model
model = get_llm_model(
    llm_provider="openai",
    model_name="gpt-4o",
    api_key="your_api_key"
)

# 2. Prepare messages
messages = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Explain quantum computing in one sentence."}
]

# 3. Get response
response = call_llm_model(model, messages)
print(response.content)
```

#### Integration with LLMAgent
If you are building an agent using `LLMAgent` (from `aworld.agents.llm_agent`), the model invocation is handled automatically. Once you configure the `AgentConfig`, the agent will manage the model lifecycle and execution steps internally.

```python
from aworld.agents.llm_agent import LLMAgent
from aworld.config.conf import AgentConfig

# Configure the agent with LLM settings
config = AgentConfig(
    llm_provider="openai",
    llm_model_name="gpt-4o",
    llm_api_key="your_api_key"
)

# Initialize the agent
agent = LLMAgent(name="Assistant", conf=config)

# The agent automatically calls the model during its execution cycle
# response = await agent.async_policy(observation) 
```

### Advanced Usage

#### Streaming Responses
For real-time feedback, you can enable streaming by setting `stream=True` in the call. 

Alternatively, when using `LLMAgent`, you can enable streaming globally by setting `llm_stream_call=True` in the agent's configuration.

```python
from aworld.models.llm import call_llm_model, acall_llm_model
from aworld.config.conf import AgentConfig

# Option 1: Enable in the call
for chunk in call_llm_model(model, messages, stream=True):
    if chunk.content:
        print(chunk.content, end="", flush=True)

# Option 2: Enable globally in AgentConfig
config = AgentConfig(
    llm_model_name="gpt-4o",
    llm_stream_call=True  # All model calls by the agent will use streaming
)
```

# Asynchronous streaming
async for chunk in await acall_llm_model(model, messages, stream=True):
    if chunk.content:
        print(chunk.content, end="", flush=True)
```

#### Tool Calls (Function Calling)
AWorld standardizes tool definitions and responses across providers.

```python
tools = [{
    "type": "function",
    "function": {
        "name": "get_weather",
        "parameters": {
            "type": "object",
            "properties": {"location": {"type": "string"}},
            "required": ["location"]
        }
    }
}]

response = call_llm_model(model, messages, tools=tools)

if response.tool_calls:
    for tool_call in response.tool_calls:
        print(f"Calling tool: {tool_call.name} with {tool_call.arguments}")
```

### ModelResponse Object
All model interactions return a `ModelResponse` object, which encapsulates all necessary data in a provider-agnostic format.

| Attribute | Description |
| :--- | :--- |
| `content` | The generated text content from the LLM. |
| `tool_calls` | A list of `ToolCall` objects if the model requested tool execution. |
| `usage` | Token usage statistics (prompt, completion, total). |
| `message` | The complete message object, ready to be appended to history. |
| `model` | The name of the model that generated the response. |

### Configuration & Initialization

#### Automatic Provider Detection
AWorld can often infer the provider from the model name:
```python
# Automatically detects 'anthropic' provider
model = get_llm_model(model_name="claude-3-5-sonnet-20241022")
```

#### Selective Initialization
For performance optimization, you can choose to initialize only sync or async clients:
```python
model = get_llm_model(
    model_name="gpt-4o",
    sync_enabled=True,
    async_enabled=False  # Skips async client initialization
)
```

#### API Key Management
Keys are resolved in the following priority:
1. Direct `api_key` parameter in code.
2. Environment variables in your `.env` file (e.g., `OPENAI_API_KEY`).
3. System environment variables.

