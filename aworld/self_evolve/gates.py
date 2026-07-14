from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aworld.self_evolve.evaluation import CandidateConfidenceDecision, ReplayCostEstimate
from aworld.self_evolve.provenance import TargetProvenance
from aworld.self_evolve.types import CandidateVariant, EvaluationSummary, GateResult


class ScoreImprovementGate:
    def __init__(self, *, min_delta: float) -> None:
        self.min_delta = min_delta

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
        return GateResult(
            gate_name="score_improvement",
            passed=delta >= self.min_delta,
            reason=(
                "score improvement meets minimum delta"
                if delta >= self.min_delta
                else "score improvement below minimum delta"
            ),
            details={"baseline": baseline_score, "candidate": candidate_score, "delta": round(delta, 10)},
        )


class CostLatencyRegressionGate:
    def __init__(
        self,
        *,
        max_cost_regression_ratio: float,
        max_latency_regression_ratio: float,
    ) -> None:
        self.max_cost_regression_ratio = max_cost_regression_ratio
        self.max_latency_regression_ratio = max_latency_regression_ratio

    def evaluate(
        self,
        *,
        baseline: EvaluationSummary,
        candidate: EvaluationSummary,
    ) -> GateResult:
        cost_ratio = _regression_ratio(baseline.metrics, candidate.metrics, "cost_usd")
        if cost_ratio is not None and cost_ratio > self.max_cost_regression_ratio:
            return GateResult(
                gate_name="cost_latency_regression",
                passed=False,
                reason="cost regression exceeds policy",
                details={"cost_regression_ratio": cost_ratio},
            )

        latency_ratio = _regression_ratio(baseline.metrics, candidate.metrics, "latency_ms")
        if latency_ratio is not None and latency_ratio > self.max_latency_regression_ratio:
            return GateResult(
                gate_name="cost_latency_regression",
                passed=False,
                reason="latency regression exceeds policy",
                details={"latency_regression_ratio": latency_ratio},
            )

        return GateResult(
            gate_name="cost_latency_regression",
            passed=True,
            reason="cost and latency regressions are within policy",
            details={
                "cost_regression_ratio": cost_ratio,
                "latency_regression_ratio": latency_ratio,
            },
        )


class NoopCandidateGate:
    def evaluate(self, *, current_content: str, candidate: CandidateVariant) -> GateResult:
        changed = candidate.content != current_content
        return GateResult(
            gate_name="noop_candidate",
            passed=changed,
            reason="candidate changes target content" if changed else "candidate content is unchanged",
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
    _FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", flags=re.DOTALL)

    def evaluate(self, candidate: CandidateVariant) -> GateResult:
        if not self._FRONTMATTER_RE.match(candidate.content):
            return GateResult(
                gate_name="skill_markdown",
                passed=False,
                reason="skill candidate must preserve YAML frontmatter",
            )
        return GateResult(
            gate_name="skill_markdown",
            passed=True,
            reason="skill candidate markdown shape is valid",
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
        passed = len(candidate.content) <= self.max_chars
        return GateResult(
            gate_name="token_limit",
            passed=passed,
            reason=(
                "candidate content is within token budget"
                if passed
                else "candidate content exceeds token budget"
            ),
            details={"max_chars": self.max_chars, "actual_chars": len(candidate.content)},
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
        return GateResult(
            gate_name="budget",
            passed=estimate.passed,
            reason=estimate.reason,
            details={
                "estimated_tokens": estimate.estimated_tokens,
                "estimated_cost_usd": estimate.estimated_cost_usd,
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


class EvidenceQualityGate:
    _COMPACTED_CONTEXT_MARKER = "tool output compacted for context reuse"
    _TRUNCATED_EVIDENCE_MARKERS = ("truncated", "tool evidence")
    _PREVIEW_MARKERS = (
        "original size:",
        "preview:",
    )

    def evaluate(self, summary: EvaluationSummary) -> GateResult:
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
    _GENERATED_OR_EXTERNAL_TRUST_LEVELS = {"generated", "external"}
    _GENERATED_OR_EXTERNAL_ORIGINS = {"agent_generated_artifact", "external"}

    def __init__(self, *, allow_generated: bool = False, allow_external: bool = False) -> None:
        self.allow_generated = allow_generated
        self.allow_external = allow_external

    def evaluate(self, provenance: TargetProvenance) -> GateResult:
        if provenance.protected:
            return GateResult(
                gate_name="trust_provenance",
                passed=False,
                reason="protected target provenance cannot be mutated",
            )
        if (
            provenance.trust_level == "generated"
            or provenance.write_origin == "agent_generated_artifact"
        ) and not self.allow_generated:
            return GateResult(
                gate_name="trust_provenance",
                passed=False,
                reason="generated target requires explicit trust policy",
            )
        if (
            provenance.trust_level == "external"
            or provenance.write_origin == "external"
        ) and not self.allow_external:
            return GateResult(
                gate_name="trust_provenance",
                passed=False,
                reason="external target requires explicit trust policy",
            )
        return GateResult(
            gate_name="trust_provenance",
            passed=True,
            reason="target provenance satisfies trust policy",
        )


class GlobalRegressionBenchmarkGate:
    _REQUIRES_REGRESSION_TARGET_TYPES = {"skill", "prompt-section", "tool-description"}

    def evaluate(
        self,
        candidate: CandidateVariant,
        summary: EvaluationSummary,
    ) -> GateResult:
        if candidate.target.target_type not in self._REQUIRES_REGRESSION_TARGET_TYPES:
            return GateResult(
                gate_name="global_regression_benchmark",
                passed=True,
                reason="target type does not require global regression benchmark",
            )
        passed = summary.metrics.get("global_regression_passed") is True
        return GateResult(
            gate_name="global_regression_benchmark",
            passed=passed,
            reason=(
                "global regression benchmark passed"
                if passed
                else "global regression benchmark is required for verified text targets"
            ),
        )


def _number_metric(metrics: dict[str, Any] | Any, key: str) -> float | None:
    value = metrics.get(key) if hasattr(metrics, "get") else None
    return float(value) if isinstance(value, (int, float)) else None


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


def _regression_ratio(
    baseline_metrics: dict[str, Any] | Any,
    candidate_metrics: dict[str, Any] | Any,
    key: str,
) -> float | None:
    baseline = _number_metric(baseline_metrics, key)
    candidate = _number_metric(candidate_metrics, key)
    if baseline is None or candidate is None or baseline <= 0:
        return None
    return (candidate - baseline) / baseline
