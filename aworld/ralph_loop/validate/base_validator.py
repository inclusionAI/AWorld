# coding: utf-8
# Copyright (c) inclusionAI.
from typing import Any

from aworld.evaluations.base import MetricResult, Scorer
from aworld.evaluations.scorers.llm_as_judge import LLMAsJudgeScorer


class RuleValidator(Scorer):
    """Unified content extraction."""

    def _extract(self, output: Any, key: str = "content") -> str:
        # output may not be a dict
        if isinstance(output, str):
            return output
        elif isinstance(output, dict):
            return output.get(key, output.get("text", str(output)))
        else:
            return str(output)


class LlmValidator(LLMAsJudgeScorer):
    """Unified post parsing processing."""

    def convert_judge_response_to_score(self, judge_response: str):
        data = self.fetch_json_from_result(judge_response)

        score = float(data.get("score", 0.0))
        metric_result: MetricResult = {
            "value": score,
            "metadata": data
        }
        return {self.name: metric_result}
