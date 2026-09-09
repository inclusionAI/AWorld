from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Any


@dataclass(frozen=True)
class PopulationBudget:
    population_size: int = 5
    max_replay_candidates: int = 2
    hard_max_replay_candidates: int = 5
    no_op_threshold: float = 0.2


@dataclass(frozen=True)
class CandidateStrategy:
    strategy_id: str
    candidate_family: str
    intended_behavior_delta: str
    lessons_addressed: tuple[str, ...] = ()
    harness_diagnostics_considered: tuple[str, ...] = ()
    success_behaviors_preserved: tuple[str, ...] = ()
    expected_metric_impact: float = 0.0
    evidence_confidence: float = 0.0
    risk: float = 0.0
    complexity: float = 0.0
    patch_operation_count: int = 0
    runtime_instruction_diff_chars: int = 0
    severity_coverage: float = 0.0
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class RankedCandidateStrategy:
    strategy: CandidateStrategy
    priority_score: float
    replay_selected: bool
    not_replayed_reason: str | None = None


def rank_candidate_strategies(
    strategies: tuple[CandidateStrategy, ...],
    *,
    budget: PopulationBudget = PopulationBudget(),
) -> tuple[RankedCandidateStrategy, ...]:
    if not strategies:
        return tuple()
    replay_limit = max(
        0,
        min(budget.max_replay_candidates, budget.hard_max_replay_candidates),
    )
    scored = [
        (
            strategy,
            _priority_score(strategy),
        )
        for strategy in strategies[: max(1, budget.population_size)]
    ]
    scored.sort(key=lambda item: _ranking_key(item[0], item[1]))
    if scored and scored[0][1] < budget.no_op_threshold:
        return tuple(
            RankedCandidateStrategy(
                strategy=strategy,
                priority_score=score,
                replay_selected=False,
                not_replayed_reason="below_no_op_threshold",
            )
            for strategy, score in scored
        )
    ranked: list[RankedCandidateStrategy] = []
    for index, (strategy, score) in enumerate(scored):
        selected = index < replay_limit
        ranked.append(
            RankedCandidateStrategy(
                strategy=strategy,
                priority_score=score,
                replay_selected=selected,
                not_replayed_reason=None if selected else "not_replayed_due_to_budget",
            )
        )
    return tuple(ranked)


def _priority_score(strategy: CandidateStrategy) -> float:
    lesson_coverage = _bounded(len(strategy.lessons_addressed) / 3)
    preserves_success_path = _bounded(len(strategy.success_behaviors_preserved) / 2)
    return (
        lesson_coverage * 0.30
        + preserves_success_path * 0.25
        + _bounded(strategy.expected_metric_impact) * 0.20
        + _bounded(strategy.evidence_confidence) * 0.15
        - _bounded(strategy.risk) * 0.10
        - _bounded(strategy.complexity) * 0.10
    )


def _ranking_key(strategy: CandidateStrategy, score: float) -> tuple[float, int, int, float, str]:
    return (
        -score,
        strategy.patch_operation_count,
        strategy.runtime_instruction_diff_chars,
        -_bounded(strategy.severity_coverage),
        strategy.strategy_id,
    )


def _bounded(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
