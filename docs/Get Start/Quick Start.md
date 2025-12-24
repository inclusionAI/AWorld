AWorld is far more than just an agent foundation framework; it’s an industrial-grade agent production closed-loop.

+ **For users**: It offers a suite of intuitive tools that enable defining agents and orchestrating multi-agent collaboration with just a single line of code.
+ **For developers**: It provides a large number of atomic functions and expandable APIs to help build production applications.
+ **For researchers**: It delivers an end-to-end pipeline, from data collection to training, making it a powerful tool for reproducing state-of-the-art (SOTA) results.

AWorld provides a complete solution for building, deploying, and evolving sophisticated multi-agent systems. If you aim to create intelligent systems capable of self-improvement and handling complex, long-horizon tasks, AWorld stands out as one of the best choices currently available in the open-source community.

<h2 id="GYfsN">Agent</h2>
<h3 id="requirements">Prerequisites</h3>
+ Python 3.11 or higher
+ pip package manager
+ Git

<h3 id="setup-steps">Setup Steps</h3>
<h4 id="1-clone-the-repository">1. Clone the Repository</h4>
```plain
git clone https://github.com/inclusionAI/AWorld.git
cd AWorld
```

<h4 id="2-install-dependencies">2. Install Dependencies</h4>
```plain
# Install in development mode
pip install -e .

# Or install 
pip install aworld
```

<h4 id="3-configure-environment">3. Configure Environment Variables</h4>
Create a `<font style="color:rgb(0, 0, 0);background-color:rgba(212, 222, 231, 0.247);">.env` file in your project root:

```plain
# LLM Configuration
LLM_PROVIDER=openai
LLM_MODEL_NAME=gpt-4
LLM_API_KEY=your-api-key-here
LLM_BASE_URL=https://api.openai.com/v1
LLM_TEMPERATURE=0.7

# Other Variables
```

<h4 id="4-verify-installation">4. Verify Installation</h4>
```plain
from aworld.agents.llm_agent import Agent
from aworld.log.util import logger

logger.info("✅ AWorld installed successfully!")
```

<h2 id="BtiWY">Environment</h2>
<h3 id="jigYF">Prerequisites</h3>
+ Docker
+ Docker Compose

<h3 id="wHCiV">Setup Steps</h3>
<h4 id="dSCtl">1. Verify</h4>
```plain
# Verify Docker versions
docker --version
docker compose --version

# Confirm the Docker daemon is running
docker ps
docker compose ps
```

<h4 id="xhdUd">2. Configuration</h4>
Copy the environment configuration file template and modify it according to your needs:

```plain
cp ./gaia-mcp-server/mcp_servers/.env_template ./gaia-mcp-server/mcp_servers/.env
```

The GAIA dataset is needed if you use the gaia-mcp-server.

<h4 id="Z1q9V">3. Launch VirtualPC MCP Server</h4>
```plain
sh run-local.sh
```

<h4 id="PvckJ">4. Connect the MCP Server</h4>
Use the configuration to connect to your VirtualPC MCP Server instance:

```plain
{
    "virtualpc-mcp-server": {
        "type": "streamable-http",
        "url": "http://localhost:8000/mcp",
        "timeout": 6000,
        "sse_read_timeout": 6000,
        "client_session_timeout_seconds": 6000
    }
}
```

<h2 id="gKF0N">Training</h2>
<h3 id="FaWk9">Prerequisites</h3>
+ aworld
+ AWorld env
+ Special train framework

<h3 id="xB4PW">Setup Steps</h3>
Example with Verl

<h4 id="g0QLt">1. Docker Image</h4>
Use a mirror of the specified training framework, example：**verl0.5-cu126-torch2.7.1-fa2.8.0，**URL: [https://github.com/volcengine/verl/tree/main/docker](https://github.com/volcengine/verl/tree/main/docker)

<h4 id="LH6Q7">2. Agent Define</h4>
Define Agent：

```plain
# define agent
agent = Agent(
    name="train_agent",
    desc="train_agent",
    system_prompt="train agent system prompt",
    mcp_config=mcp_config,
    mcp_servers=[your_server],
    conf=agent_config
)
```

<h4 id="jTNTa">3. Dataset</h4>
Specify the path to the local file, Huggingface Dataset, or load the dataset (requiring secondary processing). 

```plain
# define dataset
train_dataset, test_dataset = "/Users/your_name/dataset/train", "/Users/your_name/dataset/test"
```

<h4 id="nSTzo">4. Training Config</h4>
Customize configuration items based on specific training frameworks.

Note: It is necessary to define a reward function for evaluating agent behavior based on specific task objectives, which can be set in the training configuration.

```python
# define train config
custom_train_config = "string or json"
# define reward
reward_func = "None or string or code reference"
```

<h4 id="DUYK7">5. Run</h4>
Build a trainer instance and start training.

Note: If reward_func is code reference, it needs to be used as a parameter for AgentTrain.

```python
from train.trainer.agent_trainer import AgentTrainer

trainer = AgentTrainer(agent=agent,
                       config=custom_train_config,
                       reward_func=reward_func,
                       train_dataset=train_dataset,
                       test_dataset=test_dataset)
trainer.train()
```

