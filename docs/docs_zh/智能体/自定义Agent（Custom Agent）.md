AWorld提供最基础的Agent接口，使Agent的能力有无限的可能。目前最常用的是LLMAgent，但没有限制其他类型Agent，如使用规则，传统模型作为其决策内核。

## LLMAgent模块定制
LLM Agent最典型的能力是使用LLM来决策行为，在AWorld中抽象为5步：

1. Agent初始化，
2. 构造LLM输入，
3. 调用LLM，
4. 解析LLM输出，
5. 生成Agent响应。

### 自定义 Agent 输入
重写 `init_observation()` 函数，以自定义代理处理初始观测的方式：

```python
async def init_observation(self, observation: Observation) -> Observation:
    # 您可以从其他代理或第三方存储添加扩展信息，如用上下文丰富观察
    observation.metadata = {"timestamp": time.time(), "source": "custom"}
    return observation
```

### 自定义 Model 输入
重写 `async_messages_transform()` 函数， 在发送到模型之前自定义messages的内容：

```python
async def async_messages_transform(self,
                                   image_urls: List[str] = None,
                                   observation: Observation = None,
                                   message: Message = None,
                                   **kwargs) -> List[Dict[str, Any]]:
    messages = []

    # Add system context
    if hasattr(self, 'system_prompt'):
        messages.append({"role": "system", "content": self.system_prompt})

    # Add user message
    if message and message.content:
        messages.append({"role": "user", "content": message.content})

    # Add images if present
    if image_urls:
        for img_url in image_urls:
            messages.append({
                "role": "user",
                "content": [{"type": "image_url", "image_url": {"url": img_url}}]
            })

    return messages
```

### 自定义 Model 调用
重写 `invoke_model()` 函数，以实现自定义的模型调用和处理逻辑:

```python
async def invoke_model(self,
                       messages: List[Dict[str, str]] = [],
                       message: Message = None,
                       **kwargs) -> ModelResponse:
    """Custom model invocation logic.
       You can use neural networks, rule-based systems, or any other business logic.
    """

      # Example: Use a custom model or business logic
      if self.use_custom_logic:
          # Your custom logic here
          response_content = self.custom_model.predict(messages)
      else:
          # Use the default LLM
          response_content = await self.llm_client.chat_completion(messages)
      
      return ModelResponse(
          id=f"response_{int(time.time())}",
          model=self.model_name,
          content=response_content,
          tool_calls=None  # Set if tool calls are present
      )
```

### 自定义 Model 输出
创建 `ModelOutputParser` 子类，并在构建Agent时，将其赋值给 `model_output_parser`参数 :

```python
from aworld.models.model_output_parser import ModelOutputParser


class CustomOutputParser(ModelOutputParser[ModelResponse, AgentResult]):
    async def parse(self, resp: ModelResponse, **kwargs) -> AgentResult:
        """Custom parsing logic based on your model's API response format."""

         # 从ModelResponse获取相关信息
         content = resp.content
         tool_calls = resp.tool_calls
         
         # Create your custom AgentResult
         result = AgentResult(
             content=content,
             tool_calls=tool_calls,
             metadata={"parsed_at": time.time()}
         )
         
         return result


# 使用自定义parser
agent = Agent(
    name="my_agent",
    conf=agent_config,
    model_output_parser=CustomOutputParser()
)
```

### 自定义 Agent 响应
重写 `async_post_run()` 函数，以自定义如何构建最终的响应消息：

```python
from aworld.core.message import Message

class CustomMessage(Message):
      def __init__(self, content: str, custom_field: str = None):
            super().__init__(content=content)
            self.custom_field = custom_field
      
async def async_post_run(self,
                        policy_result: List[ActionModel],
                        policy_input: Observation,
                        message: Message = None) -> Message:
      """
      Customize the agent's response after processing.
      """
      
      # Process the policy result and create a custom response
      response_content = f"Processed {len(policy_result)} actions"
      custom_field = "custom_value"
      
       return CustomMessage(
           content=response_content,
           custom_field=custom_field
       )
```

### 自定义 Agent 决策
以上均为Agent决策的一部分，如果想要重新实现Agent的决策流程，可以重写 `async_policy()` 函数。

```python
async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, message: Message = None,
                           **kwargs) -> List[ActionModel]:
    self._finished = False
    # 构造模型输入
    messages = await self.build_llm_input(observation, info, message)
    # 调用模型
    llm_response = await self.invoke_model(messages, message=message, **kwargs)
    # 解析结果
    agent_result = await self.model_output_parser.parse(llm_response,
                                                        agent_id=self.id(),
                                                        use_tools_in_prompt=self.use_tools_in_prompt)

    self._finished = True
    return agent_result.actions
```

### 自定义 Agent 事件处理
如果框架仍不支持您想要的响应结构，或者有特殊的逻辑处理 (如基于Agent响应触发下游多个Agent)，您可以创建一个自定义的事件处理器：

```python
from aworld.runners import HandlerFactory
from aworld.runners.default_handler import DefaultHandler

# 自定义 handler 名
custom_name = "custom_handler"


@HandlerFactory.register(name=custom_name)
class CustomHandler(DefaultHandler):
    def is_valid_message(self, message: Message):
        """Check if this handler should process the message."""
        return message.category == custom_name


async def _do_handle(self, message: Message) -> AsyncGenerator[Message, None]:
    """Custom message processing logic."""
    if not self.is_valid_message(message):
        return

    # Implement your custom message processing logic here
    processed_message = self.process_custom_message(message)
    yield processed_message


# 使用自定义 handler
agent = Agent(
    name="my_agent",
    conf=agent_config,
    event_handler_name=custom_name
)
```

**重要提示:** `custom_name` 变量值在注册和代理配置中必须保持一致。

## Agent定制
用户可以实现一套自己设计的Agent，只需要符合接口规范。

### 定制整体流程
重写`run`或 `async_run`函数。

```python
# 异步
async def async_run(self, message: Message, **kwargs) -> Message:
    return Message(...)

# 同步
def run(self, message: Message, **kwargs) -> Message:
    return Message(...)

```

### 定制决策流程
重写`policy`或 `async_policy`函数。其中INPUT和OUTPUT建议使用 `Observation`和`List[ActionModel]`，这样能以最小的代价做定制。

```python
# 异步
async def async_policy(
    self, observation: INPUT, info: Dict[str, Any] = None, **kwargs
) -> OUTPUT:
    pass

# 同步
def policy(
    self, observation: INPUT, info: Dict[str, Any] = None, **kwargs
) -> OUTPUT:
    pass
```

