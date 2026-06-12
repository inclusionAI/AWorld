from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from aworld.self_evolve.evaluation import CandidateConfidenceDecision, ReplayCostEstimate
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
