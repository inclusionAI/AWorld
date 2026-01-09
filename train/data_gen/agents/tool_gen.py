# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import json
import random
from typing import Dict, Any, List

from aworld.core.agent.base import AgentFactory
from aworld.core.common import Observation, ActionModel
from aworld.core.event.base import Message
from aworld.logs.util import logger
from aworld.utils.common import new_instance
from train.data_gen.agents.onetime_use_agent import OnetimeUseAgent
from train.data_gen.agents.prompts import tool_generator_agent_system_prompt
from train.data_gen.schema import TreeNode, Specification, GeneratedTool


@AgentFactory.register(name="tool_generator_agent",
                       description="Generate tools based on input task, and the tools is a GeneratedTool structure.")
class ToolGeneratorAgent(OnetimeUseAgent):
    """Generate independent and unrelated tools."""

    def __init__(self, category: str = None, rule_gen_cls: str = None, **kwargs):
        kwargs['name'] = kwargs.get('name', 'tool_generator_agent')
        kwargs['description'] = kwargs.get('description',
                                           'Generate tools based on input task, and the tools is a GeneratedTool structure.')
        kwargs['system_prompt'] = kwargs.get(
            'system_prompt',
            tool_generator_agent_system_prompt
        )
        super().__init__(**kwargs)

        self.category = category
        self.rule_gen_cls = rule_gen_cls

    async def async_policy(self,
                           observation: Observation,
                           info: Dict[str, Any] = {},
                           message: Message = None,
                           **kwargs) -> List[ActionModel]:
        actions = await super().async_policy(observation, info, message, **kwargs)

        if actions:
            # if it has policy info, parse and use the first one
            if actions[0].policy_info:
                actions[0].policy_info = await self._parse(actions[0].policy_info)
                actions = [actions[0]]
            # wait tool result...

            resp = actions
        elif self.rule_gen_cls:
            # use rule generator
            agent = new_instance(self.rule_gen_cls, category=self.category)
            resp = agent.async_policy(observation, info, message, **kwargs)
        else:
            resp = []
            logger.warning(f"{self.id()} no action to the next node.")

        return resp

    async def _parse(self, info: str) -> List[GeneratedTool]:
        info = info.replace("```json", "").replace("```", "")
        info_json = json.loads(info)

        gen_tools = []
        if isinstance(info_json, list):
            for tool_info in info_json:
                spec = Specification(**tool_info)
                gen_tools.append(GeneratedTool(
                    spec=spec,
                    examples=[]
                ))
        else:
            gen_tools.append(GeneratedTool(
                spec=Specification(**info_json),
                examples=[]
            ))
        return gen_tools

    async def build_llm_input(self,
                              observation: Observation,
                              info: Dict[str, Any] = {},
                              message: Message = None,
                              **kwargs) -> List[Dict[str, Any]]:
        tool_node: TreeNode = observation.content
        assert tool_node, "No tool tree found in observation content."

        # category
        if tool_node.name != 'root':
            category = tool_node.name
        elif self.category:
            category = self.category
        else:
            category = random.choice([child.name for child in tool_node.children.values()])
            tool_node = tool_node.children[category]
        self.category = category

        capabilities = [child.name for child in tool_node.children.values()]
        inputs = {
            "category": self.category,
            "capabilities": capabilities,
            "sub_capabilities": self._sub_capabilities(tool_node)
        }
        observation.content = f"""Please based on the following information:\n{inputs}"""
        return await super().build_llm_input(observation, info, message, **kwargs)

    def _sub_capabilities(self, cur_node: TreeNode) -> List[str]:
        sub_capabilities = []
        for cap in cur_node.children.values():
            # sub of capability, go one level deeper
            for subchild in cap.children.values():
                sub_capabilities.append(subchild.name)

        return sub_capabilities
