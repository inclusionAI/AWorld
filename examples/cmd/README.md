

## Develop run your own Agent


### 1. Agent Structure Overview

The agent following this directory structure:

```
agent_deploy
|--agent_x
    |--__init__.py  # model init file
    |--agent.py     # agent core logic, implement your own `AWorldAgent` class
    |--.env         # agent env config
    |--requirements.txt    # agent dependency
|--agent_y
    |--__init__.py
    |--agent.py
    |--.env
    |--requirements.txt
```

### 2. Implement your own `AWorldAgent` class

```
class AWorldAgent(BaseAWorldAgent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def name(self):
        return "Agent Name"

    def description(self):
        return "Agent Description Info"

    async def run(self, prompt: str = None):
        agent_config = AgentConfig(
            llm_provider=llm_provider,
            llm_model_name=llm_model_name,
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            llm_temperature=llm_temperature,
        )

        super_agent = Agent(
            conf=agent_config,
            name="The Agent",
            system_prompt="system prompt here",
            mcp_config=mcp_config,
            mcp_servers=["google-pse-search", "aworld-playwright"],
            feedback_tool_result=True,
        )

        task = Task(
            input=prompt,
            agent=super_agent,
            conf=TaskConfig(max_steps=20),
            session_id=request.session_id,
            endless_threshold=50,
        )

        async for output in Runners.streamed_run_task(task).stream_events():
            f.write(f"Agent {self.name()} received output: {output}\n")
            yield output

```

### 3. Run your Agent

```
sh run-web.sh
or
cd examples/cmd && aworld web
```

open [http://127.0.0.1:8000](http://127.0.0.1:8000)

