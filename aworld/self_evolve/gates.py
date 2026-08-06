from __future__ import annotations

import re
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from aworld.self_evolve.evidence_diagnostics import (
    evidence_repair_constraints_from_metrics,
)
from aworld.self_evolve.evaluation import CandidateConfidenceDecision, ReplayCostEstimate
from aworld.self_evolve.failure_events import FailureOwner
from aworld.self_evolve.provenance import (
    InferredNewSkillPolicy,
    TargetMutationIntent,
    TargetProvenance,
    TargetProvenancePolicyClass,
    target_provenance_policy_class,
)
from aworld.self_evolve.replay_adaptation import ReplayAdaptationBundle
from aworld.self_evolve.regression import RegressionEvidence
from aworld.self_evolve.types import CandidateVariant, EvaluationSummary, GateResult
from aworld.self_evolve.runtime_health import (
    EvaluationRuntimeHealthStatus,
    assess_evaluation_runtime_health,
)
from aworld.self_evolve.candidate_package import (
    CandidateMutationKind,
    candidate_files_total_bytes,
    candidate_package_reference_report,
    classify_candidate_mutation,
    validate_candidate_files,
)
from aworld.skills.structure import validate_skill_markdown_structure


class ReplayAdaptationGate:
    def evaluate(self, bundle: ReplayAdaptationBundle) -> GateResult:
        readiness_values = {case.readiness for case in bundle.cases}
        readiness = "ready"
        if not bundle.ready:
            readiness = next(
                (
                    value
                    for value in (
                        "context_incomplete",
                        "unresolved",
                        "runtime_required",
                    )
                    if value in readiness_values
                ),
                "unresolved",
            )
        unavailable_dependencies = [
            dependency
            for case in bundle.cases
            for dependency in case.dependencies
            if not dependency.deterministic
            or dependency.status
            in {"context_incomplete", "unresolved", "runtime_required"}
        ]
        return GateResult(
            gate_name="replay_adaptation",
            passed=bundle.ready and not unavailable_dependencies,
            reason=(
                "replay adaptation is deterministic and ready"
                if bundle.ready and not unavailable_dependencies
                else "replay adaptation requires unavailable context or dependencies"
            ),
            details={
                "readiness": readiness,
                "case_count": len(bundle.cases),
                "unresolved_dependency_count": len(unavailable_dependencies),
                "adaptation_fingerprint": bundle.adaptation_fingerprint,
                "workspace_seed_fingerprint": bundle.workspace_seed_fingerprint,
                "environment_fingerprint": bundle.environment_fingerprint,
            },
        )


class EvaluationComparabilityGate:
    """Fail closed when baseline and candidate were not evaluated as one experiment."""

    def evaluate(
        self,
        *,
        baseline: EvaluationSummary,
        candidate: EvaluationSummary,
    ) -> GateResult:
        comparable, details = _evaluation_comparability(
            baseline.metrics,
            candidate.metrics,
        )
        if comparable:
            return GateResult(
                gate_name="evaluation_comparability",
                passed=True,
                reason="baseline and candidate evaluation plans are comparable",
                details=details,
            )
        return GateResult(
            gate_name="evaluation_comparability",
            passed=False,
            reason="baseline and candidate evaluation plans are not comparable",
            details={
                **details,
                "code": "evaluation_plan_not_comparable",
                "failure_class": "framework",
                "failure_owner": "framework",
                "failure_scope": "shared_run",
                "repairable": False,
            },
        )


class ScoreImprovementGate:
    def __init__(
        self,
        *,
        min_delta: float,
        confidence_z: float = 1.96,
        minimum_relative_margin: float = 0.01,
    ) -> None:
        self.min_delta = min_delta
        self.confidence_z = confidence_z
        self.minimum_relative_margin = minimum_relative_margin

    def evaluate(
        self,
        *,
        baseline: EvaluationSummary,
        candidate: EvaluationSummary,
    ) -> GateResult:
        baseline_score = _number_metric(baseline.metrics, "score")
        candidate_score = _number_metric(candidate.metrics, "score")
        if baseline_score is None or candidate_score is None:
            return GateResult(
                gate_name="score_improvement",
                passed=False,
                reason="score metric missing",
            )
        baseline_judge_attempts = _number_metric(baseline.metrics, "judge_attempt_count")
        baseline_judge_successes = _number_metric(baseline.metrics, "judge_success_count")
        baseline_judge_failures = _number_metric(baseline.metrics, "judge_failure_count")
        if (
            baseline_judge_attempts is not None
            and baseline_judge_attempts > 0
            and baseline_judge_successes == 0
        ):
            return GateResult(
                gate_name="score_improvement",
                passed=False,
                reason="baseline judge failed completely; score improvement is inconclusive",
                details={
                    "baseline": baseline_score,
                    "candidate": candidate_score,
                    "baseline_judge_attempt_count": baseline_judge_attempts,
                    "baseline_judge_success_count": baseline_judge_successes,
                    "baseline_judge_failure_count": baseline_judge_failures,
                },
            )
        delta = candidate_score - baseline_score
        details: dict[str, object] = {
            "baseline": baseline_score,
            "candidate": candidate_score,
            "delta": round(delta, 10),
            "minimum_delta": self.min_delta,
        }
        comparable, comparability = _evaluation_comparability(
            baseline.metrics,
            candidate.metrics,
        )
        details["comparability"] = comparability
        if not comparable:
            return GateResult(
                gate_name="score_improvement",
                passed=False,
                reason="score improvement is inconclusive across non-comparable evaluation plans",
                details={
                    **details,
                    "code": "score_improvement_incomparable",
                    "decision": "inconclusive",
                    "tiebreak_eligible": False,
                    "failure_class": "framework",
                    "failure_owner": "framework",
                    "failure_scope": "shared_run",
                    "repairable": False,
                },
            )

        stochastic = any(
            _number_metric(summary.metrics, key) is not None
            for summary in (baseline, candidate)
            for key in (
                "judge_attempt_count",
                "judge_success_count",
                "judge_repetitions",
            )
        )
        if not stochastic:
            if delta < self.min_delta:
                return _score_rejection_result(details)
            details.update(
                {
                    "code": "score_improvement_deterministic",
                    "decision": "accepted",
                    "uncertainty_model": "deterministic",
                }
            )
            return GateResult(
                gate_name="score_improvement",
                passed=True,
                reason="score improvement meets minimum delta",
                details=details,
            )

        baseline_std = _number_metric(baseline.metrics, "score_std")
        candidate_std = _number_metric(candidate.metrics, "score_std")
        baseline_count = _score_sample_count(baseline.metrics)
        candidate_count = _score_sample_count(candidate.metrics)
        if (
            baseline_std is not None
            and candidate_std is not None
            and baseline_count >= 2
            and candidate_count >= 2
        ):
            standard_error = math.sqrt(
                (baseline_std * baseline_std) / baseline_count
                + (candidate_std * candidate_std) / candidate_count
            )
            lower_bound = delta - (self.confidence_z * standard_error)
            upper_bound = delta + (self.confidence_z * standard_error)
            details.update(
                {
                    "baseline_score_std": baseline_std,
                    "candidate_score_std": candidate_std,
                    "baseline_sample_count": baseline_count,
                    "candidate_sample_count": candidate_count,
                    "delta_standard_error": standard_error,
                    "delta_confidence_lower_bound": lower_bound,
                    "delta_confidence_upper_bound": upper_bound,
                    "confidence_z": self.confidence_z,
                    "uncertainty_model": "independent_standard_error",
                }
            )
            if lower_bound >= self.min_delta:
                details.update(
                    {
                        "code": "score_improvement_confident",
                        "decision": "accepted",
                    }
                )
                return GateResult(
                    gate_name="score_improvement",
                    passed=True,
                    reason="score improvement clears the noise-aware confidence bound",
                    details=details,
                )
            if upper_bound < self.min_delta:
                return _score_rejection_result(
                    details,
                    reason="score regression is confirmed by the noise-aware confidence bound",
                )
            return GateResult(
                gate_name="score_improvement",
                passed=False,
                reason="score improvement is inconclusive under observed judge variance",
                details={
                    **details,
                    "code": "score_improvement_inconclusive",
                    "decision": "inconclusive",
                    "tiebreak_eligible": True,
                    "failure_class": "framework",
                    "failure_owner": "framework",
                    "failure_scope": "shared_run",
                    "repairable": False,
                },
            )

        scale = max(abs(baseline_score), abs(candidate_score), 1.0)
        evidence_free_margin = scale * self.minimum_relative_margin
        details.update(
            {
                "baseline_score_std": baseline_std,
                "candidate_score_std": candidate_std,
                "baseline_sample_count": baseline_count,
                "candidate_sample_count": candidate_count,
                "minimum_evidence_free_margin": evidence_free_margin,
                "uncertainty_model": "missing_repeated_score_evidence",
            }
        )
        if delta - self.min_delta >= evidence_free_margin:
            return GateResult(
                gate_name="score_improvement",
                passed=True,
                reason="score improvement clears the conservative no-variance margin",
                details={
                    **details,
                    "code": "score_improvement_large_margin",
                    "decision": "accepted",
                },
            )
        if self.min_delta - delta >= evidence_free_margin:
            return _score_rejection_result(details)
        return GateResult(
            gate_name="score_improvement",
            passed=False,
            reason="score improvement is inconclusive without repeated score evidence",
            details={
                **details,
                "code": "score_improvement_inconclusive",
                "decision": "inconclusive",
                "tiebreak_eligible": True,
                "failure_class": "framework",
                "failure_owner": "framework",
                "failure_scope": "shared_run",
                "repairable": False,
            },
        )


