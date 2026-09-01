from __future__ import annotations

import ast
import inspect

import pytest

from aworld.self_evolve.controllers import run_state as run_state_module
from aworld.self_evolve.controllers.run_execution import (
    CandidateEvaluationResult,
)
from aworld.self_evolve.controllers.run_state import (
    ExplicitRunStateAccumulator,
    GenerationFrontierState,
    VerificationFunnelRequest,
)
from aworld.self_evolve.optimizers.base import (
    CandidateGenerationOutcome,
    CandidateGenerationOutcomeKind,
)
from aworld.self_evolve.types import (
    CandidateVariant,
    EvaluationSummary,
    GateResult,
    SelfEvolveTargetRef,
)


def _summary(candidate_id: str, **metrics) -> EvaluationSummary:
    return EvaluationSummary(
        variant_id=candidate_id,
        metrics=metrics,
        dataset_split="validation",
    )


def _candidate(candidate_id: str = "candidate-1") -> CandidateVariant:
    return CandidateVariant(
        candidate_id=candidate_id,
        target=SelfEvolveTargetRef("skill", "demo", None),
        content="# Improved\n",
        rationale="exercise explicit run accumulation",
    )


def _result(
    *,
    candidate_id: str = "candidate-1",
    status: str = "rejected",
    gates: tuple[GateResult, ...] = (),
    feedback: tuple[EvaluationSummary, ...] | None = None,
) -> CandidateEvaluationResult:
    candidate_feedback = (
        (_summary(candidate_id, source="candidate"),)
        if feedback is None
        else feedback
    )
    state = {
        "candidate": _candidate(candidate_id),
        "baseline_summary": _summary("baseline", score=0.4),
        "candidate_summary": _summary(candidate_id, score=0.5),
        "held_out_summary": None,
        "replay_result": None,
        "replay_dataset": None,
        "gate_results": list(gates),
        "feedback": candidate_feedback,
        "status": status,
        "regression_evidence": None,
        "challenge_report": None,
    }
    return CandidateEvaluationResult.from_tuple(
        (
            state,
            {
                "candidate_id": candidate_id,
                "status": status,
                "failed_gates": [
                    gate.gate_name for gate in gates if not gate.passed
                ],
            },
            candidate_feedback,
        )
    )


def _merge_feedback(current, new):
    return (*current, *new)


def _record(
    accumulator: ExplicitRunStateAccumulator,
    result: CandidateEvaluationResult,
    *,
    consumed: bool = True,
    shared_measurement_failure=lambda _gate: False,
):
    return accumulator.record_candidate_evaluation(
        candidate_id="candidate-1",
        result=result,
        counts_toward_authoritative=True,
        merge_feedback=_merge_feedback,
        shared_measurement_failure=shared_measurement_failure,
        authoritative_attempt_consumed=lambda _state: consumed,
    )


def test_accumulator_records_authoritative_evidence_and_tiebreak() -> None:
    accumulator = ExplicitRunStateAccumulator()
    accumulator.begin_authoritative_candidate(
        "candidate-1",
        counts_toward_authoritative=True,
    )
    score_gate = GateResult(
        gate_name="score_improvement",
        passed=False,
        reason="tie-break remained inconclusive",
        details={"tiebreak_round": 1},
    )

    record = _record(accumulator, _result(gates=(score_gate,)))

    assert accumulator.authoritative_candidate_count == 1
    assert accumulator.authoritative_candidate_attempt_count == 1
    assert accumulator.authoritative_candidate_ids == {"candidate-1"}
    assert accumulator.authoritative_candidate_attempt_ids == {"candidate-1"}
    assert accumulator.score_tiebreak_candidate_count == 1
    assert accumulator.current_run_attempted_candidate_ids == {"candidate-1"}
    assert len(accumulator.validation_feedback) == 1
    assert accumulator.iteration_states == [record.state]
    assert accumulator.iteration_reports == [record.report_item]


