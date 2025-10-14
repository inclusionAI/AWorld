from aworld.core.agent.swarm import TeamSwarm
from .choose_agent.agent import ChooseAgent
from .choose_agent.config import choose_agent_config, choose_mcp_servers
from .choose_agent.prompt import choose_agent_system_prompt
from .coding_agent.agent import CodingAgent
from .coding_agent.config import coding_agent_config, coding_mcp_servers
from .coding_agent.prompt import coding_agent_system_prompt
from .web_agent.agent import ExecutionSearchAgent
from .web_agent.config import web_agent_config, web_mcp_servers
from .web_agent.prompt import web_agent_system_prompt
from ..mcp_tools.mcp_config import MCP_CONFIG


def build_xbench_swarm():
    choose_agent = ChooseAgent(
        name="choose_agent",
        desc="choose_agent",
        conf=choose_agent_config,
        system_prompt=choose_agent_system_prompt,
        mcp_servers=choose_mcp_servers,
        mcp_config=MCP_CONFIG
    )

    web_agent = ExecutionSearchAgent(
        name="web_agent",
        desc="你是一名专业的网络浏览专家，擅长通过浏览器操作收集、整理和分析信息。你的目标是获取最全面、最详细的网页信息",
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
        desc="你是一个代码编写专家，擅长通过编写代码、执行代码等能力来完成任务的专家",
        conf=coding_agent_config,
        system_prompt=coding_agent_system_prompt,
        mcp_servers=coding_mcp_servers,
        mcp_config=MCP_CONFIG
    )

    return TeamSwarm(choose_agent, web_agent, coding_agent, max_steps=30)
