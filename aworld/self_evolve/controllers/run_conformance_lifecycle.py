"""Typed post-screening lifecycle for repairable conformance failures."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from aworld.self_evolve.controllers.run_iteration_helpers import (
    _candidate_conformance_counterexample_ids,
    _candidate_conformance_counterexample_stages,
    _candidate_conformance_failure_signatures,
    _candidate_conformance_repair_topologies,
    _candidate_conformance_result_observations,
    _candidate_conformance_stall_signature,
    _candidate_conformance_targeted_repair_feedback,
)
from aworld.self_evolve.controllers.run_state import ExplicitRunStateAccumulator
from aworld.self_evolve.feedback_diagnostics import _merge_validation_feedback
from aworld.self_evolve.types import CandidateVariant, EvaluationSummary, GateResult


@dataclass(frozen=True)
class ConformanceLifecyclePolicy:
    """Bounded retry policy for one semantic conformance failure."""

    max_stagnant_attempts: int = 3

    def __post_init__(self) -> None:
        if self.max_stagnant_attempts < 1:
            raise ValueError("max_stagnant_attempts must be positive")


@dataclass(frozen=True)
class ConformanceLifecycleRequest:
    failures: tuple[tuple[CandidateVariant, GateResult], ...]
    feedback: tuple[EvaluationSummary, ...]
    candidate_population_empty: bool
    screening_report: dict[str, object] | None
    validation_feedback: tuple[EvaluationSummary, ...]
    run_state: ExplicitRunStateAccumulator


@dataclass(frozen=True)
class ConformanceLifecycleRuntime:
    progress_callback: Callable[[str, str], Any] | None
    emit_progress: Callable[[Callable[[str, str], Any] | None, str, str], None]


@dataclass(frozen=True)
class ConformanceLifecycleResult:
    validation_feedback: tuple[EvaluationSummary, ...]
    should_stop: bool


def advance_conformance_lifecycle(
    request: ConformanceLifecycleRequest,
    runtime: ConformanceLifecycleRuntime,
    policy: ConformanceLifecyclePolicy = ConformanceLifecyclePolicy(),
) -> ConformanceLifecycleResult:
    """Advance repair state without coupling it to the main iteration loop."""

    run_state = request.run_state
    failures = request.failures
    validation_feedback = request.validation_feedback
    repair_candidate_ids = {
        candidate.candidate_id
        for candidate, gate in failures
        if gate.gate_name == "candidate_repair_conformance"
        and isinstance(gate.details, Mapping)
        and gate.details.get("failure_class") == "candidate"
        and gate.details.get("repairable") is True
    }
    if repair_candidate_ids:
        run_state.generation.release_effective_candidates(
            repair_candidate_ids,
            same_slot_repair=True,
        )
        if request.screening_report is not None:
            request.screening_report["same_slot_conformance_repair_ids"] = sorted(
                repair_candidate_ids
            )
            request.screening_report["effective_candidate_slot_count"] = (
                run_state.generation.generated_candidate_slot_count
            )

    observed_counterexamples = _candidate_conformance_counterexample_ids(failures)
    repeated_counterexamples = run_state.generation.record_conformance_counterexamples(
        observed=set(observed_counterexamples),
        by_stage=_candidate_conformance_counterexample_stages(failures),
    )
    repeated_candidate_ids: set[str] = set()
    if repeated_counterexamples:
        for candidate, gate in failures:
            candidate_counterexamples = _candidate_conformance_counterexample_ids(
                ((candidate, gate),)
            )
            if candidate_counterexamples & repeated_counterexamples:
                repeated_candidate_ids.add(candidate.candidate_id)
        run_state.generation.release_effective_candidates(
            repeated_candidate_ids,
            repeated_contract_replacement=True,
        )
        if request.screening_report is not None:
            request.screening_report["repeated_contract_replacement_candidate_ids"] = (
                sorted(repeated_candidate_ids)
            )
            request.screening_report["repeated_counterexample_ids"] = sorted(
                repeated_counterexamples
            )

    signatures = (
        _candidate_conformance_failure_signatures(failures)
        if request.feedback and request.candidate_population_empty
        else ()
    )
    if not signatures:
        return ConformanceLifecycleResult(
            validation_feedback=validation_feedback,
            should_stop=run_state.generation.conformance_frontier_exhausted,
        )

    transition = run_state.generation.observe_conformance_progress(
        signatures=signatures,
        result_observations_by_signature=(
            _candidate_conformance_result_observations(failures)
        ),
        strategy_by_signature=_candidate_conformance_repair_topologies(failures),
        max_stagnant_attempts=policy.max_stagnant_attempts,
    )
    if transition.frontier_exhausted:
        runtime.emit_progress(
            runtime.progress_callback,
            "candidate_conformance",
            (
                "Stopped candidate generation: the same typed conformance "
                f"violation remained after {policy.max_stagnant_attempts} "
                "focused repair attempts"
            ),
        )
        return ConformanceLifecycleResult(
            validation_feedback=validation_feedback,
            should_stop=True,
        )

    signature = _candidate_conformance_stall_signature(failures)
    if signature is not None:
        validation_feedback = _merge_validation_feedback(
            validation_feedback,
            (
                _candidate_conformance_targeted_repair_feedback(
                    signature=signature,
                    failures=failures,
                    prior_strategy_fingerprints=(
                        transition.prior_strategy_fingerprints
                    ),
                ),
            ),
        )
    runtime.emit_progress(
        runtime.progress_callback,
        "candidate_conformance",
        (
            "Typed conformance failure captured; requesting the smallest "
            "authorized source repair for the active executable violation"
        ),
    )
    return ConformanceLifecycleResult(
        validation_feedback=validation_feedback,
        should_stop=False,
    )


__all__ = [
    "ConformanceLifecyclePolicy",
    "ConformanceLifecycleRequest",
    "ConformanceLifecycleResult",
    "ConformanceLifecycleRuntime",
    "advance_conformance_lifecycle",
]
