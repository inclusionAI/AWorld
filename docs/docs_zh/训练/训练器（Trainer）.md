`AgentTrainer` 是 AWorld 框架的统一训练 API 入口，为 Agent 的训练提供了一个高度抽象和灵活的接口。它支持多种后端训练框架（如 VeRL），并统一管理 Agent、数据集、奖励函数和训练配置四大核心模块。

## 主要特性
+ ✅ **统一 API 入口**：为不同的训练框架提供一致的接口
+ ✅ **模块化设计**：Agent、Dataset、Reward、Config 四大模块独立管理
+ ✅ **灵活扩展**：支持注册自定义的训练引擎
+ ✅ **完整验证**：在初始化时对所有模块进行验证
+ ✅ **错误处理**：清晰的错误提示和日志记录

## 架构设计
### 整体架构图
```plain
┌─────────────────────────────────────────────────┐
│                    AgentTrainer                 │
│              (统一训练 API 入口)                  │
└────────────────────┬────────────────────────────┘
                     │
         ┌───────────┼───────────┐
         │           │           │
      初始化验证    模块管理    框架选择
         │           │           │
         └───────────┼───────────┘
                     │
        ┌────────────┴────────────┐
        │                         │
   ┌──────────────┐         ┌──────────────┐
   │   Trainer    │         │   Trainer    │
   │  (VeRL)      │  ...... │  (Swift)     │
   └──────────────┘         └──────────────┘
        │                       │
    ┌───┴──────┐──────────┌─────┴────┐
    │          │          │          │
  Agent    Dataset     Reward    Config
```

### 模块交互流程
```plain
用户创建 AgentTrainer
        │
        ↓
┌─────────────────────────────────────┐
│  1. 参数验证                        │
│     - 确保 agent 非空               │
└─────────────────────────────────────┘
        │
        ↓
┌─────────────────────────────────────┐
│  2. 获取训练引擎类                  │
│     - 从 TRAIN_PROCESSOR 字典查询   │
│     - 默认使用 'verl'               │
└─────────────────────────────────────┘
        │
        ↓
┌─────────────────────────────────────┐
│  3. 实例化训练引擎                  │
│     - 创建 TrainerProcessor 子类    │
│     - 传入 run_path                 │
└─────────────────────────────────────┘
        │
        ↓
┌─────────────────────────────────────┐
│  4. 模块验证 (重要!)                │
│     ├─ check_agent()                │
│     ├─ check_dataset()              │
│     ├─ check_reward()               │
│     └─ check_config()               │
└─────────────────────────────────────┘
        │
        ↓
┌─────────────────────────────────────┐
│  5. 标记初始化完成                  │
│     - mark_initialized()            │
└─────────────────────────────────────┘
        │
        ↓
┌─────────────────────────────────────┐
│  6. 开始训练                        │
│     - train_processor.train()       │
└─────────────────────────────────────┘
```

## 核心模块
### AgentTrainer
Agent训练器，负责统一管理和协调所有训练相关模块。

#### 属性
| 属性 | 类型 | 说明 |
| --- | --- | --- |
| `agent` | `Union[str, Agent]` | Agent 模块或配置文件路径 |
| `train_dataset` | `Union[str, Dataset]` | 训练数据集或路径 |
| `test_dataset` | `Union[str, Dataset]` | 测试数据集或路径 |
| `reward_func` | `Union[str, Callable]` | 奖励函数或路径 |
| `config` | `Union[str, Config]` | 训练配置 |
| `train_engine_name` | `str` | 训练框架名称（默认: 'verl'） |
| `run_path` | `str` | 运行输出路径（默认: 'runs'） |
| `train_processor` | `TrainerProcessor` | 实际的训练处理器实例 |


**初始化**

```python
trainer = AgentTrainer(
    agent='path/to/agent_config.yaml',
    train_dataset='path/to/train_dataset.csv',
    test_dataset='path/to/test_dataset.csv',
    reward_func=my_reward_function,
    config=training_config,
    run_path='./outputs',
    train_engine_name='verl'
)
```

**启动训练过程**

```python
def train(self) -> None:
```

+ 检查训练处理器是否已初始化
+ 调用训练处理器的 `train()` 方法
+ 如果处理器未初始化，抛出 ValueError

**注册自定义训练引擎**

```python
AgentTrainer.register_processor(train_engine_name='custom', CustomTrainer)
```

+ `train_engine_name`: 训练框架的名称（唯一标识符）
+ `train_type`: TrainerProcessor 的子类

**注销已注册的训练引擎**

```python
AgentTrainer.unregister_processor(train_engine_name='custom')
```

+ `train_engine_name`: 训练框架的名称（唯一标识符）

### TrainerProcessor
定义训练处理器的接口规范。所有具体的训练框架继承这个基类。

#### 训练方法
```python
@abc.abstractmethod
def train(self) -> None
```

**职责：** 

+ 具体的训练逻辑
+ 数据加载
+ 模型初始化
+ 训练循环等



##### 验证和处理数据集
```python
@abc.abstractmethod
def check_dataset(self, 
                 dataset: Union[str, Dataset] = None,
                 test_dataset: Union[str, Dataset] = None) -> None
```

**职责：**

+ 验证数据集格式是否符合框架要求
+ 将数据集转换为框架支持的格式
+ 处理数据集路径（如果是字符串）
+ 验证训练集和测试集的完整性