class CostLatencyRegressionGate:
    def __init__(
        self,
        *,
        max_cost_regression_ratio: float,
        max_latency_regression_ratio: float,
        require_resource_evidence: bool = False,
    ) -> None:
        self.max_cost_regression_ratio = max_cost_regression_ratio
        self.max_latency_regression_ratio = max_latency_regression_ratio
        self.require_resource_evidence = require_resource_evidence

    def evaluate(
        self,
        *,
        baseline: EvaluationSummary,
        candidate: EvaluationSummary,
    ) -> GateResult:
        comparable, comparability = _evaluation_comparability(
            baseline.metrics,
            candidate.metrics,
        )
        if not comparable:
            return GateResult(
                gate_name="cost_latency_regression",
                passed=False,
                reason="resource regression is inconclusive across non-comparable evaluation plans",
                details={
                    "code": "resource_regression_incomparable",
                    "comparability": comparability,
                    "failure_class": "framework",
                    "failure_owner": "framework",
                    "failure_scope": "shared_run",
                    "repairable": False,
                },
            )
        cost_key = _first_comparable_metric_key(
            baseline.metrics,
            candidate.metrics,
            (
                "cost_usd",
                "judge_total_tokens",
                "judge_estimated_input_tokens_total",
            ),
        )
        latency_key = _first_comparable_metric_key(
            baseline.metrics,
            candidate.metrics,
            ("latency_ms", "judge_model_latency_ms_total"),
        )
        if (
            self.require_resource_evidence
            and cost_key is None
            and latency_key is None
        ):
            return GateResult(
                gate_name="cost_latency_regression",
                passed=False,
                reason="verified evaluation has no comparable resource evidence",
                details={
                    "code": "resource_regression_evidence_missing",
                    "cost_metric": None,
                    "latency_metric": None,
                    "failure_class": "framework",
                    "failure_owner": "framework",
                    "failure_scope": "shared_run",
                    "repairable": False,
                },
            )
        cost_ratio = (
            _normalized_regression_ratio(baseline.metrics, candidate.metrics, cost_key)
            if cost_key is not None
            else None
        )
        if cost_ratio is not None and cost_ratio > self.max_cost_regression_ratio:
            return GateResult(
                gate_name="cost_latency_regression",
                passed=False,
                reason="cost regression exceeds policy",
                details=_resource_regression_failure_details(
                    baseline.metrics,
                    candidate.metrics,
                    key=cost_key,
                    ratio=cost_ratio,
                    kind="cost",
                ),
            )

        latency_ratio = (
            _normalized_regression_ratio(
                baseline.metrics,
                candidate.metrics,
                latency_key,
            )
            if latency_key is not None
            else None
        )
        if latency_ratio is not None and latency_ratio > self.max_latency_regression_ratio:
            return GateResult(
                gate_name="cost_latency_regression",
                passed=False,
                reason="latency regression exceeds policy",
                details=_resource_regression_failure_details(
                    baseline.metrics,
                    candidate.metrics,
                    key=latency_key,
                    ratio=latency_ratio,
                    kind="latency",
                ),
            )

        return GateResult(
            gate_name="cost_latency_regression",
            passed=True,
            reason="cost and latency regressions are within policy",
            details={
                "cost_regression_ratio": cost_ratio,
                "latency_regression_ratio": latency_ratio,
                "cost_metric": cost_key,
                "latency_metric": latency_key,
                "resource_evidence_required": self.require_resource_evidence,
                "comparability": comparability,
                "normalization": "per_effective_case_when_available",
            },
        )


class NoopCandidateGate:
    def evaluate(self, *, current_content: str, candidate: CandidateVariant) -> GateResult:
        changed = candidate.content != current_content or bool(candidate.files)
        return GateResult(
            gate_name="noop_candidate",
            passed=changed,
            reason="candidate changes target content" if changed else "candidate content is unchanged",
        )


