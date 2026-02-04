# coding: utf-8
# Copyright (c) inclusionAI.
import json
import traceback
import uuid
from dataclasses import asdict
from typing import Optional, List, Dict, Any

import aworld
from aworld.config import ModelConfig
from aworld.logs.util import logger
from aworld.models.llm import get_llm_model, acall_llm_model
from aworld.models.model_response import ModelResponse
from aworld.ralph_loop.mission.types import Complexity
from aworld.ralph_loop.plan.base import BasePlanner, BasePlanReviewer, BasePlanOptimizer
from aworld.ralph_loop.plan.types import PlanningInput, PlanStep, StrategicPlan, PlanReview

planner_system_prompt = """# Role
You are an expert **Task Planner & Decomposer**. Your goal is to break down a complex user objective into a sequence of step-by-step, executable actions (a Plan).


# Planning Rules (CRITICAL)
Create a detailed strategic plan to accomplish this mission. Your plan should include:
1. **Decomposition**: Break the goal down into 1-15 steps. Each step should do exactly ONE thing.
- Detailed description of what needs to be done
- Success criteria (how to verify completion), optional
- Estimated the step task complexity
- Estimated time (in seconds)
- Estimated resources (CPU, memory)
- Alternative approaches if primary fails
2. **Dependency**: Ensure logical order. If Step B needs information from Step A, Step A must come first.
3. **Tool Grounding**: For each step, explicitly specify which tool to use and what arguments to pass.
4. **Critical Path**: Identify the sequence of steps that determines minimum completion time
5. **Contingency Planning**: Backup strategies for high-risk steps
6. Estimated time (in seconds)
7. **Efficiency**: Do not create redundant steps. The plan should be concise.
8. **Flexibility**: If the goal is simple, the plan can contain just one step.
9. **No Execution**: You are a Planner, NOT an Executor. Do NOT execute the tools. Just write the plan.

# Output Format
The format must be strictly as follows:

```json
{
  "reasoning": "A short analysis of the user goal, identified dependencies, and tool selection strategy.",
  "steps": [
    {
      "step_id": "step_1",
      "title": "Clear, concise step title",
      "description": "Clear natural language instruction of what needs to be done in this step.",
      "success_criteria": ["criterion 1", "criterion 2"],
      "estimated_time": 300,
      "complexity": "high",
      "resources_needed": {"CPU": 1, "memory": 1024 (in M)},
      "alternatives": ["backup approach"]
    }
  ],
  "dependencies": {
    "step_3": ["step_1", "step_2"],
  },
  "critical_path": ["step_1", "step_3", "step_5"]
}

**Important:**
- Ensure step_ids are sequential (step_1, step_2, etc.)
- Dependencies must reference valid step_ids
- Critical path should include only essential sequential steps
- Time estimates should be realistic (in seconds)
"""


class DefaultReviewer(BasePlanReviewer):
    async def review(self, plan: StrategicPlan, old_plan: Optional[StrategicPlan] = None) -> PlanReview:
        # check critical info only
        return PlanReview(is_valid=True)


class GeneralPlanner(BasePlanner):
    def __init__(self,
                 model_config: ModelConfig,
                 system_prompt: str = planner_system_prompt,
                 reviewer: BasePlanReviewer = None,
                 optimizer: BasePlanOptimizer = None):
        self.model_config = model_config
        self.system_prompt = system_prompt
        self._llm = None

        self.reviewer = reviewer or DefaultReviewer()
        self.optimizer = optimizer

    async def plan(self, plan_input: PlanningInput) -> StrategicPlan:
        if not self._llm:
            self._llm = get_llm_model(self.model_config)

        messages = self._build_planning_input(plan_input)
        response = await acall_llm_model(self._llm, messages=messages)
        plan = self._parse_response(response, plan_input)

        logger.info(f"Generated plan with {len(plan.steps)} steps, "
                    f"critical path: {plan.critical_path}, "
                    f"estimated time: {plan.total_estimated_time}s, "
                    f"cost: {plan.total_estimated_cost:.2f}")
        if aworld.debug_mode:
            logger.info(f"Generated plan: {asdict(plan)}")

        if self.reviewer:
            plan_review = await self.reviewer.review(plan)
            if not plan_review.is_valid:
                logger.warning(f"Generated plan has issues: {plan_review.issues}, will regenerate plan")

                plan = await self.replan(plan, plan_review.__dict__)

        if self.optimizer:
            plan = self.optimizer.optimize(plan)
            logger.info(f"Optimized plan finished, {len(plan.steps)} steps, critical path: {plan.critical_path}")

        return plan

    async def replan(self, plan: StrategicPlan, feedback: Optional[Dict[str, Any]] = None) -> StrategicPlan:
        # todo: replan processing
        return plan

    def _build_planning_input(self, plan_input: PlanningInput) -> List[dict]:
        mission = plan_input.mission
        if mission:
            complexity = mission.complexity
            text = mission.text
            completion_criteria = mission.completion_criteria.answer
        else:
            text = plan_input.user_input
            complexity = Complexity.HIGH
            completion_criteria = ''

        user_prompt = f"""# Mission
content: {text}
complexity: {complexity}
"""
        if completion_criteria:
            user_prompt += f"""
Completion Criteria: {completion_criteria}
"""
        constraints = plan_input.constraints
        if constraints:
            user_prompt += f"""
# Constraints
{constraints}
"""
        resources = plan_input.resources
        if resources:
            user_prompt += f"""
# Available Resources
{resources}
"""
        preferences = plan_input.preferences
        if preferences:
            user_prompt += f"""
# Preferences
{preferences}
"""
        feedback = plan_input.feedback
        if feedback:
            tool_definitions = feedback.pop("tools", '')
            if tool_definitions:
                user_prompt += f"""
# Available Tools
You have access to the following tools. You must ONLY use these tools. Do not hallucinate new tools.
{tool_definitions}
(Note: If a step requires logical processing without a specific tool, use the tool name "no_tool".)
"""
            user_prompt += f"""
# User Feedback
{feedback}
"""
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        return messages

    def _parse_response(self, response: ModelResponse, plan_input: PlanningInput) -> StrategicPlan:
        parsed_json = response.structured_output.get("parsed_json")
        if not parsed_json:
            try:
                import re

                content = response.content
                json_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
                if json_match:
                    content = json_match.group(1)

                parsed_json = json.loads(content)
            except Exception as e:
                logger.warning(f"Failed to parse reflection response: {e}. {response.content}")
                if aworld.debug_mode:
                    logger.error(f"Failed to parse response {response.raw_response} \n{traceback.format_exc()}")
                parsed_json = {}

        steps = []
        for step_data in parsed_json.get('steps', []):
            steps.append(PlanStep(**step_data))

        plan = StrategicPlan(
            plan_id=uuid.uuid4().hex,
            mission=plan_input.mission,
            goal=plan_input.user_input or plan_input.mission.text,
            steps=steps,
            dependency_graph=parsed_json.get("dependencies", {}),
            critical_path=parsed_json.get('critical_path', []),
        )

        plan.total_estimated_time = sum(s.estimated_time for s in steps)
        return plan
