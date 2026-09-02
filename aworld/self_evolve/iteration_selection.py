"""Iteration selection and ranking policy."""

from __future__ import annotations

import math
from typing import Mapping, Sequence

from aworld.self_evolve.evaluation_reporting import _metric_number
from aworld.self_evolve.types import EvaluationSummary, GateResult


def _select_iteration_state(
    iteration_states: list[dict[str, object]],
) -> dict[str, object] | None:
    if not iteration_states:
        return None
    for state in iteration_states:
        if state.get("status") == "accepted":
            return state
    return max(
        enumerate(iteration_states),
        key=lambda item: (_iteration_candidate_score(item[1]), item[0]),
    )[1]


def _iteration_candidate_score(
    state: Mapping[str, object],
) -> tuple[int, int, int, float, float, int, int]:
    summary = state.get("candidate_summary")
    score = float("-inf")
    if isinstance(summary, EvaluationSummary):
        candidate_score = _metric_number(summary.metrics, "score")
        if candidate_score is not None:
            score = candidate_score
    gate_results = state.get("gate_results")
    gates = tuple(gate_results) if isinstance(gate_results, (list, tuple)) else ()
    failed_count = sum(
        1 for gate in gates if isinstance(gate, GateResult) and not gate.passed
    )
    passed_count = sum(
        1 for gate in gates if isinstance(gate, GateResult) and gate.passed
    )
    failed_gate_names = {
        gate.gate_name
        for gate in gates
        if isinstance(gate, GateResult) and not gate.passed
    }
    gate_names = {gate.gate_name for gate in gates if isinstance(gate, GateResult)}
    substantive_evaluation = failed_gate_names != {"duplicate_rejected_candidate"}
    reached_evaluation = isinstance(summary, EvaluationSummary)
    reached_replay = (
        state.get("replay_result") is not None
        or bool(gate_names & {"candidate_replay", "replay_confidence"})
        or reached_evaluation
    )
    adaptation_compiled = reached_replay or any(
        isinstance(gate, GateResult)
        and gate.gate_name == "replay_adaptation"
        and gate.passed
        for gate in gates
    )
    progress_rank = (
        3
        if reached_evaluation
        else 2
        if reached_replay
        else 1
        if adaptation_compiled
        else 0
    )
    paired_delta = _iteration_candidate_paired_delta(state, gates=gates)
    return (
        int(substantive_evaluation),
        progress_rank,
        int(paired_delta is not None),
        paired_delta if paired_delta is not None else float("-inf"),
        score,
        -failed_count,
        passed_count,
    )


def _iteration_candidate_paired_delta(
    state: Mapping[str, object],
    *,
    gates: Sequence[object],
) -> float | None:
    """Return a comparable baseline-to-candidate score effect when available.

    Candidate evaluations can use different judge baselines. Absolute score is
    therefore not a causal ranking signal across rejected candidates: a proven
    regression against a high baseline must not displace a positive paired
    effect against a lower baseline and become the Campaign checkpoint.
    """

    for gate in gates:
        if not isinstance(gate, GateResult) or gate.gate_name != "score_improvement":
            continue
        details = gate.details
        if not isinstance(details, Mapping):
            continue
        comparability = details.get("comparability")
        if (
            isinstance(comparability, Mapping)
            and comparability.get("comparable") is False
        ):
            return None
        delta = details.get("delta")
        if (
            isinstance(delta, (int, float))
            and not isinstance(delta, bool)
            and math.isfinite(float(delta))
        ):
            return float(delta)

    baseline = state.get("baseline_summary")
    candidate = state.get("candidate_summary")
    if not isinstance(baseline, EvaluationSummary) or not isinstance(
        candidate, EvaluationSummary
    ):
        return None
    baseline_score = _metric_number(baseline.metrics, "score")
    candidate_score = _metric_number(candidate.metrics, "score")
    if baseline_score is None or candidate_score is None:
        return None
    delta = candidate_score - baseline_score
    return delta if math.isfinite(delta) else None


def _candidate_generation_limit(
    *,
    replay_candidate_limit: int,
) -> int:
    return max(1, replay_candidate_limit)