class TargetBehaviorDeltaGate:
    """Prevent evaluation support bootstrap from masquerading as improvement."""

    def evaluate(
        self,
        *,
        current_content: str,
        candidate: CandidateVariant,
    ) -> GateResult:
        classification = classify_candidate_mutation(
            candidate,
            current_content=current_content,
        )
        passed = classification.quality_evaluation_allowed
        if passed:
            reason = "candidate changes the releasable target behavior surface"
            code = "target_behavior_delta_present"
        elif classification.kind is CandidateMutationKind.EVALUATION_SUPPORT:
            reason = (
                "candidate only bootstraps evaluation support and must be "
                "composed with a target behavior change before quality evaluation"
            )
            code = "evaluation_support_bootstrap_only"
        else:
            reason = "candidate does not change releasable target behavior"
            code = "target_behavior_delta_missing"
        return GateResult(
            gate_name="target_behavior_delta",
            passed=passed,
            reason=reason,
            details={
                **classification.to_dict(),
                "code": code,
                "candidate_status": (
                    "prerequisite" if classification.evaluation_support_changed else "rejected"
                ),
                "failure_class": None if passed else "candidate",
                "failure_owner": None if passed else "candidate",
                "failure_scope": None if passed else "candidate",
                "repairable": not passed,
            },
        )


class MalformedCandidateGate:
    def evaluate(self, candidate: CandidateVariant) -> GateResult:
        if not candidate.content.strip():
            return GateResult(
                gate_name="malformed_candidate",
                passed=False,
                reason="candidate content is empty",
            )
        return GateResult(
            gate_name="malformed_candidate",
            passed=True,
            reason="candidate content is non-empty",
        )


class SkillMarkdownGate:
    def evaluate(self, candidate: CandidateVariant) -> GateResult:
        validation = validate_skill_markdown_structure(
            candidate.content,
        )
        details = {
            **dict(validation.details),
            "code": validation.code,
            "field_path": validation.field_path,
            "contract_fingerprint": validation.contract_fingerprint,
        }
        if not validation.passed:
            details.update(
                {
                    "failure_class": "candidate",
                    "failure_owner": "candidate",
                    "failure_scope": "candidate",
                    "repairable": True,
                }
            )
        return GateResult(
            gate_name="skill_markdown",
            passed=validation.passed,
            reason=validation.reason,
            details=details,
        )


class SkillReleaseFidelityGate:
    def evaluate(
        self,
        candidate: CandidateVariant,
        *,
        current_content: str,
        require_exact_deletion_intent: bool = False,
    ) -> GateResult:
        validation = validate_skill_markdown_structure(
            candidate.content,
            original_content=current_content,
            edit_intent=candidate.structural_edit_intent,
            require_exact_deletion_intent=(
                require_exact_deletion_intent
            ),
        )
        details = {
            **dict(validation.details),
            "code": validation.code,
            "field_path": validation.field_path,
            "contract_fingerprint": validation.contract_fingerprint,
        }
        if not validation.passed:
            details.update(
                {
                    "failure_class": "candidate",
                    "failure_owner": "candidate",
                    "failure_scope": "candidate",
                    "repairable": True,
                }
            )
        return GateResult(
            gate_name="skill_release_fidelity",
            passed=validation.passed,
            reason=validation.reason,
            details=details,
        )


class PromptSectionGate:
    def evaluate(self, candidate: CandidateVariant) -> GateResult:
        content = candidate.content.strip()
        passed = bool(content) and "\x00" not in content
        return GateResult(
            gate_name="prompt_section",
            passed=passed,
            reason=(
                "prompt section candidate is valid"
                if passed
                else "prompt section candidate must be non-empty text"
            ),
        )


class ToolDescriptionGate:
    def __init__(self, *, min_chars: int = 12) -> None:
        self.min_chars = min_chars

    def evaluate(self, candidate: CandidateVariant) -> GateResult:
        content = " ".join(candidate.content.split())
        passed = len(content) >= self.min_chars
        return GateResult(
            gate_name="tool_description",
            passed=passed,
            reason=(
                "tool description candidate is descriptive enough"
                if passed
                else "tool description candidate is too short"
            ),
            details={"min_chars": self.min_chars, "actual_chars": len(content)},
        )


class TokenLimitGate:
    def __init__(self, *, max_chars: int) -> None:
        self.max_chars = max_chars

    def evaluate(self, candidate: CandidateVariant) -> GateResult:
        actual_chars = len(candidate.content) + candidate_files_total_bytes(candidate.files)
        passed = actual_chars <= self.max_chars
        return GateResult(
            gate_name="token_limit",
            passed=passed,
            reason=(
                "candidate content is within token budget"
                if passed
                else "candidate content exceeds token budget"
            ),
            details={"max_chars": self.max_chars, "actual_chars": actual_chars},
        )


class CandidatePackageGate:
    def evaluate(self, candidate: CandidateVariant) -> GateResult:
        try:
            files = validate_candidate_files(candidate.files)
        except ValueError as exc:
            return GateResult(
                gate_name="candidate_package",
                passed=False,
                reason=str(exc),
            )
        reference_report = candidate_package_reference_report(candidate)
        if not reference_report["closed"]:
            return GateResult(
                gate_name="candidate_package",
                passed=False,
                reason="candidate skill references files missing from its release package",
                details={
                    "code": "candidate_package_reference_missing",
                    "failure_class": "candidate",
                    "failure_owner": "candidate",
                    "failure_scope": "candidate",
                    "repairable": True,
                    "file_count": len(files),
                    **reference_report,
                },
            )
        return GateResult(
            gate_name="candidate_package",
            passed=True,
            reason="candidate package file deltas are valid",
            details={"file_count": len(files), **reference_report},
        )


class ExternalCodeEvolutionGate:
    _BLOCKED_PATTERNS = (
        "darwinian_evolve",
        "darwinian",
        "agpl",
        "evolution_runner",
    )

    def evaluate(self, candidate: CandidateVariant) -> GateResult:
        lowered = candidate.content.lower()
        blocked = next((pattern for pattern in self._BLOCKED_PATTERNS if pattern in lowered), None)
        return GateResult(
            gate_name="external_code_evolution",
            passed=blocked is None,
            reason=(
                "candidate does not import external code-evolution adapters"
                if blocked is None
                else "Darwinian/code evolution must remain an external adapter"
            ),
            details={"blocked_pattern": blocked} if blocked is not None else None,
        )


