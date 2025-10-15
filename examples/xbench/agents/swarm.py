from aworld.core.agent.swarm import TeamSwarm
from .orchestrator_agent.agent import OrchestratorAgent
from .orchestrator_agent.config import orchestrator_agent_config, orchestrator_mcp_servers
from .orchestrator_agent.prompt import orchestrator_agent_system_prompt
from .coding_agent.agent import CodingAgent
from .coding_agent.config import coding_agent_config, coding_mcp_servers
from .coding_agent.prompt import coding_agent_system_prompt
from .web_agent.agent import WebAgent
from .web_agent.config import web_agent_config, web_mcp_servers
from .web_agent.prompt import web_agent_system_prompt
from ..mcp_tools.mcp_config import MCP_CONFIG

# Orchestrator Agent - responsible for task analysis and agent coordination
def build_xbench_swarm():
    orchestrator_agent = OrchestratorAgent(
        name="orchestrator_agent",
        desc="orchestrator_agent",
        conf=orchestrator_agent_config,
        system_prompt=orchestrator_agent_system_prompt,
        mcp_servers=orchestrator_mcp_servers,
        mcp_config=MCP_CONFIG
    )   

    web_agent = WebAgent(
        name="web_agent",
        desc="You are a professional web browsing expert, skilled in collecting, organizing, and analyzing information through browser operations. Your goal is to obtain the most comprehensive and detailed web information",
        conf=web_agent_config,
        system_prompt=web_agent_system_prompt,
        mcp_servers=web_mcp_servers,
        mcp_config=MCP_CONFIG,
        black_tool_actions={
            "document_server": [
                "mcpreadtext",
                "mcpreadjson",
                "mcpreadexcel",
                "mcpreadhtmltext",
                "mcpreadxml"
            ]
        }
    )

    coding_agent = CodingAgent(
        name="coding_agent",
        desc="You are a coding expert, skilled in using coding, executing code, and other abilities to complete tasks",
        conf=coding_agent_config,
        system_prompt=coding_agent_system_prompt,
        mcp_servers=coding_mcp_servers,
        mcp_config=MCP_CONFIG
    )

    return TeamSwarm(orchestrator_agent, web_agent, coding_agent, max_steps=30)
