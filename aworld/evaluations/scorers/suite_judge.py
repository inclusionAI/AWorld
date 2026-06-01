# coding: utf-8
from __future__ import annotations

from aworld.evaluations.base import EvalDataCase, MetricResult, ScorerResult
from aworld.evaluations.scorers import scorer_register
from aworld.evaluations.base import Scorer


@scorer_register("score")
class SuiteJudgeScorer(Scorer):
    def __init__(self, suite=None, name: str = None, **kwargs):
        super().__init__(name=name or getattr(suite, "suite_id", None), **kwargs)
        self.suite = suite

    async def score(self, index: int, input: EvalDataCase[dict], output: dict) -> ScorerResult:
        if self.suite is None:
            raise ValueError("suite judge is required for suite-backed evaluation")

        case_input = dict(input.case_data)
        target = dict(case_input.get("_target", {}))
        execution = await self.suite.resolve_judge_backend().execute(case_input, target, self.suite)
        payload = dict(execution.payload)
        self.suite.judge_schema.validate(payload)

        metric_result: MetricResult = {
            "value": float(payload["score"]),
            "metadata": {
                **payload,
                "_judge_backend": execution.backend_id,
            },
        }
        return ScorerResult(
            scorer_name=self.name,
            metric_results={"score": metric_result},
        )