class ProtectedPathGate:
    _PROTECTED_ROOTS = {
        "aworld",
        "aworld-cli",
        "aworld_gateway",
        "aworld-gateway",
        "runtime",
    }
    _PROTECTED_FILES = {
        "pyproject.toml",
        "setup.py",
        "requirements.txt",
        ".env",
    }
    _APP_EVALUATOR_PARTS = ("aworld-skills", "app_evaluator", "SKILL.md")
    _SELF_EVOLVE_SKILL_PARTS = ("aworld-skills", "self_evolve", "SKILL.md")

    def __init__(self, *, workspace_root: str | Path) -> None:
        self.workspace_root = Path(workspace_root).resolve()

    def evaluate(self, candidate: CandidateVariant) -> GateResult:
        path = candidate.target.path
        if path is None:
            return GateResult(
                gate_name="protected_path",
                passed=True,
                reason="candidate has no filesystem path",
            )

        candidate_path = Path(path).resolve()
        try:
            relative = candidate_path.relative_to(self.workspace_root)
        except ValueError:
            relative = candidate_path

        if relative.parts[-3:] == self._APP_EVALUATOR_PARTS:
            return GateResult(
                gate_name="protected_path",
                passed=False,
                reason="app_evaluator skill is protected from self-evolve mutation",
            )
        if relative.parts[-3:] == self._SELF_EVOLVE_SKILL_PARTS:
            return GateResult(
                gate_name="protected_path",
                passed=False,
                reason="self_evolve skill is protected from default self-mutation",
            )
        if relative.parts and relative.parts[0] in self._PROTECTED_ROOTS:
            return GateResult(
                gate_name="protected_path",
                passed=False,
                reason="protected product path cannot be mutated",
            )
        if relative.name in self._PROTECTED_FILES:
            return GateResult(
                gate_name="protected_path",
                passed=False,
                reason="protected package or secret path cannot be mutated",
            )
        return GateResult(
            gate_name="protected_path",
            passed=True,
            reason="path is allowed for candidate proposal",
        )


class BudgetGate:
    def evaluate(self, estimate: ReplayCostEstimate) -> GateResult:
        passed = estimate.passed
        reason = estimate.reason
        if estimate.token_ceiling is not None and not estimate.estimate_known:
            passed = False
            reason = "estimated replay tokens are unknown under max_run_tokens"
        return GateResult(
            gate_name="budget",
            passed=passed,
            reason=reason,
            details={
                "estimated_tokens": estimate.estimated_tokens,
                "estimated_tokens_per_replay": (
                    estimate.estimated_tokens_per_replay
                ),
                "estimated_cost_usd": estimate.estimated_cost_usd,
                "estimate_source": estimate.estimate_source.value,
                "estimate_confidence": estimate.estimate_confidence.value,
                "estimate_known": estimate.estimate_known,
                "total_replay_count": estimate.total_replay_count,
                "judge_call_count": estimate.judge_call_count,
                "verification_command_count": estimate.verification_command_count,
            },
        )


class RequiredVerificationGate:
    def evaluate(self, summary: EvaluationSummary) -> GateResult:
        command_case_count = int(_number_metric(summary.metrics, "command_case_count") or 0)
        command_pass_count = int(_number_metric(summary.metrics, "command_pass_count") or 0)
        if command_case_count <= 0:
            return GateResult(
                gate_name="required_verification",
                passed=False,
                reason="required deterministic verification command was not run",
            )
        if command_pass_count != command_case_count:
            return GateResult(
                gate_name="required_verification",
                passed=False,
                reason="required verification commands did not all pass",
                details={"command_case_count": command_case_count, "command_pass_count": command_pass_count},
            )
        return GateResult(
            gate_name="required_verification",
            passed=True,
            reason="required verification commands passed",
            details={"command_case_count": command_case_count, "command_pass_count": command_pass_count},
        )


class EvaluationRuntimeHealthGate:
    """Gate evaluator/judge availability before candidate quality policy."""

    def evaluate(
        self,
        summaries: Iterable[EvaluationSummary],
    ) -> GateResult:
        health = assess_evaluation_runtime_health(summaries)
        passed = health.status is not EvaluationRuntimeHealthStatus.UNHEALTHY
        details: dict[str, object] = {
            "runtime_health": health.to_dict(),
        }
        if not passed:
            retryable = health.retryable_infrastructure_failure
            details.update(
                {
                    "failure_class": FailureOwner.INFRASTRUCTURE.value,
                    "failure_owner": FailureOwner.INFRASTRUCTURE.value,
                    "failure_scope": "shared_run",
                    "failure_source": "native",
                    "repairable": retryable,
                    "retryable": retryable,
                    "code": "evaluation_runtime_unhealthy",
                }
            )
        if health.status is EvaluationRuntimeHealthStatus.UNKNOWN:
            reason = (
                "evaluation runtime health telemetry is unavailable; "
                "legacy evaluator result remains usable"
            )
        elif health.status is EvaluationRuntimeHealthStatus.DEGRADED:
            reason = (
                "evaluation runtime produced usable judge signals with "
                "partial failures"
            )
        elif passed:
            reason = "evaluation runtime produced usable judge signals"
        else:
            reason = "evaluation runtime did not produce a usable judge signal"
        return GateResult(
            gate_name="evaluation_runtime_health",
            passed=passed,
            reason=reason,
            details=details,
        )


