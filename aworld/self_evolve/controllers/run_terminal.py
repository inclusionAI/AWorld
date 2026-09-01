"""Typed terminal-state and report projections for explicit-target runs."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from aworld.self_evolve.failure_events import FailureOwner
from aworld.self_evolve.measurement import (
    MeasurementPolicyMode,
    MeasurementSummary,
)
from aworld.self_evolve.provenance import InferredNewSkillPolicy
from aworld.self_evolve.types import (
    CandidateVariant,
    EvaluationSummary,
    GateResult,
    SelfEvolveRunStatus,
)


GatePredicate = Callable[[GateResult], bool]
InfrastructureEvaluationPredicate = Callable[..., bool]
StatusWithoutCandidate = Callable[
    [list[dict[str, object]]],
    SelfEvolveRunStatus,
]
VerifiedApplyPolicyPredicate = Callable[[str], bool]
ReleaseNormalizationProjector = Callable[
    [Mapping[str, object]],
    dict[str, object] | None,
]


def _typed_gates(gates: tuple[GateResult, ...]) -> tuple[GateResult, ...]:
    normalized = tuple(gates)
    if not all(isinstance(gate, GateResult) for gate in normalized):
        raise TypeError("terminal gate_results must contain typed gates")
    return normalized


@dataclass(frozen=True)
class TerminalSelectionRequest:
    """Candidate/gate state entering terminal measurement and promotion."""

    selected_candidate: CandidateVariant | None
    gate_results: tuple[GateResult, ...]

    def __post_init__(self) -> None:
        if self.selected_candidate is not None and not isinstance(
            self.selected_candidate,
            CandidateVariant,
        ):
            raise TypeError("selected_candidate must be typed when present")
        object.__setattr__(self, "gate_results", _typed_gates(self.gate_results))


@dataclass(frozen=True)
class TerminalSelectionRuntime:
    """Compatibility predicates needed to classify terminal gates."""

    candidate_prerequisite_failure: GatePredicate
    measurement_materialization_blocked: GatePredicate

    def __post_init__(self) -> None:
        for field_name in (
            "candidate_prerequisite_failure",
            "measurement_materialization_blocked",
        ):
            if not callable(getattr(self, field_name)):
                raise TypeError(f"{field_name} must be callable")


@dataclass(frozen=True)
class TerminalSelectionProjection:
    """Normalized terminal candidate identity and prerequisite state."""

    gate_results: tuple[GateResult, ...]
    evolvability_preflight_blocked: bool
    candidate_prerequisite_blocked: bool
    measurement_prerequisite_blocked: bool
    repair_focus_candidate: CandidateVariant | None
    reported_selected_candidate: CandidateVariant | None


def project_terminal_selection(
    request: TerminalSelectionRequest,
    *,
    runtime: TerminalSelectionRuntime,
) -> TerminalSelectionProjection:
    """Normalize terminal gates and distinguish repair focus from selection."""

    gates = tuple(request.gate_results)
    evolvability_preflight_blocked = any(
        gate.gate_name == "evolvability_preflight" and not gate.passed
        for gate in gates
    )
    if evolvability_preflight_blocked:
        gates = tuple(
            gate
            for gate in gates
            if gate.gate_name
            not in {
                "candidate_generation",
                "candidate_generation_exhausted_by_semantic_dedup",
                "no_candidate",
            }
        )
    candidate_prerequisite_blocked = bool(
        not evolvability_preflight_blocked
        and any(runtime.candidate_prerequisite_failure(gate) for gate in gates)
    )
    repair_focus_candidate = (
        request.selected_candidate if candidate_prerequisite_blocked else None
    )
    reported_selected_candidate = (
        None
        if repair_focus_candidate is not None
        else request.selected_candidate
    )
    return TerminalSelectionProjection(
        gate_results=gates,
        evolvability_preflight_blocked=evolvability_preflight_blocked,
        candidate_prerequisite_blocked=candidate_prerequisite_blocked,
        measurement_prerequisite_blocked=any(
            not gate.passed
            and runtime.measurement_materialization_blocked(gate)
            for gate in gates
        ),
        repair_focus_candidate=repair_focus_candidate,
        reported_selected_candidate=reported_selected_candidate,
    )


@dataclass(frozen=True)
class TerminalPromotionRequest:
    """Evidence and policy required to plan terminal application."""

    selected_candidate: CandidateVariant | None
    gate_results: tuple[GateResult, ...]
    apply_policy: str
    measurement_mode: MeasurementPolicyMode
    measurement_summary: MeasurementSummary | None
    fresh_evaluation_required: bool
    optimizer_diagnostics: tuple[dict[str, object], ...]
    baseline_summary: EvaluationSummary | None
    candidate_summary: EvaluationSummary | None
    inferred_draft_creation: bool
    inferred_new_skill_policy: InferredNewSkillPolicy

    def __post_init__(self) -> None:
        if self.selected_candidate is not None and not isinstance(
            self.selected_candidate,
            CandidateVariant,
        ):
            raise TypeError("selected_candidate must be typed when present")
        object.__setattr__(self, "gate_results", _typed_gates(self.gate_results))
        if self.apply_policy not in {
            "proposal",
            "verified_only",
            "auto_verified",
        }:
            raise ValueError(f"unsupported apply policy: {self.apply_policy}")
        if not isinstance(self.measurement_mode, MeasurementPolicyMode):
            raise TypeError("measurement_mode must be typed")
        if self.measurement_summary is not None and not isinstance(
            self.measurement_summary,
            MeasurementSummary,
        ):
            raise TypeError("measurement_summary must be typed when present")
        diagnostics = tuple(self.optimizer_diagnostics)
        if not all(isinstance(item, dict) for item in diagnostics):
            raise TypeError("optimizer_diagnostics must contain dictionaries")
        object.__setattr__(self, "optimizer_diagnostics", diagnostics)
        for field_name in ("baseline_summary", "candidate_summary"):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, EvaluationSummary):
                raise TypeError(f"{field_name} must be typed when present")
        if not isinstance(self.inferred_draft_creation, bool):
            raise TypeError("inferred_draft_creation must be boolean")
        if not isinstance(
            self.inferred_new_skill_policy,
            InferredNewSkillPolicy,
        ):
            raise TypeError("inferred_new_skill_policy must be typed")


@dataclass(frozen=True)
class TerminalPromotionRuntime:
    """Compatibility policies used by terminal status planning."""

    verified_apply_policy: VerifiedApplyPolicyPredicate
    infrastructure_prevented_comparable_evaluation: (
        InfrastructureEvaluationPredicate
    )
    status_without_selected_candidate: StatusWithoutCandidate

    def __post_init__(self) -> None:
        for field_name in (
            "verified_apply_policy",
            "infrastructure_prevented_comparable_evaluation",
            "status_without_selected_candidate",
        ):
            if not callable(getattr(self, field_name)):
                raise TypeError(f"{field_name} must be callable")


@dataclass(frozen=True)
class TerminalPromotionPlan:
    """Initial status plus whether Runner must execute an apply transaction."""

    final_status: SelfEvolveRunStatus
    should_apply: bool
    promotion: Mapping[str, object] | None = None


def plan_terminal_promotion(
    request: TerminalPromotionRequest,
    *,
    runtime: TerminalPromotionRuntime,
) -> TerminalPromotionPlan:
    """Classify the run and plan, but do not execute, target mutation."""

    final_status = SelfEvolveRunStatus.SUCCEEDED
    measurement_required_blocked = bool(
        request.measurement_mode is MeasurementPolicyMode.REQUIRED
        and (
            request.measurement_summary is None
            or not request.measurement_summary.promotion_eligible
        )
    )
    if request.selected_candidate is not None and measurement_required_blocked:
        return TerminalPromotionPlan(
            final_status=SelfEvolveRunStatus.REJECTED,
            should_apply=False,
        )
    if request.selected_candidate is None:
        failed_gates = tuple(
            gate for gate in request.gate_results if not gate.passed
        )
        if (
            request.fresh_evaluation_required
            and runtime.infrastructure_prevented_comparable_evaluation(
                failed_gates,
                baseline_summary=request.baseline_summary,
                candidate_summary=request.candidate_summary,
            )
        ):
            final_status = SelfEvolveRunStatus.FAILED
        else:
            final_status = runtime.status_without_selected_candidate(
                list(request.optimizer_diagnostics)
            )
        return TerminalPromotionPlan(
            final_status=final_status,
            should_apply=False,
        )
    if not runtime.verified_apply_policy(request.apply_policy):
        return TerminalPromotionPlan(
            final_status=final_status,
            should_apply=False,
        )

    failed_gates = tuple(
        gate for gate in request.gate_results if not gate.passed
    )
    if failed_gates:
        final_status = (
            SelfEvolveRunStatus.FAILED
            if runtime.infrastructure_prevented_comparable_evaluation(
                failed_gates,
                baseline_summary=request.baseline_summary,
                candidate_summary=request.candidate_summary,
            )
            else SelfEvolveRunStatus.REJECTED
        )
        return TerminalPromotionPlan(
            final_status=final_status,
            should_apply=False,
        )
    if (
        request.inferred_draft_creation
        and request.inferred_new_skill_policy is InferredNewSkillPolicy.DRAFT_ONLY
    ):
        return TerminalPromotionPlan(
            final_status=final_status,
            should_apply=False,
            promotion={
                "policy": request.inferred_new_skill_policy.value,
                "status": "draft_retained",
                "publication_allowed": False,
                "reason": (
                    "new-skill policy permits verified draft evolution only"
                ),
            },
        )
    return TerminalPromotionPlan(
        final_status=final_status,
        should_apply=True,
    )


def settle_post_apply_status(
    status: SelfEvolveRunStatus,
    post_apply: Mapping[str, object] | None,
) -> SelfEvolveRunStatus:
    """Reject an otherwise successful run when its apply transaction fails."""

    if post_apply is not None and post_apply.get("status") != "accepted":
        return SelfEvolveRunStatus.REJECTED
    return status


@dataclass(frozen=True)
class InferredDraftPromotionRequest:
    """Inputs for the persisted inferred-skill promotion report."""

    policy: InferredNewSkillPolicy
    apply_policy: str
    selected_candidate: CandidateVariant | None
    post_apply: Mapping[str, object] | None
    draft_path: str | None
    release_path: str | None
    runtime_registry_refresh_configured: bool
    initial_promotion: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.policy, InferredNewSkillPolicy):
            raise TypeError("inferred draft policy must be typed")
        if self.apply_policy not in {
            "proposal",
            "verified_only",
            "auto_verified",
        }:
            raise ValueError(f"unsupported apply policy: {self.apply_policy}")
        if self.selected_candidate is not None and not isinstance(
            self.selected_candidate,
            CandidateVariant,
        ):
            raise TypeError("selected_candidate must be typed when present")
        if self.post_apply is not None and not isinstance(
            self.post_apply,
            Mapping,
        ):
            raise TypeError("post_apply must be a mapping when present")
        for field_name in ("draft_path", "release_path"):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, str):
                raise TypeError(f"{field_name} must be a string when present")
        if not isinstance(self.runtime_registry_refresh_configured, bool):
            raise TypeError("runtime registry refresh flag must be boolean")
        if self.initial_promotion is not None and not isinstance(
            self.initial_promotion,
            Mapping,
        ):
            raise TypeError("initial_promotion must be a mapping when present")


def project_inferred_draft_promotion(
    request: InferredDraftPromotionRequest,
) -> dict[str, object]:
    """Project inferred-draft publication state without performing I/O."""

    post_apply = request.post_apply
    published = bool(
        request.apply_policy == "auto_verified"
        and post_apply is not None
        and post_apply.get("status") == "accepted"
    )
    post_apply_metrics = (
        post_apply.get("metrics")
        if isinstance(post_apply, Mapping)
        and isinstance(post_apply.get("metrics"), Mapping)
        else {}
    )
    registry_refresh_failed = (
        post_apply_metrics.get("registry_refresh_passed") is False
    )
    promotion = dict(request.initial_promotion or {})
    if not promotion:
        promotion = {
            "policy": request.policy.value,
            "status": (
                "published"
                if published
                else "verified_only"
                if post_apply is not None
                and post_apply.get("status") == "accepted"
                else "draft_retained"
                if request.selected_candidate is not None
                else "not_selected"
            ),
            "publication_allowed": bool(
                request.policy is InferredNewSkillPolicy.AUTO_VERIFIED
                and request.apply_policy == "auto_verified"
            ),
            "reason": (
                "verified skill was published"
                if published
                else "candidate was verified in a run-owned isolated registry"
                if post_apply is not None
                and post_apply.get("status") == "accepted"
                else "publication rolled back after registry refresh failure"
                if registry_refresh_failed
                else "candidate remains isolated in the run-owned draft"
            ),
        }
    promotion.update(
        {
            "draft_path": request.draft_path,
            "release_path": request.release_path,
            "registry_refresh_status": (
                "passed"
                if published and request.runtime_registry_refresh_configured
                else "failed"
                if registry_refresh_failed
                else "not_published"
            ),
        }
    )
    if registry_refresh_failed:
        promotion["registry_refresh_error"] = post_apply_metrics.get(
            "registry_refresh_error"
        )
    return promotion


def project_target_selection_promotion_diagnostics(
    diagnostics: Mapping[str, object] | None,
    promotion: Mapping[str, object],
) -> dict[str, object]:
    """Attach promotion state to target-selection diagnostics."""

    projected = dict(diagnostics or {})
    projected.update(
        {
            "draft_status": promotion["status"],
            "promotion_policy": promotion["policy"],
            "promotion_status": promotion["status"],
            "promotion_reason": promotion["reason"],
        }
    )
    return projected


@dataclass(frozen=True)
class MeasurementReportRequest:
    """Inputs for the terminal measurement report fragment."""

    summary: MeasurementSummary | None
    mode: MeasurementPolicyMode
    candidate_prerequisite_blocked: bool
    measurement_prerequisite_blocked: bool
    gate_results: tuple[GateResult, ...]

    def __post_init__(self) -> None:
        if self.summary is not None and not isinstance(
            self.summary,
            MeasurementSummary,
        ):
            raise TypeError("measurement summary must be typed when present")
        if not isinstance(self.mode, MeasurementPolicyMode):
            raise TypeError("measurement mode must be typed")
        for field_name in (
            "candidate_prerequisite_blocked",
            "measurement_prerequisite_blocked",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise TypeError(f"{field_name} must be boolean")
        object.__setattr__(self, "gate_results", _typed_gates(self.gate_results))


def project_measurement_report(
    request: MeasurementReportRequest,
    *,
    candidate_prerequisite_failure: GatePredicate,
    measurement_materialization_blocked: GatePredicate,
) -> dict[str, object] | None:
    """Project measured or typed prerequisite-blocked measurement state."""

    if request.summary is not None:
        return request.summary.to_dict()
    if request.candidate_prerequisite_blocked:
        prerequisite_gate = next(
            gate
            for gate in request.gate_results
            if candidate_prerequisite_failure(gate)
        )
        details = (
            prerequisite_gate.details
            if isinstance(prerequisite_gate.details, Mapping)
            else {}
        )
        return {
            "schema_version": "aworld.self_evolve.measurement_summary.v1",
            "mode": request.mode.value,
            "status": "not_started",
            "validity_status": "prerequisite_blocked",
            "measurement_readiness_stage": "candidate_admission_blocked",
            "decision_reason": str(
                details.get("code") or "candidate_admission_blocked"
            ),
            "effect_direction": "unmeasured",
            "independent_case_count": 0,
            "comparable_pair_count": 0,
            "promotion_eligible": False,
            "next_action": str(
                details.get("next_action")
                or (
                    "repair_framework_control_selection"
                    if details.get("failure_owner")
                    == FailureOwner.FRAMEWORK.value
                    else "continue_candidate_repair"
                )
            ),
        }
    if not request.measurement_prerequisite_blocked:
        return None
    prerequisite_gate = next(
        gate
        for gate in request.gate_results
        if not gate.passed and measurement_materialization_blocked(gate)
    )
    details = (
        prerequisite_gate.details
        if isinstance(prerequisite_gate.details, Mapping)
        else {}
    )
    return {
        "schema_version": "aworld.self_evolve.measurement_summary.v1",
        "mode": request.mode.value,
        "status": "not_started",
        "validity_status": "prerequisite_blocked",
        "measurement_readiness_stage": "contract_blocked",
        "decision_reason": str(
            details.get("code") or "measurement_prerequisite_blocked"
        ),
        "effect_direction": "unmeasured",
        "independent_case_count": 0,
        "comparable_pair_count": 0,
        "promotion_eligible": False,
        "next_action": str(
            details.get("next_action") or "repair_measurement"
        ),
    }


def project_post_apply_report(
    post_apply: Mapping[str, object] | None,
    *,
    release_normalization: ReleaseNormalizationProjector,
) -> dict[str, object]:
    """Project apply/publication state into terminal report fields."""

    if post_apply is None:
        return {}
    projected: dict[str, object] = {
        "post_apply": post_apply,
        "release_state": post_apply.get("release_state"),
        "published": post_apply.get("published") is True,
    }
    verified_target_path = post_apply.get("verified_target_path")
    if isinstance(verified_target_path, str):
        projected["verified_target_path"] = verified_target_path
    normalization = release_normalization(post_apply)
    if normalization is not None:
        projected["release_normalization"] = normalization
    return projected


def release_normalization_report(
    post_apply: Mapping[str, object],
) -> dict[str, object] | None:
    """Project verified-release normalization diagnostics."""

    metrics = post_apply.get("metrics")
    if not isinstance(metrics, Mapping):
        return None
    pre_fingerprint = metrics.get("pre_normalization_fingerprint")
    normalized_fingerprint = metrics.get("normalized_release_fingerprint")
    equivalence_passed = metrics.get("normalization_equivalence_passed")
    preserved_constraints = metrics.get("preserved_runtime_constraints")
    runtime_constraint_lesson_map = metrics.get(
        "runtime_constraint_lesson_map"
    )
    addressed_lesson_ids = metrics.get("addressed_lesson_ids")
    if (
        pre_fingerprint is None
        and normalized_fingerprint is None
        and equivalence_passed is None
    ):
        return None
    return {
        "pre_normalization_fingerprint": pre_fingerprint,
        "normalized_release_fingerprint": normalized_fingerprint,
        "normalization_verification_passed": equivalence_passed,
        "preserved_runtime_constraints": (
            list(preserved_constraints)
            if isinstance(preserved_constraints, list)
            else []
        ),
        "runtime_constraint_lesson_map": (
            list(runtime_constraint_lesson_map)
            if isinstance(runtime_constraint_lesson_map, list)
            else []
        ),
        "addressed_lesson_ids": (
            list(addressed_lesson_ids)
            if isinstance(addressed_lesson_ids, list)
            else []
        ),
        "removed_internal_line_count": metrics.get(
            "removed_internal_line_count"
        ),
        "structural_validation_passed": metrics.get(
            "structural_validation_passed"
        ),
        "structural_failure_code": metrics.get("structural_failure_code"),
        "structural_failure_field_path": metrics.get(
            "structural_failure_field_path"
        ),
        "normalization_structural_intent_rebind_passed": metrics.get(
            "normalization_structural_intent_rebind_passed"
        ),
        "failure_class": metrics.get("failure_class"),
        "failure_owner": metrics.get("failure_owner"),
        "failure_scope": metrics.get("failure_scope"),
        "repairable": metrics.get("repairable"),
        "structural_contract_fingerprint": metrics.get(
            "structural_contract_fingerprint"
        ),
        "status": post_apply.get("status"),
    }
