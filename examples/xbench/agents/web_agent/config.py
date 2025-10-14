import os

from aworld.config import AgentConfig, ModelConfig, AgentMemoryConfig

web_agent_config = AgentConfig(
    llm_config=ModelConfig(
        llm_temperature=0.6,
        llm_model_name=os.environ.get("LLM_MODEL_NAME"),
        llm_provider=os.environ.get("LLM_PROVIDER"),
        llm_api_key=os.environ.get("LLM_API_KEY"),
        llm_base_url=os.environ.get("LLM_BASE_URL")
    ),
    memory_config=AgentMemoryConfig(history_rounds=20, enable_summary=True, summary_rounds=30,
                                    summary_context_length=64000),
    use_vision=False
)

web_mcp_servers = ["amnicontext-server", "ms-playwright", "document_server", "image_server"]
