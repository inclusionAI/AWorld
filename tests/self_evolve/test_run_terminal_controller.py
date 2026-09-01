from __future__ import annotations

import ast
import inspect

import pytest

from aworld.self_evolve.controllers import run_terminal as terminal_module
from aworld.self_evolve.controllers.run_terminal import (
    InferredDraftPromotionRequest,
    MeasurementReportRequest,
    TerminalPromotionRequest,
    TerminalPromotionRuntime,
    TerminalSelectionRequest,
    TerminalSelectionRuntime,
    plan_terminal_promotion,
    project_inferred_draft_promotion,
    project_measurement_report,
    project_post_apply_report,
    project_terminal_selection,
    settle_post_apply_status,
)
from aworld.self_evolve.measurement import (
    EffectDirection,
    ExperimentValidityStatus,
    MeasurementNextAction,
    MeasurementPolicyMode,
    MeasurementSummary,
    SwapAxis,
)
from aworld.self_evolve.provenance import InferredNewSkillPolicy
from aworld.self_evolve.types import (
    CandidateVariant,
    GateResult,
    SelfEvolveRunStatus,
    SelfEvolveTargetRef,
)


def _candidate() -> CandidateVariant:
    return CandidateVariant(
        candidate_id="candidate-1",
        target=SelfEvolveTargetRef("skill", "demo"),
        content="# Improved\n",
        rationale="exercise terminal projection",
    )


def _summary(*, promotion_eligible: bool = True) -> MeasurementSummary:
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
        promotion_eligible=promotion_eligible,
        decision_reason="positive effect established",
        next_action=MeasurementNextAction.PROMOTE_CANDIDATE,
        attribution_report_path=None,
        independent_case_count=2,
        comparable_pair_count=2,
        measurement_readiness_stage="minimum_independent_evidence",
    )


def _promotion_request(**overrides) -> TerminalPromotionRequest:
    values = {
        "selected_candidate": _candidate(),
        "gate_results": (),
        "apply_policy": "auto_verified",
        "measurement_mode": MeasurementPolicyMode.OFF,
        "measurement_summary": None,
        "fresh_evaluation_required": False,
        "optimizer_diagnostics": (),
        "baseline_summary": None,
        "candidate_summary": None,
        "inferred_draft_creation": False,
        "inferred_new_skill_policy": InferredNewSkillPolicy.AUTO_VERIFIED,
    }
    values.update(overrides)
    return TerminalPromotionRequest(**values)


def _promotion_runtime() -> TerminalPromotionRuntime:
    return TerminalPromotionRuntime(
        verified_apply_policy=lambda policy: policy in {
            "verified_only",
            "auto_verified",
        },
        infrastructure_prevented_comparable_evaluation=(
            lambda gates, **_kwargs: any(
                gate.details
                and gate.details.get("failure_class") == "infrastructure"
                for gate in gates
            )
        ),
        status_without_selected_candidate=(
            lambda _diagnostics: SelfEvolveRunStatus.REJECTED
        ),
    )


def test_terminal_selection_filters_synthetic_generation_gate() -> None:
    projection = project_terminal_selection(
        TerminalSelectionRequest(
            selected_candidate=None,
            gate_results=(
                GateResult("candidate_generation", False, "no candidate"),
                GateResult("evolvability_preflight", False, "not evolvable"),
            ),
        ),
        runtime=TerminalSelectionRuntime(
            candidate_prerequisite_failure=lambda _gate: False,
            measurement_materialization_blocked=lambda _gate: False,
        ),
    )

    assert projection.evolvability_preflight_blocked is True
    assert [gate.gate_name for gate in projection.gate_results] == [
        "evolvability_preflight"
    ]


def test_terminal_request_rejects_untyped_gates() -> None:
    with pytest.raises(TypeError, match="typed gates"):
        TerminalSelectionRequest(
            selected_candidate=None,
            gate_results=(object(),),  # type: ignore[arg-type]
        )


