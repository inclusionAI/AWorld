# BFCL Multi-Agent Examples

This directory contains examples demonstrating how to use AWorld framework with the Berkeley Function Calling Leaderboard (BFCL) for both single-agent and multi-agent function calling evaluations.

## Table of Contents

- [BFCL Multi-Agent Examples](#bfcl-multi-agent-examples)
  - [Table of Contents](#table-of-contents)
  - [Overview](#overview)
    - [Key Features](#key-features)
  - [Installation](#installation)
    - [Prerequisites](#prerequisites)
  - [Environment Setup](#environment-setup)
    - [Required Environment Variables](#required-environment-variables)
    - [Setup Steps](#setup-steps)
  - [Examples](#examples)
    - [Single Agent BFCL Handler](#single-agent-bfcl-handler)
    - [Multi-Agent BFCL Handler](#multi-agent-bfcl-handler)
      - [Key Features:](#key-features-1)
      - [Architecture:](#architecture)
  - [Usage](#usage)
    - [Running BFCL Evaluations](#running-bfcl-evaluations)
    - [Configuration Options](#configuration-options)
  - [Contributing](#contributing)
  - [Additional Resources](#additional-resources)

## Overview

The Berkeley Function Calling Leaderboard (BFCL) is a comprehensive evaluation framework for assessing Large Language Models' ability to invoke functions. This directory provides AWorld-based implementations that can be used with BFCL for both single-agent and multi-agent scenarios.

### Key Features

- **Single Agent Implementation**: Traditional function calling with a single LLM agent
- **Multi-Agent Implementation**: Collaborative function calling using multiple specialized agents
- **AWorld Integration**: Leverages AWorld's agent framework and swarm capabilities
- **BFCL Compatibility**: Fully compatible with the Berkeley Function Calling Leaderboard evaluation framework

## Installation

### Prerequisites

- Python 3.11+
- AWorld framework installed
- Prepare basic BFCL environment variables following the BFCL installation guidance: [BFCL README](gorilla/berkeley-function-call-leaderboard/README.md)
- OpenRouter API key (for model access)

## Environment Setup

The `init_env.sh` script sets up the necessary environment variables:

```bash
export BFCL_PROJECT_ROOT=/path/to/berkeley-function-call-leaderboard
export OPENROUTER_API_KEY=your_api_key_here
export AGI_API_KEY=****
export AGI_BASE_URL=*****
cp $(python -c "import bfcl_eval, pathlib; print(pathlib.Path(bfcl_eval.__path__[0]) / 'test_case_ids_to_generate.json.example')") $BFCL_PROJECT_ROOT/test_case_ids_to_generate.json
```

### Required Environment Variables

- `BFCL_PROJECT_ROOT`: Path to the BFCL project root directory
- `OPENROUTER_API_KEY`: Your OpenRouter API key for accessing LLM models
- You may need AGI related environment variables if you want to use models need .

### Setup Steps

1. **Install BFCL package**:
   ```bash
   pip install -e .
   ```

2. **Set up environment variables**:
   ```bash
   source init_env.sh
   ```


## Examples

### Single Agent BFCL Handler

**File**: `gorilla/berkeley-function-call-leaderboard/bfcl_eval/model_handler/api_inference/aworld.py`

This implementation uses a single AWorld agent to handle function calling tasks.


### Multi-Agent BFCL Handler

**File**: `gorilla/berkeley-function-call-leaderboard/bfcl_eval/model_handler/api_inference/aworld_multi_agent.py`

This implementation uses multiple AWorld agents working together in a swarm configuration for enhanced function calling capabilities.

#### Key Features:
- **Swarm Architecture**: Multiple agents collaborate on function calling tasks
- **Verification Agent**: Dedicated agent for validating function calls
- **Enhanced Prompting**: Specialized prompts for multi-agent coordination
- **State Management**: Maintains conversation state across multiple agents

#### Architecture:
1. **Execute Agent**: Primary agent responsible for generating function calls
2. **Verify Agent**: Secondary agent that validates and suggests improvements
3. **Swarm Coordinator**: Manages communication and coordination between agents


## Usage

### Running BFCL Evaluations

1. **Generate LLM Responses**:
   ```bash
   cd gorilla/berkeley-function-call-leaderboard
   python -m bfcl_eval --model aworld --test-category all
   ```

or you can use the following script
```
#!/bin/bash
set -e

TARGET_DIR=xxx
MODEL_NAME="AworldLocal/SingleAgent[xlam-lp-70b]"
# TEST_CATEGORY="multi_turn_long_context"
TEST_CATEGORY="multi_turn_miss_func"

NUM_PROCESSES=3
LOG_DIR=xxx
LOG_FILE="${LOG_DIR}/xlam_single_agent.log"

cd "$TARGET_DIR"

mkdir -p "$LOG_DIR"

echo "Starting evaluation for model: ${MODEL_NAME}"
echo "Logging output to: ${LOG_FILE}"


python bfcl_eval_mp.py \
    --model "$MODEL_NAME" \
    --test-category "$TEST_CATEGORY" \
    --num-processes $NUM_PROCESSES 2>&1 | tee "$LOG_FILE"

echo "Evaluation finished."
``` 

2. **Evaluate Generated Responses**:
   ```
   cd AWorld/examples/bfcl_multi_agents/gorilla/berkeley-function-call-leaderboard/bfcl_eval/
   python __main__.py evaluate
   ```

### Configuration Options

- `--model`: Specify the model handler (aworld or aworld_multi_agent)
- `--test-category`: Choose evaluation categories (all, single_turn, multi_turn, etc.)
- `--run-ids`: Run specific test cases
- `--evaluate`: Switch to evaluation mode


## Contributing

To add new models or modify existing implementations:

1. Create a new handler in `api_inference/` directory
2. Inherit from `BaseHandler` class
3. Implement required methods for your use case
4. Update configuration files as needed

## Additional Resources
- more information about BFCL installation please refer to [BFCL README](gorilla/berkeley-function-call-leaderboard/README.md)
- [BFCL Documentation](https://gorilla.cs.berkeley.edu/leaderboard)
- [Gorilla Project](https://github.com/ShishirPatil/gorilla)
- [Berkeley Function Calling Leaderboard](https://gorilla.cs.berkeley.edu/leaderboard.html)