class EvidenceQualityGate:
    _COMPACTED_CONTEXT_MARKER = "tool output compacted for context reuse"
    _TRUNCATED_EVIDENCE_MARKERS = ("truncated", "tool evidence")
    _PREVIEW_MARKERS = (
        "original size:",
        "preview:",
    )

    def evaluate(
        self,
        summary: EvaluationSummary,
        *,
        baseline: EvaluationSummary | None = None,
    ) -> GateResult:
        evidence_block_count = int(_number_metric(summary.metrics, "evidence_block_count") or 0)
        evidence_manifest_entry_count = int(
            _number_metric(summary.metrics, "evidence_manifest_entry_count") or 0
        )
        evidence_manifest_invalid_entry_count = int(
            _number_metric(summary.metrics, "evidence_manifest_invalid_entry_count") or 0
        )
        evidence_bundle_entry_count = int(
            _number_metric(summary.metrics, "evidence_bundle_entry_count") or 0
        )
        evidence_bundle_valid = _bool_metric(summary.metrics, "evidence_bundle_valid") is True
        artifact_first_evidence = (
            _bool_metric(summary.metrics, "evidence_strategy_passed") is True
            and evidence_manifest_entry_count > 0
        )
        canonical_bundle_evidence = evidence_bundle_valid and evidence_bundle_entry_count > 0
        has_evidence = (
            summary.metrics.get("has_evidence") == 1.0
            or evidence_block_count > 0
            or artifact_first_evidence
            or canonical_bundle_evidence
        )
        compacted = _bool_metric(summary.metrics, "evidence_compacted")
        if compacted is None:
            compacted = _contains_compacted_evidence_marker(summary.metrics)
        incomplete = _bool_metric(summary.metrics, "evidence_incomplete")
        if incomplete is None:
            incomplete = False
        evidence_constraints = evidence_repair_constraints_from_metrics(
            summary.metrics
        )
        baseline_constraints = (
            evidence_repair_constraints_from_metrics(baseline.metrics)
            if baseline is not None
            else ()
        )
        constraint_regressions, resolved_constraints = _evidence_constraint_delta(
            baseline_constraints,
            evidence_constraints,
        )
        baseline_compacted = (
            _bool_metric(baseline.metrics, "evidence_compacted")
            if baseline is not None
            else None
        )
        baseline_incomplete = (
            _bool_metric(baseline.metrics, "evidence_incomplete")
            if baseline is not None
            else None
        )
        compacted_regressed = (
            baseline is not None and compacted is True and baseline_compacted is not True
        )
        incomplete_regressed = (
            baseline is not None and incomplete is True and baseline_incomplete is not True
        )
        has_declared_evidence_constraints = bool(
            summary.metrics.get("evidence_repair_constraints")
        )
        regressed_constraint_identities = {
            str(item.get("constraint_identity_digest") or "")
            for item in constraint_regressions
        }
        ownership_constraints = (
            tuple(
                item
                for item in evidence_constraints
                if item.identity_digest in regressed_constraint_identities
            )
            if baseline is not None
            else evidence_constraints
        )
        constraint_owners = {item.owner for item in ownership_constraints}
        if FailureOwner.INFRASTRUCTURE in constraint_owners:
            failure_owner = FailureOwner.INFRASTRUCTURE
        elif FailureOwner.FRAMEWORK in constraint_owners:
            # A valid artifact bundle that becomes insufficient only at the
            # bounded projection boundary cannot safely be blamed on the
            # candidate until the framework exposes enough evidence to decide.
            failure_owner = FailureOwner.FRAMEWORK
        elif FailureOwner.TASK in constraint_owners:
            failure_owner = FailureOwner.TASK
        else:
            failure_owner = FailureOwner.CANDIDATE
        details = {
            "has_evidence": has_evidence,
            "evidence_block_count": evidence_block_count,
            "evidence_compacted": compacted,
            "evidence_incomplete": incomplete,
            "evidence_strategy_passed": artifact_first_evidence,
            "evidence_manifest_entry_count": evidence_manifest_entry_count,
            "evidence_manifest_invalid_entry_count": evidence_manifest_invalid_entry_count,
            "evidence_bundle_valid": evidence_bundle_valid,
            "evidence_bundle_entry_count": evidence_bundle_entry_count,
            "evidence_repair_constraints": [
                item.to_dict() for item in evidence_constraints
            ],
            "evidence_constraint_count": len(evidence_constraints),
            "evidence_comparison_mode": (
                "baseline_relative" if baseline is not None else "absolute"
            ),
            "baseline_evidence_repair_constraints": [
                item.to_dict() for item in baseline_constraints
            ],
            "evidence_constraint_regressions": constraint_regressions,
            "resolved_evidence_constraints": resolved_constraints,
            "evidence_compacted_regressed": compacted_regressed,
            "evidence_incomplete_regressed": incomplete_regressed,
            "failure_class": failure_owner.value,
            "failure_owner": failure_owner.value,
            "failure_scope": (
                "shared_run"
                if failure_owner
                in {
                    FailureOwner.FRAMEWORK,
                    FailureOwner.INFRASTRUCTURE,
                }
                else "candidate"
            ),
            "failure_source": "native",
            "repairable": True,
        }
        if not has_evidence:
            return GateResult(
                gate_name="evidence_quality",
                passed=False,
                reason="verified apply requires replay tool evidence",
                details=details,
            )
        if artifact_first_evidence and evidence_manifest_invalid_entry_count > 0:
            return GateResult(
                gate_name="evidence_quality",
                passed=False,
                reason="artifact-first evidence is not fully verifiable",
                details=details,
            )
        if baseline is not None and (
            constraint_regressions
            or compacted_regressed
            or incomplete_regressed
        ):
            return GateResult(
                gate_name="evidence_quality",
                passed=False,
                reason="candidate evidence quality regressed relative to baseline",
                details=details,
            )
        if baseline is not None:
            # Pre-existing constraints remain visible in diagnostics, but only
            # new or worsened constraints are attributed to this candidate.
            evidence_constraints = ()
            has_declared_evidence_constraints = False
            compacted = False
            incomplete = False
        if has_declared_evidence_constraints and evidence_constraints:
            return GateResult(
                gate_name="evidence_quality",
                passed=False,
                reason="typed evidence repair constraints remain unsatisfied",
                details=details,
            )
        if incomplete:
            return GateResult(
                gate_name="evidence_quality",
                passed=False,
                reason="evaluation evidence is compacted or incomplete",
                details=details,
            )
        if canonical_bundle_evidence:
            return GateResult(
                gate_name="evidence_quality",
                passed=True,
                reason="evaluation evidence is present via canonical evidence bundle",
                details=details,
            )
        if artifact_first_evidence:
            return GateResult(
                gate_name="evidence_quality",
                passed=True,
                reason="evaluation evidence is present via artifact-first manifest",
                details=details,
            )
        if compacted:
            return GateResult(
                gate_name="evidence_quality",
                passed=False,
                reason="evaluation evidence is compacted or incomplete",
                details=details,
            )
        return GateResult(
            gate_name="evidence_quality",
            passed=True,
            reason="evaluation evidence is present and not compacted",
            details=details,
        )


class JudgeOnlySignalGate:
    def evaluate(self, decision: CandidateConfidenceDecision) -> GateResult:
        passed = decision.deterministic_signal_present
        return GateResult(
            gate_name="judge_only_signal",
            passed=passed,
            reason=(
                "candidate has deterministic signal"
                if passed
                else "judge-only improvements remain limited confidence"
            ),
            details={"confidence": decision.confidence},
        )


@dataclass(frozen=True)
class StoppingConditionState:
    iteration: int = 0
    stalled_iterations: int = 0
    pending_duplicate: bool = False
    cooldown_remaining_seconds: int = 0
    repeated_gate_failures: int = 0


class StoppingConditionGate:
    def __init__(
        self,
        *,
        max_iterations: int,
        max_stalled_iterations: int,
        max_repeated_gate_failures: int,
    ) -> None:
        self.max_iterations = max_iterations
        self.max_stalled_iterations = max_stalled_iterations
        self.max_repeated_gate_failures = max_repeated_gate_failures

    def evaluate(self, state: StoppingConditionState) -> GateResult:
        if state.iteration >= self.max_iterations:
            return GateResult(
                gate_name="stopping_condition",
                passed=False,
                reason="max iteration limit reached",
            )
        if state.stalled_iterations >= self.max_stalled_iterations:
            return GateResult(
                gate_name="stopping_condition",
                passed=False,
                reason="stalled improvement limit reached",
            )
        if state.pending_duplicate:
            return GateResult(
                gate_name="stopping_condition",
                passed=False,
                reason="duplicate pending proposal exists",
            )
        if state.cooldown_remaining_seconds > 0:
            return GateResult(
                gate_name="stopping_condition",
                passed=False,
                reason="target is in cooldown",
                details={"cooldown_remaining_seconds": state.cooldown_remaining_seconds},
            )
        if state.repeated_gate_failures >= self.max_repeated_gate_failures:
            return GateResult(
                gate_name="stopping_condition",
                passed=False,
                reason="repeated gate failure limit reached",
            )
        return GateResult(
            gate_name="stopping_condition",
            passed=True,
            reason="stopping conditions allow another iteration",
        )


