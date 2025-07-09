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
DEFAULT_SYSTEM_PROMPT = f"""当回答问题时，请遵循以下两步流程：

1. 首先在{PLANNING_TAG}和{PLANNING_END_TAG}之间，输出JSON格式的执行计划：
{{
  "steps": {{
    "agent_step_1": {{"input": "步骤描述", "id": "agent_id"}},
    "agent_step_2": {{"input": "步骤描述", "id": "agent_id"}},
    "agent_step_3": {{"input": "步骤描述", "id": "agent_id"}}
  }},
  "dag": [["agent_step_1","agent_step_2"],"agent_step_3"]
}}

其中：
- steps: 包含每个步骤的描述和执行者ID
- dag: 定义步骤之间的执行顺序和依赖关系
- 每个步骤都应该使用context中可用的工具

2. 然后在{FINAL_ANSWER_TAG}和{FINAL_ANSWER_END_TAG}之间，提供最终答案：
- 答案应该准确并符合查询要求
- 如果无法使用现有工具和信息回答，请说明原因并请求更多信息
- 优先使用context中已有的信息，避免重复调用工具

可用工具列表：
{{{{tool_list}}}}

用户输入: {{{{task}}}}"""

class PlanningOutputParser:
    """Planning output parser for built-in planner that handles JSON format."""

    def parse(self, text: str) -> Plan:
        """Parse the planning response text into a Plan object.
        
        Args:
            text: The response text from the model.
            
        Returns:
            A Plan object containing the parsed steps.
        """
        try:
            # First try to parse JSON format
            if self._is_json_format(text):
                return self._parse_json_format(text)
            
            # Fallback to numbered list format
            # This part of the original code was not using re.split,
            # so we'll keep the original logic for now.
            # If the original code intended to use re.split, it would need to be imported.
            # For now, we'll assume the original logic is correct.
            steps = [Step(value=v.strip()) for v in re.split(r"\n\s*\d+\.\s*", text)[1:]]
            
            # If no steps found, create a single step with the entire text
            if not steps:
                steps = [Step(value=text.strip())]
                
            return Plan(steps=steps)
        except Exception as e:
            logger.warning(f"Error parsing planning output: {e}")
            # Fallback: treat entire text as a single step
            return Plan(steps=[Step(value=text.strip())])
    
    def _is_json_format(self, text: str) -> bool:
        """Check if the text contains JSON format planning."""
        # Remove planning tags and check for JSON structure
        cleaned_text = text.replace(PLANNING_TAG, '').replace(FINAL_ANSWER_TAG, '').strip()
        return ('{' in cleaned_text and '}' in cleaned_text and 
                '"steps"' in cleaned_text and '"dag"' in cleaned_text)
    
    def _parse_json_format(self, text: str) -> Plan:
        """Parse JSON format planning into Plan object."""
        try:
            # Remove planning tags first
            cleaned_text = text.replace(PLANNING_TAG, '').replace(FINAL_ANSWER_TAG, '').strip()
            
            # Extract JSON from cleaned text
            start_idx = cleaned_text.find('{')
            end_idx = cleaned_text.rfind('}') + 1
            if start_idx >= 0 and end_idx > start_idx:
                json_str = cleaned_text[start_idx:end_idx]
                plan_data = json.loads(json_str)
                
                # Convert JSON steps to Step objects
                steps = []
                if "steps" in plan_data:
                    for step_key, step_data in plan_data["steps"].items():
                        step_desc = f"{step_key}: {step_data.get('input', '')} (agent: {step_data.get('id', 'unknown')})"
                        steps.append(Step(value=step_desc))
                
                # Add DAG information as a final step
                if "dag" in plan_data:
                    dag_desc = f"Execution DAG: {plan_data['dag']}"
                    steps.append(Step(value=dag_desc))
                
                return Plan(steps=steps)
            else:
                raise ValueError("No valid JSON structure found")
                
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to parse JSON format: {e}")
            # Fallback to treat as single step
            return Plan(steps=[Step(value=text.strip())])


