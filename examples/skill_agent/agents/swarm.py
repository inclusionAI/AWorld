from aworld.core.agent.swarm import Swarm
from .orchestrator_agent.agent import OrchestratorAgent
from .orchestrator_agent.config import orchestrator_agent_config, orchestrator_mcp_servers
from .orchestrator_agent.prompt import orchestrator_agent_system_prompt
from ..mcp_tools.mcp_config import MCP_CONFIG

# Orchestrator Agent - responsible for task analysis and agent coordination
def build_swarm():
    orchestrator_agent = OrchestratorAgent(
        name="orchestrator_agent",
        desc="orchestrator_agent",
        conf=orchestrator_agent_config,
        system_prompt=orchestrator_agent_system_prompt,
        mcp_servers=orchestrator_mcp_servers,
        mcp_config=MCP_CONFIG,
        skill_configs={
            "planning": {
                "name": "规划能力",
                "desc": "使用todo监控任务执行进度",
                "usage": "使用todo监控任务执行进度",
                "tool_list": {
                    "amnicontext-server": ["add_todo", "get_todo"]
                }
            },
            "scratchpad": {
                "name": "文档编辑",
                "desc": "文档编辑",
                "usage": "使用文档编辑能力，记录任务执行过程中的关键信息",
                "tool_list": {
                    "amnicontext-server": ["add_knowledge", "get_knowledge", "grep_knowledge", "list_knowledge_info", "update_knowledge"]
                }
            }
        }
    )

    return Swarm(orchestrator_agent)