def test_terminal_selection_preserves_repair_focus_identity() -> None:
    prerequisite = GateResult(
        "candidate_composition_prerequisite",
        False,
        "support must be composed",
    )
    projection = project_terminal_selection(
        TerminalSelectionRequest(
            selected_candidate=_candidate(),
            gate_results=(prerequisite,),
        ),
        runtime=TerminalSelectionRuntime(
            candidate_prerequisite_failure=lambda gate: gate is prerequisite,
            measurement_materialization_blocked=lambda _gate: False,
        ),
    )

    assert projection.candidate_prerequisite_blocked is True
    assert projection.repair_focus_candidate == _candidate()
    assert projection.reported_selected_candidate is None


def test_terminal_promotion_requires_eligible_measurement() -> None:
    plan = plan_terminal_promotion(
        _promotion_request(
            measurement_mode=MeasurementPolicyMode.REQUIRED,
            measurement_summary=_summary(promotion_eligible=False),
        ),
        runtime=_promotion_runtime(),
    )

    assert plan.final_status is SelfEvolveRunStatus.REJECTED
    assert plan.should_apply is False


def test_terminal_promotion_separates_draft_policy_from_apply() -> None:
    draft_plan = plan_terminal_promotion(
        _promotion_request(
            inferred_draft_creation=True,
            inferred_new_skill_policy=InferredNewSkillPolicy.DRAFT_ONLY,
        ),
        runtime=_promotion_runtime(),
    )
    apply_plan = plan_terminal_promotion(
        _promotion_request(),
        runtime=_promotion_runtime(),
    )

    assert draft_plan.should_apply is False
    assert draft_plan.promotion is not None
    assert draft_plan.promotion["status"] == "draft_retained"
    assert apply_plan.should_apply is True
    assert settle_post_apply_status(
        apply_plan.final_status,
        {"status": "rejected"},
    ) is SelfEvolveRunStatus.REJECTED


def test_inferred_draft_promotion_projects_publication_and_refresh() -> None:
    promotion = project_inferred_draft_promotion(
        InferredDraftPromotionRequest(
            policy=InferredNewSkillPolicy.AUTO_VERIFIED,
            apply_policy="auto_verified",
            selected_candidate=_candidate(),
            post_apply={
                "status": "accepted",
                "metrics": {"registry_refresh_passed": True},
            },
            draft_path="/tmp/draft",
            release_path="/tmp/release",
            runtime_registry_refresh_configured=True,
        )
    )

    assert promotion == {
        "policy": "auto_verified",
        "status": "published",
        "publication_allowed": True,
        "reason": "verified skill was published",
        "draft_path": "/tmp/draft",
        "release_path": "/tmp/release",
        "registry_refresh_status": "passed",
    }


def test_measurement_report_projects_candidate_prerequisite() -> None:
    prerequisite = GateResult(
        "candidate_prerequisite",
        False,
        "missing support",
        details={
            "code": "support_missing",
            "failure_owner": "candidate",
        },
    )
    report = project_measurement_report(
        MeasurementReportRequest(
            summary=None,
            mode=MeasurementPolicyMode.REQUIRED,
            candidate_prerequisite_blocked=True,
            measurement_prerequisite_blocked=False,
            gate_results=(prerequisite,),
        ),
        candidate_prerequisite_failure=lambda gate: gate is prerequisite,
        measurement_materialization_blocked=lambda _gate: False,
    )

    assert report is not None
    assert report["measurement_readiness_stage"] == (
        "candidate_admission_blocked"
    )
    assert report["decision_reason"] == "support_missing"
    assert report["next_action"] == "continue_candidate_repair"


def test_post_apply_report_is_one_atomic_projection() -> None:
    post_apply = {
        "status": "accepted",
        "release_state": "verified",
        "published": True,
        "verified_target_path": "/tmp/verified",
    }
    report = project_post_apply_report(
        post_apply,
        release_normalization=lambda _value: {"status": "accepted"},
    )

    assert report["post_apply"] is post_apply
    assert report["release_state"] == "verified"
    assert report["published"] is True
    assert report["verified_target_path"] == "/tmp/verified"
    assert report["release_normalization"] == {"status": "accepted"}


def test_terminal_controller_does_not_import_runner() -> None:
    tree = ast.parse(inspect.getsource(terminal_module))
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_modules.update(
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )

    assert "aworld.self_evolve.runner" not in imported_modules
