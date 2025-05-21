# Running GAIA Benchmark in Docker

## Prerequisites

1. **Docker Installation**
   - Ensure Docker is installed and running on your machine
   - Verify installation:
     ```bash
     docker ps
     ```

2. **Repository Setup**
   - Clone the repository and switch to the benchmark branch:
     ```bash
     git clone https://github.com/inclusionAI/AWorld
     cd AWorld
     git checkout gaia-benchmark
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
     cp .env.template .env.docker
     ```
   - Edit `.env.docker` file and replace all `{YOUR_CONFIG}` placeholders with your actual values

## Running the Benchmark

1. **Start the Container**
   - Build and run the GAIA container:
     ```bash
     sh run-gaia.sh
     ```
   - Wait for the build to complete and the container to start

2. **Access the Interface**
   - Open your browser and navigate to [http://127.0.0.1:8080](http://127.0.0.1:8080)
   - Register a new account
   - Log in to your account

3. **Run the Benchmark**
   - Select `gaia_agent` from the top menu
   - Choose a GAIA question from the list
   - Click the send button to start the benchmark

## Troubleshooting

- If you encounter any issues with Docker, ensure it's running properly
- For dataset access problems, verify your Hugging Face SSH keys are correctly configured
- Check the container logs if the interface is not accessible

## Additional Resources

- [GAIA Benchmark Documentation](https://huggingface.co/datasets/gaia-benchmark/GAIA)
- [Hugging Face SSH Keys Setup Guide](https://huggingface.co/settings/keys)