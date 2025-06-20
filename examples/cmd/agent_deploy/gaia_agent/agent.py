import logging
import os
import json
from examples.gaia.gaia_agent_runner import GaiaAgentRunner

logger = logging.getLogger(__name__)


class AWorldAgent:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        path_cwd = os.path.dirname(os.path.abspath(__file__))
        mcp_path = os.path.join(path_cwd, "mcp.json")
        with open(mcp_path, "r") as f:
            self.mcp_config = json.load(f)
        os.makedirs(os.path.join(os.getcwd(), "static"), exist_ok=True)

    def get_agent_info(self):
        return {"name": "GAIA Agent", "description": "GAIA Agent is a world agent"}

    async def run(self, prompt: str):
        llm_provider = os.getenv("LLM_PROVIDER_GAIA", "openai")
        llm_model_name = os.getenv("LLM_MODEL_NAME_GAIA")
        llm_api_key = os.getenv("LLM_API_KEY_GAIA")
        llm_base_url = os.getenv("LLM_BASE_URL_GAIA")
        llm_temperature = os.getenv("LLM_TEMPERATURE_GAIA", 0.0)

        if not llm_model_name or not llm_api_key or not llm_base_url:
            raise ValueError(
                "LLM_MODEL_NAME, LLM_API_KEY, LLM_BASE_URL must be set in your envrionment variables"
            )

        runner = GaiaAgentRunner(
            llm_provider=llm_provider,
            llm_model_name=llm_model_name,
            llm_base_url=llm_base_url,
            llm_api_key=llm_api_key,
            llm_temperature=llm_temperature,
            mcp_config=self.mcp_config,
        )

        if prompt is None and request is not None:
            prompt = request.messages[-1].content

        logger.info(f">>> Gaia Agent: prompt={prompt}, runner={runner}")

        async for line in runner.run(prompt):
            logger.info(f">>> Gaia Agent Line: {line}")
            yield line
