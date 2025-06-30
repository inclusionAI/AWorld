import logging
import os
import json
from aworld.cmd import BaseAWorldAgent, ChatCompletionRequest
from aworld.config.conf import AgentConfig, TaskConfig
from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import Swarm
from aworld.core.task import Task
from aworld.runner import Runners
from .prompt import *

logger = logging.getLogger(__name__)


class AWorldAgent(BaseAWorldAgent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def name(self):
        return "Team Agent"

    def description(self):
        return "Team Agent with fetch and time mcp server"

    async def run(self, prompt: str = None, request: ChatCompletionRequest = None):
        llm_provider = os.getenv("LLM_PROVIDER_TEAM", "openai")
        llm_model_name = os.getenv("LLM_MODEL_NAME_TEAM")
        llm_api_key = os.getenv("LLM_API_KEY_TEAM")
        llm_base_url = os.getenv("LLM_BASE_URL_TEAM")
        llm_temperature = os.getenv("LLM_TEMPERATURE_TEAM", 0.0)

        if not llm_model_name or not llm_api_key or not llm_base_url:
            raise ValueError(
                "LLM_MODEL_NAME, LLM_API_KEY, LLM_BASE_URL must be set in your envrionment variables"
            )

        agent_config = AgentConfig(
            llm_provider=llm_provider,
            llm_model_name=llm_model_name,
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            llm_temperature=llm_temperature,
        )

        path_cwd = os.path.dirname(os.path.abspath(__file__))
        mcp_path = os.path.join(path_cwd, "mcp.json")
        with open(mcp_path, "r") as f:
            mcp_config = json.load(f)

        google_pse_search_agent = Agent(
            conf=agent_config,
            name="🔎 Team Search Agent",
            system_prompt=search_sys_prompt,
            agent_prompt=search_agent_prompt,
            mcp_config=mcp_config,
            mcp_servers=["google-pse-search"],
        )

        aworldsearch_server_agent = Agent(
            conf=agent_config,
            name="🔎 Team Aworldsearch Server Agent",
            system_prompt=search_sys_prompt,
            agent_prompt=search_agent_prompt,
            mcp_config=mcp_config,
            mcp_servers=["aworldsearch-server"],
        )

        aworld_playwright_agent = Agent(
            conf=agent_config,
            name="🔎 Team Aworld Playwright Agent",
            system_prompt=search_sys_prompt,
            agent_prompt=search_agent_prompt,
            mcp_config=mcp_config,
            mcp_servers=["aworld-playwright"],
        )

        summary_agent = Agent(
            conf=agent_config,
            name="💬 Team Summary Agent",
            system_prompt=summary_sys_prompt,
            agent_prompt=summary_agent_prompt,
        )

        # default is sequence swarm mode
        swarm = Swarm(
            google_pse_search_agent,
            aworldsearch_server_agent,
            summary_agent,
            max_steps=10,
        )

        if prompt is None and request is not None:
            prompt = request.messages[-1].content

        task = Task(
            input=prompt,
            swarm=swarm,
            conf=TaskConfig(max_steps=20),
        )

        async for output in Runners.streamed_run_task(task).stream_events():
            logger.info(f"Agent Ouput: {output}")
            yield output
