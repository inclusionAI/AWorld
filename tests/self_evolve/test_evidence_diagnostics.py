from __future__ import annotations

from aworld.self_evolve.evidence_diagnostics import (
    EvidenceRepairConstraint,
    evidence_repair_constraints_from_metrics,
    merge_evidence_repair_constraints,
)
from aworld.self_evolve.failure_events import FailureOwner
from aworld.self_evolve.feedback import normalize_feedback_summary
from aworld.self_evolve.evolution_context import (
    _merge_typed_repair_constraints_across_feedback,
)
from aworld.self_evolve.gates import EvidenceQualityGate
from aworld.self_evolve.types import EvaluationSummary


def _constraint(
    *,
    subject_kind: str = "quantitative_claim",
    occurrence_count: int = 1,
) -> EvidenceRepairConstraint:
    return EvidenceRepairConstraint(
        subject_kind=subject_kind,
        failure_mode="unsupported_claim",
        source_layer="candidate_output",
        required_action="support_or_omit",
        owner=FailureOwner.CANDIDATE,
        occurrence_count=occurrence_count,
    )


def test_evidence_constraints_merge_multi_trajectory_occurrences_by_identity() -> None:
    merged = merge_evidence_repair_constraints(
        (_constraint(occurrence_count=2),),
        (_constraint(occurrence_count=3),),
        (_constraint(subject_kind="quote"),),
    )

    assert len(merged) == 2
    quantitative = next(
        item for item in merged if item.subject_kind == "quantitative_claim"
    )
    assert quantitative.occurrence_count == 5


def test_evolution_context_projects_cumulative_constraints_to_each_feedback_item() -> None:
    first = _constraint(occurrence_count=2).to_dict()
    second = _constraint(occurrence_count=3).to_dict()

    merged = _merge_typed_repair_constraints_across_feedback(
        (
            {
                "variant_id": "member-a",
                "evidence_repair_constraints": [first],
            },
            {
                "variant_id": "member-b",
                "evidence_repair_constraints": [second],
            },
        )
    )

    for item in merged:
        constraints = item["evidence_repair_constraints"]
        assert isinstance(constraints, list)
        assert constraints[0]["occurrence_count"] == 5


def test_valid_bundle_compaction_is_owned_by_framework_projection() -> None:
    constraints = evidence_repair_constraints_from_metrics(
        {
            "has_evidence": 1.0,
            "evidence_compacted": True,
            "evidence_incomplete": True,
            "evidence_bundle_valid": True,
            "evidence_bundle_entry_count": 4,
        }
    )

    assert len(constraints) == 1
    assert constraints[0].failure_mode == "projection_compacted"
    assert constraints[0].source_layer == "artifact_projection"
    assert constraints[0].owner is FailureOwner.FRAMEWORK


def test_incomplete_uncompacted_claim_support_remains_candidate_owned() -> None:
    constraints = evidence_repair_constraints_from_metrics(
        {
            "has_evidence": 1.0,
            "evidence_compacted": False,
            "evidence_incomplete": True,
            "evidence_bundle_valid": True,
        }
    )

    assert len(constraints) == 1
    assert constraints[0].failure_mode == "support_incomplete"
    assert constraints[0].owner is FailureOwner.CANDIDATE


def test_runtime_read_budget_constraint_merges_with_judge_constraint() -> None:
    candidate_constraint = _constraint().to_dict()

    constraints = evidence_repair_constraints_from_metrics(
        {
            "evidence_incomplete": True,
            "judge_artifact_projection_incomplete": True,
            "evidence_repair_constraints": [candidate_constraint],
        }
    )

    assert {constraint.owner for constraint in constraints} == {
        FailureOwner.CANDIDATE,
        FailureOwner.FRAMEWORK,
    }
    framework_constraint = next(
        constraint
        for constraint in constraints
        if constraint.owner is FailureOwner.FRAMEWORK
    )
    assert framework_constraint.required_action == "expand_bounded_projection"


def test_runtime_read_budget_does_not_create_constraint_after_complete_judgment() -> None:
    constraints = evidence_repair_constraints_from_metrics(
        {
            "evidence_incomplete": False,
            "judge_artifact_projection_incomplete": True,
        }
    )

    assert constraints == ()


def test_feedback_preserves_typed_constraint_identity_and_required_action() -> None:
    constraint = _constraint().to_dict()

    feedback = normalize_feedback_summary(
        EvaluationSummary(
            variant_id="candidate",
            dataset_split="validation",
            metrics={
                "failed_gates": ["evidence_quality"],
                "evidence_repair_constraints": [constraint],
            },
        )
    )

    assert feedback["evidence_repair_constraints"] == [constraint]
    assert "support_or_omit" in feedback["required_behaviors"]


def test_evidence_quality_gate_exposes_typed_framework_ownership() -> None:
    gate = EvidenceQualityGate().evaluate(
        EvaluationSummary(
            variant_id="candidate",
            dataset_split="validation",
            metrics={
                "has_evidence": 1.0,
                "evidence_block_count": 3,
                "evidence_compacted": True,
                "evidence_incomplete": True,
                "evidence_bundle_valid": True,
                "evidence_bundle_entry_count": 3,
                "evidence_strategy_passed": True,
                "evidence_manifest_entry_count": 3,
            },
        )
    )

    assert gate.passed is False
    assert gate.details is not None
    assert gate.details["failure_owner"] == "framework"
    constraints = gate.details["evidence_repair_constraints"]
    assert isinstance(constraints, list)
    assert constraints[0]["constraint_identity_digest"]