class HeldOutVerificationGate:
    def __init__(self, *, min_eval_cases: int) -> None:
        self.min_eval_cases = min_eval_cases

    def evaluate(self, decision: CandidateConfidenceDecision) -> GateResult:
        held_out_passed = (
            decision.confidence == "verified"
            and decision.verification_split == "held_out"
            and decision.held_out_case_count >= self.min_eval_cases
            and decision.deterministic_signal_present
        )
        single_case_replay_passed = (
            decision.confidence == "verified"
            and decision.verification_mode == "single_case_replay"
            and decision.verification_split == "single_case_replay"
            and decision.deterministic_signal_present
            and decision.baseline_replay_count >= 2
            and decision.candidate_replay_count >= 3
        )
        trajectory_set_validation_passed = (
            decision.confidence == "verified"
            and decision.verification_mode == "trajectory_set_validation"
            and decision.verification_split == "trajectory_set_validation"
            and decision.held_out_case_count > 0
            and decision.deterministic_signal_present
        )
        passed = (
            held_out_passed
            or single_case_replay_passed
            or trajectory_set_validation_passed
        )
        if held_out_passed:
            reason = "candidate is verified on sufficient held-out cases"
        elif single_case_replay_passed:
            reason = "candidate is verified by stable single-case replay"
        elif trajectory_set_validation_passed:
            reason = "candidate is verified by trajectory-set validation"
        else:
            reason = "candidate is not verified on sufficient held-out cases"
        return GateResult(
            gate_name="held_out_verification",
            passed=passed,
            reason=reason,
            details={
                "confidence": decision.confidence,
                "held_out_case_count": decision.held_out_case_count,
                "min_eval_cases": self.min_eval_cases,
                "verification_split": decision.verification_split,
                "verification_mode": decision.verification_mode,
                "baseline_replay_count": decision.baseline_replay_count,
                "candidate_replay_count": decision.candidate_replay_count,
            },
        )


class TrustProvenanceGate:
    def __init__(self, *, allow_generated: bool = False, allow_external: bool = False) -> None:
        self.allow_generated = allow_generated
        self.allow_external = allow_external

    def evaluate(
        self,
        provenance: TargetProvenance | None,
        *,
        unresolved_reason: str | None = None,
        target_intent: TargetMutationIntent | str | None = None,
    ) -> GateResult:
        if provenance is None or unresolved_reason is not None:
            if unresolved_reason is None:
                reason_detail: object = "no target provenance was supplied"
            elif isinstance(unresolved_reason, str):
                reason_detail = unresolved_reason
            else:
                reason_detail = (
                    "invalid unresolved reason type: "
                    f"{type(unresolved_reason).__name__}"
                )
            return GateResult(
                gate_name="trust_provenance",
                passed=False,
                reason="target provenance is unresolved",
                details={
                    "provenance_status": "unresolved",
                    "unresolved_reason": reason_detail,
                },
            )
        if not isinstance(provenance, TargetProvenance):
            return GateResult(
                gate_name="trust_provenance",
                passed=False,
                reason="target provenance is invalid",
                details={
                    "provenance_status": "invalid",
                    "invalid_type": type(provenance).__name__,
                },
            )
        policy_class = target_provenance_policy_class(provenance)
        if policy_class is None:
            return GateResult(
                gate_name="trust_provenance",
                passed=False,
                reason="target provenance classification is not trusted",
            )
        if policy_class == TargetProvenancePolicyClass.PROTECTED:
            return GateResult(
                gate_name="trust_provenance",
                passed=False,
                reason="protected target provenance cannot be mutated",
            )
        try:
            typed_intent = (
                TargetMutationIntent(target_intent)
                if target_intent is not None
                else None
            )
        except ValueError:
            return GateResult(
                gate_name="trust_provenance",
                passed=False,
                reason="target mutation intent is invalid",
            )
        draft_evolution = (
            policy_class == TargetProvenancePolicyClass.GENERATED
            and typed_intent == TargetMutationIntent.INFERRED_DRAFT_CREATION
        )
        if (
            policy_class == TargetProvenancePolicyClass.GENERATED
            and not self.allow_generated
            and not draft_evolution
        ):
            return GateResult(
                gate_name="trust_provenance",
                passed=False,
                reason="generated target requires explicit trust policy",
            )
        if (
            policy_class == TargetProvenancePolicyClass.EXTERNAL
            and not self.allow_external
        ):
            return GateResult(
                gate_name="trust_provenance",
                passed=False,
                reason="external target requires explicit trust policy",
            )
        return GateResult(
            gate_name="trust_provenance",
            passed=True,
            reason=(
                "generated target is authorized only for isolated draft evolution"
                if draft_evolution
                else "target provenance satisfies trust policy"
            ),
            details=(
                {"authorized_scope": "draft_evolution"}
                if draft_evolution
                else None
            ),
        )


