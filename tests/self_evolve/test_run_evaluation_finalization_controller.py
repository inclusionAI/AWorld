from __future__ import annotations

import ast
import inspect
from dataclasses import replace
from types import SimpleNamespace

from aworld.self_evolve.budget import (
    CandidateAttemptKey,
    CandidateAttemptStage,
)
from aworld.self_evolve.controllers import (
    run_evaluation_finalization as finalization_module,
)
from aworld.self_evolve.controllers.run_evaluation_execution import (
    CandidateEvaluationExecutionResult,
)
from aworld.self_evolve.controllers.run_evaluation_finalization import (
    CandidateEvaluationFinalizationPolicy,
    CandidateEvaluationFinalizationRequest,
    CandidateEvaluationFinalizationRuntime,
    finalize_candidate_evaluation,
)
from aworld.self_evolve.controllers.run_execution import (
    CandidateEvaluationRequest,
)
from aworld.self_evolve.controllers.run_replay_execution import (
    CandidateReplayExecutionResult,
)
from aworld.self_evolve.datasets import EvalCase, SelfEvolveDataset
from aworld.self_evolve.measurement import (
    ControlledExperimentSpec,
    EffectDirection,
    ExperimentValidityStatus,
    MeasurementNextAction,
    MeasurementPolicyMode,
    MeasurementSummary,
    SwapAxis,
)
from aworld.self_evolve.types import (
    CandidateVariant,
    DatasetRecipe,
    EvaluationSummary,
    GateResult,
    SelfEvolveTargetRef,
)


def _dataset() -> SelfEvolveDataset:
    return SelfEvolveDataset(
        cases=(EvalCase(case_id="case-1", input={"content": "task"}),),
        recipe=DatasetRecipe(
            source={"kind": "evaluation-finalization-test"},
            split_seed="seed",
            splits={"train": ["case-1"]},
            trainable_case_ids=("case-1",),
        ),
    )


def _candidate() -> CandidateVariant:
    return CandidateVariant(
        candidate_id="candidate-1",
        target=SelfEvolveTargetRef("skill", "demo", None),
        content="# Improved\n",
        rationale="exercise evaluation finalization",
    )


def _summary(variant_id: str, score: float) -> EvaluationSummary:
    return EvaluationSummary(
        variant_id=variant_id,
        metrics={"score": score},
        dataset_split="validation",
    )


class _AttemptTracker:
    def __init__(self) -> None:
        self.events: list[tuple[CandidateAttemptStage, str | None]] = []

    def terminal(self, _key) -> bool:
        return False

    def emit(self, _key, stage, *, reason_code=None, **_kwargs) -> None:
        self.events.append((stage, reason_code))


def _evaluation_request(
    *,
    apply_policy: str = "proposal",
    target_type: str = "skill",
    attempt_tracker=None,
) -> CandidateEvaluationRequest:
    target_ref = SelfEvolveTargetRef(target_type, "demo", None)
    return CandidateEvaluationRequest(
        run_id="run-1",
        target=SimpleNamespace(identity=target_ref),
        dataset=_dataset(),
        candidate=_candidate(),
        apply_policy=apply_policy,
        target_provenance=None,
        iteration_number=1,
        candidate_number=1,
        candidate_count=1,
        attempt_key=(
            CandidateAttemptKey("run-1", 0, 0)
            if attempt_tracker is not None
            else None
        ),
        attempt_tracker=attempt_tracker,
    )


def _execution(
    *gates: GateResult,
    fresh_evaluation_completed: bool = True,
) -> CandidateEvaluationExecutionResult:
    return CandidateEvaluationExecutionResult(
        gate_results=gates,
        baseline_summary=_summary("baseline", 0.4),
        candidate_summary=_summary("candidate-1", 0.8),
        held_out_summary=None,
        regression_evidence=None,
        challenge_report=None,
        fresh_evaluation_completed=fresh_evaluation_completed,
    )


def _replay() -> CandidateReplayExecutionResult:
    return CandidateReplayExecutionResult(
        gate_results=(),
        replay_result=None,
        replay_dataset=None,
        replay_started=False,
    )


def _feedback_builder(**kwargs) -> tuple[EvaluationSummary, ...]:
    return (
        EvaluationSummary(
            variant_id=kwargs["candidate"].candidate_id,
            metrics={
                "failed_gates": [
                    gate.gate_name for gate in kwargs["failed_gates"]
                ]
            },
            dataset_split="validation",
        ),
    )


def _runtime(materialize_measurement) -> CandidateEvaluationFinalizationRuntime:
    return CandidateEvaluationFinalizationRuntime(
        materialize_measurement=materialize_measurement,
        typed_gate_failure=lambda gate: replace(
            gate,
            details={**dict(gate.details or {}), "typed": True},
        )
        if not gate.passed
        else gate,
        feedback_builder=_feedback_builder,
    )


def _policy(
    mode: MeasurementPolicyMode = MeasurementPolicyMode.OFF,
) -> CandidateEvaluationFinalizationPolicy:
    return CandidateEvaluationFinalizationPolicy(
        measurement_mode=mode,
        auto_apply_target_types=("skill",),
    )


def _request(
    *,
    evaluation: CandidateEvaluationRequest | None = None,
    execution: CandidateEvaluationExecutionResult | None = None,
    measurement_experiment: ControlledExperimentSpec | None = None,
) -> CandidateEvaluationFinalizationRequest:
    return CandidateEvaluationFinalizationRequest(
        evaluation=evaluation or _evaluation_request(),
        replay=_replay(),
        execution=execution or _execution(),
        measurement_experiment=measurement_experiment,
    )


