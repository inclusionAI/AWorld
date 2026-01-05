AWorld LLM Interface 是一个统一的底层接口，旨在通过一致的 API 与各种 LLM 提供商（Provider）进行交互。

### 1. 总览
**AWorld** 的模型接口为开发者提供了一种标准化的方式来调用不同的语言模型。你不需要处理各个模型厂商（如 OpenAI、Anthropic 等）复杂的 SDK 和响应格式，只需通过一套 API 即可完成对话补全、流式输出和工具调用。

核心特性：
- **统一 API**：一套代码即可支持 OpenAI、Anthropic、Azure 等多家厂商。
- **全异步支持**：原生支持同步和异步调用，满足不同性能需求。
- **流式响应**：支持实时 Token 生成，提升前端交互体验。
- **标准化响应**：所有模型返回统一的 `ModelResponse` 对象。
- **工具调用**：内置对 Function Calling 和工具执行的支持。

### 2. 支持的提供商 (Providers)
AWorld 目前支持以下主流厂商，并支持通过扩展基类来快速接入新模型。

| 提供商 | 说明 |
| :--- | :--- |
| `openai` | 支持 OpenAI 协议的模型（如 GPT-4o, GPT-3.5, 以及兼容的国产大模型） |
| `anthropic` | Anthropic Claude 系列模型（如 Claude 3.5 Sonnet, Opus 等） |
| `azure_openai` | 微软 Azure OpenAI 服务集成 |

### 3. 快速开始

#### 3.1 使用底层接口
通过 `get_llm_model` 初始化模型，并使用 `call_llm_model` 进行简单的对话补全。

```python
from aworld.models.llm import get_llm_model, call_llm_model

# 1. 初始化模型
model = get_llm_model(
    llm_provider="openai",
    model_name="gpt-4o",
    api_key="your_api_key"
)

# 2. 准备消息
messages = [
    {"role": "system", "content": "你是一个专业的智能助手。"},
    {"role": "user", "content": "用一句话解释什么是量子计算。"}
]

# 3. 获取响应
response = call_llm_model(model, messages)
print(response.content)
```

#### 3.2 与 LLMAgent 集成
如果你使用的是 `LLMAgent` (位于 `aworld.agents.llm_agent`) 构建智能体，模型调用过程是自动完成的。在创建 Agent 时配置好 `AgentConfig` 后，`LLMAgent` 内部会在执行周期中自动调用对应的模型进行推理。

```python
from aworld.agents.llm_agent import LLMAgent
from aworld.config.conf import AgentConfig

# 在配置中设置 LLM 参数
config = AgentConfig(
    llm_provider="openai",
    llm_model_name="gpt-4o",
    llm_api_key="your_api_key"
)

# 初始化 Agent
agent = LLMAgent(name="Assistant", conf=config)

# Agent 会在执行决策（policy）时自动调用模型
# response = await agent.async_policy(observation)
```

### 4. 高级用法

#### 4.1 流式响应 (Streaming)
为了实现即时反馈，可以通过在调用时设置 `stream=True` 来开启流式输出。

此外，在使用 `LLMAgent` 时，也可以通过在 Agent 配置中设置 `llm_stream_call=True` 来全局开启流式输出。

```python
from aworld.models.llm import call_llm_model, acall_llm_model
from aworld.config.conf import AgentConfig

# 方式一：在调用时开启
for chunk in call_llm_model(model, messages, stream=True):
    if chunk.content:
        print(chunk.content, end="", flush=True)

# 方式二：在 AgentConfig 中全局开启
config = AgentConfig(
    llm_model_name="gpt-4o",
    llm_stream_call=True  # 该 Agent 的所有模型调用都将默认使用流式
)
```

# 异步流式输出
async for chunk in await acall_llm_model(model, messages, stream=True):
    if chunk.content:
        print(chunk.content, end="", flush=True)
```

#### 4.2 工具调用 (Tool Calls)
AWorld 统合了不同厂商的工具定义和响应处理流程。

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
        print(f"正在调用工具: {tool_call.name}, 参数: {tool_call.arguments}")
```

### 5. ModelResponse 对象
所有模型交互都会返回一个 `ModelResponse` 对象，它以厂商无关的格式封装了所有必要信息。

| 属性 | 说明 |
| :--- | :--- |
| `content` | LLM 生成的文本内容。 |
| `tool_calls` | 模型请求执行的工具列表（`ToolCall` 对象列表）。 |
| `usage` | Token 使用统计（提示词、补全、总计）。 |
| `message` | 完整的消息对象，可直接追加到历史记录中。 |
| `model` | 实际生成响应的模型名称。 |

### 6. 配置与初始化

#### 6.1 自动厂商探测
AWorld 通常可以根据模型名称自动推断所属的 Provider：
```python
# 自动探测并使用 'anthropic' 提供商
model = get_llm_model(model_name="claude-3-5-sonnet-20241022")
```

#### 6.2 选择性初始化
为了优化性能，你可以选择仅初始化同步或异步客户端：
```python
model = get_llm_model(
    model_name="gpt-4o",
    sync_enabled=True,
    async_enabled=False  # 跳过异步客户端初始化
)
```

#### 6.3 API Key 管理
API Key 的解析优先级如下：
1. 代码中直接传入的 `api_key` 参数。
2. `.env` 文件中的环境变量（如 `OPENAI_API_KEY`）。
3. 操作系统环境变量。

