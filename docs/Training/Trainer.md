`AgentTrainer` is the unified training API entry point in the AWorld framework, providing a highly abstracted and flexible interface for agent training. It supports multiple backend training frameworks (e.g., VeRL) and centrally manages four core modules: **Agent**, **Dataset**, **Reward Function**, and **Training Configuration**.

### Key Features
+ ✅ **Unified API Entry Point**: Offers a consistent interface across different training frameworks
+ ✅ **Modular Design**: Independently manages the four core modules—Agent, Dataset, Reward, and Config
+ ✅ **Flexible Extensibility**: Supports registration of custom training engines
+ ✅ **Comprehensive Validation**: Validates all modules during initialization
+ ✅ **Robust Error Handling**: Provides clear error messages and detailed logging

### Architecture Design
This modular, extensible design allows AWorld to support diverse training methodologies while maintaining a clean, unified API for users.

#### Architecture
```plain
┌─────────────────────────────────────────────────┐
│                    AgentTrainer                 │
│            (Unified Training API Entry Point)   │
└────────────────────┬────────────────────────────┘
                     │
         ┌───────────┼───────────┐
         │           │           │
  Initialization   Module      Framework
   Validation     Management    Selection
         │           │           │
         └───────────┼───────────┘
                     │
        ┌────────────┴────────────┐
        │                         │
   ┌──────────────┐         ┌──────────────┐
   │   Trainer    │         │   Trainer    │
   │   (VeRL)     │  ...... │   (Swift)    │
   └──────────────┘         └──────────────┘
        │                       │
    ┌───┴──────┐──────────┌─────┴────┐
    │          │          │          │
  Agent    Dataset     Reward    Config
```

#### Module Interaction Flow
```plain
┌─────────────────────────────────────┐
│  1. Parameter Validation            │
│     - Ensures agent is not empty    │
└─────────────────────────────────────┘
        │
        ↓
┌─────────────────────────────────────┐
│  2. Fetch Training Engine Class     │
│     - Looks up in TRAIN_PROCESSOR   │
│     - Default 'verl' if unspecified │
└─────────────────────────────────────┘
        │
        ↓
┌─────────────────────────────────────┐
│  3. Instantiate Training Engine     │
│     - Creates a subclass            │
│     - Passes run_path for output    │
└─────────────────────────────────────┘
        │
        ↓
┌─────────────────────────────────────┐
│  4. Module Validation (Critical!)   │
│     ├─ check_agent()                │
│     ├─ check_dataset()              │
│     ├─ check_reward()               │
│     └─ check_config()               │
└─────────────────────────────────────┘
        │
        ↓
┌─────────────────────────────────────┐
│  5. Mark Initialization as Complete │
│     - mark_initialized()            │
└─────────────────────────────────────┘
        │
        ↓
┌─────────────────────────────────────┐
│  6. Start Training                  │
│     - train_processor.train()       │
└─────────────────────────────────────┘
```

### Core Modules
#### **AgentTrainer**
The central training coordinator that unifies and manages all training-related components.

全屏复制

| **Attribute** | **Type** | **Description** |
| --- | --- | --- |
| `agent` | `Union[str, Agent]` | Agent instance or config file path |
| `train_dataset` | `Union[str, Dataset]` | Training dataset or path |
| `test_dataset` | `Union[str, Dataset]` | Test dataset or path |
| `reward_func` | `Union[str, Callable]` | Reward function or its path |
| `config` | `Union[str, Config]` | Training configuration |
| `train_engine_name` | `str` | Training framework name (default: `'verl'`<br/>) |
| `run_path` | `str` | Output directory (default: `'runs'`<br/>) |
| `train_processor` | `TrainerProcessor` | Actual training processor instance |


##### Initialization Example
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

##### Start Training
```python


def train(self) -> None:
    # Checks if the training processor is initialized
    # Calls train_processor.train()
    # Raises ValueError if not initialized
```

##### Register a Custom Training Engine
```python


AgentTrainer.register_processor(
    train_engine_name='custom', 
    train_type=CustomTrainer
)
# - `train_engine_name`: Unique identifier for the framework
# - `train_type`: Subclass of `TrainerProcessor`
```

##### Unregister a Training Engine
```python


AgentTrainer.unregister_processor(train_engine_name='custom')
```

