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
        passed = (
            decision.confidence == "verified"
            and decision.verification_split == "held_out"
            and decision.held_out_case_count >= self.min_eval_cases
            and decision.deterministic_signal_present
        )
        return GateResult(
            gate_name="held_out_verification",
            passed=passed,
            reason=(
                "candidate is verified on sufficient held-out cases"
                if passed
                else "candidate is not verified on sufficient held-out cases"
            ),
            details={
                "confidence": decision.confidence,
                "held_out_case_count": decision.held_out_case_count,
                "min_eval_cases": self.min_eval_cases,
                "verification_split": decision.verification_split,
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
