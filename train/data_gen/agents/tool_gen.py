# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import json
import random
from typing import Dict, Any, List

from aworld.core.common import Observation, ActionModel
from aworld.core.event.base import Message
from aworld.core.tool_call_data_generation_framework import GeneratedTool, ToolSpec
from aworld.logs.util import logger
from aworld.utils.common import new_instance
from train.data_gen.agents.onetime_use_agent import OnetimeUseAgent
from train.data_gen.schema import TreeNode


class ToolModelGeneratorAgent(OnetimeUseAgent):
    """Generate independent and unrelated tools."""

    def __init__(self, category: str = None, rule_gen_cls: str = None, **kwargs):
        kwargs['name'] = kwargs.get('name', 'tool_model_generator_agent')
        kwargs['description'] = kwargs.get('description', 'Solve the task based on input task.')
        kwargs['system_prompt'] = kwargs.get(
            'system_prompt',
            '''You are a tool design expert. Please design a complete tool definition and description based on the given category and capability list.

Please return the tool definition in the following JSON Schema format:
{
    "name": "tool_name",
    "description": "Tool Function Description",
    "parameters": {
        "type": "object",
        "properties": {
            "param_name": {
                "type": "string|integer|boolean|array|object",
                "description": "parameter description",
                "minimum": 1,  // Optional constraints
                "maximum": 100,  // Optional constraints
                "pattern": "^[a-z]+$"  // Optional constraints
            }
        },
        "required": ["required_param_name"]
    },
    "output_parameters": {
        "type": "object",
        "description": "Return value description",
        "properties": {
            "result": {"type": "object", "description": "result data"}
        }
    }
}

Requirements:
- The tool name should be in English and follow the RESTful naming convention
- Parameter design should be reasonable and include necessary constraints
- The return value structure should be clear and in line with actual usage scenarios
- The description should be accurate and professional

Please return the complete JSON format tool definition.
'''
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
                actions[0].policy_info = self._parse(actions[0].policy_info)
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

    async def _parse(self, info: str) -> GeneratedTool:
        info_json = json.loads(info)
        spec = ToolSpec(**info_json)

        gen_tool = GeneratedTool(
            spec=spec,
            examples=[]
        )
        return gen_tool

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
        observation.content = """Please based on the following information:
Category: {category}
Capability list:
{capability_list}

Sub capability:
{sub_capability}""".format(
            category=category,
            capability_list=capabilities,
            sub_capability=self._sub(tool_node)
        )
        return await super().build_llm_input(observation, info, message, **kwargs)

    def _sub(self, capability: TreeNode) -> str:
        """Format tool sub capability information for LLM prompt"""
        lines = [f"- {capability.name}: {capability.description}"]

        for sub in capability.children.values():
            lines.append(f"  - {sub.name}: {sub.description}")
            for subchild in sub.children.values():
                lines.append(f"    - {subchild.name}")

        return "\n".join(lines)