#### **TrainerProcessor**
Abstract base class defining the interface for all training frameworks. Concrete implementations must inherit from this class.

##### `train()`
**Responsibilities**:

+ Implement specific training logic
+ Load data
+ Initialize model
+ Execute training loop

##### `check_dataset(...)`
**Responsibilities**:

+ Validate dataset format against framework requirements
+ Convert datasets to framework-compatible formats
+ Resolve dataset paths (if provided as strings)
+ Verify integrity of train/test splits

##### `check_agent(...)`
**Responsibilities**:

+ Validate agent or its configuration
+ Convert AWorld Agent to framework-specific representation
+ Load agent from config file (if string path provided)
+ Ensure model and parameters are valid

##### `check_reward(...)` (also supervised labels/scores)
**Responsibilities**:

+ Validate function signature and return type
+ Convert reward config to framework-supported format
+ Load reward function from file (if string path provided)
+ Confirm callability

##### `check_config(...)`
**Responsibilities**:

+ Validate config against framework constraints
+ Augment with defaults where missing
+ Load config from file (if string path provided)
+ Return enhanced configuration

### Custom Training Processor
AWorld imposes no restrictions on training paradigms. Developers can implement various strategies:

+ **Supervised Learning**: Train on pre-collected labeled datasets
+ **Reinforcement Learning**: Learn optimal policies through environment interaction
+ **Transfer Learning**: Leverage pretrained models to accelerate new task adaptation
+ **Online Learning**: Continuously update model parameters during task execution

A custom trainer should typically support:

+ Model parameter management
+ Experience/data collection
+ Training strategy execution
+ Model update mechanisms

#### Step 1: Inherit from `TrainerProcessor`
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
        self.framework_config = None

    def train(self):
        logger.info("Starting training with MyFramework...")
        # Your training logic:
        # - Load data
        # - Initialize model
        # - Training loop
        # - Save checkpoints
        logger.info("Training completed!")

    def check_agent(self, agent: Union[str, Agent]):
        if isinstance(agent, str):
            logger.info(f"Loading agent from {agent}")
            # ... loading logic
        else:
            logger.info("Validating Agent object")
            # ... validation logic

    def check_dataset(self, 
                      dataset: Union[str, Dataset] = None,
                      test_dataset: Union[str, Dataset] = None):
        if dataset:
            if isinstance(dataset, str):
                logger.info(f"Loading dataset from {dataset}")
                # ... loading logic
            else:
                logger.info("Validating dataset format")
                # ... validation logic
        if test_dataset:
            # similar handling
            pass

    def check_reward(self, reward_func: Union[str, Callable[..., Any]] = None):
        if reward_func:
            if isinstance(reward_func, str):
                logger.info(f"Loading reward function from {reward_func}")
                # ... loading logic
            elif callable(reward_func):
                logger.info("Validating reward function")
                # ... validation logic
            else:
                raise ValueError("Reward func must be callable or file path")

    def check_config(self, config: Union[str, Any] = None):
        if config is None:
            config = self.get_default_config()
        elif isinstance(config, str):
            logger.info(f"Loading config from {config}")
            # ... loading logic
        self.framework_config = config
        return config

    @staticmethod
    def get_default_config():
        return {
            'learning_rate': 1e-4,
            'batch_size': 32,
            'epochs': 10,
            'warmup_steps': 1000,
        }
```

#### Step 2: Expose the Trainer in Module `__init__.py`
```python
# train/adapter/your_framework/__init__.py

from .my_trainer import MyFrameworkTrainer

__all__ = ['MyFrameworkTrainer']
```

#### Step 3: Register the Framework with `AgentTrainer`
**Option A: Dynamic Registration (in user code)**

```python
from train.adapter.my_framework import MyFrameworkTrainer
from train.trainer.agent_trainer import AgentTrainer

AgentTrainer.register_processor('my_framework', MyFrameworkTrainer)

trainer = AgentTrainer(
    agent=my_agent,
    train_engine_name='my_framework'
)
```

**Option B: Automatic Registration (recommended for libraries)**

```python
# train/trainer/__init__.py

from .agent_trainer import AgentTrainer
from train.adapter.my_framework import MyFrameworkTrainer

# Auto-register when module is imported
AgentTrainer.register_processor('my_framework', MyFrameworkTrainer)
```

