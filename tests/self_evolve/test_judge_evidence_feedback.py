from __future__ import annotations

from copy import deepcopy

from aworld.self_evolve.evaluation import _aggregate_aworld_evaluator_metrics
from aworld.self_evolve.feedback import normalize_feedback_summary
from aworld.self_evolve.optimizers.base import OptimizerRequest
from aworld.self_evolve.optimizers.llm_mutator import build_mutation_prompt
from aworld.self_evolve.types import EvaluationSummary, SelfEvolveTargetRef


def _report(issues: list[str], *, score: float = 80, constraints=()):
    return {
        "summary": {"judge": {"score": {"mean": score}}},
        "gate": {"status": "fail", "metric_name": "score", "value": score},
        "results": [{
            "metrics": {"score": {"value": score}},
            "judge": {
                "evidence_quality": {
                    "has_evidence": True,
                    "evidence_incomplete": True,
                    "evidence_issues": issues,
                },
                "evidence_repair_constraints": list(constraints),
            },
        }],
    }


def test_repeated_judge_keeps_differently_worded_causes_through_repair_prompt(tmp_path):
    first = "The response makes a causal claim that the source does not establish."
    second = "The cited material supports correlation, but the answer asserts causation."
    reports = [_report([first]), _report([second], score=78)]
    original = deepcopy(reports)

    metrics = _aggregate_aworld_evaluator_metrics(
        reports, case_count=1, input_path=tmp_path / "trajectory.log"
    )

    assert metrics["evidence_issues"] == [first, second]
    assert metrics["score"] == 79
    assert metrics["score_samples"] == [80, 78]
    assert metrics["judge_gate_passed"] is False
    assert reports == original

    summary = EvaluationSummary(
        variant_id="candidate", dataset_split="validation",
        metrics={
            **metrics,
            "failed_gates": ["evidence_quality"],
            "repairable": True,
            "repair_candidate_package": {
                "candidate_id": "candidate", "content": "# Demo\nRead the source.\n", "files": [],
            },
        },
    )
    assert normalize_feedback_summary(summary)["evidence"]["issues"] == [first, second]
    prompt = build_mutation_prompt(OptimizerRequest(
        target=SelfEvolveTargetRef("skill", "demo"),
        current_content="# Demo\n", target_fingerprint="sha256:baseline",
        trace_packs=(), validation_feedback=(summary,),
    ), candidate_index=0)
    assert first in prompt and second in prompt


def test_repeated_judge_issues_deduplicate_stably_and_keep_partial_rounds(tmp_path):
    reports = [
        _report(["Cause A", " Cause A ", "", "Cause B"]),
        _report([]),
        _report(["Cause B", "Cause C", "Cause A"]),
    ]
    metrics = _aggregate_aworld_evaluator_metrics(
        reports, case_count=1, input_path=tmp_path / "trajectory.log"
    )
    assert metrics["evidence_issues"] == ["Cause A", "Cause B", "Cause C"]


def test_repeated_judge_issue_merge_has_fixed_count_and_text_limits(tmp_path):
    reports = [
        _report(["Cause " + str(index) + ": " + "x" * 900 for index in range(40)]),
        _report(["Another cause " + str(index) for index in range(40)]),
    ]
    metrics = _aggregate_aworld_evaluator_metrics(
        reports, case_count=1, input_path=tmp_path / "trajectory.log"
    )
    assert len(metrics["evidence_issues"]) == 16
    assert all(len(issue) <= 480 for issue in metrics["evidence_issues"])
    assert metrics["evidence_issues"][0].startswith("Cause 0:")
    assert metrics["evidence_issues"][-1].startswith("Cause 15:")


def test_merging_judge_prose_preserves_typed_constraint_identity_and_counts(tmp_path):
    constraint = {
        "subject_kind": "general_claim", "failure_mode": "unsupported_claim",
        "source_layer": "candidate_output", "required_action": "support_or_omit",
        "owner": "candidate", "occurrence_count": 1,
    }
    reports = [
        _report(["The claim exceeds the source."], constraints=[constraint]),
        _report(["The source does not establish the asserted conclusion."], constraints=[constraint]),
    ]
    metrics = _aggregate_aworld_evaluator_metrics(
        reports, case_count=1, input_path=tmp_path / "trajectory.log"
    )
    assert len(metrics["evidence_issues"]) == 2
    assert len(metrics["evidence_repair_constraints"]) == 1
    merged = metrics["evidence_repair_constraints"][0]
    assert merged["occurrence_count"] == 2
    assert merged["failure_mode"] == "unsupported_claim"
    assert merged["required_action"] == "support_or_omit"
    assert merged["owner"] == "candidate"
