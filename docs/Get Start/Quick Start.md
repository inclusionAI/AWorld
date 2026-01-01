AWorld is far more than just an agent foundation framework; it’s an industrial-grade agent production closed-loop.

+ **For users**: It offers a suite of intuitive tools that enable defining agents and orchestrating multi-agent collaboration with just a single line of code.
+ **For developers**: It provides a large number of atomic functions and expandable APIs to help build production applications.
+ **For researchers**: It delivers an end-to-end pipeline, from data collection to training, making it a powerful tool for reproducing state-of-the-art (SOTA) results.

AWorld provides a complete solution for building, deploying, and evolving sophisticated multi-agent systems. If you aim to create intelligent systems capable of self-improvement and handling complex, long-horizon tasks, AWorld stands out as one of the best choices currently available in the open-source community.

## Agent
### Prerequisites
+ Python 3.11 or higher
+ pip package manager
+ Git

### Setup Steps
#### 1. Clone the Repository
```plain
git clone https://github.com/inclusionAI/AWorld.git
cd AWorld
```

#### 2. Install Dependencies
```plain
# Install in development mode
pip install -e .

# Or install 
pip install aworld
```

#### 3. Configure Environment Variables
Create a `.env` file in your project root:

```plain
# LLM Configuration
LLM_PROVIDER=openai
LLM_MODEL_NAME=gpt-4
LLM_API_KEY=your-api-key-here
LLM_BASE_URL=https://api.openai.com/v1
LLM_TEMPERATURE=0.7

# Other Variables
```

#### 4. Verify Installation
```plain
from aworld.agents.llm_agent import Agent
from aworld.log.util import logger

logger.info("✅ AWorld installed successfully!")
```

## Environment
### Prerequisites
+ Docker
+ Docker Compose

### Setup Steps
#### 1. Verify
```plain
# Verify Docker versions
docker --version
docker compose --version

# Confirm the Docker daemon is running
docker ps
docker compose ps
```

#### 2. Configuration
Copy the environment configuration file template and modify it according to your needs:

```plain
cp ./gaia-mcp-server/mcp_servers/.env_template ./gaia-mcp-server/mcp_servers/.env
```

The GAIA dataset is needed if you use the gaia-mcp-server.

#### 3. Launch VirtualPC MCP Server
```plain
sh run-local.sh
```

#### 4. Connect the MCP Server
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

## Training</h2>
### Prerequisites
+ aworld
+ AWorld env
+ Special train framework

### Setup Steps
Example with Verl

#### 1. Docker Image
Use a mirror of the specified training framework, example：**verl0.5-cu126-torch2.7.1-fa2.8.0，**URL: [https://github.com/volcengine/verl/tree/main/docker](https://github.com/volcengine/verl/tree/main/docker)

#### 2. Agent Define
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

#### 3. Dataset
Specify the path to the local file, Huggingface Dataset, or load the dataset (requiring secondary processing). 

```plain
# define dataset
train_dataset, test_dataset = "/Users/your_name/dataset/train", "/Users/your_name/dataset/test"
```

#### 4. Training Config
Customize configuration items based on specific training frameworks.

Note: It is necessary to define a reward function for evaluating agent behavior based on specific task objectives, which can be set in the training configuration.

```python
# define train config
custom_train_config = "string or json"
# define reward
reward_func = "None or string or code reference"
```

#### 5. Run
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

