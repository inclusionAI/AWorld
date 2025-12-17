# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Basic Usage

### Quick Start

```python
from aworld.config.conf import AgentConfig
from aworld.models.llm import get_llm_model, call_llm_model, acall_llm_model

# Create configuration
config = AgentConfig(
    llm_provider="openai",  # Options: "openai", "anthropic", "azure_openai"
    llm_model_name="gpt-4o",
    llm_temperature=0.0,
    llm_api_key="your_api_key",
    llm_base_url="your_llm_server_address"
)

# Initialize the model
model = get_llm_model(config)

# Prepare messages
messages = [
    {"role": "system", "content": "You are a helpful AI assistant."},
    {"role": "user", "content": "Explain Python in three sentences."}
]

# Get response
response = model.completion(messages)
print(response.content)  # Access content directly from ModelResponse
```

### Using call_llm_model (Recommended)

```python
from aworld.models.llm import get_llm_model, call_llm_model

# Initialize model
model = get_llm_model(
    llm_provider="openai",
    model_name="gpt-4o",
    api_key="your_api_key",
    base_url="https://api.openai.com/v1"
)

# Prepare messages
messages = [
    {"role": "system", "content": "You are a helpful AI assistant."},
    {"role": "user", "content": "Write a short poem about programming."}
]

# Using call_llm_model - returns ModelResponse object
response = call_llm_model(model, messages)
print(response.content)  # Access content directly from ModelResponse

A unified interface for interacting with various LLM providers through a consistent API.

### Selective Sync/Async Initialization

For performance optimization, you can control whether to initialize synchronous or asynchronous providers:
By default, both `sync_enabled` and `async_enabled` are set to `True`, which means both synchronous and asynchronous providers will be initialized.

```python
# Initialize only synchronous provider
model = get_llm_model(
    llm_provider="openai",
    model_name="gpt-4o",
    sync_enabled=True,    # Initialize sync provider
    async_enabled=False,  # Don't initialize async provider
    api_key="your_api_key"
)

# Initialize only asynchronous provider
model = get_llm_model(
    llm_provider="anthropic",
    model_name="claude-3-5-sonnet-20241022",
    sync_enabled=False,   # Don't initialize sync provider
    async_enabled=True,   # Initialize async provider
    api_key="your_api_key"
)

# Initialize both (default behavior)
model = get_llm_model(
    llm_provider="openai",
    model_name="gpt-4o",
    sync_enabled=True,
    async_enabled=True
)
```

### HTTP Client Mode

You can use direct HTTP requests instead of the SDK by specifying `client_type=ClientType.HTTP` parameter:

```python
from aworld.config.conf import AgentConfig, ClientType
from aworld.models.llm import get_llm_model, call_llm_model

# Initialize model with HTTP client mode
model = get_llm_model(
    llm_provider="openai",
    model_name="gpt-4o",
    api_key="your_api_key",
    base_url="https://api.openai.com/v1",
    client_type=ClientType.HTTP  # Use HTTP client instead of SDK
)

# Use it exactly the same way as SDK mode
messages = [
    {"role": "system", "content": "You are a helpful AI assistant."},
    {"role": "user", "content": "Tell me a short joke."}
]

# The model uses HTTP requests under the hood
response = call_llm_model(model, messages)
print(response.content)

# Streaming also works with HTTP client
for chunk in call_llm_model(model, messages, stream=True):
    if chunk.content:
        print(chunk.content, end="", flush=True)
```

This approach can be useful when:
- You need more control over the HTTP requests
- You have compatibility issues with the official SDK
- You're using a model that follows OpenAI API protocol but isn't fully compatible with the SDK

### Tool Calls Support

```python
from aworld.models.llm import get_llm_model, call_llm_model
import json

# Initialize model
model = get_llm_model(
    llm_provider="openai",
    model_name="gpt-4o",
    api_key="your_api_key"
)

# Define tools
tools = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather in a given location",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "The city and state, e.g. San Francisco, CA"
                    }
                },
                "required": ["location"]
            }
        }
    }
]

# Prepare messages
messages = [
    {"role": "user", "content": "What's the weather like in San Francisco?"}
]

# Call model with tools
response = call_llm_model(model, messages, tools=tools, tool_choice="auto")

# Check for tool calls
if response.tool_calls:
    for tool_call in response.tool_calls:
        print(f"Tool name: {tool_call.name}")
        print(f"Arguments: {tool_call.arguments}")
        
        # Handle tool call
        if tool_call.name == "get_weather":
            # Parse arguments
            args = json.loads(tool_call.arguments)
            location = args.get("location")
            
            # Mock getting weather data
            weather = "Sunny, 25°C"
            
            # Add tool response to messages
            messages.append(response.message)  # Add assistant message
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "name": tool_call.name,
                "content": f"{{\"weather\": \"{weather}\"}}"
            })
            
            # Call model again
            final_response = call_llm_model(model, messages)
            print("\nFinal response:", final_response.content)
else:
    print("\nResponse content:", response.content)
```

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file/```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L485): Asynchronously call a model
- [apply_chat_template()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L488-L521): Apply chat templates to messages

## Basic Usage

### Quick Start

```
# AWorld LLM Interface

A unified interface for interacting with various LLM providers through a consistent API.

## Features

- Unified API for multiple LLM providers. Currently, only OpenAI and Anthropic are supported.
- Synchronous and asynchronous calls with optional initialization control
- Streaming responses support
- Tool calls support
- Unified ModelResponse object for all provider responses
- Easy extension with custom providers

## Supported Providers

- `openai`: Models supporting OpenAI API protocol (OpenAI, compatible models)
- `anthropic`: Models supporting Anthropic API protocol (Claude models)
- `azure_openai`: Azure OpenAI service

## Core Components

### LLMModel Class

The [LLMModel](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L27-L346) class is the main interface for interacting with language models. It provides a unified wrapper around different LLM providers and handles provider identification, initialization, and method delegation.

Key features:
- Automatic provider detection based on model name or endpoint
- Support for both synchronous and asynchronous operations
- Consistent API across different providers
- Configuration management through [AgentConfig](file:///Users/gain/PycharmProjects/AWorld/aworld/config/conf.py#L617-L874) or direct parameters

### Provider Classes

Provider classes ([OpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L12-L245), [AnthropicProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/anthropic_provider.py#L9-L138), [AzureOpenAIProvider](file:///Users/gain/PycharmProjects/AWorld/aworld/models/openai_provider.py#L248-L331), etc.) implement provider-specific logic:

- Message preprocessing to convert between formats
- Response postprocessing to unify outputs
- Streaming response handling
- Error handling and conversion

### ModelResponse Class

The [ModelResponse](file:///Users/gain/PycharmProjects/AWorld/aworld/models/model_response.py#L66-L317) class provides a standardized format for all LLM responses:

- `content`: Generated text content
- `tool_calls`: Tool calls made by the model
- `usage`: Token usage statistics
- `error`: Error information if any
- `raw_response`: Original provider response

### Convenience Functions

Helper functions for easier model interaction:
- [get_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L375-L406): Create an LLMModel instance
- [call_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L409-L446): Synchronously call a model
- [acall_llm_model()](file:///Users/gain/PycharmProjects/AWorld/aworld/models/llm.py#L449-L4