# AWorld Train

This module helps you build reinforcement learning (RL) training pipelines for agents developed with the AWorld agent framework, and adapt those agents to different RL frameworks. It provides:
- framework-specific integrations that adapt AWorld agents to external RL frameworks;
- framework-agnostic utilities reusable across integrations;
- runnable examples that show end-to-end training with AWorld + a chosen RL framework.

## Directory Structure

```
train/
  frameworks/
    <framework-name>/            # e.g., verl
      core/                      # adaptation code (inheritance or extensions as needed)
      utils/                     # utilities specific to this framework
  utils/                         # framework-agnostic utilities (e.g., data processing, networking)
  examples/
    <framework-name>/            # e.g., verl
      agents/                    # agent implementation with AWorld
      configs/                   # training configs (e.g., agent.yaml)
      datasets/                  # datasets used for training/evaluation
      scripts/                   # run scripts, entry points, helpers
```

### Notes
- `frameworks/`: Contains the adaptation layer required to integrate AWorld agents with each RL framework. Each subdirectory is named after the RL framework project and contains `core/` (adaptation and extension code) and `utils/` (helpers specific to that framework).
- `utils/`: Contains framework-agnostic, reusable utilities. Consider placing common data processing, logging, or network utilities here.
- `examples/`: For each RL framework, provides a minimal end-to-end example showing how to develop an AWorld agent and train it within that framework. Subdirectories are named by framework and include `agents/`, `configs/`, `datasets/`, and `scripts/`.

## How to Run an RL Example (High-Level Steps)
1) Install the agent development framework (e.g., AWorld).
   - Example: `pip install -e .` at the repository root or `pip install aworld` if using a published package.
2) Install the RL framework (e.g., VeRL).
   - Example: `pip install verl` (pin a specific version if required).
3) Prepare the dataset.
   - Download or generate the dataset expected by the example under `train/examples/<framework>/datasets/`.
4) Prepare the agent and environment/tool configurations.
   - Edit files under `train/examples/<framework>/configs/` (e.g., `agent.yaml`, tool/environment configs).
5) Write or use a training script to launch training.
   - Use or modify scripts under `train/examples/<framework>/scripts/` to start the training run.

## Reference Example
For reference, see the AWorld + VeRL AgentLoop example under `train/examples/verl/`, which demonstrates how to develop an AWorld agent and integrate it into VeRL AgentLoop.
