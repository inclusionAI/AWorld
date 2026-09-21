import json

import pytest

from aworld.self_evolve.evolution_context import (
    MAX_PROMPT_FEEDBACK_CHARS,
    _budget_prompt_feedback,
    _compact_prompt_feedback_item,
    compile_evolution_context,
)
from aworld.self_evolve.optimizers.base import OptimizerRequest
from aworld.self_evolve.types import EvaluationSummary, SelfEvolveTargetRef


def _judged_feedback(split: str, *, large_diagnostics: bool) -> EvaluationSummary:
    diagnostics = [
        {
            "code": "score_improvement_inconclusive",
            "stage": "evaluation",
            "reason": f"Checkpoint {index}: " + "paired judge variance " * 20,
            "required_action": "Address the observed answer-quality gap. " * 12,
            "details": {
                "variance_context": "Repeated evaluations overlap. " * 20,
                "confidence_context": "Improvement remains inconclusive. " * 20,
            },
        }
        for index in range(16 if large_diagnostics else 1)
    ]
    return EvaluationSummary(
        variant_id="judged-candidate",
        dataset_split=split,
        metrics={
            "failed_gates": ["score_improvement"],
            "score": 80.0,
            "score_delta": -2.0,
            "evidence_issues": [
                "Comparative claims exceed the abstract-level evidence."
                if split == "validation"
                else "The answer names examples absent from the retrieved evidence."
            ],
            "candidate_validation_diagnostics": diagnostics,
            "repair_candidate_package": {
                "candidate_id": "judged-candidate",
                "content": "# Demo\n\nScope large page reads.\n",
                "files": [],
            },
        },
    )


@pytest.mark.parametrize("large_diagnostics", [False, True])
def test_judge_issues_survive_focused_prompt_compaction(large_diagnostics):
    feedback = tuple(
        _judged_feedback(split, large_diagnostics=large_diagnostics)
        for split in ("validation", "held_out")
    )
    context = compile_evolution_context(
        OptimizerRequest(
            target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
            current_content="# Demo\n",
            target_fingerprint="sha256:baseline",
            trace_packs=(),
            validation_feedback=feedback,
        )
    )

    payload = context.to_prompt_payload(candidate_index=0)
    findings = {
        item["dataset_split"]: item for item in payload["validation_feedback"]
    }

    assert payload["repair_focus"]["dataset_split"] == "held_out"
    assert payload["repair_support"]["dataset_split"] == "validation"
    assert payload["repair_support"]["feedback_compacted"] is True
    assert "repair_candidate_package" not in payload["repair_support"]
    for original in feedback:
        split = original.dataset_split
        expected_issues = original.metrics["evidence_issues"]
        assert findings[split]["evidence"]["issues"] == expected_issues
        assert findings[split]["metrics"]["score_delta"] == -2.0
        assert findings[split]["failed_gates"] == ["score_improvement"]
        assert findings[split].get("feedback_compacted", False) is large_diagnostics
        checkpoint = "repair_focus" if split == "held_out" else "repair_support"
        assert payload[checkpoint]["evidence"]["issues"] == expected_issues
    encoded = json.dumps(
        payload["validation_feedback"], ensure_ascii=False, separators=(",", ":")
    )
    assert len(encoded) <= MAX_PROMPT_FEEDBACK_CHARS


def test_compacted_judge_issues_keep_existing_text_and_count_limits():
    issue = "Claim exceeds evidence at /Users/alice/private/report.txt. "
    compact = _compact_prompt_feedback_item(
        {
            "variant_id": "candidate",
            "dataset_split": "validation",
            "metrics": {"score": 67.0},
            "evidence": {
                "issues": [f"Issue {index}: {issue * 20}" for index in range(5)],
                "expected_output": "Private reference answer must not be projected.",
            },
        }
    )

    issues = compact["evidence"]["issues"]
    assert len(issues) == 3
    assert all(len(value) <= 240 for value in issues)
    assert all(value.startswith(f"Issue {index}:") for index, value in enumerate(issues))
    assert all("<LOCAL_PATH>" in value for value in issues)
    assert "/Users/alice" not in json.dumps(compact)
    assert set(compact["evidence"]) == {"issues"}
    assert compact["metrics"] == {"score": 67.0}


@pytest.mark.parametrize(
    "evidence", [None, {}, {"issues": "not a list"}, {"issues": [None, {}, " "]}]
)
def test_compacted_feedback_does_not_invent_missing_judge_issues(evidence):
    compact = _compact_prompt_feedback_item({"evidence": evidence})

    assert "evidence" not in compact


def test_compacted_judge_issues_respect_the_existing_feedback_budget():
    feedback = tuple(
        {
            "variant_id": f"candidate-{index}",
            "dataset_split": "validation",
            "evidence": {"issues": ["Evidence gap: " + "x" * 226] * 3},
        }
        for index in range(24)
    )

    compacted = _budget_prompt_feedback(feedback)

    encoded = json.dumps(compacted, ensure_ascii=False, separators=(",", ":"))
    assert len(encoded) <= MAX_PROMPT_FEEDBACK_CHARS
    assert compacted[0]["evidence"]["issues"] == feedback[0]["evidence"]["issues"]
    assert compacted[-1]["feedback_items_omitted"] > 0
    assert compacted[-1]["reason"] == "prompt_feedback_char_budget"
