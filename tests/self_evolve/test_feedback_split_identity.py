from dataclasses import replace

from aworld.self_evolve.evolution_context import compile_evolution_context
from aworld.self_evolve.feedback_diagnostics import (
    _merge_validation_feedback,
    _validation_feedback_failure_family,
)
from aworld.self_evolve.optimizers.base import OptimizerRequest
from aworld.self_evolve.types import EvaluationSummary, SelfEvolveTargetRef


def _feedback(split: str, *, variant: str = "candidate") -> EvaluationSummary:
    return EvaluationSummary(
        variant_id=variant,
        dataset_split=split,
        metrics={
            "failed_gates": ["score_improvement", "evidence_quality"],
            "failure_class": "candidate",
            "repairable": True,
            "score": 68.0 if split == "validation" else 86.5,
            "score_delta": -12.0 if split == "validation" else 1.0,
            "evidence_incomplete": split == "validation",
            "evidence_issues": [
                "Qualify conclusions that exceed retrieved support."
                if split == "validation"
                else "Preserve the supported comparison."
            ],
            "candidate_validation_diagnostics": [
                {"code": "failed_gate", "stage": "score_improvement"}
            ],
            "repair_candidate_package": {
                "candidate_id": variant,
                "content": "# Demo\n\nScope large page reads.\n",
                "files": [],
            },
        },
    )


def test_feedback_preserves_validation_and_held_out_with_same_failure_family():
    validation = _feedback("validation")
    held_out = _feedback("held_out")

    # Dataset roles must not create extra failure families for budget grants.
    assert _validation_feedback_failure_family(validation) == (
        _validation_feedback_failure_family(held_out)
    )
    merged = _merge_validation_feedback((), (validation, held_out))

    assert merged == (validation, held_out)


def test_feedback_replaces_only_the_same_split_checkpoint():
    validation = _feedback("validation", variant="old-validation")
    held_out = _feedback("held_out", variant="held-out")
    newer_validation = _feedback("validation", variant="new-validation")

    merged = _merge_validation_feedback(
        (validation, held_out), (newer_validation,)
    )

    assert merged == (held_out, newer_validation)


def test_both_split_findings_reach_focused_mutation_context():
    merged = _merge_validation_feedback(
        (), (_feedback("validation"), _feedback("held_out"))
    )
    request = OptimizerRequest(
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        current_content="# Demo\n",
        target_fingerprint="sha256:baseline",
        trace_packs=(),
        validation_feedback=merged,
    )

    payload = compile_evolution_context(request).to_prompt_payload(candidate_index=0)
    findings = {item["dataset_split"]: item for item in payload["validation_feedback"]}

    assert set(findings) == {"validation", "held_out"}
    assert findings["validation"]["metrics"]["score_delta"] == -12.0
    assert findings["validation"]["evidence"]["evidence_incomplete"] is True
    assert findings["held_out"]["metrics"]["score"] == 86.5
    assert "Qualify conclusions" in findings["validation"]["evidence"]["issues"][0]


def test_split_aware_feedback_remains_bounded():
    items = tuple(replace(_feedback("validation"), dataset_split=f"split-{i}") for i in range(20))

    merged = _merge_validation_feedback((), items)

    assert merged == items[-16:]