def test_shared_measurement_failure_releases_slot_and_feedback() -> None:
    accumulator = ExplicitRunStateAccumulator()
    accumulator.begin_authoritative_candidate(
        "candidate-1",
        counts_toward_authoritative=True,
    )
    measurement_gate = GateResult(
        gate_name="trusted_improvement_measurement",
        passed=False,
        reason="shared control invalid",
        details={"failure_class": "measurement"},
    )
    record = _record(
        accumulator,
        _result(gates=(measurement_gate,)),
        consumed=False,
        shared_measurement_failure=lambda gate: (
            gate.gate_name == "trusted_improvement_measurement"
        ),
    )

    assert accumulator.authoritative_candidate_count == 0
    assert accumulator.authoritative_candidate_attempt_count == 1
    assert accumulator.authoritative_candidate_ids == set()
    assert accumulator.validation_feedback == ()
    assert accumulator.current_run_attempted_candidate_ids == set()
    assert record.feedback == ()
    assert record.state["feedback"] == ()

    decision = accumulator.finalize_candidate_record(
        record,
        shared_replay_failure_blocks_population=lambda _replay: False,
        infrastructure_prevented_comparable_evaluation=(
            lambda *_args, **_kwargs: False
        ),
    )

    assert decision.should_stop is True
    assert decision.stop_reason == "shared_measurement_invalid"
    assert accumulator.baseline_preflight_blocked is True
    assert accumulator.rejected_candidate_ids == set()


def test_prerequisite_result_is_tracked_without_rejection() -> None:
    accumulator = ExplicitRunStateAccumulator()
    accumulator.begin_authoritative_candidate(
        "candidate-1",
        counts_toward_authoritative=True,
    )
    prerequisite_gate = GateResult(
        gate_name="candidate_capability_tool",
        passed=False,
        reason="tool contract missing",
    )
    record = _record(
        accumulator,
        _result(status="prerequisite", gates=(prerequisite_gate,)),
    )

    decision = accumulator.finalize_candidate_record(
        record,
        shared_replay_failure_blocks_population=lambda _replay: False,
        infrastructure_prevented_comparable_evaluation=(
            lambda *_args, **_kwargs: False
        ),
    )

    assert decision.should_stop is False
    assert accumulator.prerequisite_candidate_ids == ["candidate-1"]
    assert accumulator.rejected_candidate_ids == set()


def test_infrastructure_and_acceptance_decisions_update_run_frontier() -> None:
    infrastructure = ExplicitRunStateAccumulator()
    infrastructure.begin_authoritative_candidate(
        "candidate-1",
        counts_toward_authoritative=True,
    )
    record = _record(
        infrastructure,
        _result(
            gates=(
                GateResult(
                    gate_name="evaluation",
                    passed=False,
                    reason="backend failed",
                ),
            )
        ),
    )
    blocked = infrastructure.finalize_candidate_record(
        record,
        shared_replay_failure_blocks_population=lambda _replay: False,
        infrastructure_prevented_comparable_evaluation=(
            lambda *_args, **_kwargs: True
        ),
    )
    assert blocked.stop_reason == "infrastructure_blocked"
    assert infrastructure.infrastructure_blocked is True

    accepted = ExplicitRunStateAccumulator()
    accepted.begin_authoritative_candidate(
        "candidate-1",
        counts_toward_authoritative=True,
    )
    accepted_record = _record(
        accepted,
        _result(status="accepted", gates=()),
    )
    selected = accepted.finalize_candidate_record(
        accepted_record,
        shared_replay_failure_blocks_population=lambda _replay: False,
        infrastructure_prevented_comparable_evaluation=(
            lambda *_args, **_kwargs: False
        ),
    )
    assert selected.accepted is True
    assert selected.stop_reason == "candidate_accepted"


def test_authoritative_reservation_underflow_is_rejected() -> None:
    accumulator = ExplicitRunStateAccumulator()

    with pytest.raises(RuntimeError, match="underflow"):
        _record(
            accumulator,
            _result(),
            consumed=False,
        )


