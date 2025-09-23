from aworld.evaluations.base import EvalDataCase, EvalCaseDataType, MetricResult
from aworld.config.conf import ModelConfig
from aworld.logs.util import logger
import json
import re
from typing import Optional
from aworld.evaluations.scorers.metrics import MetricNames
from aworld.evaluations.scorers.scorer_registry import scorer_register
from aworld.evaluations.scorers.llm_as_judge import LLMAsJudgeScorer


@scorer_register(MetricNames.ANSWER_ACCURACY)
class AnswerAccuracyLLMScorer(LLMAsJudgeScorer):

    def build_judge_agent_prompt(self, index: int, input: EvalDataCase[EvalCaseDataType], output: dict) -> str:
        return ""

    def build_judge_system_prompt(self) -> str:
        return "You are a mathematical calculation agent."

    def build_judge_agent_task_input(self, index: int, input: EvalDataCase[EvalCaseDataType], output: dict) -> str:
        return super().build_judge_agent_task_input(index, input, output)

    def convert_judge_response_to_score(self, judge_response: str) -> Optional[dict[str, MetricResult]]:
        return super().convert_judge_response_to_score(judge_response)
