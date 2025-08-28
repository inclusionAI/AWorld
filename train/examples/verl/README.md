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
    pip install verl==0.5.0
    ```

## Quick Start
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
    - `os.environ["AGENT_TOOL_CONFIG_PATH"]`: Filepath for agent tools config
    - `agent.yaml`: Specify training agent
    - `tool.yaml`: Tool environment configuration
3) Run training:
```bash
bash scripts/run.sh
```
