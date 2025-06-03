# Running AWorld Agent

## Prerequisites

1. **Conda Environment**
   - Ensure Conda is installed and configured on your machine
   - Create a Conda environment:
     ```bash
     conda create -n aworld python=3.11
     conda activate aworld
     ```

2. **Clone Repository**
   - Clone the repository and switch to the main branch:
     ```bash
     git clone https://github.com/inclusionAI/AWorld
     cd AWorld
     ```

3. **Dataset Preparation**
   - Download the GAIA dataset from [Hugging Face](https://huggingface.co/datasets/gaia-benchmark/GAIA)
   - Place it in the correct directory:
     ```bash
     git clone git@hf.co:datasets/gaia-benchmark/GAIA examples/gaia/GAIA
     ```
   - ⚠️ **Note**: You need to configure [Hugging Face SSH Keys](https://huggingface.co/settings/keys) to access the GAIA repository

4. **API Configuration**
   - Set up your environment variables:
     ```bash
     cp .env.template .env
     ```
   - Edit the `.env` file and replace all `{YOUR_CONFIG}` placeholders with your actual values

## Running the Agent

1. **Start the Web Server**
   - Build and run the AWorld Agent:
     ```bash
     sh run-server.sh
     ```
   - Wait for the installation to complete

2. **Access the Interface**
   - Open your browser and navigate to [http://127.0.0.1:8000](http://127.0.0.1:8000)

## Troubleshooting

   - For dataset access problems, verify that your Hugging Face SSH keys are correctly configured
   - Set up a pip mirror if necessary

## Develop Your Own Agent

   - Copy `examples/web/agent_deploy/weather_agent` to `examples/web/agent_deploy/{YOUR_AGENT_NAME}`
   - Write your code in `examples/web/agent_deploy/{YOUR_AGENT_NAME}/agent.py`
   - Configure the MCP Server in `examples/web/agent_deploy/{YOUR_AGENT_NAME}/mcp.json`

## Additional Resources

   - [GAIA Benchmark Documentation](https://huggingface.co/datasets/gaia-benchmark/GAIA)
   - [Hugging Face SSH Keys Setup Guide](https://huggingface.co/settings/keys)
