from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest

from aworld.self_evolve.evaluation_reporting import _evidence_quality_gate
from aworld.self_evolve.evolution_context import compile_evolution_context
from aworld.self_evolve.feedback_history import _feedback_from_report
from aworld.self_evolve.gates import EvidenceQualityGate
from aworld.self_evolve.optimizers.base import OptimizerRequest
from aworld.self_evolve.types import EvaluationSummary, SelfEvolveTargetRef


def _report(tmp_path: Path, *, failing: set[str], labelled: bool = True) -> tuple[dict, Path]:
    candidate = "evaluated-parent"
    package_dir = tmp_path / "candidates" / candidate
    package_dir.mkdir(parents=True)
    (package_dir / "candidate.json").write_text(json.dumps({
        "candidate_id": candidate, "content": "# Demo\n\nBound page reads.\n", "files": [],
    }))
    gates = []
    for split in ("validation", "held_out"):
        details = {"failure_class": "candidate", "repairable": True}
        if labelled:
            details["dataset_split"] = split
        gates.append({
            "gate_name": "evidence_quality", "passed": split not in failing,
            "reason": "recorded baseline-relative result", "details": details,
        })
    report = {
        "run_id": "previous-cycle", "selected_candidate_id": candidate,
        "gate_results": list(reversed(gates)) if labelled else gates,
        "iterations": [{
            "candidate_id": candidate, "status": "rejected",
            "failed_gates": ["evidence_quality"],
            "candidate_metrics": {
                "score": 89.0, "evidence_incomplete": True,
                "evidence_issues": ["Qualify the unsupported causal comparison."],
            },
            "held_out_metrics": {
                "score": 86.0, "evidence_incomplete": True,
                "evidence_issues": ["Preserve the supported explanation."],
            },
        }],
    }
    return report, tmp_path / "report.json"


def _prompt(feedback):
    request = OptimizerRequest(
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        current_content="# Demo\n", target_fingerprint="sha256:base", trace_packs=(),
        validation_feedback=feedback,
    )
    return compile_evolution_context(request).to_prompt_payload(candidate_index=0)


@pytest.mark.parametrize("labelled", [True, False])
@pytest.mark.parametrize("failing", [{"validation"}, {"held_out"}, {"validation", "held_out"}])
def test_historical_focus_tracks_recorded_failure_and_keeps_both_splits(tmp_path, failing, labelled):
    report, path = _report(tmp_path, failing=failing, labelled=labelled)
    original = deepcopy(report)

    feedback = _feedback_from_report(report, report_path=path)
    split_items = {item.dataset_split: item for item in feedback
                   if item.dataset_split in {"validation", "held_out"}}
    payload = _prompt(feedback)

    assert report == original
    assert set(split_items) == {"validation", "held_out"}
    for split, key in (("validation", "candidate_metrics"), ("held_out", "held_out_metrics")):
        item = split_items[split]
        assert item.metrics["evidence_issues"] == report["iterations"][0][key]["evidence_issues"]
        assert item.metrics["score"] == report["iterations"][0][key]["score"]
        assert item.metrics["failed_gates"] == (["evidence_quality"] if split in failing else [])
        assert ("repair_candidate_package" in item.metrics) is (split in failing)
    assert payload["repair_focus"]["dataset_split"] == (
        "held_out" if "held_out" in failing else "validation"
    )
    findings = {item["dataset_split"]: item for item in payload["validation_feedback"]
                if item.get("dataset_split") in {"validation", "held_out"}}
    assert set(findings) == {"validation", "held_out"}
    assert findings["validation"]["evidence"]["issues"] == ["Qualify the unsupported causal comparison."]


def test_legacy_single_split_is_unambiguous(tmp_path):
    report, path = _report(tmp_path, failing={"validation"}, labelled=False)
    report["gate_results"] = report["gate_results"][:1]
    report["iterations"][0].pop("held_out_metrics")

    payload = _prompt(_feedback_from_report(report, report_path=path))

    assert payload["repair_focus"]["dataset_split"] == "validation"


@pytest.mark.parametrize("ambiguity", ["single_gate", "extra_gate", "mixed_identity"])
def test_ambiguous_legacy_history_does_not_assign_failure_to_either_split(tmp_path, ambiguity):
    report, path = _report(tmp_path, failing={"validation"}, labelled=False)
    if ambiguity == "single_gate":
        report["gate_results"] = report["gate_results"][:1]
    elif ambiguity == "extra_gate":
        report["gate_results"].append(deepcopy(report["gate_results"][0]))
    else:
        report["iterations"][0]["candidate_metrics"]["evaluation_identity"] = {
            "variant_fingerprint": "sha256:one", "dataset_split": "validation",
        }
        report["iterations"][0]["held_out_metrics"]["evaluation_identity"] = {
            "variant_fingerprint": "sha256:other", "dataset_split": "held_out",
        }

    feedback = _feedback_from_report(report, report_path=path)

    for item in feedback:
        if item.dataset_split in {"validation", "held_out"}:
            assert item.metrics["failed_gates"] == []
            assert "repair_candidate_package" not in item.metrics
    unknown = next(item for item in feedback if item.dataset_split == "historical_repair")
    assert unknown.metrics["failed_gates"] == ["evidence_quality"]
    assert "repair_candidate_package" in unknown.metrics
    assert any(item.get("code") == "historical_judge_failure_split_unknown"
               for item in unknown.metrics["candidate_validation_diagnostics"])


@pytest.mark.parametrize("split", ["validation", "held_out"])
def test_evidence_gate_records_origin_without_changing_its_decision(split):
    baseline = EvaluationSummary(variant_id="base", dataset_split=split, metrics={
        "has_evidence": True, "evidence_block_count": 1, "evidence_incomplete": False,
    })
    candidate = replace(baseline, variant_id="candidate", metrics={
        **baseline.metrics, "evidence_incomplete": True,
    })
    original = EvidenceQualityGate().evaluate(candidate, baseline=baseline)

    labelled = _evidence_quality_gate(candidate, baseline=baseline)

    assert labelled == replace(original, details={**(original.details or {}), "dataset_split": split})
    assert labelled.passed is False
