# Configuration

Configure AWorld CLI once, then reuse the same environment across workspaces.

## Interactive Setup

Run the built-in configuration flow:

```bash
aworld-cli --config
```

## Workspace `.env`

You can also place a `.env` file in the working directory and start `aworld-cli` from the same location.

Required variables:

```bash
LLM_MODEL_NAME="your_model_name"
LLM_PROVIDER="openai"
LLM_API_KEY="your_model_api_key"
LLM_BASE_URL="your_base_url"
```

Recommended notes:

- Use a strong coding-capable model for the main developer flow.
- Keep the `.env` file in the same workspace where you run `aworld-cli`.
- If you use `aworld-cli --config`, the CLI writes the equivalent configuration for you.
