# coding: utf-8
# Copyright (c) inclusionAI.
import json
from typing import Any, Dict, List, Optional

from aworld.config import EvaluationConfig, ModelConfig
from aworld.evaluations.base import Scorer, ScorerResult, EvalStatus, MetricResult, EvalDataCase
from aworld.evaluations.scorers import scorer_register
from aworld.logs.util import logger
from aworld.ralph_loop.validate.base_validator import LlmValidator, RuleValidator
from aworld.ralph_loop.validate.types import ValidationMetrics


class TrajectoryValidator(RuleValidator):
    def _parse_trajectory(self, output: Any) -> Dict:
        if isinstance(output, dict):
            if "trajectory" in output:
                output = output["trajectory"]
            return output
        elif isinstance(output, str):
            return json.loads(output)
        else:
            raise ValueError(f"Unsupported trajectory type: {type(output)}")

    def _success_result(self, key: str = None) -> ScorerResult:
        return ScorerResult(
            scorer_name=self.name,
            metric_results={
                key if key else "trajectory": MetricResult(value=1.0, eval_status=EvalStatus.PASSED, metadata={}),
            }
        )

    def _failed_result(self, reason: str, key: str = None) -> ScorerResult:
        return ScorerResult(
            scorer_name=self.name,
            metric_results={
                key if key else "trajectory": MetricResult(value=0.0,
                                                           eval_status=EvalStatus.FAILED,
                                                           metadata={"reason": reason})
            }
        )


@scorer_register(ValidationMetrics.TRAJECTORY_STRUCTURE)
class TrajectoryStructureScorer(TrajectoryValidator):
    def __init__(self, eval_config: EvaluationConfig = None):
        super().__init__(name=ValidationMetrics.TRAJECTORY_STRUCTURE, eval_config=eval_config)
        self.required_fields = {
            "step_level": ["id", "meta", "state", "action", "reward"],
            "meta_level": ["session_id", "task_id", "agent_id", "step", "execute_time"],
            "state_level": ["input", "messages", "context"],
            "action_level": ["content", "tool_calls", "is_agent_finished"],
        }

    async def score(self, index: int, input: EvalDataCase, output: Any) -> ScorerResult:
        try:
            trajectory = self._parse_trajectory(output)
            if isinstance(trajectory, str):
                trajectory = json.loads(trajectory)

            if not trajectory:
                return self._failed_result("Empty trajectory")

            # Step verification
            for i, step in enumerate(trajectory):
                error = self._validate_step(step, i)
                if error:
                    return self._failed_result(error)

            return self._success_result(key=ValidationMetrics.TRAJECTORY_STRUCTURE)
        except Exception as e:
            return self._failed_result(reason=f"Verification failed: {str(e)}",
                                       key=ValidationMetrics.TRAJECTORY_STRUCTURE)

    def _validate_step(self, step: Dict, step_idx: int) -> Optional[str]:
        # step field
        for field in self.required_fields["step_level"]:
            if field not in step:
                return f"step {step_idx} missing field: {field}"

        meta = step.get("meta", {})
        for field in self.required_fields["meta_level"]:
            if field not in meta:
                return f"step {step_idx} meta missing field: {field}"

        state = step.get("state", {})
        for field in self.required_fields["state_level"]:
            if field not in state:
                return f"step {step_idx} state missing field: {field}"

        action = step.get("action", {})
        for field in self.required_fields["action_level"]:
            if field not in action:
                return f"step {step_idx} action missing field: {field}"

        return None


