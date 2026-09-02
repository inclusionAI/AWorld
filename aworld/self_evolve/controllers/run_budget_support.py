"""Leaf budget helpers shared by Runner construction and startup."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

from aworld.self_evolve.budget import (
    BudgetStage,
    BudgetUsage,
    ZeroBudgetUsageProofProvider,
)
from aworld.self_evolve.history_support import (
    _non_negative_numeric_int as _non_negative_int,
)
from aworld.self_evolve.regression import RegressionEvidence
from aworld.self_evolve.types import EvaluationSummary

def _judge_actual_token_usage(
    *summaries: EvaluationSummary | None,
    expected_summary_count: int | None = None,
) -> tuple[int | None, str]:
    """Return complete usage or the strongest observed token lower bound."""

    total = 0
    sources: set[str] = set()
    executed = _unique_evaluation_summaries(
        summary
        for summary in summaries
        if summary is not None
        and summary.dataset_split != "single_case_replay"
        and summary.metrics.get("evaluation_fresh_execution") is not False
    )
    expected = (
        len(executed) if expected_summary_count is None else expected_summary_count
    )
    if isinstance(expected, bool) or expected < 0:
        raise ValueError("expected_summary_count must be non-negative")
    complete = len(executed) == expected
    for summary in executed:
        metrics = summary.metrics
        raw_total = metrics.get("judge_total_tokens")
        if (
            isinstance(raw_total, int)
            and not isinstance(raw_total, bool)
            and raw_total >= 0
        ):
            total += raw_total
            sources.add("judge_total_tokens")
            continue
        raw_input = metrics.get("judge_input_tokens_total")
        raw_output = metrics.get("judge_output_tokens_total")
        if all(
            isinstance(value, int) and not isinstance(value, bool) and value >= 0
            for value in (raw_input, raw_output)
        ):
            total += int(raw_input) + int(raw_output)
            sources.add("judge_input_output_tokens")
            continue
        estimated_input = metrics.get("judge_estimated_input_tokens_total")
        if (
            isinstance(estimated_input, (int, float))
            and not isinstance(estimated_input, bool)
            and estimated_input >= 0
        ):
            total += int(estimated_input)
            sources.add("judge_estimated_input_tokens_lower_bound")
            complete = False
            continue
        complete = False
    if not executed:
        return None, "reserved_fallback_missing_judge_telemetry"
    if not complete:
        return (
            total,
            "known_lower_bound_incomplete_judge_telemetry:"
            + ("+".join(sorted(sources)) or "missing_dimensions"),
        )
    return total, "+".join(sorted(sources))


def _unique_evaluation_summaries(
    summaries: Iterable[EvaluationSummary],
) -> tuple[EvaluationSummary, ...]:
    unique: list[EvaluationSummary] = []
    seen: set[str] = set()
    for index, summary in enumerate(summaries):
        metrics = summary.metrics
        execution_id = metrics.get("evaluation_alias_of_execution_id") or metrics.get(
            "evaluation_execution_id"
        )
        if not isinstance(execution_id, str) or not execution_id:
            if summary.dataset_split == "single_case_replay":
                continue
            execution_id = (
                f"legacy:{index}:{summary.variant_id}:{summary.dataset_split}"
            )
        if execution_id in seen:
            continue
        seen.add(execution_id)
        unique.append(summary)
    return tuple(unique)


def _same_evaluation_execution(
    first: EvaluationSummary,
    second: EvaluationSummary,
) -> bool:
    def execution_id(summary: EvaluationSummary) -> object:
        return summary.metrics.get(
            "evaluation_alias_of_execution_id"
        ) or summary.metrics.get("evaluation_execution_id")

    first_id = execution_id(first)
    second_id = execution_id(second)
    return isinstance(first_id, str) and bool(first_id) and first_id == second_id


def _execution_usage_report(
    *,
    optimizer_diagnostics: list[dict[str, object]],
    iteration_states: list[dict[str, object]],
    stages: Mapping[str, Mapping[str, Any]],
) -> dict[str, Mapping[str, int]]:
    candidate_tokens: dict[str, int] = {}
    for iteration in optimizer_diagnostics:
        diagnostics = iteration.get("diagnostics")
        if not isinstance(diagnostics, Mapping):
            continue
        population = diagnostics.get("candidate_population_execution")
        if not isinstance(population, Mapping):
            continue
        usage = population.get("token_usage")
        if not isinstance(usage, Mapping):
            continue
        for key, value in usage.items():
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                candidate_tokens[str(key)] = candidate_tokens.get(str(key), 0) + value

    judge_attempt_count = 0
    judge_estimated_input_tokens = 0
    judge_summaries: list[EvaluationSummary] = []
    for state in iteration_states:
        summaries = [
            state.get(key)
            for key in (
                "baseline_summary",
                "candidate_summary",
                "held_out_summary",
            )
        ]
        evidence = state.get("regression_evidence")
        if isinstance(evidence, RegressionEvidence):
            summaries.extend(
                summary
                for result in evidence.suite_results
                for summary in (
                    result.baseline_summary,
                    result.candidate_summary,
                )
                if result.fresh_execution
            )
        judge_summaries.extend(
            summary for summary in summaries if isinstance(summary, EvaluationSummary)
        )
    for summary in _unique_evaluation_summaries(judge_summaries):
        attempts = summary.metrics.get("judge_attempt_count")
        if isinstance(attempts, int) and not isinstance(attempts, bool):
            judge_attempt_count += max(0, attempts)
        estimated = summary.metrics.get("judge_estimated_input_tokens_total")
        if isinstance(estimated, (int, float)) and not isinstance(estimated, bool):
            judge_estimated_input_tokens += max(0, int(estimated))

    replay_stage = stages.get("replay", {})
    evaluation_stage = stages.get("evaluation", {})
    candidate_stage = stages.get("candidate_generation", {})
    return {
        "token_usage": {
            **candidate_tokens,
            "judge_estimated_input_tokens": judge_estimated_input_tokens,
        },
        "replay_usage": {
            "scheduled_repetition_tasks": _non_negative_int(
                replay_stage.get("item_count")
            ),
        },
        "evaluation_usage": {
            "scheduled_tasks": _non_negative_int(evaluation_stage.get("item_count")),
            "judge_attempt_count": judge_attempt_count,
        },
        "candidate_generation_usage": {
            "scheduled_slots": _non_negative_int(candidate_stage.get("item_count")),
        },
    }

def configured_budget_usage(
    *,
    tokens: int | None,
    cost_usd: float | Decimal | None,
    wall_seconds: float | Decimal | None,
    token_ceiling: int | None,
    cost_ceiling: Decimal | None,
    wall_ceiling: Decimal | None,
) -> BudgetUsage | None:
    """Resolve a complete configured estimate without confusing zero with unknown."""

    if (
        (token_ceiling is not None and tokens is None)
        or (cost_usd is None and cost_ceiling is not None)
        or (wall_seconds is None and wall_ceiling is not None)
    ):
        return None
    usage = BudgetUsage(
        tokens=0 if tokens is None else tokens,
        cost_usd=Decimal("0") if cost_usd is None else Decimal(str(cost_usd)),
        wall_seconds=(
            Decimal("0") if wall_seconds is None else Decimal(str(wall_seconds))
        ),
    )
    return None if usage == BudgetUsage() else usage


def backend_proves_zero_budget_usage(
    backend: object | None,
    stage: BudgetStage,
) -> bool:
    """Accept only an explicit stage-scoped backend proof of zero usage."""

    if not isinstance(backend, ZeroBudgetUsageProofProvider):
        return False
    try:
        return backend.proves_zero_budget_usage(stage) is True
    except Exception:
        return False


__all__ = [
    "_execution_usage_report",
    "_judge_actual_token_usage",
    "_same_evaluation_execution",
    "_unique_evaluation_summaries",
    "backend_proves_zero_budget_usage",
    "configured_budget_usage",
]
