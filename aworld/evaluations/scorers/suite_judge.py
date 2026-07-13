# coding: utf-8
from __future__ import annotations

from aworld.evaluations.base import EvalDataCase, MetricResult, ScorerResult
from aworld.evaluations.scorers import scorer_register
from aworld.evaluations.base import Scorer
from aworld.evaluations.scorers.state_extractors import get_eval_state


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
        state = get_eval_state(output)
        if state:
            target = {**target, **state}
        execution = await self.suite.resolve_judge_backend().execute(case_input, target, self.suite)
        payload = self.suite.judge_schema.validate_payload(dict(execution.payload))

        metadata = {
            **payload,
            "_judge_backend": execution.backend_id,
        }
        if execution.diagnostics:
            metadata["_judge_diagnostics"] = [
                dict(item) for item in execution.diagnostics
            ]
        metric_result: MetricResult = {
            "value": float(payload["score"]),
            "metadata": metadata,
        }
        metric_results = {"score": metric_result}
        declared_trajectory_metrics = {
            scorer.metric_name
            for scorer in getattr(self.suite, "trajectory_scorers", tuple())
        }
        declared_runtime_metrics = {
            scorer.metric_name
            for scorer in getattr(self.suite, "outcome_scorers", tuple())
        } | set(getattr(self.suite, "reward_metrics", tuple())) | set(getattr(self.suite, "standard_metrics", tuple()))
        for metric_name, value in payload.items():
            if (
                metric_name == "score"
                or metric_name in declared_trajectory_metrics
                or metric_name in declared_runtime_metrics
                or not isinstance(value, (int, float, bool, str))
            ):
                continue
            metric_results[metric_name] = {
                "value": value,
                "metadata": metadata,
            }
        return ScorerResult(
            scorer_name=self.name,
            metric_results=metric_results,
        )
