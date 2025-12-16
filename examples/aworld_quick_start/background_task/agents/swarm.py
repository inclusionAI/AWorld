import os

from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig, ModelConfig
from aworld.core.agent.swarm import Swarm, TeamSwarm
from .dayilreminder.prompt import daily_reminder_agent_system_prompt
from .orchestrator_agent.config import orchestrator_agent_config
from .orchestrator_agent.prompt import orchestrator_agent_system_prompt
from ..mcp_tools.mcp_config import MCP_CONFIG


def build_swarm():
    orchestrator_agent = Agent(
        name="orchestrator_agent",
        desc="orchestrator_agent",
        conf=orchestrator_agent_config,
        system_prompt=orchestrator_agent_system_prompt,
        mcp_config=MCP_CONFIG,
    )

    daily_reminder_agent = Agent(
        name="daily_reminder_agent",
        desc="daily_reminder_agent",
        conf=AgentConfig(
            llm_config=ModelConfig(
                llm_temperature=0.,
                llm_model_name=os.environ.get("LLM_MODEL_NAME"),
                llm_provider=os.environ.get("LLM_PROVIDER"),
                llm_api_key=os.environ.get("LLM_API_KEY"),
                llm_base_url=os.environ.get("LLM_BASE_URL")
            )
        ),
        mcp_servers=['filesystem-server'],
        system_prompt=daily_reminder_agent_system_prompt,
        mcp_config=MCP_CONFIG,
    )

    return TeamSwarm(orchestrator_agent, daily_reminder_agent)