@scorer_register(ValidationMetrics.TRAJECTORY_TOOL_CALLS)
class TrajectoryToolCallsScorer(TrajectoryValidator):
    """Verify the validity of tool calls in the trajectory."""

    def __init__(self, eval_config: EvaluationConfig = None):
        super().__init__(name=ValidationMetrics.TRAJECTORY_TOOL_CALLS, eval_config=eval_config)

    async def score(self, index: int, input: Any, output: Any) -> ScorerResult:
        try:
            trajectory = self._parse_trajectory(output)

            issues = []
            tool_call_count = 0
            successful_calls = 0

            for i, step in enumerate(trajectory):
                action = step.get("action", {})
                tool_calls = action.get("tool_calls", [])

                if not tool_calls:
                    continue

                tool_call_count += len(tool_calls)

                for j, tool_call in enumerate(tool_calls):
                    if "id" not in tool_call:
                        issues.append(f"step {i} tool call {j}: missing id")
                    if "function" not in tool_call:
                        issues.append(f"step {i} tool call {j}: missing function")
                    else:
                        func = tool_call.get("function", {})
                        if "name" not in func:
                            issues.append(
                                f"step {i} tool call {j}: function missing name"
                            )
                        if "arguments" not in func:
                            issues.append(
                                f"step {i} tool call {j}: function missing arguments"
                            )

                state = step.get("state", {})
                input_data = state.get("input", {})
                action_result = input_data.get("action_result", [])

                if action_result:
                    for result in action_result:
                        if result.get("success", False):
                            successful_calls += 1

            if tool_call_count == 0:
                score = 1.0
                message = "no tool call"
            elif issues:
                score = 0.0
                message = f"tool call with issue: {'; '.join(issues[:3])}"
            else:
                score = 1.0
                message = f"Tool call is valid (total: {tool_call_count}, success: {successful_calls})"

            if score >= 0.5:
                return self._success_result(key=ValidationMetrics.TRAJECTORY_TOOL_CALLS)
            else:
                return self._failed_result(message, key=ValidationMetrics.TRAJECTORY_TOOL_CALLS)
        except Exception as e:
            return self._failed_result(reason=f"Verification failed: {str(e)}",
                                       key=ValidationMetrics.TRAJECTORY_TOOL_CALLS)


@scorer_register(ValidationMetrics.TRAJECTORY_COMPLETENESS)
class TrajectoryCompletenessScorer(TrajectoryValidator):
    def __init__(self, eval_config: EvaluationConfig = None):
        super().__init__(name=ValidationMetrics.TRAJECTORY_COMPLETENESS, eval_config=eval_config)

    async def score(self, index: int, input: Any, output: Any) -> ScorerResult:
        try:
            trajectory = self._parse_trajectory(output)

            last_step = trajectory[-1]
            action = last_step.get("action", {})
            is_finished = action.get("is_agent_finished", False)

            issues = []
            if not is_finished:
                issues.append("Trajectory not completed: final step is_agent_finished=False")

            content = action.get("content", "")
            if not content or not content.strip():
                issues.append("Trajectory not completed: final step not content")

            state = last_step.get("state", {})
            messages = state.get("messages", [])

            if not messages:
                issues.append("empty messages")
            else:
                roles = [msg.get("role") for msg in messages]
                if "user" not in roles:
                    issues.append("missing user message")
                if "assistant" not in roles:
                    issues.append("missing assistant message")

            if issues:
                return self._failed_result("; ".join(issues), key=ValidationMetrics.TRAJECTORY_COMPLETENESS)

            return self._success_result(key=ValidationMetrics.TRAJECTORY_COMPLETENESS)
        except Exception as e:
            return self._failed_result(reason=f"Verification failed: {str(e)}",
                                       key=ValidationMetrics.TRAJECTORY_COMPLETENESS)


@scorer_register(ValidationMetrics.TRAJECTORY_EFFICIENCY)
class TrajectoryEfficiencyScorer(TrajectoryValidator):
    def __init__(self, eval_config: EvaluationConfig = None, max_steps: int = 10, max_time: float = 60.0):
        super().__init__(name=ValidationMetrics.TRAJECTORY_EFFICIENCY, eval_config=eval_config)
        self.max_steps = max_steps
        self.max_time = max_time

    async def score(self, index: int, input: EvalDataCase, output: Any) -> ScorerResult:
        try:
            trajectory = self._parse_trajectory(output)
            step_count = len(trajectory)
            first_step = trajectory[0]
            last_step = trajectory[-1]

            start_time = first_step.get("meta", {}).get("execute_time", 0)
            end_time = last_step.get("meta", {}).get("execute_time", 0)
            total_time = end_time - start_time

            # step efficiency evaluation
            step_efficiency = min(1.0, self.max_steps / step_count)
            # time efficiency evaluation
            time_efficiency = min(1.0, self.max_time / max(total_time, 0.1))
            efficiency_score = (step_efficiency + time_efficiency) / 2

            message = (
                f"step: {step_count}/{self.max_steps}, "
                f"time: {total_time:.2f}s/{self.max_time}s, "
                f"score: {efficiency_score:.2f}"
            )

            if efficiency_score >= 0.5:
                logger.info(f"efficiency message: {message}")
                return self._success_result(key=ValidationMetrics.TRAJECTORY_EFFICIENCY)
            else:
                return self._failed_result(message, key=ValidationMetrics.TRAJECTORY_EFFICIENCY)
        except Exception as e:
            return self._failed_result(reason=f"Verification failed: {str(e)}",
                                       key=ValidationMetrics.TRAJECTORY_EFFICIENCY)


