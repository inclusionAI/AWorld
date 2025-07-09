# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import json
import re
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


class ThinkingOutputParser:
    """Parser for responses that include thinking process and planning."""
    
    def __init__(self, agent_name: str):
        self.agent_name = agent_name
        self.planning_parser = PlanningOutputParser()
    
    def parse(self, resp: ModelResponse) -> AgentResult:
        """Parse response with thinking process into AgentResult.
        
        Args:
            resp: Model response containing thinking, planning and final answer
            
        Returns:
            AgentResult containing parsed actions
        """
        if not resp or not resp.content:
            logger.warning("No valid response content!")
            return AgentResult(actions=[], current_state=None)
            
        content = resp.content.strip()
        print("content", content)
        
        # Extract planning section
        planning_match = re.search(r'<PLANNING_TAG>(.*?)</PLANNING_TAG>', content, re.DOTALL)
        final_answer_match = re.search(r'<FINAL_ANSWER_TAG>(.*?)</FINAL_ANSWER_TAG>', content, re.DOTALL)
        
        actions = []
        is_call_tool = False
        
        # Parse planning section if exists
        if planning_match:
            plan_text = planning_match.group(1).strip()
            try:
                # Parse JSON plan
                plan_data = json.loads(plan_text)
                if "steps" in plan_data:
                    for step_key, step_data in plan_data["steps"].items():
                        # Convert each step to an action
                        if step_data.get("id"):  # If step has agent ID, treat as agent call
                            actions.append(ActionModel(
                                tool_name=step_data["id"],
                                agent_name=self.agent_name,
                                params={"content": step_data.get("input", "")},
                                policy_info=step_data.get("input", "")
                            ))
                            is_call_tool = True
                        else:  # Otherwise treat as regular step
                            actions.append(ActionModel(
                                agent_name=self.agent_name,
                                policy_info=step_data.get("input", "")
                            ))
            except json.JSONDecodeError:
                logger.warning("Failed to parse planning JSON")
        
        # If no valid planning steps found, use final answer
        if not actions and final_answer_match:
            final_answer = final_answer_match.group(1).strip()
            actions.append(ActionModel(
                agent_name=self.agent_name,
                policy_info=final_answer
            ))
            
        # If neither planning nor final answer found, use entire content
        if not actions:
            actions.append(ActionModel(
                agent_name=self.agent_name,
                policy_info=content
            ))
            
        return AgentResult(actions=actions, current_state=None, is_call_tool=is_call_tool)


class PlanAgent(Agent):
    """Plan Agent implementation using built-in planner's output parser."""

    def __init__(self,
                 conf: Union[Dict[str, Any], ConfigDict, AgentConfig],
                 name: str,
                 **kwargs):
        # Initialize with ThinkingOutputParser as response parser
        super().__init__(
            conf=conf,
            name=name,
            resp_parse_func=self._parse_thinking_response,
            **kwargs
        )
        
        # Initialize planner and parsers
        self.planner = BuiltInPlanner(llm_model=self.llm)
        self.thinking_parser = ThinkingOutputParser(self.id())
        
        # Set system prompt from planner
        self.system_prompt = self.planner.system_prompt
        self.system_prompt_template = StringPromptTemplate.from_template(self.system_prompt)
    
    def _parse_thinking_response(self, resp: ModelResponse) -> AgentResult:
        """Parse response using ThinkingOutputParser.
        
        Args:
            resp: Model response from LLM
            
        Returns:
            AgentResult containing parsed actions
        """
        return self.thinking_parser.parse(resp)
