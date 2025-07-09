# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import logging
from typing import Any, Dict, List, Union

from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig, ConfigDict
from aworld.core.agent.base import AgentResult
from aworld.core.common import ActionModel, Observation
from aworld.core.event.base import Message
from aworld.models.model_response import ModelResponse
from aworld.planner.built_in_planner import PlanningOutputParser, BuiltInPlanner
from aworld.core.context.prompts.string_prompt_template import StringPromptTemplate

logger = logging.getLogger(__name__)


class PlanAgent(Agent):
    """Plan Agent implementation using built-in planner's output parser."""

    def __init__(self,
                 conf: Union[Dict[str, Any], ConfigDict, AgentConfig],
                 name: str,
                 **kwargs):
        # Initialize with PlanningOutputParser as response parser
        super().__init__(
            conf=conf,
            name=name,
            resp_parse_func=self.plan_response_parse,
            **kwargs
        )
        
        # Initialize planner
        self.planner = BuiltInPlanner(llm_model=self.llm)
        self.planner.output_parser = PlanningOutputParser()
        
        # Set default system prompt if not provided
        self.system_prompt = self.planner.system_prompt
        self.system_prompt_template = StringPromptTemplate.from_template(self.system_prompt)

    def plan_response_parse(self, resp: ModelResponse) -> AgentResult:
        """Parse LLM response using PlanningOutputParser.
        
        Args:
            resp: Model response from LLM
            
        Returns:
            AgentResult containing parsed actions
        """
        if not resp:
            logger.warning("LLM no valid response!")
            return AgentResult(actions=[], current_state=None)
            
        # Parse response into Plan using planner's output parser
        plan = self.planner.output_parser.parse(resp.content)
        
        # Convert Plan steps to actions
        actions = []
        for step in plan.steps:
            actions.append(ActionModel(
                agent_name=self.id(),
                policy_info=step.value
            ))
            
        return AgentResult(actions=actions, current_state=None)

    # async def custom_system_prompt(self, context: "Context", content: str) -> str:
    #     return self.planner.build_plan_instruction(context=context, input=content)
