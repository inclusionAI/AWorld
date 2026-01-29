# coding: utf-8
# Copyright (c) inclusionAI.
from aworld.config import EvaluationConfig
from aworld.evaluations.base import Scorer, EvalDataCase, ScorerResult, MetricResult
from aworld.evaluations.scorers import scorer_register
from aworld.ralph_loop.validate.base_validator import RuleValidator
from aworld.ralph_loop.validate.types import ValidationMetrics


@scorer_register(ValidationMetrics.READABILITY)
class ReadabilityScorer(RuleValidator):
    """Rule based readability evaluation."""

    def __init__(self, eval_config: EvaluationConfig = None):
        super().__init__(name=ValidationMetrics.PROFILE, eval_config=eval_config)

    async def score(self, index, input: EvalDataCase[dict], output: dict) -> ScorerResult:
        content = self._extract(output)

        avg_sentence_length = self._calculate_avg_sentence_length(content)
        avg_word_length = self._calculate_avg_word_length(content)
        paragraph_count = self._count_paragraphs(content)

        score = 1.0
        if avg_sentence_length > 50:
            score -= 0.2
        if avg_word_length > 8:
            score -= 0.1
        # No paragraph
        if paragraph_count == 0:
            score -= 0.2

        score = max(0.0, min(1.0, score))

        metric_result: MetricResult = {
            "value": score,
            "metadata": {
                "avg_sentence_length": avg_sentence_length,
                "avg_word_length": avg_word_length,
                "paragraph_count": paragraph_count
            }
        }

        return ScorerResult(
            scorer_name=self.name,
            metric_results={"readability": metric_result}
        )

    def _calculate_avg_sentence_length(self, text: str) -> float:
        import re

        sentences = re.split(r'[.!?]+', text)
        sentences = [s.strip() for s in sentences if s.strip()]
        if not sentences:
            return 0.0
        total_words = sum(len(s.split()) for s in sentences)
        return total_words / len(sentences)

    def _calculate_avg_word_length(self, text: str) -> float:
        words = text.split()
        if not words:
            return 0.0
        total_chars = sum(len(w) for w in words)
        return total_chars / len(words)

    def _count_paragraphs(self, text: str) -> int:
        paragraphs = [p.strip() for p in text.split('\n') if p.strip()]
        return len(paragraphs)


@scorer_register(ValidationMetrics.PROFILE)
class ProfileScorer(RuleValidator):
    """Rule based profile evaluation."""

    def __init__(self, eval_config: EvaluationConfig = None):
        super().__init__(name=ValidationMetrics.PROFILE, eval_config=eval_config)

    async def score(self, index, input: EvalDataCase[dict], output: dict) -> ScorerResult:
        # get metrics from output
        execution_time = output.get("execution_time", 0.0)
        memory_usage = output.get("memory_usage", 0.0)

        # get threshold from input
        max_execution_time = input.case_data.get("max_execution_time", 1000.0)
        max_memory_usage = input.case_data.get("max_memory_usage", 1000.0)

        time_score = 1.0 if execution_time <= max_execution_time else 0.5
        memory_score = 1.0 if memory_usage <= max_memory_usage else 0.5

        overall_score = (time_score + memory_score) / 2

        metric_result: MetricResult = {
            "value": overall_score,
            "metadata": {
                "execution_time": execution_time,
                "memory_usage": memory_usage,
                "max_execution_time": max_execution_time,
                "max_memory_usage": max_memory_usage,
                "time_score": time_score,
                "memory_score": memory_score
            }
        }

        return ScorerResult(
            scorer_name=self.name,
            metric_results={ValidationMetrics.PROFILE: metric_result}
        )
