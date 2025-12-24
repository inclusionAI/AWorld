Stream agent responses in real-time for responsive user interfaces and applications.

<h2 id="xx4xx">Overview</h2>
Messages from the **AWorld** runtime can be **streamed** to the client. If you’re building a UI on AWorld, enabling streaming allows your UI to update in real-time as the agent generates a response (tokens), executes tools, or changes state.

When working with agents that execute long-running operations (e.g., complex tool calls, extensive searches, or code execution), streaming provides immediate feedback to the user.

<h2 id="AzSZb">Quick Start</h2>
AWorld supports flexible streaming modes via the `Runners.streaming_run` interface.

To enable streaming, use `Runners.streaming_run` (for direct execution) or `Runners.streaming_run_task` (for task objects):

```python
import asyncio
from aworld.runner import Runners
from aworld.core.common import StreamingMode
from aworld.core.event.base import ChunkMessage, AgentMessage

async def main():
    # 1. Create your agent (assuming 'agent' is already defined)
    # ... 

    # 2. Start streaming
    stream = Runners.streaming_run(
        input="Write a short poem about coding.",
        agent=agent,
        streaming_mode=StreamingMode.ALL  # Receive all event types
    )

    # 3. Consume the stream
    async for msg in stream:
        if isinstance(msg, ChunkMessage):
             # Partial token content
            print(msg.payload.content, end="", flush=True)
        elif isinstance(msg, AgentMessage):
            # Complete agent step
            print(f"\n[Agent Step]: {msg.payload}")
            
if __name__ == "__main__":
    asyncio.run(main())
```

<h2 id="zHqSr">Streaming Modes</h2>
AWorld provides granular control over what information is streamed back to the client via the `StreamingMode` enum.

| Mode | Description |
| :--- | :--- |
| `StreamingMode.CORE` | (Default) Streams core life-cycle events: Agent, Tool, Chunk, Task, and Group messages. |
| `StreamingMode.CHUNK` | Only streams `ChunkMessage` events (raw LLM token generation). Best for simple "typewriter" interfaces. |
| `StreamingMode.OUTPUT` | Only streams final `Output` messages. |
| `StreamingMode.CHUNK_OUTPUT` | Streams both partial tokens (`ChunkMessage`) and final outputs. |
| `StreamingMode.ALL` | Streams **all** events including detailed debug info, state changes, and memory operations. |


```python
from aworld.core.common import StreamingMode

# Only get the raw tokens
stream = Runners.streaming_run(..., streaming_mode=StreamingMode.CHUNK)
```

<h2 id="ZONms">Understanding Message Flow</h2>
Unlike simple LLM streaming, AWorld streams **Events**. An agent execution involves multiple steps: reasoning, tool calling, tool execution, and memory updates.

<h3 id="yzT5p">Message Types Reference</h3>
The stream yields `Message` objects. Key message types you will encounter:

+ `ChunkMessage`: Represents a partial piece of content (e.g., a token from the LLM).
    - `msg.category`: "chunk"
    - `msg.payload`: Object containing `content` (str).
+ `ToolMessage`: Indicates a tool call is requested or completed.
    - `msg.category`: "tool"
    - `msg.payload`: List of `ActionModel` (tool name, params).
+ `AgentMessage`: Represents a completed agent step or observation.
    - `msg.category`: "agent"
+ `TaskMessage`: Indicates task status updates (Start, Finish, Error).
    - `msg.topic`: `TopicType.TASK_RESPONSE` signals the end of the stream.

<h2 id="ofjCb">Example: handling Different Events</h2>
Here is a comprehensive example handling various event types to build a rich UI experience:

```python
from aworld.core.event.base import ChunkMessage, ToolMessage, TopicType

async for msg in Runners.streaming_run(input="Search for weather in Beijing", agent=agent):
    
    # 1. Handle Real-time Tokens (Typewriter effect)
    if isinstance(msg, ChunkMessage):
        # chunk.payload is typically a ModelResponse chunk
        content = msg.payload.content
        if content:
            print(content, end="", flush=True)

    # 2. Handle Tool Calls (Show "Executing..." status)
    elif isinstance(msg, ToolMessage):
        actions = msg.payload
        for action in actions:
            print(f"\n[Tool Call]: {action.tool_name} (params: {action.params})")

    # 3. Handle Task Completion
    elif msg.topic == TopicType.TASK_RESPONSE:
        result = msg.payload
        if result.success:
            print(f"\n[Finished]: {result.answer}")
        else:
            print(f"\n[Failed]: {result.msg}")
```

