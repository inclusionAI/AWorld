# Prerequisites

## Clone Repository
- Clone the repository and switch to the main branch:

  ```bash
  git clone https://github.com/inclusionAI/AWorld.git
  cd AWorld
  ```

## Conda Environment
- Ensure Conda is installed and configured on your machine.
- Create a Conda environment specifically for GAIA:

  ```bash
  conda env create -f examples/gaia/aworld-gaia.yml
  conda activate aworld-gaia
  ```

## Python Dependencies
- Install Python dependencies:

  ```bash
  pip install "marker-pdf[full]" --no-deps
  python setup.py install
  ```

## System Tools Setup
### MacOS
- Install additional dependencies for MacOS:

  ```bash
  brew install libmagic
  brew install ffmpeg
  brew install --cask libreoffice
  ```

### Linux64
- Install additional dependencies for Linux64:

  ```bash
  apt-get install -y --no-install-recommends libmagic1 libreoffice ffmpeg
  ```

## Dataset Preparation
- Download the GAIA dataset from Hugging Face.
- Place it in the correct directory:

  ```bash
  git clone git@hf.co:datasets/gaia-benchmark/GAIA examples/gaia/GAIA
  ```

  ⚠️ **Note**: You need to configure Hugging Face SSH Keys to access the GAIA repository.
  ⚠️ **Note**: This will be the absolute path for `GAIA_DATASET_PATH` variable in the .env file.

## Environment Variable Configuration
- Create the `.env` file from the template:

  ```bash
  cp .env.template .env
  ```
- Set up your environment variables:
- Edit the `.env` file and replace all `{YOUR_CONFIG}` placeholders with your actual values.

# Running the GAIA
- Then you'll be able to taste the appetizer running the following line:
  ```bash
  python examples/gaia/run.py --split validation --q c61d22de-5f6c-4958-a7f6-5e9707bd3466
  ```

## Required Arguments (default 0:165 for validation set)
- `--split` (str)
  - **Description**: Split of the dataset, e.g., `validation`, `test`.
  - **Choices**: `validation`, `test`
  - **Default**: `validation`
  - **Example**: `--split test`
  
- `--start` (int)
  - **Description**: Start index of the dataset.
  - **Default**: `0`
  - **Example**: `--start 10`

- `--end` (int)
  - **Description**: End index of the dataset.
  - **Default**: `165`
  - **Example**: `--end 100`

## Optional Arguments

- `--q` (str)
  - **Description**: Question Index, e.g., `0-0-0-0-0`. If provided, this argument overrides the `--start` and `--end` arguments.
  - **Example**: `--q 0-0-0-0-0`

- `--skip` (flag)
  - **Description**: Skip the question if it has been processed before.
  - **Example**: `--skip`

- `--blacklist_file_path` (str)
  - **Description**: Path to the blacklist file, e.g., `blacklist.txt`.
  - **Example**: `--blacklist_file_path blacklist.txt`

## Example Commands

### Basic Usage
Process the dataset from index 10 to 50 in the validation split:

```bash
python script_name.py --start 10 --end 50 --split validation
```

### Using Question Index
Process a specific question identified by `0-0-0-0-0`:

```bash
python script_name.py --q 0-0-0-0-0
```

### Skipping Previously Processed Questions
Process the dataset from index 10 to 50, skipping any questions that have already been processed:

```bash
python script_name.py --start 10 --end 50 --skip
```

### Using a Blacklist File
Process the dataset from index 10 to 50, using a blacklist file named `blacklist.txt`:

```bash
python script_name.py --start 10 --end 50 --blacklist_file_path blacklist.txt
```

### Notes
- If using the `--q` argument, ensure the question index format is correct.
- The `--blacklist_file_path` file should contain a list of question indices or dataset indices to be skipped, one per line.


## Example Logs
- You should see logs similar to the following:
  ```
  YYYY-MM-DD HH:MM:SS - root - INFO - Agent answer: egalitarian
  YYYY-MM-DD HH:MM:SS - root - INFO - Correct answer: egalitarian
  YYYY-MM-DD HH:MM:SS - examples.gaia.utils - INFO - Evaluating egalitarian as a string.
  YYYY-MM-DD HH:MM:SS - root - INFO - Question 0 Correct!
  ```

# Troubleshooting
- For dataset access problems, verify that your Hugging Face SSH keys are correctly configured.
- Set up a pip mirror if necessary.