def test_selection_projects_typed_evidence_and_fresh_rerun_policy() -> None:
    accumulator = ExplicitRunStateAccumulator()
    accumulator.begin_authoritative_candidate(
        "candidate-1",
        counts_toward_authoritative=True,
    )
    record = _record(
        accumulator,
        _result(status="rejected", gates=()),
    )

    ordinary = accumulator.select_iteration_evidence(
        fresh_evaluation_required=False,
        selector=lambda states: states[-1],
    )
    assert ordinary is not None
    assert ordinary.state is record.state
    assert ordinary.selected_candidate is not None
    assert ordinary.candidate_summary.metrics["score"] == 0.5

    fresh_rerun = accumulator.select_iteration_evidence(
        fresh_evaluation_required=True,
        selector=lambda states: states[-1],
    )
    assert fresh_rerun is not None
    assert fresh_rerun.selected_candidate is None
    assert fresh_rerun.candidate_summary is ordinary.candidate_summary


def test_generation_frontier_tracks_repeated_policy_and_materialization() -> None:
    frontier = GenerationFrontierState()
    outcome = CandidateGenerationOutcome(
        candidate_index=0,
        kind=CandidateGenerationOutcomeKind.POLICY_FILTERED,
        policy_id="scope-policy",
        enforcement="hard",
        repairable=True,
    )

    assert frontier.record_policy_filter_stall(
        signature="policy-signature",
        outcomes=(outcome,),
        fully_filtered=True,
        max_consecutive_stalls=2,
    ) is False
    assert frontier.record_policy_filter_stall(
        signature="policy-signature",
        outcomes=(outcome,),
        fully_filtered=True,
        max_consecutive_stalls=2,
    ) is True
    assert frontier.last_policy_filter_outcomes == (outcome,)

    assert frontier.record_materialization_stall(
        signature="materialization-signature",
        full_population_failed=True,
        max_consecutive_stalls=2,
    ) is False
    assert frontier.record_materialization_stall(
        signature="materialization-signature",
        full_population_failed=True,
        max_consecutive_stalls=2,
    ) is True
    assert frontier.stop_reason() == "materialization_frontier_repeated"


def test_generation_retry_and_duplicate_stalls_reset_atomically() -> None:
    frontier = GenerationFrontierState()

    assert frontier.claim_infrastructure_retry(
        retryable=True,
        max_retries=2,
    ) is True
    assert frontier.record_duplicate_population(
        all_candidates_previously_attempted=True,
        max_consecutive_stalls=2,
    ) is False
    frontier.last_policy_filter_signature = "policy"
    frontier.consecutive_policy_filter_stalls = 1

    frontier.reset_candidate_progress_stalls()

    assert frontier.infrastructure_retries == 0
    assert frontier.duplicate_population_stalls == 0
    assert frontier.consecutive_policy_filter_stalls == 0
    assert frontier.last_policy_filter_signature is None


def test_conformance_counterexamples_move_between_pending_and_resolved() -> None:
    frontier = GenerationFrontierState()

    first_repeated = frontier.record_conformance_counterexamples(
        observed={"counterexample-a", "counterexample-b"},
        by_stage={
            "candidate_screening": {
                "counterexample-a",
                "counterexample-b",
            }
        },
    )
    second_repeated = frontier.record_conformance_counterexamples(
        observed={"counterexample-b", "counterexample-c"},
        by_stage={"candidate_repair_conformance": {"counterexample-c"}},
    )

    assert first_repeated == set()
    assert second_repeated == {"counterexample-b"}
    assert frontier.pending_conformance_counterexamples == {
        "counterexample-b",
        "counterexample-c",
    }
    assert frontier.resolved_conformance_counterexamples == {
        "counterexample-a"
    }
    assert frontier.conformance_counterexamples_by_stage == {
        "candidate_screening": {
            "counterexample-a",
            "counterexample-b",
        },
        "candidate_repair_conformance": {"counterexample-c"},
    }


