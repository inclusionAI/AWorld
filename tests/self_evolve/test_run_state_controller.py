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
