# Apps

Each subdirectory contains an example provided by us. If you haven't set the LLM parameters as environment variables, please configure the AgentConfig before running.

## AgentConfig Parameters

When constructing the `AgentConfig`, you can set the parameters as follows:

- **llm_provider**: `str`  
  (Currently supports only "openai")
- **llm_model_name**: `str`  
  (Default: `"gpt-4o"`)
- **llm_temperature**: `float`  
  (Default: `1.0`)
- **llm_base_url**: `str` **(Required)**  
  (e.g., for OpenAI's official service, set to `https://api.openai.com/v1/`)
- **llm_api_key**: `str` **(Required)**  
  (Fill in with your API key)

## Writing an Agent and Tool

- **Agent**:  
  To write an agent, please refer to the [Agent README](../aworld/agents/README.md).

- **Tool in Environment**:  
  For instructions on writing a tool within the environment, please refer to the [Virtual Environments README](../aworld/virtual_environments/README.md).