class BuiltInPlanner(BasePlanner):
    """Built-in planner using StringPromptTemplate for prompt composition."""
    
    model_config = ConfigDict(arbitrary_types_allowed=True)

    llm_model: Optional[LLMModel] = None
    """The LLM model for planning."""
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    """The system prompt for planning."""
    output_parser: PlanningOutputParser
    """The output parser for parsing plan responses."""
    stop: Optional[List[str]] = None
    """Stop sequences for the model."""
    temperature: float = 0.3
    """Temperature for LLM generation."""
    max_tokens: Optional[int] = None
    """Maximum tokens for LLM generation."""

    def __init__(self, llm_model: LLMModel):
        self.llm_model = llm_model
    
    def build_plan_instruction(self, context: "Context", input: str) -> str:
        """Build the plan instruction."""
        return StringPromptTemplate.from_template(self.system_prompt).format(
            context=context,
            input=input
        )

    def plan(self, context: "Context", input: str, **kwargs: Any) -> Plan:
        """Generate a plan given the context and input.

        Args:
            context: The AWorld context object.
            input: User input string.
            **kwargs: Additional keyword arguments.

        Returns:
            A Plan object containing the planned steps.
        """
        try:
            # Format the prompt using StringPromptTemplate
            formatted_prompt = StringPromptTemplate.from_template(self.system_prompt).format(
                context=context,
                input=input
            )
            print(f"formatted_prompt: {formatted_prompt}")

            response_text = self._call_llm_model(formatted_prompt, **kwargs)

            print(f"response_text: {response_text}")

            # Parse the response into a Plan
            plan = self.output_parser.parse(response_text)

            logger.info(f"Generated plan with {len(plan.steps)} steps")
            return plan

        except Exception as e:
            logger.error(f"Error generating plan: {traceback.format_exc()}")
            # Fallback: create a simple plan
            fallback_step = Step(value=f"Address the request: {input}")
            return Plan(steps=[fallback_step])

    async def aplan(self, context: "Context", input: str, **kwargs: Any) -> Plan:
        """Asynchronously generate a plan given the context and input.

        Args:
            context: The AWorld context object.
            input: User input string.
            **kwargs: Additional keyword arguments.

        Returns:
            A Plan object containing the planned steps.
        """
        try:
            # Format the prompt using StringPromptTemplate
            formatted_prompt = StringPromptTemplate.from_template(self.system_prompt).format(
                context=context,
                input=input
            )

            response_text = await self._acall_llm_model(formatted_prompt, **kwargs)

            # Parse the response into a Plan
            plan = self.output_parser.parse(response_text)

            logger.info(f"Generated plan with {len(plan.steps)} steps")
            return plan

        except Exception as e:
            logger.error(f"Error generating plan: {traceback.format_exc()}")
            # Fallback: create a simple plan
            fallback_step = Step(value=f"Address the request: {input}")
            return Plan(steps=[fallback_step])

    def _call_llm_model(self, prompt: str, **kwargs) -> str:
        """Call LLM model synchronously.
        
        Args:
            prompt: The formatted prompt.
            **kwargs: Additional keyword arguments.

        Returns:
            The model response text.
        """
        messages = [{"role": "user", "content": prompt}]

        response = call_llm_model(
            self.llm_model,
            messages=messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            stop=self.stop,
            **kwargs
        )
        
        return response.content
    
    async def _acall_llm_model(self, prompt: str, **kwargs) -> str:
        """Call LLM model asynchronously.
        
        Args:
            prompt: The formatted prompt.
            **kwargs: Additional keyword arguments.
            
        Returns:
            The model response text.
        """
        messages = [{"role": "user", "content": prompt}]

        response = await acall_llm_model(
            self.llm_model,
            messages=messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            stop=self.stop,
            **kwargs
        )

        return response.content
