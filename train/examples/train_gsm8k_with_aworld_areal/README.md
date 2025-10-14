# Training GSM8K with AWorld and ARealML

This example demonstrates how to train a mathematical reasoning model on the GSM8K dataset using the AWorld integrated with AReaL.

## Requirements

- Python 3.11+
- AWorld
- ARealML
- A CUDA-compatible GPU (recommended)

## Quick Start

### Setup

1. Install the required dependencies:

```bash
cd AWorld
pip install .
```

2. Ensure you have access to the AWorld and AReaL is properly installed.

### Data Preparation

The GSM8K dataset can be loaded using the Hugging Face datasets library:

```python
from datasets import load_dataset
dataset = load_dataset("openai/gsm8k")
```

The dataset will be automatically downloaded when running the training script.

### Running the Training

Execute the training script with the provided configuration:

```bash
bash examples/train_gsm8k_with_aworld_areal/run.sh
```

This will start the training process using the parameters defined in `gsm8k_grpo.yaml`.

## Modules

### Configuration

The training configuration is defined in `gsm8k_grpo.yaml`. Key parameters include:

- `actor`: Train actor, `path` and `max_tokens_per_mb` may need change.
- `async_training`: Whether to use asynchronous training
- `cluster`: Cluster configuration for distributed training
- `rollout`: Parameters for rollout generation
- `sglang`: Configuration for SGLang integration
- `dataset`: Paths to training and validation datasets, `path` may need change.
- `utils`: Settings for saving, recovery, evaluation, and logging

### Workflow

The training uses a custom `Gsm8kWorkflow` class defined in `custom_workflow.py`, which:

1. Builds an agent with the specified configuration
2. Implements a reward function specific to GSM8K problems
3. Handles the interaction between the agent and the training environment

### Reward Function

The reward function evaluates the correctness of the model's output by:
- Extracting the final answer from the model's reasoning
- Comparing it with the ground truth answer
- Providing a binary reward (1 for correct, 0 for incorrect)

## Customization

You can customize the training by:

1. Modifying the `gsm8k_grpo.yaml` configuration file
2. Build agent and adjusting the reward function in `custom_workflow.py`
3. Changing the agent configuration in the workflow

## Evaluation

Training results will be saved in the specified output directory. You can monitor:
- Training progress through logs
- Model checkpoints at specified intervals
- Evaluation metrics on the validation set
