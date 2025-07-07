# Running AWorld Agent

## Prerequisites

1. **Conda Environment**
   - Ensure Conda is installed and configured on your machine
   - Create a Conda environment:
     ```bash
     conda create -n aworld python=3.11 -y
     conda activate aworld
     ```

2. **Clone Repository**
   - Clone the repository and switch to the main branch:
     ```bash
     git clone https://github.com/inclusionAI/AWorld
     cd AWorld
     ```

3. **Dataset Preparation**
   - Download the GAIA dataset from [Hugging Face](https://huggingface.co/datasets/gaia-benchmark/GAIA)
   - Place it in the correct directory:
     ```bash
     git clone git@hf.co:datasets/gaia-benchmark/GAIA examples/gaia/GAIA
     ```
   - ⚠️ **Note**: You need to configure [Hugging Face SSH Keys](https://huggingface.co/settings/keys) to access the GAIA repository

4. **API Configuration**
   - Set up your environment variables:
     ```bash
     cp examples/cmd/agent_deploy/${agent_name}/.env.template examples/cmd/agent_deploy/${agent_name}/.env
     ```
   - Edit the `.env` file and replace all `{YOUR_CONFIG}` placeholders with your actual values

## Running the Agent

1. **Start the Web Server**
   - Build and run the AWorld Agent:
     ```bash
     sh run-web.sh
     ```
   - Wait for the installation to complete

2. **Access the Interface**
   - Open your browser and navigate to [http://127.0.0.1:8000](http://127.0.0.1:8000)

## Troubleshooting

   - For dataset access problems, verify that your Hugging Face SSH keys are correctly configured
   - Set up a pip mirror if necessary

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



## Additional Resources

   - [GAIA Benchmark Documentation](https://huggingface.co/datasets/gaia-benchmark/GAIA)
   - [Hugging Face SSH Keys Setup Guide](https://huggingface.co/settings/keys)
