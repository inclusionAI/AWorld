import json
import re
from aworld.core.agent.base import AgentResult
from aworld.core.agent.output_parser import AgentOutputParser
from aworld.core.common import ActionModel, Observation
from aworld.logs.util import logger
from aworld.models.model_response import ModelResponse
from aworld.planner.plan import Plan, parse_step_infos, parse_step_json

class BuiltInPlannerOutputParser(AgentOutputParser):
    """Parser for responses that include thinking process and planning."""

    def __init__(self, agent_name: str):
        self.agent_name = agent_name

    def parse(self, resp: ModelResponse) -> AgentResult:
        if not resp or not resp.content:
            logger.warning("No valid response content!")
            return AgentResult(actions=[], current_state=None)

        content = resp.content.strip()
        
        # Extract planning section
        planning_match = re.search(r'<PLANNING_TAG>(.*?)</PLANNING_TAG>', content, re.DOTALL)
        final_answer_match = re.search(r'<FINAL_ANSWER_TAG>(.*?)</FINAL_ANSWER_TAG>', content, re.DOTALL)
        step_json = ""
        if planning_match:
            step_json = planning_match.group(1).strip()
        final_answer = ""
        if final_answer_match:
            final_answer = final_answer_match.group(1).strip()

        actions = []
        is_call_tool = False

        # Parse planning section if exists
        if step_json or final_answer:
            try:
                step_infos = parse_step_json(step_json)
                plan = Plan(step_infos=step_infos, answer=final_answer)
                logger.info(f"BuiltInPlannerOutputParser|plan|{plan.json()}")
                actions.append(ActionModel(
                    agent_name=self.agent_name,
                    policy_info=plan.json()
                ))
            except json.JSONDecodeError:
                logger.warning("Failed to parse planning JSON")

        # If neither planning nor final answer found, use entire content
        if not actions:
            actions.append(ActionModel(
                agent_name=self.agent_name,
                policy_info=content
            ))
        
        logger.info(f"BuiltInPlannerOutputParser|actions|{actions}")

        return AgentResult(actions=actions, current_state=None, is_call_tool=is_call_tool)
