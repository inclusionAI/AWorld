# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import json
import logging
from typing import Any, Dict, List, Union
import re

from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig, ConfigDict
from aworld.core.agent.base import AgentResult
from aworld.models.model_response import ModelResponse
from aworld.planner.built_in_output_parser import BuiltInPlannerOutputParser
from aworld.planner.built_in_planner import BuiltInPlanner
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
        self.planner = BuiltInPlanner()
        
        # Set default system prompt if not provided
        self.system_prompt = self.planner.system_prompt
        self.system_prompt_template = StringPromptTemplate.from_template(self.system_prompt)

    def plan_response_parse(self, resp: ModelResponse) -> AgentResult:
        return BuiltInPlannerOutputParser(self.id()).parse(resp)
