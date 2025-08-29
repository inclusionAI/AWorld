# 示例：AWorld GAIA Agent + VeRL

[English](./README.md)

## 安装
1) 准备环境 \
    建议使用 conda 或 venv 创建全新虚拟环境。 \
    推荐 Python 3.10。
2) 安装 AWorld
    ```bash
    pip install aworld
    ```
3) 安装 VeRL
    ```bash
    pip install verl==0.5.0
    ```

## 快速开始
```bash
cd train/examples/verl
```
1) 准备数据集：
```bash
python datasets/create_dataset.py \
  --dataset_path ${/path/to/GAIA}/2023 \
  --output_dir datasets/ \
  --train_size 300 \
  --test_size 100
```
2) 编辑 `train/examples/verl/configs/` 下的配置。
    - `os.environ["AGENT_TOOL_CONFIG_PATH"]`：智能体工具配置文件路径
    - `agent.yaml`：指定训练所用的智能体
    - `tool.yaml`：工具与环境配置

### 配置 `scripts/run.sh`（自定义参数设置）

在运行训练前，先根据需要修改 `train/examples/verl/scripts/run.sh` 中的 `custom` 区域：
- **path_to_train**：设置为你本地 `AWorld/train` 的绝对路径。
- **reward_fn_file_path** 与 **reward_fn_name**：指向你的奖励函数文件与导出函数名。例如如果在 `gaia_reward_function.py` 中实现了 `gaia_reward_func`，则按需设置这两项。
- **agent_loop_config_path** 与 **AGENT_TOOL_CONFIG_PATH**：分别为智能体 loop 配置（`agent.yaml`）与工具配置（`tool.yaml`）的路径。注意 `AGENT_TOOL_CONFIG_PATH` 需要通过环境变量导出。
- **dummy_tool_config_path**（可选）：如需启用自动工具选择（auto_tool_choice）可设置该路径。

示例片段：
```bash
# =================== custom ===================
path_to_train="/abs/path/to/AWorld/train"

reward_fn_name=gaia_reward_func
reward_fn_file_path=${path_to_train}/examples/verl/scripts/gaia_reward_function.py

# Agent config
agent_loop_config_path=${path_to_train}/examples/verl/configs/agent.yaml
export AGENT_TOOL_CONFIG_PATH=${path_to_train}/examples/verl/configs/tool.yaml

# Optional: enable auto_tool_choice with a dummy tool config
dummy_tool_config_path=${path_to_train}/examples/verl/configs/dummy_tool_config.yaml
```

> 可选：若你自定义奖励函数，可采用如下函数签名（与训练管线兼容）：
```python
def my_reward_fn(data_source, solution_str, ground_truth, extra_info=None):
    # 返回一个数值型奖励
    return 0.0
```

3) 运行训练：
```bash
bash scripts/run.sh
```


