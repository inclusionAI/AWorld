AWorld中可以通过定义方便的构建Agent，使用最常见的llm_agent作为示例，提供Agent的一些能力细节：

1. 如何快速构建一个Agent
2. 如何定制一个Agent

## Agent构建
### 声明一个Agent
```python
from aworld.agents.llm_agent import Agent

# 设置Agent名
agent = Agent(name="my_agent")
```

### LLM配置
#### 方法 1: 使用环境变量
```python
import os

## 用于LLM的环境变量
os.environ["LLM_PROVIDER"] = "openai"  # Choose from: openai, anthropic, azure_openai
os.environ["LLM_MODEL_NAME"] = "gpt-4"
os.environ["LLM_API_KEY"] = "your-api-key"
os.environ["LLM_BASE_URL"] = "https://api.openai.com/v1"  # Optional for OpenAI
```

#### 方法 2: 使用AgentConfig
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

#### 方法 3: 使用ModelConfig
如果有多个Agent使用相同的LLM服务，可以使用相同的ModelConfig实例：

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

### Prompts设置
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

# 设置system prompt
system_prompt = """You are a helpful AI assistant that can assist users with various tasks.
You should be polite, accurate, and provide clear explanations."""

agent = Agent(
    name="my_agent",
    conf=agent_config,
    system_prompt=system_prompt
)
```

### 工具设置
#### 本地工具
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


# 使用 @be_tool 定义

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

#### MCP (Model Context Protocol)
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

# 配置 MCP 服务

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

# 创建工具agent
tool_agent = Agent(name="tool_agent", conf=agent_config)

# 创建主agent，其将tool_agent作为工具使用
agent = Agent(
    name="my_agent",
    conf=agent_config,
    system_prompt=system_prompt,
    agent_names=['tool_agent']
)
```

