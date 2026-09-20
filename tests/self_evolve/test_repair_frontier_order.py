from copy import deepcopy
from dataclasses import replace
import hashlib
import json

import pytest

from aworld.self_evolve.candidate_package import candidate_package_fingerprint
from aworld.self_evolve.controllers.run_iteration_helpers import (
    _iteration_validation_feedback,
)
from aworld.self_evolve.feedback_history import _feedback_from_report
from aworld.self_evolve.optimizers.base import OptimizerRequest
from aworld.self_evolve.optimizers.llm_mutator import _build_mutation_prompt
from aworld.self_evolve.repair_selection import with_repair_selection
from aworld.self_evolve.types import (
    CandidateFileDelta,
    CandidateVariant,
    EvaluationSummary,
    GateResult,
    SelfEvolveTargetRef,
    to_json_dict,
)


def _fp(value):
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _candidate(candidate_id, parents=()):
    return CandidateVariant(
        candidate_id,
        SelfEvolveTargetRef("skill", "demo"),
        "# Demo\n\n" + candidate_id + " behavior.\n",
        "bounded repair",
        parent_candidate_ids=parents,
        target_fingerprint=_fp("baseline"),
        files=(
            CandidateFileDelta(
                "replay/runtime.py", content=f"print('{candidate_id}')\n"
            ),
        ),
    )


def _metrics(candidate, split):
    identity = {
        "schema_version": "aworld.self_evolve.evaluation_identity.v1",
        "role": "candidate",
        "dataset_split": split,
        "fingerprint": _fp(candidate.candidate_id + split),
        "variant_fingerprint": candidate_package_fingerprint(candidate),
        "backend_fingerprint": _fp("judge"),
        "dataset_fingerprint": _fp("render-" + candidate.candidate_id),
    }
    return {
        "score": 89.0,
        "evidence_incomplete": True,
        "evidence_bundle_valid": True,
        "evidence_issues": [candidate.candidate_id + " " + split + " exact issue"],
        "evaluation_agent_signal": True,
        "evaluation_fresh_execution": True,
        "judge_success_count": 3,
        "judge_failure_count": 0,
        "judge_timeout_count": 0,
        "evaluation_identity": identity,
        "evaluation_identity_fingerprint": identity["fingerprint"],
        "comparison_case_ids": [split + "-case"],
    }


def _report(candidate, *, child=False):
    gates = [
        {
            "gate_name": name,
            "passed": passed,
            "reason": "original gate result",
            "details": {
                "dataset_split": split,
                "failure_class": "candidate",
                "repairable": not passed,
            },
        }
        for split, name, passed in [
            ("validation", "score_improvement", child),
            ("validation", "evidence_quality", not child),
            ("held_out", "evidence_quality", child),
            ("held_out", "held_out_verification", True),
        ]
    ]
    return {
        "run_id": candidate.candidate_id,
        "selected_candidate_id": candidate.candidate_id,
        "gate_results": gates,
        "iterations": [
            {
                "candidate_id": candidate.candidate_id,
                "status": "rejected",
                "failed_gates": [x["gate_name"] for x in gates if not x["passed"]],
                "candidate_metrics": _metrics(candidate, "validation"),
                "held_out_metrics": _metrics(candidate, "held_out"),
            }
        ],
    }


def _feedback(tmp_path, candidate, report):
    root = tmp_path / candidate.candidate_id
    package = root / "candidates" / candidate.candidate_id
    package.mkdir(parents=True)
    serialized = json.dumps(to_json_dict(candidate))
    path = package / "candidate.json"
    path.write_text(serialized)
    before = deepcopy(report)
    feedback = _feedback_from_report(report, report_path=root / "report.json")
    assert report == before and path.read_text() == serialized
    return feedback


