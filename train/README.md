<div align="center">

# AWorld Train

*Framework-agnostic training adapters, examples, and utilities for training AWorld agents with external RL/training frameworks*

[![License: MIT][license-image]][license-url]

</div>

<div align="center">

[中文版本](./README_zh.md) | [Quick Start](#quick-start) | [Directory](#-directory-structure) | [Development](#development) | [Contributing](#contributing)

</div>

---

## 1. Overview

AWorld Train provides a unified way to run training pipelines for agents built with the AWorld framework. It includes framework adapters (e.g., VeRL), runnable examples, and shared utilities so you can quickly bring your agent into different training ecosystems.

### 1.1 Features

- **Multi-Framework Adapters**: Plug AWorld agents into external frameworks (e.g., VeRL). More can be added via a simple adapter pattern.
- **Runnable Examples**: End-to-end examples under `train/examples/` to start training immediately.
- **Dataset Utilities**: Helper scripts to build datasets for training/evaluation (e.g., GAIA dataset preparation for VeRL example).
- **Reward Function Hooks**: Easily plug in a custom reward function via file path and function name.
- **Scalable**: Works on single machine; distributed capabilities depend on the chosen framework.

## 2. Quick Start

We recommend starting with the VeRL example.

### 2.1 Prerequisites

- Python 3.10+ (recommended)
- A fresh virtual environment (conda or venv)

### 2.2 Install

#### 2.2.1 Install MCP env (VirtualPC MCP Server)

Step 1: Configure environment

```bash
cd {path/to/AWorld}/env
cp ./gaia-mcp-server/mcp_servers/.env_template ./gaia-mcp-server/mcp_servers/.env
```

Step 2: Launch locally

```bash
sh run-local.sh
```

For Kubernetes deployment, see [env/README.md § 2.2 Kubernetes Cluster Deployment](../env/README.md#22-kubernetes-cluster-deployment).

#### 2.2.2 Install Python packages

```bash
# Install Aworld
pip install aworld

# Framework-specific deps (VeRL example)
pip install verl==0.5.0
```

### 2.3 Run the VeRL Example
Refer to the documentation [VeRL example README](./examples/train_gaia_with_aworld_verl/README.md)


## 3. Directory Structure

```
train/
  adapter/
    verl/
      aworld_agent_loop.py       # Core adapter bridging VeRL AgentLoop and AWorld agents
      common.py                  # Utilities for converting trajectories/messages
      README.md
    swift/
      aworld_agent_trainer.py    # Experimental Swift adapter
  examples/
    train_gaia_with_aworld_verl/
      agent.yaml                 # Example agent loop and training settings
      configs/
        tool.yaml                # Tool environment configuration
      datasets/
        create_dataset.py        # GAIA dataset preparation utility
      metrics/
        gaia_reward_function.py  # Example reward function
      run.sh                     # Example launch script
      README.md                  # Example-specific docs
      README_zh.md
    train_gaia_with_aworld_swift/
      gaia_agent_trainer.py      # Example integration with Swift
      plugin.py                  # Example plugin
  README.md
  README_zh.md
```

## 4. Development

### 4.1 Add a New Framework Adapter

1) Create `train/adapter/<framework_name>/`.
2) Implement the minimal adapter surface (e.g., loop/trainer class) that exposes a clean API to your example code.
3) Keep reusable logic in the adapter; avoid placing example-specific code here.

### 4.2 Create a New Example

1) Create `train/examples/<your_example_name>/`.
2) Add `configs/`, `datasets/`, `metrics/`, and a minimal `run.sh` as needed.
3) Prefer absolute paths in scripts for reproducibility.

### 4.3 Reward Function Interface

Pass the reward function via script arguments or environment variables, providing both the file path and exported function name. A minimal example:

```python
# reward.py
def my_reward_fn(data_source, solution_str, ground_truth, extra_info=None):
    # Compute and return a numeric reward
    return 0.0
```

### 4.4 Configuration Conventions

- `agent.yaml` describes agent loop/training settings for the framework/example
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
