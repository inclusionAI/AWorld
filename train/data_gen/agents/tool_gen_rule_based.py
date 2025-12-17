# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
import random
from typing import List, Dict, Any

from aworld.core.agent.base import BaseAgent
from aworld.core.common import Observation, ActionModel
from aworld.core.event.base import Message
from train.data_gen.schema import TreeNode, Complexity, Specification, GeneratedTool, Diversity


class ToolRuleGeneratorAgent(BaseAgent, abc.ABC):
    __metaclass__ = abc.ABCMeta

    def __init__(self, category: str = None, **kwargs):
        kwargs['name'] = kwargs.get('name', 'tool_rule_generator_agent')
        super().__init__(**kwargs)

        self.category = category

    async def async_policy(
            self, observation: Observation, info: Dict[str, Any] = None, message: Message = None, **kwargs
    ) -> List[ActionModel]:
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

        # tool capabilities
        capabilities = [child.name for child in tool_node.children.values()]

        # tool name
        api_name = await self._gen_name(category, capabilities)
        # tool description
        description = await self._gen_desc(category, capabilities)
        # tool input parameters
        parameters = await self._gen_params(category, capabilities)
        # tool output parameters
        output_parameters = await self._gen_output_params(category, capabilities)

        tool_spec = Specification(
            name=api_name,
            description=description,
            category=category,
            parameters=parameters,
            output_parameters=output_parameters,
            capabilities=capabilities,
        )

        # Calculate complexity score
        complexity_score = await self._cal_complexity(tool_spec)
        complexity = Complexity.LOW if complexity_score < 0.3 else Complexity.HIGH if complexity_score > 0.75 else Complexity.MEDIUM

        # Calculate diversity score
        diversity_score = await self._cal_diversity(tool_spec)
        diversity = Diversity.LOW if diversity_score < 0.3 else Diversity.HIGH if diversity_score > 0.75 else Diversity.MEDIUM

        tool_spec.diversity = diversity
        tool_spec.complexity = complexity

        # gen examples for few shot
        examples = await self._gen_examples(tool_spec)
        gen_tool = GeneratedTool(
            spec=tool_spec,
            examples=examples,
            complexity_score=complexity_score,
            diversity_score=diversity_score
        )

        return [ActionModel(agent_name=self.id(), policy_info=gen_tool)]

    @abc.abstractmethod
    async def _gen_name(self, category: str, capabilities: List[str]) -> str:
        """Generate name based on category and abilities."""

    @abc.abstractmethod
    async def _gen_desc(self, category: str, capabilities: List[str]) -> str:
        """Generate description based on category and abilities."""

    @abc.abstractmethod
    async def _gen_params(self, category: str, capabilities: List[str]) -> Dict[str, Dict[str, Any]]:
        """Generate input parameters based on category and abilities."""

    @abc.abstractmethod
    async def _gen_output_params(self, category: str, capabilities: List[str]) -> Dict[str, Dict[str, Any]]:
        """Generate output parameters (tool call return) based on category and abilities."""

    @abc.abstractmethod
    async def _gen_examples(self, tool_spec: Specification) -> List[Dict[str, Any]]:
        """Generate examples of few shot based on tool spec information."""

    @abc.abstractmethod
    async def _cal_complexity(self, tool_spec: Specification) -> float:
        """Calculate complexity of tool based on tool spec information."""

    @abc.abstractmethod
    async def _cal_diversity(self, tool_spec: Specification) -> float:
        """Calculate diversity of tool based on tool spec information."""
