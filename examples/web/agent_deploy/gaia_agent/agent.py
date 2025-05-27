import logging
import os
import json

logger = logging.getLogger(__name__)


class Agent:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        path_cwd = os.path.dirname(os.path.abspath(__file__))
        mcp_path = os.path.join(path_cwd, "mcp.json")
        with open(mcp_path, "r") as f:
            self.mcp_config = json.load(f)

    def get_agent_info(self):
        return {"name": "GAIA Agent", "description": "GAIA Agent is a world agent"}

    async def run(self, prompt: str):
        llm_provider = os.getenv("LLM_PROVIDER", "openai")
        llm_model_name = os.getenv("LLM_MODEL_NAME")
        llm_api_key = os.getenv("LLM_API_KEY")
        llm_base_url = os.getenv("LLM_BASE_URL")
        llm_temperature = os.getenv("LLM_TEMPERATURE", 0.0)

        if not llm_model_name or not llm_api_key or not llm_base_url:
            raise ValueError(
                "LLM_MODEL_NAME, LLM_API_KEY, LLM_BASE_URL must be set in your envrionment variables"
            )

        from examples.gaia.gaia_agent_runner import GaiaAgentRunner
        import asyncio

        runner = GaiaAgentRunner(
            llm_provider=llm_provider,
            llm_model_name=llm_model_name,
            llm_base_url=llm_base_url,
            llm_api_key=llm_api_key,
            llm_temperature=llm_temperature,
            mcp_config=self.mcp_config,
        )

        logger.info(f">>> Gaia Agent: prompt={prompt}, runner={runner}")

        async for line in runner.run(prompt):
            logger.info(f">>> Gaia Agent Line: {line}")
            yield line
