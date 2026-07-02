# coding: utf-8
from __future__ import annotations

from typing import Any, Mapping

from aworld.evaluations.base import EvalDataCase, MetricResult, Scorer, ScorerResult
from aworld.evaluations.runtime_composition import (
    RolloutState,
    StateCheckGrader,
    StepReward,
    aggregate_step_rewards,
)
from aworld.evaluations.scorers import scorer_register
from aworld.evaluations.scorers.state_extractors import get_eval_state


def _rollout_state_from_output(input: EvalDataCase[dict], output: Any) -> RolloutState:
    state = get_eval_state(output)
    raw = state.get("raw_response") if isinstance(state.get("raw_response"), Mapping) else {}
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), Mapping) else {}
    outcome = raw.get("outcome") if isinstance(raw.get("outcome"), Mapping) else artifacts.get("outcome", {})
    rewards = []
    for reward in raw.get("step_rewards") or []:
        if isinstance(reward, Mapping):
            rewards.append(
                StepReward(
                    metric_name=str(reward["metric_name"]),
                    step_index=int(reward["step_index"]),
                    value=float(reward["value"]),
                    weight=float(reward.get("weight", 1.0)),
                    partial_credit=bool(reward.get("partial_credit", False)),
                    reason=str(reward.get("reason", "")),
                    metadata=dict(reward.get("metadata") or {}),
                )
            )
    return RolloutState(
        case_id=getattr(input, "eval_case_id", str(state.get("case_id", ""))),
        status=str(state.get("status", "success")),
        answer=state.get("answer"),
        outcome=dict(outcome or {}),
        step_rewards=rewards,
        usage=dict(state.get("usage") or {}),
        timing=dict(state.get("timing") or {}),
        standard_metrics=dict((state.get("metadata") or {}).get("standard_metrics") or {}),
        metadata=dict(state.get("metadata") or {}),
    )


@scorer_register("runtime_outcome")
class RuntimeOutcomeScorer(Scorer):
    def __init__(self, name: str = "runtime_outcome", **kwargs):
        super().__init__(name=name)

    async def score(self, index: int, input: EvalDataCase[dict], output: dict) -> ScorerResult:
        state = _rollout_state_from_output(input, output)
        metric_results: dict[str, MetricResult] = {}
        target = dict(input.case_data.get("_target", {})) if isinstance(input.case_data, Mapping) else {}
        for metric_name, criteria in self.eval_criterias.items():
            params = dict(criteria.scorer_params or {})
            grader_payload = params.get("grader") or {}
            grader = StateCheckGrader(
                metric_name=metric_name,
                source=str(grader_payload.get("source", "outcome")),
                path=tuple(grader_payload.get("path") or ()),
                op=str(grader_payload.get("op", "==")),
                expected=grader_payload.get("expected"),
                weight=float(grader_payload.get("weight", 1.0)),
                required=bool(grader_payload.get("required", True)),
            )
            metric_results[metric_name] = grader.grade(state=state, case=input, target=target).to_metric_result()
        return ScorerResult(scorer_name=self.name, metric_results=metric_results)


@scorer_register("runtime_reward")
class RuntimeRewardScorer(Scorer):
    def __init__(self, name: str = "runtime_reward", **kwargs):
        super().__init__(name=name)

    async def score(self, index: int, input: EvalDataCase[dict], output: dict) -> ScorerResult:
        state = _rollout_state_from_output(input, output)
        return ScorerResult(scorer_name=self.name, metric_results=aggregate_step_rewards(state))


@scorer_register("runtime_standard_metric")
class RuntimeStandardMetricScorer(Scorer):
    def __init__(self, name: str = "runtime_standard_metric", **kwargs):
        super().__init__(name=name)

    async def score(self, index: int, input: EvalDataCase[dict], output: dict) -> ScorerResult:
        state = _rollout_state_from_output(input, output)
        metric_results: dict[str, MetricResult] = {}
        for metric_name in self.eval_criterias:
            metric_results[metric_name] = {"value": state.standard_metrics.get(metric_name, 0)}
        return ScorerResult(scorer_name=self.name, metric_results=metric_results)
