<font style="color:rgb(13, 18, 57);">AWorld provides a fundamental Agent interface that unlocks limitless possibilities for agent capabilities. While the </font>**<font style="color:rgb(13, 18, 57);">LLMAgent</font>**<font style="color:rgb(13, 18, 57);"> is the most commonly used implementation, the framework imposes no restrictions on other agent types—such as those powered by rule-based systems or traditional machine learning models—as their decision core.</font>

<h2 id="npPgL"><font style="color:rgb(13, 18, 57);">LLMAgent customization</font></h2>
The most typical ability of LLM Agent is to use LLM to make decisions, which is abstracted into 5 steps in AWorld:

1. Agent initialization,
2. Construct LLM input,
3. Call LLM,
4. Parse the LLM output,
5. Generate Agent response.

<h3 id="H5hYx">Customizing Agent Input</h3>
Override the `init_observation()` function to customize how your agent processes initial observations:

```python
async def init_observation(self, observation: Observation) -> Observation:
    # You can add extended information from other agents or third-party storage
    # For example, enrich the observation with additional context
    observation.metadata = {"timestamp": time.time(), "source": "custom"}
    return observation
```

<h3 id="bMevF">Customizing Model Input</h3>
Override the `async_messages_transform()` function to customize how messages are transformed before being sent to the  
model:

```python
async def async_messages_transform(self,
                                   image_urls: List[str] = None,
                                   observation: Observation = None,
                                   message: Message = None,
                                   **kwargs) -> List[Dict[str, Any]]:
    """
    Transform input data into the format expected by the LLM.
    
    Args:
         image_urls: List of images encoded using base64
         observation: Observation from the environment
         message: Event received by the Agent
    """
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

<h3 id="AL32g">Customizing Model Logic</h3>
Override the `invoke_model()` function to implement custom model logic:

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

<h3 id="DOUEy">Customizing Model Output</h3>
Create a custom `ModelOutputParser` class and specify it using the `model_output_parser` parameter:

```python
from aworld.models.model_output_parser import ModelOutputParser


class CustomOutputParser(ModelOutputParser[ModelResponse, AgentResult]):
    async def parse(self, resp: ModelResponse, **kwargs) -> AgentResult:
        """Custom parsing logic based on your model's API response format."""

         # Extract relevant information from the model response
         content = resp.content
         tool_calls = resp.tool_calls
         
         # Create your custom AgentResult
         result = AgentResult(
             content=content,
             tool_calls=tool_calls,
             metadata={"parsed_at": time.time()}
         )
         
         return result

# Use the custom parser

agent = Agent(
    name="my_agent",
    conf=agent_config,
    model_output_parser=CustomOutputParser()
)
```

<h3 id="qLyhb">Customizing Agent Response</h3>
Override the `async_post_run()` function to customize how your agent responds:

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

<h3 id="aBJ4H">Custom Agent Policy</h3>
Override the `async_policy()` function to customize your agent policy logic:

```python
async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, message: Message = None,
                           **kwargs) -> List[ActionModel]:
    self._finished = False
    # build model input messages
    messages = await self.build_llm_input(observation, info, message)
    # call model
    llm_response = await self.invoke_model(messages, message=message, **kwargs)
    # parse model response
    agent_result = await self.model_output_parser.parse(llm_response,
                                                        agent_id=self.id(),
                                                        use_tools_in_prompt=self.use_tools_in_prompt)

    self._finished = True
    return agent_result.actions
```

<h3 id="qPvls">Custom Agent Event Parsing</h3>
If the framework still does not support the response structure you want, or if there is special logic processing (such as triggering multiple downstream agents based on Agent response), you can create a custom agent response event handler:

```python
from aworld.runners import HandlerFactory
from aworld.runners.default_handler import DefaultHandler

# Define a custom handler name
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


# Use the custom handler
agent = Agent(
    name="my_agent",
    conf=agent_config,
    event_handler_name=custom_name
)
```

**Important Note:** The `custom_name` variable value must remain consistent across your handler registration and agent configuration.

<h2 id="eBvOz">Agent customization</h2>
Users can also implement their own designed Agent, as long as it complies with interface specifications.

<h3 id="whnhd">Customize the overall process</h3>
Override the`run()` or`async_run()` function to implement custom model logic:

```python
# async
async def async_run(self, message: Message, **kwargs) -> Message:
    return Message(...)

# sync
def run(self, message: Message, **kwargs) -> Message:
    return Message(...)

```

<h3 id="bYTbX">Customized decision process</h3>
It is recommended to use Observation and List [ActionModel] for INPUT and OUTPUT, which can be customized with minimal cost.

Override the`policy()` or`async_policy()` function to implement custom model logic:

```python
# async
async def async_policy(
    self, observation: INPUT, info: Dict[str, Any] = None, **kwargs
) -> OUTPUT:
    pass

# sync
def policy(
    self, observation: INPUT, info: Dict[str, Any] = None, **kwargs
) -> OUTPUT:
    pass
```

