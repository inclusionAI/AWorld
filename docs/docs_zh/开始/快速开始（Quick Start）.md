AWorld 不仅仅是一个简单的 Agent 开发库，它更像是一个**工业级的 Agent 生产线**。

+ **对于使用者**：它提供了系列的便捷工具，能够一行定义Agent和构建多Agent的协作。
+ **对于开发者**：它提供了大量的原子功能和可扩展接口，帮助构建真正的生产应用。
+ **对于研究员**：它提供了从数据收集到 RL 训练的完整 Pipeline，是复现 SOTA 效果的利器。

AWorld为构建、部署和进化复杂的多智能体系统提供了完整解决方案。如果您希望构建**能够自我进化、处理复杂长程任务**的智能体系统，AWorld 是目前开源社区中较好的选择之一。

## Agent
### 前置依赖
+ Python 3.11 或更高
+ pip安装管理器
+ Git

### 步骤
#### 1. 克隆项目
```plain
git clone https://github.com/inclusionAI/AWorld.git
cd AWorld
```

#### 2. 安装
```plain
# Install in development mode
pip install -e .

# Or install 
pip install aworld
```

#### 3. 配置环境变量
建议在根路径创建.env文件，内容示例：

```plain
# LLM Configuration
LLM_PROVIDER=openai
LLM_MODEL_NAME=gpt-4
LLM_API_KEY=your-api-key-here
LLM_BASE_URL=https://api.openai.com/v1
LLM_TEMPERATURE=0.7

# Other Variables
```

#### 4. 安装验证
```plain
from aworld.agents.llm_agent import Agent
from aworld.log.util import logger

logger.info("✅ AWorld installed successfully!")
```

## 环境
### 前置依赖
+ Docker
+ Docker Compose

### 步骤
#### 1. 依赖验证
```plain
# Verify Docker versions
docker --version
docker compose --version

# Confirm the Docker daemon is running
docker ps
docker compose ps
```

#### 2. 配置
创建.env文件，如从项目中拷贝后修改：

```plain
cp ./gaia-mcp-server/mcp_servers/.env_template ./gaia-mcp-server/mcp_servers/.env
```

如果使用gaia-mcp-server，需要GAIA数据集。

#### 3. 启动MCP服务
```plain
sh run-local.sh
```

#### 4. 连接MCP服务
使用以下配置连接已启动的MCP服务：

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

## 训练
### 前置依赖
+ aworld
+ AWorld env
+ 特定的训练框架

### 步骤
以VeRL为例。

#### 1. 镜像
使用指定训练框架的镜像，如：**verl0.5-cu126-torch2.7.1-fa2.8.0，**地址：[https://github.com/volcengine/verl/tree/main/docker](https://github.com/volcengine/verl/tree/main/docker)

#### 2. 定义Agent
定义想要训练的Agent：

```plain
# 定义agent
agent = Agent(
    name="train_agent",
    desc="train_agent",
    system_prompt="train agent system prompt",
    mcp_config=mcp_config,
    mcp_servers=[your_server],
    conf=agent_config
)
```

#### 3. 选择数据集
**从**本地文件路径，或Huggingface Dateset中指定路径或加载数据集（需二次加工场景）。如：

```plain
# 定义数据集
train_dataset, test_dataset = "/Users/your_name/dataset/train", "/Users/your_name/dataset/test"
```

#### 4. 训练配置
基于特定的训练框架，自定义配置项。

注意：需要根据具体的任务目标，定义用于评估智能体行为的奖励函数，可以设置在训练配置。

```python
# 定义训练配置
custom_train_config = "string or json"
# 定义reward
reward_func = "None or string"
```

#### 5. 运行
构建trainer实例并启动训练。

注：如果reward_func是代码引用(如gaia_reward函数)，需要作为AgentTrainer的参数。

```python
from train.trainer.agent_trainer import AgentTrainer

trainer = AgentTrainer(agent=agent,
                       config=custom_train_config,
                       reward_func=reward_func,
                       train_dataset=train_dataset,
                       test_dataset=test_dataset)
trainer.train()
```

