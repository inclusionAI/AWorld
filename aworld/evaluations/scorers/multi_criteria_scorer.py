# coding: utf-8
# Copyright (c) inclusionAI.
from typing import List, Dict
from aworld.evaluations.scorers.base_validator import LLMAsJudgeScorer
from aworld.evaluations.base import MetricResult, EvalDataCase, EvalCriteria


class MultiCriteriaScorer(LLMAsJudgeScorer):
    def __init__(self, criteria_list: List[EvalCriteria], model_config=None, **kwargs):
        super().__init__(name="multi_criteria", model_config=model_config, **kwargs)
        self.criteria_list = criteria_list

    def _build_judge_system_prompt(self) -> str:
        criteria_text = "\n".join([
            f"{i + 1}. {c.criteria} (Weight: {c.max_value})"
            for i, c in enumerate(self.criteria_list)
        ])

        return f"""# Role
You are a professional quality assessment expert. You need to evaluate the output content one by one based on the following criteria.

# Evaluation Criteria
{criteria_text}

# Evaluation Rule
For each criterion, determine whether it meets:
- Satisfaction: Give weight points to the standard
- Not satisfied: give 0 points
- Partial satisfaction: Give partial scores (between 0 and weight points)

# Output Format
# Please output strictly in the following JSON format:
{{
  "criteria_scores": [
      {{"index": 0, "score": <float>, "satisfied": <bool>, "reason": "<string>"}},
      {{"index": 1, "score": <float>, "satisfied": <bool>, "reason": "<string>"}},
      ...
  ],
  "total_score": <float>,
  "summary": "<string>"
}}
"""

    def build_judge_data(self, index: int, input: EvalDataCase, output: dict) -> str:
        question = input.case_data.get("description", input.case_data.get("question", ""))
        answer = output.get("answer", str(output))

        return f"""# Task Description
{question}

# Model Output
{answer}

Please evaluate according to the evaluation criteria indicated in the system prompt.
"""

    def convert_judge_response_to_score(self, judge_response: str) -> Dict[str, MetricResult]:
        data = self.fetch_json_from_result(judge_response)
        criteria_scores = data.get("criteria_scores", [])
        return {self.name: MetricResult(
            value=data.get("total_score", 0.0),
            metadata={"summary": data.get("summary", ""), "scores": criteria_scores}
        )}