class NewSkillPromotionGate:
    """Authorize draft evolution separately from publication into aworld-skills."""

    def evaluate(
        self,
        candidate: CandidateVariant,
        *,
        target_intent: TargetMutationIntent | str | None,
        policy: InferredNewSkillPolicy | str,
        apply_policy: str,
        workspace_root: str | Path,
        provenance: TargetProvenance | None,
    ) -> GateResult:
        try:
            typed_intent = (
                TargetMutationIntent(target_intent)
                if target_intent is not None
                else None
            )
            typed_policy = InferredNewSkillPolicy(policy)
        except ValueError:
            return GateResult(
                gate_name="new_skill_promotion",
                passed=False,
                reason="new-skill promotion policy or target intent is invalid",
            )
        if typed_intent != TargetMutationIntent.INFERRED_DRAFT_CREATION:
            return GateResult(
                gate_name="new_skill_promotion",
                passed=True,
                reason="candidate does not create an inferred skill draft",
                details={"applicable": False},
            )
        if typed_policy == InferredNewSkillPolicy.DISABLED:
            return GateResult(
                gate_name="new_skill_promotion",
                passed=False,
                reason="inferred new-skill creation is disabled by policy",
                details={"policy": typed_policy.value, "publication_allowed": False},
            )
        if provenance is None or provenance.target != candidate.target:
            return GateResult(
                gate_name="new_skill_promotion",
                passed=False,
                reason="candidate identity does not match generated target provenance",
                details={"policy": typed_policy.value, "publication_allowed": False},
            )
        path_error = _run_owned_draft_path_error(
            candidate.target,
            workspace_root=workspace_root,
        )
        if path_error is not None:
            return GateResult(
                gate_name="new_skill_promotion",
                passed=False,
                reason=path_error,
                details={"policy": typed_policy.value, "publication_allowed": False},
            )
        release_path = (
            Path(workspace_root)
            / "aworld-skills"
            / candidate.target.target_id
            / "SKILL.md"
        )
        publication_allowed = (
            typed_policy == InferredNewSkillPolicy.AUTO_VERIFIED
            and apply_policy == "auto_verified"
        )
        if publication_allowed and (release_path.exists() or release_path.is_symlink()):
            return GateResult(
                gate_name="new_skill_promotion",
                passed=False,
                reason="new-skill release path appeared after target inference",
                details={
                    "policy": typed_policy.value,
                    "publication_allowed": False,
                    "release_path": str(release_path),
                },
            )
        return GateResult(
            gate_name="new_skill_promotion",
            passed=True,
            reason=(
                "verified publication is authorized after ordinary gates pass"
                if publication_allowed
                else "draft evolution is authorized but publication is disabled"
            ),
            details={
                "policy": typed_policy.value,
                "publication_allowed": publication_allowed,
                "release_path": str(release_path),
            },
        )


def _run_owned_draft_path_error(
    target: Any,
    *,
    workspace_root: str | Path,
) -> str | None:
    raw_path = getattr(target, "path", None)
    target_id = getattr(target, "target_id", None)
    if not isinstance(raw_path, str) or not raw_path:
        return "inferred skill draft has no run-owned path"
    root = Path(workspace_root).resolve()
    path = Path(raw_path).absolute()
    try:
        relative = path.relative_to(root)
        path.resolve(strict=False).relative_to(root)
    except ValueError:
        return "inferred skill draft escapes the workspace"
    expected_suffix = ("draft_target", str(target_id), "SKILL.md")
    parts = relative.parts
    if (
        len(parts) != 6
        or parts[:2] != (".aworld", "self_evolve")
        or parts[3:] != expected_suffix
        or not parts[2]
    ):
        return "inferred skill draft is not owned by exactly one run"
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return "inferred skill draft path traverses a symlink"
    return None


class GlobalRegressionBenchmarkGate:
    _REQUIRES_REGRESSION_TARGET_TYPES = {"skill", "prompt-section", "tool-description"}

    def evaluate(
        self,
        candidate: CandidateVariant,
        evidence: RegressionEvidence | None,
    ) -> GateResult:
        if candidate.target.target_type not in self._REQUIRES_REGRESSION_TARGET_TYPES:
            return GateResult(
                gate_name="global_regression_benchmark",
                passed=True,
                reason="target type does not require global regression benchmark",
            )
        if evidence is None or not isinstance(evidence, RegressionEvidence):
            return GateResult(
                gate_name="global_regression_benchmark",
                passed=False,
                reason=(
                    "independent regression evidence is required for verified "
                    "text targets"
                ),
                details={
                    "code": "independent_regression_evidence_missing",
                    "legacy_evaluator_boolean_accepted": False,
                    "failure_class": "framework",
                    "failure_owner": "framework",
                    "repairable": False,
                },
            )
        if evidence.candidate_id != candidate.candidate_id:
            return GateResult(
                gate_name="global_regression_benchmark",
                passed=False,
                reason="regression evidence belongs to a different candidate",
                details={
                    "code": "regression_candidate_mismatch",
                    "expected_candidate_id": candidate.candidate_id,
                    "evidence_candidate_id": evidence.candidate_id,
                    "evidence_fingerprint": evidence.fingerprint,
                    "failure_class": "framework",
                    "failure_owner": "framework",
                    "repairable": False,
                },
            )
        passed = evidence.passed
        failed_suite_gates = [
            (result.spec.suite_id, gate)
            for result in evidence.suite_results
            for gate in result.gate_results
            if not gate.passed
        ]
        shared_failure = any(
            not result.fresh_execution
            for result in evidence.suite_results
        ) or any(
            isinstance(gate.details, Mapping)
            and gate.details.get("failure_class")
            in {"infrastructure", "budget", "framework"}
            for _, gate in failed_suite_gates
        )
        failure_code = (
            next(
                (
                    str(gate.details["code"])
                    for _, gate in failed_suite_gates
                    if isinstance(gate.details, Mapping)
                    and isinstance(gate.details.get("code"), str)
                ),
                "independent_regression_execution_failed",
            )
            if shared_failure
            else "independent_regression_failed"
        )
        return GateResult(
            gate_name="global_regression_benchmark",
            passed=passed,
            reason=(
                "independent regression benchmark suite passed"
                if passed
                else "independent regression benchmark suite did not pass"
            ),
            details={
                "code": (
                    "independent_regression_passed"
                    if passed
                    else failure_code
                ),
                "evidence_fingerprint": evidence.fingerprint,
                "suite_count": len(evidence.suite_results),
                "failed_suite_ids": [
                    result.spec.suite_id
                    for result in evidence.suite_results
                    if not result.passed
                ],
                "data_independent": evidence.data_independent,
                "execution_independent": evidence.execution_independent,
                "implementation_independent": (
                    evidence.implementation_independent
                ),
                "selection_backend_id": evidence.selection_backend_id,
                "regression_backend_id": evidence.regression_backend_id,
                "suite_failures": [
                    {
                        "suite_id": suite_id,
                        "gate_name": gate.gate_name,
                        "reason": gate.reason,
                        "details": dict(gate.details or {}),
                    }
                    for suite_id, gate in failed_suite_gates
                ],
                **(
                    {}
                    if passed
                    else {
                        "failure_class": (
                            "framework" if shared_failure else "candidate"
                        ),
                        "failure_owner": (
                            "framework" if shared_failure else "candidate"
                        ),
                        "failure_scope": (
                            "shared_run" if shared_failure else "candidate"
                        ),
                        "repairable": not shared_failure,
                    }
                ),
            },
        )


