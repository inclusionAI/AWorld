from __future__ import annotations

from aworld.self_evolve.evaluation import CandidateConfidenceDecision, ReplayCostEstimate
from aworld.self_evolve.gates import (
    BudgetGate,
    CostLatencyRegressionGate,
    JudgeOnlySignalGate,
    NoopCandidateGate,
    ProtectedPathGate,
    ScoreImprovementGate,
    SkillMarkdownGate,
)
from aworld.self_evolve.types import CandidateVariant, EvaluationSummary, SelfEvolveTargetRef


def _candidate(content: str, *, path: str | None = "SKILL.md") -> CandidateVariant:
    return CandidateVariant(
        candidate_id="cand-1",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo", path=path),
        content=content,
        rationale="test",
        target_fingerprint="sha256:old",
    )


def test_score_improvement_gate_requires_min_delta() -> None:
    gate = ScoreImprovementGate(min_delta=0.1)

    passed = gate.evaluate(
        baseline=EvaluationSummary(variant_id="baseline", metrics={"score": 0.5}),
        candidate=EvaluationSummary(variant_id="cand-1", metrics={"score": 0.7}),
    )
    failed = gate.evaluate(
        baseline=EvaluationSummary(variant_id="baseline", metrics={"score": 0.5}),
        candidate=EvaluationSummary(variant_id="cand-1", metrics={"score": 0.55}),
    )

    assert passed.passed is True
    assert passed.details["delta"] == 0.2
    assert failed.passed is False
    assert failed.reason == "score improvement below minimum delta"


def test_cost_latency_regression_gate_limits_regressions() -> None:
    gate = CostLatencyRegressionGate(max_cost_regression_ratio=0.25, max_latency_regression_ratio=0.5)

    passed = gate.evaluate(
        baseline=EvaluationSummary(
            variant_id="baseline",
            metrics={"cost_usd": 1.0, "latency_ms": 100.0},
        ),
        candidate=EvaluationSummary(
            variant_id="cand-1",
            metrics={"cost_usd": 1.2, "latency_ms": 140.0},
        ),
    )
    failed = gate.evaluate(
        baseline=EvaluationSummary(
            variant_id="baseline",
            metrics={"cost_usd": 1.0, "latency_ms": 100.0},
        ),
        candidate=EvaluationSummary(
            variant_id="cand-1",
            metrics={"cost_usd": 1.5, "latency_ms": 140.0},
        ),
    )

    assert passed.passed is True
    assert failed.passed is False
    assert failed.reason == "cost regression exceeds policy"


def test_noop_and_skill_markdown_gates_reject_bad_candidates() -> None:
    current = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"

    assert NoopCandidateGate().evaluate(current_content=current, candidate=_candidate(current)).passed is False
    assert SkillMarkdownGate().evaluate(_candidate("# Demo\n\nMissing frontmatter.\n")).passed is False
    assert SkillMarkdownGate().evaluate(
        _candidate("---\nname: demo\n---\n# Demo\n\nUpdated guidance.\n")
    ).passed is True


def test_protected_path_gate_blocks_product_and_app_evaluator_paths() -> None:
    gate = ProtectedPathGate(workspace_root="/repo")

    assert gate.evaluate(_candidate("x", path="/repo/aworld/core/runtime.py")).passed is False
    assert gate.evaluate(_candidate("x", path="/repo/aworld-cli/src/main.py")).passed is False
    assert gate.evaluate(_candidate("x", path="/repo/aworld-skills/app_evaluator/SKILL.md")).passed is False
    assert gate.evaluate(_candidate("x", path="/repo/generated/SKILL.md")).passed is True


def test_budget_and_judge_only_gates_downgrade_or_reject() -> None:
    budget_gate = BudgetGate()
    budget = ReplayCostEstimate(
        passed=False,
        reason="estimated replay tokens exceed max_run_tokens",
        baseline_replay_count=1,
        candidate_replay_count=1,
        total_replay_count=2,
        verification_command_count=0,
        judge_call_count=0,
        estimated_tokens=10_000,
    )

    assert budget_gate.evaluate(budget).passed is False
    assert budget_gate.evaluate(budget).reason == "estimated replay tokens exceed max_run_tokens"

    judge_gate = JudgeOnlySignalGate()
    decision = CandidateConfidenceDecision(
        confidence="limited",
        reason="verified confidence requires a deterministic signal",
        selection_split="validation",
        verification_split="held_out",
        deterministic_signal_present=False,
        held_out_case_count=3,
    )

    result = judge_gate.evaluate(decision)
    assert result.passed is False
    assert result.reason == "judge-only improvements remain limited confidence"
