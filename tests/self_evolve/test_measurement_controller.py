from types import SimpleNamespace

import pytest

from aworld.self_evolve.controllers.measurement import (
    CandidateMeasurementController,
    complete_measurement_usage,
    measurement_promotion_gate,
    measurement_target_resolution,
)
from aworld.self_evolve.measurement import (
    EffectDirection,
    ExperimentValidityStatus,
    MeasurementNextAction,
    MeasurementPolicyMode,
    MeasurementSummary,
    MeasurementUsage,
    SwapAxis,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore


def test_measurement_usage_stays_unknown_when_any_observation_is_incomplete() -> None:
    observations = (
        SimpleNamespace(usage=MeasurementUsage(tokens=10, wall_seconds=1.0)),
        SimpleNamespace(usage=MeasurementUsage(tokens=20)),
    )

    usage = complete_measurement_usage(
        observations,
        candidate_opportunities=2,
    )

    assert usage.tokens is None
    assert usage.wall_seconds is None
    assert usage.candidate_opportunities == 2


def test_direct_measurement_target_has_explicit_resolution_authority() -> None:
    resolution = measurement_target_resolution(None)

    assert resolution.confidence == 1.0
    assert resolution.origin == "direct_target_argument"
    assert resolution.inference_bypassed is True


def test_measurement_promotion_gate_preserves_measurement_decision() -> None:
    summary = MeasurementSummary(
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

    gate = measurement_promotion_gate(summary)

    assert gate.passed is True
    assert gate.gate_name == "trusted_improvement_measurement"
    assert gate.details["next_action"] == "promote_candidate"


def test_measurement_controller_rejects_empty_primary_metric(tmp_path) -> None:
    with pytest.raises(ValueError, match="primary_metric"):
        CandidateMeasurementController(
            store=FilesystemSelfEvolveStore(tmp_path),
            primary_metric=" ",
            summaries={},
        )