@scorer_register(ValidationMetrics.TRAJECTORY_QUALITY)
class TrajectoryQualityScorer(LlmValidator, TrajectoryValidator):
    def __init__(self, eval_config: EvaluationConfig = None, model_config: ModelConfig = None):
        super().__init__(name=ValidationMetrics.TRAJECTORY_QUALITY, eval_config=eval_config, model_config=model_config)

    def _build_judge_system_prompt(self) -> str:
        return """# Role
You are a Lead **Agent Trajectory Auditor**. Your objective is to evaluate the execution log of an AI Agent to ensure logical consistency, correct tool usage, and operational efficiency.

# Evaluation
Analyze the provided [Trajectory] based on the [User Goal] and [Available Tools].
Assign a score from **0.0 (Fail)** to **1.0 (Perfect)** for each dimension below. Then, calculate the final **Score**.

## Dimensions & Weights
1.  **Reasoning Logic (Weight: 0.4)**
    - **Criteria**: Is the `Thought` process coherent? Does the Agent correctly interpret the previous `Observation`? Does it avoid hallucinating data not present in the observation?
    - **1.0**: Perfect logic flow.
    - **0.0**: Complete hallucination or nonsense reasoning.

2.  **Tool Usage (Weight: 0.3)**
    - **Criteria**: Are the chosen tools appropriate? Are the arguments/parameters syntactically correct and contextually accurate?
    - **1.0**: Flawless tool calls.
    - **0.0**: Repeated invalid calls or nonexistent tools.

3.  **Efficiency (Weight: 0.2)**
    - **Criteria**: Is the path to the solution direct? Are there redundant steps or infinite loops?
    - **1.0**: Minimum necessary steps used.
    - **0.0**: Stuck in a loop or excessive wandering.

4.  **Goal Achievement (Weight: 0.1)**
    - **Criteria**: Did the sequence of actions actually result in the user's requested outcome?
    - **1.0**: Goal fully achieved.
    - **0.0**: Goal not met.

# Calculation Formula
Final Score = (Reasoning × 0.4) + (Tool Usage × 0.3) + (Efficiency × 0.2) + (Goal Achievement × 0.1)

# Output Format
Please strictly output in the following JSON format:
{
  "dimension_scores": {
    "reasoning": <float 0.0-1.0>,
    "tool_usage": <float 0.0-1.0>,
    "efficiency": <float 0.0-1.0>,
    "goal_achievement": <float 0.0-1.0>
  },
  "score": <float 0.00-1.00>,
  "critical_issues": [
    "List specific logical errors or loops. Empty list if perfect."
  ],
  "step_review": [
    {
      "step_index": 1,
      "status": "VALID | INVALID | SUBOPTIMAL",
      "comment": "Brief critique of this specific step."
    }
  ],
  "reason": "Concise explanation of the final score."
}

Please evaluate the following Agent Trajectory:
"""

    def build_judge_data(self, index: int, input: EvalDataCase, output: Any) -> str:
        try:
            trajectory = self._parse_trajectory(output)

            user_goal = input.case_data.get("user_input", "Unknown")
            available_tools = input.case_data.get("available_tools", [])
            steps_summary = []

            for i, step in enumerate(trajectory):
                action = step.get("action", {})
                content = action.get("content", "")
                tool_calls = action.get("tool_calls", [])

                step_info = f"step {i + 1}: "
                if tool_calls:
                    tool_names = [
                        tc.get("function", {}).get("name", "unknown")
                        for tc in tool_calls
                    ]
                    step_info += f"tool calls: {', '.join(tool_names)}"
                if content:
                    step_info += f" | output: {content[:100]}"

                steps_summary.append(step_info)

            prompt = f"""**User Goal**:
{user_goal}

**Available Tools**:
{available_tools}

**Trajectory**:
{steps_summary}
"""
            return prompt
        except Exception as e:
            return f"Verification failed: {str(e)}"
