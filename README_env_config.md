# Environment configuration (.env)

You can configure AWorld-CLI by placing a `.env` file in your **working directory** (the directory from which you run `aworld-cli`). This is an alternative to using `aworld-cli --config`.

## Required variables

Create a `.env` file with the following variables (adjust values for your setup):

```bash
LLM_MODEL_NAME="your_model_name"
# Claude-Sonnet-4 or above suggested for best experience

LLM_PROVIDER="openai"

LLM_API_KEY="your_model_api_key"

LLM_BASE_URL="your_base_url"
```

- **LLM_MODEL_NAME**: The model identifier (e.g. `claude-sonnet-4`, `gpt-4o`). Claude-Sonnet-4 or above is recommended.
- **LLM_PROVIDER**: The provider name (e.g. `openai`, `anthropic`).
- **LLM_API_KEY**: Your API key for the chosen provider.
- **LLM_BASE_URL**: Base URL for the API (use the default for the provider if unsure).

After saving `.env`, run `aworld-cli` from the same directory to start. The CLI will load these settings automatically.
