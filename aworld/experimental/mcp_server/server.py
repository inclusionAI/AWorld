import json
from pydantic import create_model
from mcp.server.fastmcp import FastMCP
from mcp.types import Tool as MCPTool, ContentBlock, TextContent
from typing import Any, Sequence

from aworld.core.agent.agent_desc import get_agent_desc
from aworld.config.conf import TaskConfig
from aworld.utils.run_util import exec_agent
from aworld.core.agent.base import AgentFactory
from aworld.core.context.base import Context
from aworld.core.task import TaskResponse
from aworld.core.agent.base import BaseAgent
from aworld.core.agent.swarm import Swarm


class AgentMCP(FastMCP):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._input_schema_cache = self._agent_exec_input_schema()
        self._tool_name_prefix = "_aworld_agent_"

    def _agent_exec_input_schema(self) -> dict:
        AgentExecModel = create_model(
            'AgentExecModel',
            question=(Any, None),
            task_conf=(TaskConfig, None)
        )
        input_schema = AgentExecModel.model_json_schema()
        input_schema['required'] = ['question']
        return input_schema

    def _mcp_error(self, error_msg: str) -> Sequence[ContentBlock]:
        return [TextContent(type="text", text=json.dumps({"error": error_msg}))]

    async def list_tools(self) -> list[MCPTool]:
        tool_list = await super().list_tools()
        agent_desc = get_agent_desc()
        for agent_name, agent_val_dict in agent_desc.items():
            tool = MCPTool(
                name=self._tool_name_prefix + agent_name,
                desc=agent_val_dict["desc"],
                inputSchema=self._input_schema_cache
            )
            tool_list.append(tool)
        return tool_list

    async def _call_agent(self, name: str, arguments: dict[str, Any]) -> Sequence[ContentBlock] | dict[str, Any]:
        agent = AgentFactory.agent_instance(name)
        if not agent:
            return self._mcp_error(f"Agent {name} not found")

        if 'question' not in arguments:
            return self._mcp_error("Missing required parameter 'question'")

        question = arguments['question']
        task_conf = arguments.get('task_conf', None)
        try:
            task_response: TaskResponse = await exec_agent(question=question, agent=agent, context=Context(), task_conf=task_conf)
            return [TextContent(type="text", text=task_response.answer)]
        except Exception as e:
            return self._mcp_error(f"Error: {str(e)}")

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Sequence[ContentBlock] | dict[str, Any]:
        if name.startswith(self._tool_name_prefix):
            name = name[len(self._tool_name_prefix):]
            return await self._call_agent(name, arguments)
        else:
            return await super().call_tool(name, arguments)

    def add_agent(self, agent: BaseAgent | list[BaseAgent]):
        if isinstance(agent, list):
            Swarm.register_agent(agent)
        else:
            Swarm.register_agent([agent])
