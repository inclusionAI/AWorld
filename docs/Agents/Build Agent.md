# Building and Running Agents
Using the most common llm_agent as an example, this tutorial provides detailed guidance on:

1. How to quickly build an Agent
2. How to customize an Agent  
This document is divided into two parts to explain AWorld's design philosophy.

## Quick Agent Setup
### Declaring an Agent
```python
from aworld.agents.llm_agent import Agent

# Assign a name to your agent
agent = Agent(name="my_agent")
```

### Configuring LLM
#### Method 1: Using Environment Variables
```python
import os

## Set up LLM service using environment variables
os.environ["LLM_PROVIDER"] = "openai"  # Choose from: openai, anthropic, azure_openai
os.environ["LLM_MODEL_NAME"] = "gpt-4"
os.environ["LLM_API_KEY"] = "your-api-key"
os.environ["LLM_BASE_URL"] = "https://api.openai.com/v1"  # Optional for OpenAI
```

#### Method 2: Using AgentConfig
```python
import os
from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig

agent_config = AgentConfig(
    llm_provider=os.getenv("LLM_PROVIDER", "openai"),
    llm_model_name=os.getenv("LLM_MODEL_NAME"),
    llm_base_url=os.getenv("LLM_BASE_URL"),
    llm_api_key=os.getenv("LLM_API_KEY"),
)

agent = Agent(name="my_agent", conf=agent_config)
```

#### Method 3: Using Shared ModelConfig
When multiple agents use the same LLM service, you can specify a shared ModelConfig:

```python
import os
from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig, ModelConfig

# Create a shared model configuration
model_config = ModelConfig(
    llm_provider=os.getenv("LLM_PROVIDER", "openai"),
    llm_model_name=os.getenv("LLM_MODEL_NAME"),
    llm_base_url=os.getenv("LLM_BASE_URL"),
    llm_api_key=os.getenv("LLM_API_KEY"),
)

# Use the shared model config in agent configuration
agent_config = AgentConfig(
    llm_config=model_config,
)

agent = Agent(name="my_agent", conf=agent_config)
```

### Configuring Prompts
```python
from aworld.agents.llm_agent import Agent
import os
from aworld.config.conf import AgentConfig, ModelConfig

model_config = ModelConfig(
    llm_provider=os.getenv("LLM_PROVIDER", "openai"),
    llm_model_name=os.getenv("LLM_MODEL_NAME"),
    llm_base_url=os.getenv("LLM_BASE_URL"),
    llm_api_key=os.getenv("LLM_API_KEY"),
)

agent_config = AgentConfig(
    llm_config=model_config,
)

# Define your system prompt
system_prompt = """You are a helpful AI assistant that can assist users with various tasks.
You should be polite, accurate, and provide clear explanations."""

agent = Agent(
    name="my_agent",
    conf=agent_config,
    system_prompt=system_prompt
)
```

### Configuring Tools
#### Local Tools
```python
from aworld.agents.llm_agent import Agent
import os
from aworld.config.conf import AgentConfig, ModelConfig
from aworld.core.tool.func_to_tool import be_tool

model_config = ModelConfig(
    llm_provider=os.getenv("LLM_PROVIDER", "openai"),
    llm_model_name=os.getenv("LLM_MODEL_NAME"),
    llm_base_url=os.getenv("LLM_BASE_URL"),
    llm_api_key=os.getenv("LLM_API_KEY"),
)

agent_config = AgentConfig(
    llm_config=model_config,
)

system_prompt = """You are a helpful agent with access to various tools."""


# Define a local tool using the @be_tool decorator

@be_tool(tool_name='greeting_tool', tool_desc="A simple greeting tool that returns a hello message")
def greeting_tool() -> str:
    return "Hello, world!"


agent = Agent(
    name="my_agent",
    conf=agent_config,
    system_prompt=system_prompt,
    tool_names=['greeting_tool']
)
```

#### MCP (Model Context Protocol) Tools
```python
from aworld.agents.llm_agent import Agent
import os
from aworld.config.conf import AgentConfig, ModelConfig

model_config = ModelConfig(
    llm_provider=os.getenv("LLM_PROVIDER", "openai"),
    llm_model_name=os.getenv("LLM_MODEL_NAME"),
    llm_base_url=os.getenv("LLM_BASE_URL"),
    llm_api_key=os.getenv("LLM_API_KEY"),
)

agent_config = AgentConfig(
    llm_config=model_config,
)

system_prompt = """You are a helpful agent with access to file system operations."""

# Configure MCP servers

mcp_config = {
    "mcpServers": {
        "GorillaFileSystem": {
            "type": "stdio",
            "command": "python",
            "args": ["examples/BFCL/mcp_tools/gorilla_file_system.py"],
        },
    }
}

agent = Agent(
    name="my_agent",
    conf=agent_config,
    system_prompt=system_prompt,
    mcp_servers=list(mcp_config.get("mcpServers", {}).keys()),
    mcp_config=mcp_config
)
```

#### Agent as Tool
```python
from aworld.agents.llm_agent import Agent
import os
from aworld.config.conf import AgentConfig, ModelConfig

model_config = ModelConfig(
    llm_provider=os.getenv("LLM_PROVIDER", "openai"),
    llm_model_name=os.getenv("LLM_MODEL_NAME"),
    llm_base_url=os.getenv("LLM_BASE_URL"),
    llm_api_key=os.getenv("LLM_API_KEY"),
)

agent_config = AgentConfig(
    llm_config=model_config,
)

system_prompt = """You are a helpful agent that can delegate tasks to other specialized agents."""

# Create a specialized tool agent
tool_agent = Agent(name="tool_agent", conf=agent_config)

# Create the main agent that can use the tool agent
agent = Agent(
    name="my_agent",
    conf=agent_config,
    system_prompt=system_prompt,
    agent_names=['tool_agent']
)
```