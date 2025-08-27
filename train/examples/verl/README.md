# AWorld Train

This module provides:
- adapters/: reusable adapters for specific RL frameworks (e.g., VERL)
- utils/: framework-agnostic utilities
- examples/: runnable examples organized by framework

## Layout

```
train/
  adapters/
    verl/
      core/
      utils/
  utils/
  examples/
    verl/
      agents/
      configs/
      datasets/
      scripts/
      logs/
      outputs/
```

## Principles
- Keep framework-specific logic in adapters.
- Keep shared utilities in utils.
- Keep runnable, thin wrappers and configs in examples.

## Installation
1) Set up environment \
    We recommend using either conda or venv to create a new virtual environment. \
    Python 3.10 is recommended.
2) Install Aworld
    ```bash
    pip install aworld
    ```
3) Install verl
    ```bash
    pip install verl==0.5.0
    ```

## Quick Start (GAIA + VERL)
```bash
cd train/examples/verl
```
1) Prepare dataset:
```bash
python create_dataset.py \
  --dataset_path ${/path/to/GAIA}/2023 \
  --output_dir datasets/ \
  --train_size 300 \
  --test_size 100
```
2) Edit configs under `train/examples/verl/configs/`.
    - `agent.yaml`: Specify agent
    - `tool.yaml`: Tool environment configuration
3) Run training:
```bash
bash scripts/run.sh
```

## Importing adapters
```python
from train.adapters.verl.core.aworld_agent_loop import AworldAgentLoop
```
