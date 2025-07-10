# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""Built-in planner implementation for AWorld.

This module is inspired by langchain-experimental's chat_planner.py and ADK's planners
but adapted for AWorld's architecture using StringPromptTemplate for prompt composition.
"""

import logging
import json
import traceback
from typing import Any, Dict, Optional, List, TYPE_CHECKING
from pydantic import ConfigDict, BaseModel

from aworld.core.context.base import Context
from aworld.config.conf import ModelConfig
from aworld.models.llm import LLMModel, call_llm_model
from aworld.planner.base import BasePlanner, Plan, Step
from aworld.core.context.prompts.string_prompt_template import StringPromptTemplate

if TYPE_CHECKING:
    from aworld.core.context.base import Context

logger = logging.getLogger(__name__)

# Tags for response structure
PLANNING_TAG = "<PLANNING_TAG>"
PLANNING_END_TAG = "</PLANNING_TAG>"
FINAL_ANSWER_TAG = "<FINAL_ANSWER_TAG>"
FINAL_ANSWER_END_TAG = "</FINAL_ANSWER_TAG>"

# Default system prompt
DEFAULT_SYSTEM_PROMPT = f"""When answering questions, please follow these two steps:

1. First, output an execution plan in JSON format between {PLANNING_TAG} and {PLANNING_END_TAG}:
{{
  "steps": {{
    "agent_step_1": {{"input": "step description", "id": "tool_name"}},
    "agent_step_2": {{"input": "step description", "id": "tool_name"}},
    "agent_step_3": {{"input": "step description", "id": "tool_name"}}
  }},
  "dag": [["agent_step_1","agent_step_2"],"agent_step_3"]
}}

Where:
- steps: Contains each step's description and executor ID
  * "id" MUST be a valid tool name from the available tools list below
  * "input" describes what this step should accomplish
- dag: Defines the execution order and dependencies between steps in "steps"
  * Each element in dag refers to step keys in "steps"
  * Arrays like ["step1","step2"] mean parallel execution
  * Sequential steps are separated by commas

2. Then, provide the final answer between {FINAL_ANSWER_TAG} and {FINAL_ANSWER_END_TAG}:
- The answer should be accurate and meet the query requirements
- If unable to answer using existing tools and information, explain why and request more information
- Prioritize using information already available in the context to avoid redundant tool calls

Available Tools:
{{{{tool_list}}}}

User Input: {{{{task}}}}"""


class BuiltInPlanner(BasePlanner):
    """The LLM model for planning."""
    system_prompt: str = DEFAULT_SYSTEM_PROMPT

    def build_plan_instruction(self, context: "Context", input: str) -> str:
        """Build the plan instruction."""
        return StringPromptTemplate.from_template(self.system_prompt).format(
            context=context,
            input=input
        )