def _number_metric(metrics: dict[str, Any] | Any, key: str) -> float | None:
    value = metrics.get(key) if hasattr(metrics, "get") else None
    return (
        float(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else None
    )


def _evaluation_comparability(
    baseline_metrics: Mapping[str, Any],
    candidate_metrics: Mapping[str, Any],
) -> tuple[bool, dict[str, Any]]:
    baseline_plan = baseline_metrics.get("comparison_plan_fingerprint")
    candidate_plan = candidate_metrics.get("comparison_plan_fingerprint")
    baseline_count = _number_metric(
        baseline_metrics,
        "comparison_effective_case_count",
    )
    candidate_count = _number_metric(
        candidate_metrics,
        "comparison_effective_case_count",
    )
    if baseline_count is None:
        baseline_count = _number_metric(baseline_metrics, "effective_case_count")
    if candidate_count is None:
        candidate_count = _number_metric(candidate_metrics, "effective_case_count")
    baseline_case_ids = baseline_metrics.get("comparison_case_ids")
    candidate_case_ids = candidate_metrics.get("comparison_case_ids")
    reasons: list[str] = []
    if (baseline_plan is None) != (candidate_plan is None):
        reasons.append("comparison_plan_missing_on_one_side")
    elif baseline_plan is not None and baseline_plan != candidate_plan:
        reasons.append("comparison_plan_fingerprint_mismatch")
    if (
        baseline_count is not None
        and candidate_count is not None
        and int(baseline_count) != int(candidate_count)
    ):
        reasons.append("effective_case_count_mismatch")
    if (
        isinstance(baseline_case_ids, (list, tuple))
        and isinstance(candidate_case_ids, (list, tuple))
        and list(baseline_case_ids) != list(candidate_case_ids)
    ):
        reasons.append("comparison_case_identity_mismatch")
    if baseline_plan is not None and (
        baseline_metrics.get("comparison_cardinality_preserved") is not True
        or candidate_metrics.get("comparison_cardinality_preserved") is not True
    ):
        reasons.append("comparison_cardinality_not_preserved")
    return not reasons, {
        "comparable": not reasons,
        "reasons": reasons,
        "baseline_plan_fingerprint": baseline_plan,
        "candidate_plan_fingerprint": candidate_plan,
        "baseline_effective_case_count": baseline_count,
        "candidate_effective_case_count": candidate_count,
    }


def _evidence_constraint_delta(
    baseline: Iterable[Any],
    candidate: Iterable[Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    baseline_by_identity = {item.identity_digest: item for item in baseline}
    candidate_by_identity = {item.identity_digest: item for item in candidate}
    regressions: list[dict[str, Any]] = []
    resolved: list[dict[str, Any]] = []
    for identity in sorted({*baseline_by_identity, *candidate_by_identity}):
        baseline_item = baseline_by_identity.get(identity)
        candidate_item = candidate_by_identity.get(identity)
        baseline_count = baseline_item.occurrence_count if baseline_item is not None else 0
        candidate_count = candidate_item.occurrence_count if candidate_item is not None else 0
        delta = candidate_count - baseline_count
        if delta > 0 and candidate_item is not None:
            regressions.append(
                {
                    **candidate_item.to_dict(),
                    "baseline_occurrence_count": baseline_count,
                    "candidate_occurrence_count": candidate_count,
                    "occurrence_delta": delta,
                }
            )
        elif delta < 0 and baseline_item is not None:
            resolved.append(
                {
                    **baseline_item.to_dict(),
                    "baseline_occurrence_count": baseline_count,
                    "candidate_occurrence_count": candidate_count,
                    "occurrence_delta": delta,
                }
            )
    return regressions, resolved


def _score_rejection_result(
    details: Mapping[str, object],
    *,
    reason: str = "score improvement below minimum delta",
) -> GateResult:
    return GateResult(
        gate_name="score_improvement",
        passed=False,
        reason=reason,
        details={
            **details,
            "code": "score_improvement_below_minimum",
            "decision": "rejected",
            "failure_class": "candidate",
            "failure_owner": "candidate",
            "failure_scope": "candidate",
            "repairable": True,
        },
    )


def _score_sample_count(metrics: Mapping[str, Any]) -> int:
    for key in ("score_sample_count", "judge_success_count", "judge_repetitions"):
        value = metrics.get(key)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value >= 0
        ):
            return int(value)
    return 0


def _bool_metric(metrics: dict[str, Any] | Any, key: str) -> bool | None:
    value = metrics.get(key) if hasattr(metrics, "get") else None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
    return None


def _contains_compacted_evidence_marker(value: Any) -> bool:
    if isinstance(value, str):
        lowered = value.lower()
        return (
            EvidenceQualityGate._COMPACTED_CONTEXT_MARKER in lowered
            or all(marker in lowered for marker in EvidenceQualityGate._TRUNCATED_EVIDENCE_MARKERS)
            or all(marker in lowered for marker in EvidenceQualityGate._PREVIEW_MARKERS)
        )
    if isinstance(value, dict):
        return any(_contains_compacted_evidence_marker(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_compacted_evidence_marker(item) for item in value)
    return False


def _normalized_resource_metric(
    metrics: Mapping[str, Any],
    key: str,
) -> tuple[float | None, float | None]:
    value = _number_metric(metrics, key)
    if value is None:
        return None, None
    case_count = _number_metric(metrics, "effective_case_count")
    if case_count is None:
        case_count = _number_metric(metrics, "comparison_effective_case_count")
    if case_count is None or case_count <= 0:
        return value, None
    return value / case_count, case_count


def _normalized_regression_ratio(
    baseline_metrics: Mapping[str, Any],
    candidate_metrics: Mapping[str, Any],
    key: str,
) -> float | None:
    baseline, _ = _normalized_resource_metric(baseline_metrics, key)
    candidate, _ = _normalized_resource_metric(candidate_metrics, key)
    if baseline is None or candidate is None or baseline <= 0:
        return None
    return (candidate - baseline) / baseline


def _resource_regression_failure_details(
    baseline_metrics: Mapping[str, Any],
    candidate_metrics: Mapping[str, Any],
    *,
    key: str,
    ratio: float,
    kind: str,
) -> dict[str, Any]:
    baseline_value, baseline_count = _normalized_resource_metric(
        baseline_metrics,
        key,
    )
    candidate_value, candidate_count = _normalized_resource_metric(
        candidate_metrics,
        key,
    )
    return {
        f"{kind}_regression_ratio": ratio,
        f"{kind}_metric": key,
        "normalization": "per_effective_case_when_available",
        "baseline_normalized_value": baseline_value,
        "candidate_normalized_value": candidate_value,
        "baseline_effective_case_count": baseline_count,
        "candidate_effective_case_count": candidate_count,
        "code": f"{kind}_regression_exceeds_policy",
        "failure_class": "candidate",
        "failure_owner": "candidate",
        "failure_scope": "candidate",
        "repairable": True,
    }


def _first_comparable_metric_key(
    baseline_metrics: Mapping[str, Any],
    candidate_metrics: Mapping[str, Any],
    keys: Iterable[str],
) -> str | None:
    return next(
        (
            key
            for key in keys
            if _number_metric(baseline_metrics, key) is not None
            and _number_metric(candidate_metrics, key) is not None
        ),
        None,
    )
