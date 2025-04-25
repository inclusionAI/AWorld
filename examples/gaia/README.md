# Running Gaia Benchmark

AWorld is an AI-powered simulation environment for creating and interacting with virtual worlds.

You could follow the following steps to run the Gaia benchmark.

## Prerequisites

- Git
- Conda (Miniconda or Anaconda)
- Python 3.11
- Node.js

## Installation

### 1. Clone the repository:
```bash
git clone https://github.com/inclusionAI/AWorld.git
cd AWorld
```

### 2. Create and activate a Conda environment:
```bash
conda create --name aworld python=3.11
conda activate aworld
```

### 3. Setup the environment:
```bash
python setup.py install

git clone https://github.com/haris-musa/excel-mcp-server.git
cd excel-mcp-server
uv pip install -e .
```

### 4. Download GAIA Dataset
https://huggingface.co/datasets/gaia-benchmark/GAIA/tree/main

## Configuration

### Create a `.env` file in the project root and add your API credentials:
```bash
GAIA_DATASET_PATH=<Your Dataset Absolute Path>
LLM_API_KEY=<Your API Key>
LLM_BASE_URL=<Your Service Provider URL>
...
```

## Running the Application

1. Start the local MCP servers
```bash
uv run excel-mcp-server
```
The server should now be running with the configuration specified in `mcp.json`.

1. Run the script
```bash
python ./examples/gaia/run.py
```
Now you could check the output log in the console.

## Troubleshooting

If you encounter issues:

- Verify all environment variables are set correctly
- Check that all dependencies are installed
- Ensure the dataset path is correctly configured
- Check the server logs for any error messages

## Support
For additional help, please [open an issue](https://github.com/inclusionAI/AWorld/issues/new) on our GitHub repository.
