<div align="center">

# AWorld Train

*为使用 AWorld 构建的智能体，提供与外部 RL/训练框架对接的适配层、可运行示例与通用工具*

[![License: MIT][license-image]][license-url]

</div>

<div align="center">

[English](./README.md) | [快速开始](#-快速开始) | [开发](#-开发) | [贡献](#-贡献)

</div>

---

## 1. 概述

AWorld Train 旨在为基于 AWorld 的智能体提供统一的训练运行方式。它包含框架适配器（如 VeRL）、可直接运行的示例以及共享工具，帮助你快速在不同训练生态上启动训练流程。

### 1.1 特性

- **多框架适配**：将 AWorld 智能体对接到外部框架（如 VeRL），也可按统一模式扩展更多框架。
- **可运行示例**：`train/examples/` 下提供端到端示例，可即刻开跑。
- **配置驱动**：标准化的智能体/工具配置与脚本入口，便于复现实验。
- **数据集工具**：内置脚本用于构建训练/评测数据集（示例包含 GAIA 数据集处理）。
- **奖励函数挂钩**：可通过文件路径与函数名注入自定义奖励函数。
- **可扩展性**：本地单机可用；分布式能力依赖所选训练框架。

## 2. 快速开始

推荐从 VeRL 示例入手。

### 2.1 先决条件

- 建议 Python 3.10+
- 使用 conda 或 venv 创建全新虚拟环境

### 2.2 安装

#### 2.2.1 安装 MCP 环境（VirtualPC MCP Server）

步骤 1：配置环境

```bash
cd {path/to/AWorld}/env
cp ./gaia-mcp-server/mcp_servers/.env_template ./gaia-mcp-server/mcp_servers/.env
```

步骤 2：本地启动

```bash
sh run-local.sh
```

如需 Kubernetes 集群部署，请参考 [env/README.md § 2.2 Kubernetes Cluster Deployment](../env/README.md#22-kubernetes-cluster-deployment)。

#### 2.2.2 安装 Python 依赖

```bash
# 安装 AWorld
pip install aworld

# 按需安装框架依赖（以 VeRL 示例为例）
pip install verl==0.5.0
```

### 2.3 运行 VeRL 示例

参考示例文档 [VeRL 示例 README](./examples/train_gaia_with_aworld_verl/README_zh.md)

## 3. 目录结构

```
train/
  adapter/
    verl/
      aworld_agent_loop.py       # VeRL AgentLoop 与 AWorld 智能体的桥接
      common.py                  # 轨迹/消息到 VeRL 输出的转换工具
      README.md
    swift/
      aworld_agent_trainer.py    # Swift 适配（实验性）
  examples/
    train_gaia_with_aworld_verl/
      agent.yaml                 # 示例智能体 loop 与训练配置
      configs/
        tool.yaml                # 工具/运行时配置
      datasets/
        create_dataset.py        # GAIA 数据集准备脚本
      metrics/
        gaia_reward_function.py  # 示例奖励函数
      run.sh                     # 示例启动脚本
      README.md                  # 示例英文文档
      README_zh.md               # 示例中文文档
    train_gaia_with_aworld_swift/
      gaia_agent_trainer.py      # Swift 示例整合
      plugin.py                  # 示例插件
  README.md
  README_zh.md
```

## 4. 开发

### 4.1 新增框架适配器

1) 创建 `train/adapter/<framework_name>/`。
2) 实现最小适配面（如 loop/trainer 类），对外暴露清晰 API 供示例调用。
3) 可复用逻辑放在适配层，示例特定逻辑放在 `train/examples/`。

### 4.2 新增示例

1) 创建 `train/examples/<your_example_name>/`。
2) 按需新增 `configs/`、`datasets/`、`metrics/`，并提供最小可运行 `run.sh`。
3) 建议脚本中使用绝对路径，便于复现实验。

### 4.3 奖励函数接口

通过脚本参数或环境变量传入奖励函数的文件路径与函数名。例如：

```python
# reward.py
def my_reward_fn(data_source, solution_str, ground_truth, extra_info=None):
    # 返回数值型奖励
    return 0.0
```

### 4.4 配置约定

- `agent.yaml`：描述该示例的智能体 loop/训练设置
- `tool.yaml`：描述工具/运行时配置；常通过 `AGENT_TOOL_CONFIG_PATH` 引用

## 5. 贡献

欢迎贡献！建议：

- 让适配层最小、可复用
- 示例特定逻辑放在 `train/examples/`
- 为新示例补充清晰文档与可运行脚本

## 6. 参考

- AWorld：`https://github.com/alipay/AWorld`
- VeRL：`https://github.com/OpenGVLab/VeRL`
- GAIA（示例所用的数据集参考）

---

<div align="center">

**AWorld Train** — 让你的 AWorld 智能体快速接入主流训练框架

[license-image]: https://img.shields.io/badge/License-MIT-yellow.svg
[license-url]: https://opensource.org/licenses/MIT

</div>


