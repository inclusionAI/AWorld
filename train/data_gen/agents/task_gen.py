# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import json
from typing import Dict, Any, List

from aworld.agents.llm_agent import Agent
from aworld.core.agent.base import AgentFactory
from aworld.core.common import Observation
from aworld.core.event.base import Message
from aworld.memory.main import MemoryFactory
from aworld.memory.models import MemorySystemMessage, MemoryToolMessage
from train.data_gen.agents.prompts import task_generator_agent_system_prompt
from train.data_gen.agents.util import tools_meta
from train.data_gen.graph_parser import ExecutionGraph
from train.data_gen.tool_repository import ToolRepository


@AgentFactory.register(name="task_generator_agent", desc="Generate task and answer based on user input.")
class TaskGeneratorAgent(Agent):
    def __init__(self, tool_repository: ToolRepository, **kwargs):
        kwargs['name'] = kwargs.get('name', 'task_generator_agent')
        kwargs['description'] = kwargs.get('description', 'Generate task and answer based on user input.')
        kwargs['system_prompt'] = kwargs.get(
            'system_prompt',
            task_generator_agent_system_prompt
        )
        super().__init__(**kwargs)

        self.tool_repository = tool_repository
        self.max_tool_num = self.conf.ext.get("max_tool_num", 5)

    async def build_llm_input(self,
                              observation: Observation,
                              info: Dict[str, Any] = {},
                              message: Message = None,
                              **kwargs) -> List[Dict[str, Any]]:
        context = message.context
        tools_info = context.agent_info.get(context.get_task().id)
        if tools_info is not None:
            # use histories
            constraint = "Generate follow-up task related to previous task"
            if '{constraint}' in self.system_prompt:
                system_prompt = self.system_prompt.format(constraint=constraint)
            else:
                system_prompt = self.system_prompt
            messages = [
                {"role": "system", "content": system_prompt}
            ]

            session_id = message.context.get_task().session_id
            task_id = message.context.get_task().id
            histories = MemoryFactory.instance().get_last_n(self.memory_config.history_rounds, filters={
                "agent_id": self.id(),
                "session_id": session_id,
                "task_id": task_id
            }, agent_memory_config=self.memory_config)
            if not histories:
                messages.append({
                    "role": "user",
                    "content": f"Please generate an task based on tools information: {tools_info}"
                })
                return messages

            dialogs = []
            for history in histories:
                if isinstance(history, (MemorySystemMessage, MemoryToolMessage)):
                    continue

                dialogs.append(history.to_openai_message())
            messages.append({
                "role": "user",
                "content": f"Previous conversation history: {dialogs}\n"
                           f"Please generate an task based on tools information: {tools_info}"
            })
        else:
            # no histories need
            if self.tool_repository:
                con: str = observation.content

                con_dict = json.loads(con)
                execution_str = con_dict.get('execution_graph', '')
                graph = await ExecutionGraph.parse(execution_str)
                tool_ids: List[str] = await ExecutionGraph.collect_entity(graph)

                tools = []
                for tool_id in tool_ids:
                    tool = await self.tool_repository.get_tool(tool_id)
                    if tool:
                        tools.append(tool)

                tools_info = tools_meta(tools, self.max_tool_num)
                # add to context for next
                context.agent_info[context.get_task().id] = tools_info

                tools_info = "\n".join(tools_info)

                info = {"tools": tools_info, "chain": execution_str}
                user_content = f"Please generate an initial task based on the information: {info}"
                messages = [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": user_content}
                ]
            else:
                messages = [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": f"Please generate an task based on information: {observation.content}"}
                ]
        return messages
