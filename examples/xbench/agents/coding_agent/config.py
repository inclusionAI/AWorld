import os

from aworld.config import AgentConfig, ModelConfig, AgentMemoryConfig

coding_agent_config = AgentConfig(
    llm_config=ModelConfig(
        llm_temperature=0.1,
        llm_model_name=os.environ.get("LLM_MODEL_NAME"),
        llm_provider=os.environ.get("LLM_PROVIDER"),
        llm_api_key=os.environ.get("LLM_API_KEY"),
        llm_base_url=os.environ.get("LLM_BASE_URL")
    ),
    memory_config=AgentMemoryConfig(history_rounds=20, enable_summary=True, summary_rounds=20,
                                    summary_context_length=40000),
    use_vision=False
)
coding_mcp_servers = ["terminal-server", "ms-playwright", "amnicontext-server"]