def test_conformance_strategy_transition_detects_unmaterialized_switch() -> None:
    frontier = GenerationFrontierState()

    initial = frontier.observe_conformance_strategies(
        signatures=("contract-a",),
        topology_by_signature={"contract-a": ("topology-1",)},
        max_switch_attempts=2,
    )
    materialized = frontier.observe_conformance_strategies(
        signatures=("contract-a",),
        topology_by_signature={"contract-a": ("topology-2",)},
        max_switch_attempts=2,
    )
    repeated = frontier.observe_conformance_strategies(
        signatures=("contract-a",),
        topology_by_signature={"contract-a": ("topology-2",)},
        max_switch_attempts=2,
    )

    assert initial.new_switch_requests == ("contract-a",)
    assert initial.prior_topology_fingerprints == ("topology-1",)
    assert materialized.materialized_switches == ("contract-a",)
    assert materialized.frontier_exhausted is False
    assert repeated.unmaterialized_switches == ("contract-a",)
    assert repeated.frontier_exhausted is True
    assert frontier.conformance_strategy_switch_not_materialized is True
    assert frontier.stop_reason() == (
        "conformance_strategy_switch_not_materialized"
    )


def test_generation_slots_and_repair_releases_share_one_counter() -> None:
    frontier = GenerationFrontierState()

    assert frontier.begin_generation_slots(2) == 1
    frontier.record_effective_candidate("candidate-1", consumes_slot=True)
    frontier.record_effective_candidate("candidate-2", consumes_slot=True)
    frontier.release_effective_candidates(
        {"candidate-1"},
        same_slot_repair=True,
    )

    assert frontier.candidate_generation_attempt_slot_count == 2
    assert frontier.effective_generated_candidate_ids == {"candidate-2"}
    assert frontier.generated_candidate_slot_count == 1
    assert frontier.conformance_same_slot_repair_count == 1

    frontier.exhaust_generation_limit(max_generated_candidates=2)
    assert frontier.frontier_exhausted is True
    assert frontier.repair_capacity_reserved is True
    assert frontier.stop_reason() == (
        "repair_capacity_reserved_without_typed_frontier"
    )


def test_verification_funnel_projects_accumulator_state() -> None:
    accumulator = ExplicitRunStateAccumulator(
        authoritative_candidate_count=1,
        authoritative_candidate_attempt_count=2,
        authoritative_candidate_ids={"candidate-1"},
        authoritative_candidate_attempt_ids={"candidate-1", "candidate-2"},
        prerequisite_candidate_ids=["candidate-3", "candidate-3"],
        score_tiebreak_candidate_count=1,
    )
    accumulator.generation.record_effective_candidate(
        "candidate-1",
        consumes_slot=True,
    )
    accumulator.generation.verification_frontier_exhausted = True

    report = accumulator.verification_funnel_report(
        VerificationFunnelRequest(
            screening_max_cases=3,
            repair_iteration_horizon=8,
            candidate_generation_batch_count=2,
            max_generated_candidates=4,
            repair_reserved_slot_count=1,
            unique_generated_candidate_count=2,
            policy_filtered_candidate_count=1,
            max_authoritative_candidates=2,
            max_score_tiebreak_candidates=1,
            authoritative_case_observations={
                "case-1": {"status": "passed"}
            },
        )
    )

    assert report["generated_candidate_slot_count"] == 1
    assert report["authoritative_candidate_count"] == 1
    assert report["authoritative_candidate_attempt_ids"] == [
        "candidate-1",
        "candidate-2",
    ]
    assert report["prerequisite_candidate_ids"] == ["candidate-3"]
    assert report["frontier_exhausted"] is True
    assert report["authoritative_case_observations"] == {
        "case-1": {"status": "passed"}
    }


def test_run_state_controller_does_not_import_runner() -> None:
    tree = ast.parse(inspect.getsource(run_state_module))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )

    assert "aworld.self_evolve.runner" not in imported_modules