def _unused_materializer(**_kwargs):
    raise AssertionError("measurement must not materialize")


def _measurement_summary() -> MeasurementSummary:
    return MeasurementSummary(
        experiment_id="experiment-00000000000000000000000000000000",
        mode=MeasurementPolicyMode.REQUIRED,
        swap_axis=SwapAxis.ARTIFACT,
        validity_status=ExperimentValidityStatus.VALID,
        effect_direction=EffectDirection.POSITIVE,
        effect_estimate=0.2,
        confidence_lower_bound=0.1,
        confidence_upper_bound=0.3,
        budget_normalized=True,
        promotion_eligible=True,
        decision_reason="positive effect established",
        next_action=MeasurementNextAction.PROMOTE_CANDIDATE,
        attribution_report_path=None,
        independent_case_count=2,
        comparable_pair_count=2,
        measurement_readiness_stage="minimum_independent_evidence",
    )


def test_proposal_preserves_candidate_failure_without_blocking_outcome() -> None:
    result = finalize_candidate_evaluation(
        _request(
            execution=_execution(
                GateResult(
                    "score_improvement",
                    False,
                    "score did not improve",
                    details={"failure_class": "candidate"},
                )
            )
        ),
        _policy(),
        _runtime(_unused_materializer),
    )

    assert result.state.status == "accepted"
    assert result.report_item["failed_gates"] == ["score_improvement"]
    assert result.state.gate_results[0].details["typed"] is True


def test_infrastructure_failure_blocks_attempt_and_proposal() -> None:
    tracker = _AttemptTracker()
    result = finalize_candidate_evaluation(
        _request(
            evaluation=_evaluation_request(attempt_tracker=tracker),
            execution=_execution(
                GateResult(
                    "evaluation",
                    False,
                    "backend failed",
                    details={"failure_class": "infrastructure"},
                )
            ),
        ),
        _policy(),
        _runtime(_unused_materializer),
    )

    assert result.state.status == "rejected"
    assert tracker.events == [
        (CandidateAttemptStage.BLOCKED, "candidate_evaluation_blocked")
    ]


def test_verified_apply_rejects_non_allowlisted_target_type() -> None:
    result = finalize_candidate_evaluation(
        _request(
            evaluation=_evaluation_request(
                apply_policy="verified_only",
                target_type="prompt",
            )
        ),
        _policy(),
        _runtime(_unused_materializer),
    )

    assert result.state.status == "rejected"
    gate = next(
        gate
        for gate in result.state.gate_results
        if gate.gate_name == "auto_apply_target_type"
    )
    assert gate.passed is False
    assert gate.details["target_type"] == "prompt"


def test_required_measurement_is_attached_to_state_and_report() -> None:
    experiment = object.__new__(ControlledExperimentSpec)
    materialization_calls: list[dict[str, object]] = []

    def materialize(**kwargs):
        materialization_calls.append(kwargs)
        return _measurement_summary()

    result = finalize_candidate_evaluation(
        _request(
            evaluation=_evaluation_request(apply_policy="verified_only"),
            measurement_experiment=experiment,
        ),
        _policy(MeasurementPolicyMode.REQUIRED),
        _runtime(materialize),
    )

    assert len(materialization_calls) == 1
    assert result.state.status == "accepted"
    assert result.state.payload["measurement_summary"].promotion_eligible
    assert result.report_item["measurement"]["promotion_eligible"] is True
    assert any(
        gate.gate_name == "trusted_improvement_measurement"
        and gate.passed
        for gate in result.state.gate_results
    )


def test_required_measurement_failure_becomes_typed_rejection() -> None:
    experiment = object.__new__(ControlledExperimentSpec)

    def failed_materialization(**_kwargs):
        raise ValueError("observation identity drifted")

    result = finalize_candidate_evaluation(
        _request(
            evaluation=_evaluation_request(apply_policy="verified_only"),
            measurement_experiment=experiment,
        ),
        _policy(MeasurementPolicyMode.REQUIRED),
        _runtime(failed_materialization),
    )

    assert result.state.status == "rejected"
    gate = next(
        gate
        for gate in result.state.gate_results
        if gate.gate_name == "trusted_improvement_measurement"
    )
    assert gate.details["code"] == "measurement_materialization_failed"
    assert gate.details["typed"] is True


def test_candidate_prerequisite_failure_skips_measurement_materialization() -> None:
    experiment = object.__new__(ControlledExperimentSpec)
    prerequisite = GateResult(
        gate_name="candidate_replay",
        passed=False,
        reason="candidate capability failed",
        details={
            "failure_class": "candidate",
            "failure_owner": "candidate",
            "failure_scope": "candidate",
            "repairable": True,
            "checkpoint_stage": "screening",
            "evaluator_skipped": True,
        },
    )

    result = finalize_candidate_evaluation(
        _request(
            execution=_execution(prerequisite),
            measurement_experiment=experiment,
        ),
        _policy(MeasurementPolicyMode.REQUIRED),
        _runtime(_unused_materializer),
    )

    assert "measurement_summary" not in result.state.payload
    assert all(
        gate.gate_name != "trusted_improvement_measurement"
        for gate in result.state.gate_results
    )


def test_finalization_controller_does_not_import_runner() -> None:
    tree = ast.parse(inspect.getsource(finalization_module))
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
