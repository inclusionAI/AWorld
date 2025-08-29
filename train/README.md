<div align="center">

# AWorld Train

*Framework-agnostic training adapters, examples, and utilities for training AWorld agents with external RL/training frameworks*

[![License: MIT][license-image]][license-url]

</div>

<div align="center">

[中文版本](./README_zh.md) | [Quick Start](#quick-start) | [Development](#development) | [Contributing](#contributing)

</div>

---

## 1. Overview

AWorld Train provides a unified way to run training pipelines for agents built with the AWorld framework. It includes framework adapters (e.g., VeRL), runnable examples, and shared utilities so you can quickly bring your agent into different training ecosystems.

### 1.1 Features

- **Multi-Framework Adapters**: Plug AWorld agents into external frameworks (e.g., VeRL). More can be added via a simple adapter pattern.
- **Runnable Examples**: End-to-end examples under `train/examples/` to start training immediately.
- **Config-Driven**: Standardized configs (agent/tool) and script entrypoints for reproducible runs.
- **Dataset Utilities**: Helper scripts to build datasets for training/evaluation (e.g., GAIA dataset preparation for VeRL example).
- **Reward Function Hooks**: Easily plug in a custom reward function via file path and function name.
- **Scalable**: Works on single machine; distributed capabilities depend on the chosen framework.

## 2. Quick Start

We recommend starting with the VeRL example.

### 2.1 Prerequisites

- Python 3.10+ (recommended)
- A fresh virtual environment (conda or venv)

### 2.2 Install

```bash
# In repository root
pip install -e .

# Framework-specific deps (VeRL example)
pip install verl==0.5.0
```

### 2.3 Run the VeRL Example

```bash
cd train/examples/train_gaia_with_aworld_verl
```

1) Prepare dataset

```bash
python datasets/create_dataset.py \
  --dataset_path ${/path/to/GAIA}/2023 \
  --output_dir datasets/ \
  --train_size 300 \
  --test_size 100
```

2) Configure

- Edit configs under `train/examples/verl/configs/`
  - `agent.yaml`: Agent loop and training settings
  - `tool.yaml`: Tool environment configuration
- Export tool config path

```bash
export AGENT_TOOL_CONFIG_PATH=$(pwd)/configs/tool.yaml
```

3) Configure `scripts/run.sh` (custom section)

Set absolute path to `train/`, reward function file/name, and config paths. Example snippet:

```bash
# =================== custom ===================
path_to_train="/abs/path/to/AWorld/train"

reward_fn_name=gaia_reward_func
reward_fn_file_path=${path_to_train}/examples/train_gaia_with_aworld_verl/metrics/gaia_reward_function.py

# Agent config
agent_loop_config_path=${path_to_train}/examples/train_gaia_with_aworld_verl/configs/agent.yaml
export AGENT_TOOL_CONFIG_PATH=${path_to_train}/examples/train_gaia_with_aworld_verl/configs/tool.yaml

# Optional: enable auto_tool_choice with a dummy tool config
dummy_tool_config_path=${path_to_train}/examples/train_gaia_with_aworld_verl/configs/dummy_tool_config.yaml
```

4) Launch training

```bash
bash run.sh
```

### 2.4 Other Frameworks

This package also contains a `swift` directory with an experimental adapter and example code. Please refer to the source code under `train/frameworks/swift/` and `train/examples/swift/` to integrate with your Swift-based training workflows.

## 3. Directory Structure

```
train/
  frameworks/
    verl/
      aworld_agent_loop.py       # Core adapter bridging VeRL AgentLoop and AWorld agents
      common.py                  # Utilities for converting trajectories/messages
      README.md
    swift/
      aworld_agent_trainer.py    # Experimental Swift adapter
  examples/
    verl/
      agents/                    # Example agent code
      configs/                   # agent.yaml, tool.yaml
      datasets/                  # dataset scripts
      scripts/                   # run.sh, reward function, etc.
      README.md
    swift/
      gaia_agent_trainer.py      # Example integration with Swift
      plugin.py                  # Example plugin
  utils/                         # Shared training utilities
  README.md
```

## 4. Development

### 4.1 Add a New Framework Adapter

1) Create `train/frameworks/<framework_name>/`.
2) Implement the minimal adapter surface (e.g., loop/trainer class) that exposes a clean API to your example code.
3) Keep reusable logic in the adapter; avoid placing example-specific code here.

### 4.2 Create a New Example

1) Create `train/examples/<framework_name>/`.
2) Add `agents/`, `configs/`, `datasets/`, `scripts/` as needed.
3) Provide a minimal run script (e.g., `scripts/run.sh`).

### 4.3 Reward Function Interface

Pass the reward function via script arguments or environment variables, providing both the file path and exported function name. A minimal example:

```python
# reward.py
def my_reward_fn(data_source, solution_str, ground_truth, extra_info=None):
    # Compute and return a numeric reward
    return 0.0
```

### 4.4 Configuration Conventions

- `agent.yaml` describes agent loop/training settings for the framework
- `tool.yaml` describes tool/runtime configs; often referenced via `AGENT_TOOL_CONFIG_PATH`

## 5. Contributing

We welcome contributions! Please consider:

- Keeping adapters minimal and reusable
- Placing example-specific logic under `train/examples/`
- Adding clear docs and runnable scripts for new examples

## 6. References

- AWorld: `https://github.com/alipay/AWorld`
- VeRL: `https://github.com/OpenGVLab/VeRL`
- GAIA (dataset reference used by examples)

---

<div align="center">

**AWorld Train** — Bring your AWorld agents to your favorite training frameworks

[license-image]: https://img.shields.io/badge/License-MIT-yellow.svg
[license-url]: https://opensource.org/licenses/MIT

</div>