##### 验证和处理 Agent
```python
@abc.abstractmethod
def check_agent(self, agent: Union[str, Agent]) -> None
```

**职责：**

+ 验证 Agent 或其配置
+ 将 AWorld Agent 转换为特定框架支持的形式
+ 处理 Agent 配置文件（如果是字符串）
+ 确保 Agent 模型和参数的有效性

---

##### 验证和处理奖励函数(有监督标注)
```python
@abc.abstractmethod
def check_reward(self, 
                reward_func: Union[str, Callable[..., Any]] = None) -> None
```

**职责：**

+ 验证奖励函数的签名和返回类型
+ 将奖励函数配置转换为框架支持的格式
+ 处理奖励函数代码文件（如果是字符串）
+ 验证奖励函数的可调用性



##### 验证和改进训练配置
```python
@abc.abstractmethod
def check_config(self, 
                config: Union[str, Any] = None) -> Any
```

**职责：**

+ 验证训练配置是否满足框架要求
+ 补充和改进配置（添加默认值等）
+ 处理配置文件（如果是字符串）
+ 返回改进后的配置

## 自定义训练处理器
AWorld没有限制训练模式和策略，能够自定义使用多种训练模式和策略，如：

+ 监督学习。使用预收集的数据集进行监督学习，适用于有标注数据的场景。
+ 强化学习。通过与环境的交互学习最优策略，适用于决策优化场景。
+ 迁移学习。利用预训练模型进行迁移学习，加速新任务的训练过程。
+ 在线学习。在任务执行过程中持续学习，实时更新模型参数。

模块通常需要包含以下功能：

+ 模型参数管理
+ 经验数据收集
+ 训练策略执行
+ 模型更新机制

为 AWorld 添加新的训练框架支持，需要：

#### 步骤 1: 继承 TrainerProcessor
```python
# train/adapter/your_framework/my_trainer.py

from train.trainer.trainer_processor import TrainerProcessor
from typing import Union, Callable, Any
from datasets import Dataset
from aworld.agents.llm_agent import Agent
from aworld.logs.util import logger

class MyFrameworkTrainer(TrainerProcessor):
    """My custom training framework adapter."""
    
    def __init__(self, run_path: str):
        super().__init__(run_path)
        # 初始化框架特定的属性
        self.framework_config = None
    
    def train(self):
        """实现具体的训练逻辑"""
        logger.info("Starting training with MyFramework...")
        
        # 你的训练代码
        # - 加载数据
        # - 初始化模型
        # - 训练循环
        # - 保存检查点
        
        logger.info("Training completed!")
    
    def check_agent(self, agent: Union[str, Agent]):
        """验证和转换 Agent"""
        if isinstance(agent, str):
            # 从配置文件加载 Agent
            logger.info(f"Loading agent from {agent}")
            # ... 加载逻辑
        else:
            # 验证 Agent 对象
            logger.info("Validating Agent object")
            # ... 验证逻辑
    
    def check_dataset(self, 
                     dataset: Union[str, Dataset] = None, 
                     test_dataset: Union[str, Dataset] = None):
        """验证和转换数据集"""
        if dataset:
            if isinstance(dataset, str):
                # 加载数据集
                logger.info(f"Loading dataset from {dataset}")
                # ... 加载逻辑
            else:
                # 验证数据集格式
                logger.info("Validating dataset format")
                # ... 验证逻辑
        
        if test_dataset:
            # 类似处理测试集
            pass
    
    def check_reward(self, reward_func: Union[str, Callable[..., Any]] = None):
        """验证奖励函数"""
        if reward_func:
            if isinstance(reward_func, str):
                # 从文件加载奖励函数
                logger.info(f"Loading reward function from {reward_func}")
                # ... 加载逻辑
            elif callable(reward_func):
                # 验证可调用性
                logger.info("Validating reward function")
                # ... 验证逻辑
            else:
                raise ValueError("Reward func must be callable or file path")
    
    def check_config(self, config: Union[str, Any] = None):
        """验证和改进配置"""
        if config is None:
            # 使用默认配置
            config = self.get_default_config()
        elif isinstance(config, str):
            # 从文件加载配置
            logger.info(f"Loading config from {config}")
            # ... 加载逻辑
        
        # 验证和改进配置
        self.framework_config = config
        return config
    
    @staticmethod
    def get_default_config():
        """返回默认配置"""
        return {
            'learning_rate': 1e-4,
            'batch_size': 32,
            'epochs': 10,
            'warmup_steps': 1000,
        }
```

#### 步骤 2: 注册训练框架
```python
# train/adapter/your_framework/__init__.py

from .my_trainer import MyFrameworkTrainer

__all__ = ['MyFrameworkTrainer']
```

#### 步骤 3: 在 AgentTrainer 中注册
选项 A: 在代码中动态注册

```python
from train.adapter.my_framework import MyFrameworkTrainer
from train.trainer.agent_trainer import AgentTrainer

# 注册框架
AgentTrainer.register_processor('my_framework', MyFrameworkTrainer)

# 使用框架
trainer = AgentTrainer(
    agent=my_agent,
    train_engine_name='my_framework'
)
```

选项 B: 在初始化时注册（推荐用于库）

```python
# train/trainer/__init__.py

from .agent_trainer import AgentTrainer
from train.adapter.my_framework import MyFrameworkTrainer

# 在模块导入时自动注册
AgentTrainer.register_processor('my_framework', MyFrameworkTrainer)

```

