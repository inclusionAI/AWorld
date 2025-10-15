import os

from aworld.config import AgentConfig, ModelConfig

orchestrator_agent_config = AgentConfig(
    llm_config=ModelConfig(
        llm_temperature=0.1,
        llm_model_name=os.environ.get("LLM_MODEL_NAME"),
        llm_provider=os.environ.get("LLM_PROVIDER"),
        llm_api_key=os.environ.get("LLM_API_KEY"),
        llm_base_url=os.environ.get("LLM_BASE_URL")
    ),
    use_vision=False
)

orchestrator_mcp_servers = ["amnicontext-server"]
