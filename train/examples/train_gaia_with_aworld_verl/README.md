# Example: AWorld GAIA Agent + VeRL

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
    pip install train_gaia_with_aworld_verl==0.5.0
    ```

## Quick Start
```bash
cd train/examples/train_gaia_with_aworld_verl
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
    - `os.environ["AGENT_TOOL_CONFIG_PATH"]`: Filepath for agent tools config
    - `agent.yaml`: Specify training agent
    - `tool.yaml`: Tool environment configuration
   
### Configure `scripts/run.sh` (custom section)

Before running training, customize the `custom` section inside `train/examples/verl/scripts/run.sh`:
- **path_to_train**: Set this to the absolute path of your local `AWorld/train` directory.
- **reward_fn_file_path** and **reward_fn_name**: Point to your own reward function file and the exported function name. For example, if you implement `gaia_reward_func` in `my_reward_function.py`, set them accordingly.
- **agent_loop_config_path** and **AGENT_TOOL_CONFIG_PATH**: Provide the paths to your agent loop config (`agent.yaml`) and tool config (`tool.yaml`). Note that `AGENT_TOOL_CONFIG_PATH` is exported as an environment variable.
- **dummy_tool_config_path** (optional): Set to enable auto tool choice (auto_tool_choice).

Example snippet:
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
3) Run training:
```bash
bash run.sh
```
