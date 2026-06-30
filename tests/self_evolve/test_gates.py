from __future__ import annotations

from aworld.self_evolve.evaluation import CandidateConfidenceDecision, ReplayCostEstimate
from aworld.self_evolve.gates import (
    BudgetGate,
    CostLatencyRegressionGate,
    ExternalCodeEvolutionGate,
    GlobalRegressionBenchmarkGate,
    HeldOutVerificationGate,
    JudgeOnlySignalGate,
    MalformedCandidateGate,
    NoopCandidateGate,
    PromptSectionGate,
    ProtectedPathGate,
    RequiredVerificationGate,
    ScoreImprovementGate,
    SkillMarkdownGate,
    StoppingConditionGate,
    StoppingConditionState,
    TokenLimitGate,
    ToolDescriptionGate,
    TrustProvenanceGate,
)
from aworld.self_evolve.provenance import TargetProvenance
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
    assert MalformedCandidateGate().evaluate(_candidate("")).passed is False
    assert MalformedCandidateGate().evaluate(_candidate("Updated guidance.")).passed is True


def test_prompt_tool_token_and_external_code_candidate_gates() -> None:
    assert PromptSectionGate().evaluate(_candidate("Follow these steps clearly.")).passed is True
    assert PromptSectionGate().evaluate(_candidate("")).passed is False
    assert ToolDescriptionGate().evaluate(_candidate("Use browser to inspect authenticated state.")).passed is True
    assert ToolDescriptionGate().evaluate(_candidate("bad")).passed is False
    assert TokenLimitGate(max_chars=12).evaluate(_candidate("short text")).passed is True
    assert TokenLimitGate(max_chars=4).evaluate(_candidate("too long")).passed is False
    assert ExternalCodeEvolutionGate().evaluate(_candidate("import darwinian_evolve")).passed is False


def test_required_verification_gate_requires_all_commands_to_pass() -> None:
    gate = RequiredVerificationGate()

    passed = gate.evaluate(
        EvaluationSummary(
            variant_id="cand-1",
            metrics={
                "deterministic_signal": True,
                "command_case_count": 2,
                "command_pass_count": 2,
            },
        )
    )
    failed = gate.evaluate(
        EvaluationSummary(
            variant_id="cand-1",
            metrics={
                "deterministic_signal": True,
                "command_case_count": 2,
                "command_pass_count": 1,
            },
        )
    )
    missing = gate.evaluate(EvaluationSummary(variant_id="cand-1", metrics={}))

    assert passed.passed is True
    assert failed.passed is False
    assert failed.reason == "required verification commands did not all pass"
    assert missing.passed is False
    assert missing.reason == "required deterministic verification command was not run"


def test_protected_path_gate_blocks_product_and_app_evaluator_paths() -> None:
    gate = ProtectedPathGate(workspace_root="/repo")

    assert gate.evaluate(_candidate("x", path="/repo/aworld/core/runtime.py")).passed is False
    assert gate.evaluate(_candidate("x", path="/repo/aworld-cli/src/main.py")).passed is False
    assert gate.evaluate(_candidate("x", path="/repo/aworld-skills/app_evaluator/SKILL.md")).passed is False
    assert gate.evaluate(_candidate("x", path="/repo/aworld-skills/self_evolve/SKILL.md")).passed is False
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


def test_stopping_condition_gate_rejects_iteration_stall_duplicate_failure_and_cooldown() -> None:
    gate = StoppingConditionGate(
        max_iterations=3,
        max_stalled_iterations=2,
        max_repeated_gate_failures=2,
    )

    assert gate.evaluate(StoppingConditionState(iteration=3)).passed is False
    assert gate.evaluate(StoppingConditionState(stalled_iterations=2)).reason == "stalled improvement limit reached"
    assert gate.evaluate(StoppingConditionState(pending_duplicate=True)).reason == "duplicate pending proposal exists"
    assert gate.evaluate(StoppingConditionState(cooldown_remaining_seconds=60)).reason == "target is in cooldown"
    assert gate.evaluate(StoppingConditionState(repeated_gate_failures=2)).reason == "repeated gate failure limit reached"
    assert gate.evaluate(StoppingConditionState(iteration=1)).passed is True


def test_held_out_and_global_regression_gates_require_independent_verification() -> None:
    held_out_gate = HeldOutVerificationGate(min_eval_cases=2)

    limited = held_out_gate.evaluate(
        CandidateConfidenceDecision(
            confidence="limited",
            reason="insufficient held-out eval cases for verified confidence",
            selection_split="validation",
            verification_split=None,
            deterministic_signal_present=True,
            held_out_case_count=1,
        )
    )
    verified = held_out_gate.evaluate(
        CandidateConfidenceDecision(
            confidence="verified",
            reason="held-out deterministic evaluation is sufficient",
            selection_split="validation",
            verification_split="held_out",
            deterministic_signal_present=True,
            held_out_case_count=2,
        )
    )

    assert limited.passed is False
    assert limited.reason == "candidate is not verified on sufficient held-out cases"
    assert verified.passed is True

    regression_gate = GlobalRegressionBenchmarkGate()
    assert regression_gate.evaluate(
        _candidate("x"),
        EvaluationSummary(variant_id="cand-1", metrics={"global_regression_passed": False}),
    ).passed is False
    assert regression_gate.evaluate(
        _candidate("x"),
        EvaluationSummary(variant_id="cand-1", metrics={"global_regression_passed": True}),
    ).passed is True
    assert regression_gate.evaluate(
        CandidateVariant(
            candidate_id="cand-1",
            target=SelfEvolveTargetRef(target_type="workspace-artifact", target_id="demo"),
            content="x",
            rationale="test",
        ),
        EvaluationSummary(variant_id="cand-1", metrics={}),
    ).passed is True


def test_held_out_gate_accepts_stable_single_case_replay_verification() -> None:
    gate = HeldOutVerificationGate(min_eval_cases=30)

    result = gate.evaluate(
        CandidateConfidenceDecision(
            confidence="verified",
            reason="single-case replay verification is sufficient",
            selection_split="validation",
            verification_split="single_case_replay",
            deterministic_signal_present=True,
            held_out_case_count=0,
            verification_mode="single_case_replay",
            baseline_replay_count=2,
            candidate_replay_count=3,
        )
    )

    assert result.passed is True
    assert result.reason == "candidate is verified by stable single-case replay"
    assert result.details["verification_mode"] == "single_case_replay"
    assert result.details["baseline_replay_count"] == 2
    assert result.details["candidate_replay_count"] == 3


def test_trust_provenance_gate_rejects_protected_generated_and_external_targets() -> None:
    target = SelfEvolveTargetRef(target_type="skill", target_id="demo")
    gate = TrustProvenanceGate()

    protected = gate.evaluate(
        TargetProvenance(
            target=target,
            source_kind="skill",
            write_origin="repository",
            trust_level="protected",
            protected=True,
            reason="read-only",
        )
    )
    generated = gate.evaluate(
        TargetProvenance(
            target=target,
            source_kind="workspace_artifact",
            write_origin="agent_generated_artifact",
            trust_level="generated",
            protected=False,
            reason="generated artifact",
        )
    )
    trusted = gate.evaluate(
        TargetProvenance(
            target=target,
            source_kind="skill",
            write_origin="repository",
            trust_level="local",
            protected=False,
            reason="local skill",
        )
    )

    assert protected.passed is False
    assert protected.reason == "protected target provenance cannot be mutated"
    assert generated.passed is False
    assert generated.reason == "generated target requires explicit trust policy"
    assert trusted.passed is True
