import os
from aworld.config.conf import AgentConfig
    
def get_agent_config():
    openrouter_conf = AgentConfig(
        llm_provider="openai",
        llm_model_name=os.getenv('LLM_MODEL_NAME'),
        llm_api_key=os.getenv('OPENROUTER_KEY'),
        llm_base_url=os.getenv('LLM_BASE_URL'),
        llm_temperature=0.0,
    )

    return openrouter_conf