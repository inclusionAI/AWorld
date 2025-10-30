from aworld.core.agent.swarm import Swarm
from .orchestrator_agent.agent import OrchestratorAgent
from .orchestrator_agent.config import orchestrator_agent_config
from .orchestrator_agent.prompt import orchestrator_agent_system_prompt
from ..mcp_tools.mcp_config import MCP_CONFIG

# Orchestrator Agent - responsible for task analysis and agent coordination
def build_swarm():
    orchestrator_agent = OrchestratorAgent(
        name="orchestrator_agent",
        desc="orchestrator_agent",
        conf=orchestrator_agent_config,
        system_prompt=orchestrator_agent_system_prompt,
        mcp_config=MCP_CONFIG,
    )

    return Swarm(orchestrator_agent)