def _prompt(feedback, index=0):
    text = _build_mutation_prompt(
        OptimizerRequest(
            target=SelfEvolveTargetRef("skill", "demo"),
            current_content="# Demo\n",
            target_fingerprint=_fp("baseline"),
            trace_packs=(),
            validation_feedback=feedback,
        ),
        candidate_index=index,
    )
    return text, json.loads(text[text.index('{"acceptance_constraints"') :])


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("historical_child", [False, True])
def test_verified_child_checkpoint_prevents_regression_to_ancestor_failure(
    tmp_path, reverse, index, historical_child
):
    parent, child = _candidate("parent"), _candidate("child", ("parent",))
    parent_feedback = _feedback(tmp_path, parent, _report(parent))
    child_report = _report(child, child=True)
    child_feedback = _feedback(tmp_path, child, child_report)
    if not historical_child:
        gates = [GateResult(**gate) for gate in child_report["gate_results"]]
        summaries = {
            split: EvaluationSummary(
                child.candidate_id, child_report["iterations"][0][key], split
            )
            for split, key in (
                ("validation", "candidate_metrics"),
                ("held_out", "held_out_metrics"),
            )
        }
        raw = _iteration_validation_feedback(
            candidate=child,
            baseline_summary=None,
            candidate_summary=summaries["validation"],
            held_out_summary=summaries["held_out"],
            failed_gates=[g for g in gates if not g.passed],
        )
        child_feedback = with_repair_selection(
            raw,
            candidate=child,
            summaries=summaries,
            split_gates={
                split: [g for g in gates if g.details["dataset_split"] == split]
                for split in summaries
            },
        )
    feedback = (
        (*child_feedback, *parent_feedback)
        if reverse
        else (*parent_feedback, *child_feedback)
    )
    text, payload = _prompt(feedback, index)
    focus = payload["repair_focus"]
    assert focus["variant_id"] == "child" and focus["dataset_split"] == "validation"
    assert focus["failed_gates"] == ["evidence_quality"]
    assert focus["evidence"]["issues"] == ["child validation exact issue"]
    assert focus["repair_candidate_package"]["content"] == child.content.strip()
    runtime = focus["repair_candidate_package"]["files"][0]
    assert (
        runtime["content_sha256"]
        == hashlib.sha256(child.files[0].content.encode()).hexdigest()
    )
    assert runtime["preserve_unchanged"] and runtime["content_omitted"]
    assert text.startswith("Repair the judged target behavior")
    assert "repair_selection" not in text
    assert (
        len(
            json.dumps(
                payload["validation_feedback"],
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        <= 16000
    )
    assert any(x["variant_id"] == "parent" for x in payload["validation_feedback"])


@pytest.mark.parametrize(
    "problem",
    [
        "unrelated",
        "no_signal",
        "stale",
        "zero_judges",
        "judge_failure",
        "wrong_variant",
        "wrong_role",
        "wrong_split",
        "different_backend",
        "different_panel",
        "different_target",
        "still_failing",
        "missing_pass",
        "current_no_signal",
        "wrong_identity",
        "bool_success",
    ],
)
def test_unproved_child_does_not_displace_deeper_ancestor(tmp_path, problem):
    parent, child = (
        _candidate("parent"),
        _candidate("child", () if problem == "unrelated" else ("parent",)),
    )
    if problem == "different_target":
        child = replace(child, target_fingerprint=_fp("other-baseline"))
    report = _report(child, child=True)
    metrics = report["iterations"][0]["held_out_metrics"]
    if problem == "no_signal":
        metrics["evaluation_agent_signal"] = False
    elif problem == "stale":
        metrics["evaluation_fresh_execution"] = False
    elif problem == "zero_judges":
        metrics["judge_success_count"] = 0
    elif problem == "judge_failure":
        metrics["judge_failure_count"] = 1
    elif problem == "wrong_variant":
        metrics["evaluation_identity"]["variant_fingerprint"] = _fp("other")
    elif problem == "wrong_role":
        metrics["evaluation_identity"]["role"] = "baseline"
    elif problem == "wrong_split":
        metrics["evaluation_identity"]["dataset_split"] = "validation"
    elif problem == "different_backend":
        metrics["evaluation_identity"]["backend_fingerprint"] = _fp("other-judge")
    elif problem == "different_panel":
        metrics["comparison_case_ids"] = ["other-case"]
    elif problem == "still_failing":
        report["gate_results"][2]["passed"] = False
    elif problem == "missing_pass":
        report["gate_results"].pop(2)
    elif problem == "current_no_signal":
        report["iterations"][0]["candidate_metrics"]["evaluation_agent_signal"] = False
    elif problem == "wrong_identity":
        metrics["evaluation_identity_fingerprint"] = _fp("other-evaluation")
    elif problem == "bool_success":
        metrics["judge_success_count"] = True
    feedback = (
        *_feedback(tmp_path, parent, _report(parent)),
        *_feedback(tmp_path, child, report),
    )
    _, payload = _prompt(feedback)
    if problem != "still_failing":
        assert payload["repair_focus"]["variant_id"] == "parent"
    assert payload["repair_focus"]["dataset_split"] == "held_out"


def test_held_out_progress_cannot_displace_independent_regression(tmp_path):
    parent, child = _candidate("parent"), _candidate("child", ("parent",))
    parent_feedback = _feedback(tmp_path, parent, _report(parent))
    original = next(x for x in parent_feedback if x.dataset_split == "held_out")
    envelope = {
        "schema_version": "aworld.self_evolve.independent_regression_feedback.v1",
        "candidate_id": "parent",
        "evidence_fingerprint": _fp("independent"),
        "suites": [
            {
                "suite_id": "independent-contract",
                "fresh_execution": True,
                "baseline": {"score": 90.0},
                "candidate": {"score": 80.0},
                "failed_gates": [
                    {
                        "gate_name": "score_improvement",
                        "passed": False,
                        "reason": "original independent failure",
                        "details": {"failure_owner": "candidate", "repairable": True},
                    }
                ],
            }
        ],
    }
    regression = replace(
        original,
        dataset_split="regression",
        metrics={
            **original.metrics,
            "failed_gates": ["global_regression_benchmark"],
            "independent_regression": envelope,
        },
    )
    _, payload = _prompt(
        (
            *parent_feedback,
            regression,
            *_feedback(tmp_path, child, _report(child, child=True)),
        )
    )
    assert payload["repair_focus"]["variant_id"] == "parent"
    assert payload["repair_focus"]["dataset_split"] == "regression"


def test_ambiguous_child_checkpoint_does_not_supersede_parent(tmp_path):
    parent, child = _candidate("parent"), _candidate("child", ("parent",))
    before = _feedback(tmp_path, parent, _report(parent))
    after = _feedback(tmp_path, child, _report(child, child=True))
    source = next(x for x in after if "repair_selection" in x.metrics)
    metrics = deepcopy(source.metrics)
    metrics["repair_selection"]["checkpoints"]["held_out"]["evaluation_fingerprint"] = (
        _fp("conflicting-evaluation")
    )
    _, payload = _prompt((*before, *after, replace(source, metrics=metrics)))
    assert payload["repair_focus"]["variant_id"] == "parent"


def test_cyclic_lineage_does_not_authorize_checkpoint_supersession(tmp_path):
    parent, child, grandchild = (
        _candidate("parent", ("grandchild",)),
        _candidate("child", ("parent",)),
        _candidate("grandchild", ("child",)),
    )
    feedback = (
        *_feedback(tmp_path, parent, _report(parent)),
        *_feedback(tmp_path, child, _report(child, child=True)),
        *_feedback(tmp_path, grandchild, _report(grandchild, child=True)),
    )
    _, payload = _prompt(feedback)
    assert payload["repair_focus"]["variant_id"] == "parent"


@pytest.mark.parametrize("missing_metadata", [False, True])
def test_conflicting_source_cannot_borrow_same_candidate_checkpoint(
    tmp_path, missing_metadata
):
    parent, child = _candidate("parent"), _candidate("child", ("parent",))
    before = _feedback(tmp_path, parent, _report(parent))
    after = _feedback(tmp_path, child, _report(child, child=True))
    source = next(item for item in after if "repair_selection" in item.metrics)
    metrics = deepcopy(source.metrics)
    metrics["repair_candidate_package"]["content"] = "# Wrong package source"
    if missing_metadata:
        metrics.pop("repair_selection")
    _, payload = _prompt((*before, *after, replace(source, metrics=metrics)))
    assert payload["repair_focus"]["variant_id"] == "parent"
    assert payload["repair_focus"]["dataset_split"] == "held_out"


def test_history_checkpoint_projection_tolerates_gate_extension_fields(tmp_path):
    parent, child = _candidate("parent"), _candidate("child", ("parent",))
    report = _report(child, child=True)
    for gate in report["gate_results"]:
        gate["schema_version"] = "future-gate-format"
    report["gate_results"][2].pop("reason")
    _, payload = _prompt(
        (
            *_feedback(tmp_path, parent, _report(parent)),
            *_feedback(tmp_path, child, report),
        )
    )
    assert payload["repair_focus"]["variant_id"] == "child"
