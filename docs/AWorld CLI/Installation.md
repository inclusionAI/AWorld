# Installation

Install AWorld and AWorld CLI into the same environment, then launch the CLI from your working directory.

## Install From Source

```bash
git clone https://github.com/inclusionAI/AWorld && cd AWorld

conda create -n aworld_env python=3.11 -y
conda activate aworld_env

pip install -e .
cd aworld-cli
pip install -e .
```

## Next Step

After installation, continue with [Configuration](Configuration.md).
