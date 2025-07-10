
import json
import logging
import re
from aworld.models.model_response import ModelResponse

from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig, ConfigDict
from aworld.core.agent.base import AgentResult
from aworld.core.common import ActionModel, Observation
from aworld.core.event.base import Message
from aworld.models.model_response import ModelResponse

logger = logging.getLogger(__name__)



class BuiltInPlannerOutputParser:
    """Parser for responses that include thinking process and planning."""
    
    def __init__(self, agent_name: str):
        self.agent_name = agent_name
    
    def parse(self, resp: ModelResponse) -> AgentResult:
        print("resp", resp)
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
                actions.append(ActionModel(
                    agent_name=self.agent_name,
                    policy_info=plan_text
                ))
                print("actions", actions)
                return AgentResult(actions=actions, current_state=None, is_call_tool=is_call_tool)
                
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
        print("actions", actions)
            
        return AgentResult(actions=actions, current_state=None, is_call_tool=is_call_tool)
