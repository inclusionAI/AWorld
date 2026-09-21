from __future__ import annotations

from copy import deepcopy
import json
import os

import pytest

from aworld.self_evolve.campaign import (
    _campaign_prior_run_ids_by_champion,
    _campaign_report_quality,
)
from aworld.self_evolve.run_history import _load_prior_rejected_feedback
import aworld.self_evolve.run_history as history_module
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.types import SelfEvolveTargetRef


TARGET = SelfEvolveTargetRef("skill", "demo", "/workspace/demo/SKILL.md")


def _report(run_id: str, count: int, *, target=TARGET) -> dict:
    return {
        "run_id": run_id,
        "status": "rejected",
        "target": {
            "target_type": target.target_type,
            "target_id": target.target_id,
            "path": target.path,
        },
        "iterations": [
            {
                "candidate_id": f"{run_id}-candidate-{index}",
                "status": "rejected",
                "failed_gates": ["score_improvement"],
                "candidate_metrics": {"score": index, "evidence_incomplete": False},
                "baseline_metrics": {"score": index + 1},
            }
            for index in range(count)
        ],
    }


def _write(store, report):
    path = store.run_path(report["run_id"]) / "report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report))
    return path


def _quality_report(anchor: str, candidate: str, *, deep=True, failures=1, score=80):
    return {
        "status": "rejected",
        anchor: candidate,
        "candidate_metrics": {"score": score},
        "gate_results": [
            {
                "gate_name": "held_out_verification"
                if deep
                else "candidate_generation",
                "passed": True,
            },
            *[{"gate_name": f"failed-{i}", "passed": False} for i in range(failures)],
        ],
    }


def test_candidate_anchor_spelling_does_not_override_better_checkpoints(tmp_path):
    store = FilesystemSelfEvolveStore(tmp_path)
    ancestor = _quality_report("repair_focus_candidate_id", "ancestor", failures=2)
    child = _quality_report("selected_candidate_id", "child", failures=1)
    _write(store, {**ancestor, "run_id": "ancestor-run"})
    _write(store, {**child, "run_id": "child-run"})
    assert _campaign_report_quality(child) > _campaign_report_quality(ancestor)
    assert (
        _campaign_prior_run_ids_by_champion(store, ("ancestor-run", "child-run"))[-1]
        == "child-run"
    )
    assert _campaign_report_quality(child) == _campaign_report_quality(
        {
            **{
                key: value
                for key, value in child.items()
                if key != "selected_candidate_id"
            },
            "repair_focus_candidate_id": "child",
        }
    )


def test_weak_selected_candidate_does_not_displace_deep_repair_focus():
    deep = _quality_report("repair_focus_candidate_id", "deep", deep=True, score=70)
    weak = _quality_report("selected_candidate_id", "weak", deep=False, score=100)
    assert _campaign_report_quality(deep) > _campaign_report_quality(weak)
    equal_depth_weak = _quality_report("selected_candidate_id", "weak", score=60)
    assert _campaign_report_quality(deep) > _campaign_report_quality(equal_depth_weak)
    unmeasured = {
        "status": "rejected",
        "selected_candidate_id": "unmeasured",
        "candidate_metrics": {"score": 100},
    }
    assert _campaign_report_quality(deep) > _campaign_report_quality(unmeasured)


@pytest.mark.parametrize("value", [None, "", "  ", 42, False])
def test_invalid_anchor_does_not_count_as_a_candidate(value):
    report = _quality_report("repair_focus_candidate_id", value)
    absent = {
        key: val for key, val in report.items() if key != "repair_focus_candidate_id"
    }
    assert _campaign_report_quality(report) == _campaign_report_quality(absent)


@pytest.mark.parametrize("priority", ["succeeded", "verified", "required"])
def test_authoritative_quality_precedes_candidate_anchor_depth(priority):
    deep = _quality_report("repair_focus_candidate_id", "deep", deep=True)
    stronger = _quality_report("selected_candidate_id", "stronger", deep=False)
    if priority == "succeeded":
        stronger["status"] = "succeeded"
    elif priority == "verified":
        stronger["post_apply"] = {"release_state": "verified_only"}
    else:
        for report in (deep, stronger):
            report["measurement"] = {
                "mode": "required",
                "validity_status": "valid",
                "effect_direction": "positive",
                "promotion_eligible": report is stronger,
            }
    assert _campaign_report_quality(stronger) > _campaign_report_quality(deep)


def test_shadow_measurement_does_not_promote_report_quality():
    report = _quality_report("selected_candidate_id", "candidate")
    shadow = {
        **report,
        "measurement": {
            "mode": "shadow",
            "validity_status": "valid",
            "effect_direction": "positive",
            "promotion_eligible": True,
            "confidence_lower_bound": 100,
        },
    }
    assert _campaign_report_quality(report) == _campaign_report_quality(shadow)


def test_history_window_cannot_be_consumed_by_one_thirteen_item_report(tmp_path):
    store = FilesystemSelfEvolveStore(tmp_path)
    for run_id, count in (("cycle-1", 13), ("cycle-3", 2), ("cycle-5", 2)):
        _write(store, _report(run_id, count))
    # Supplied order has the old champion last, hence it is loaded first.
    feedback = _load_prior_rejected_feedback(
        store,
        TARGET,
        current_run_id="cycle-6",
        allowed_run_ids=("cycle-5", "cycle-3", "cycle-1"),
    )
    assert [item.variant_id for item in feedback] == [
        "cycle-1-candidate-0",
        "cycle-3-candidate-0",
        "cycle-5-candidate-0",
        "cycle-1-candidate-1",
        "cycle-3-candidate-1",
        "cycle-5-candidate-1",
        *[f"cycle-1-candidate-{i}" for i in range(2, 8)],
    ]
    assert len(feedback) == 12
    assert feedback[1].metrics["baseline_score"] == 1


@pytest.mark.parametrize("limit", [-1, 0, 1, 3, 12])
def test_single_report_order_and_original_limit_are_preserved(tmp_path, limit):
    store = FilesystemSelfEvolveStore(tmp_path)
    _write(store, _report("only", 13))
    result = _load_prior_rejected_feedback(
        store, TARGET, current_run_id="next", limit=limit, allowed_run_ids=("only",)
    )
    assert [item.variant_id for item in result] == [
        f"only-candidate-{i}" for i in range(max(0, limit))
    ]


def test_history_keeps_filtering_shared_empty_malformed_and_wrong_targets(tmp_path):
    store = FilesystemSelfEvolveStore(tmp_path)
    _write(store, _report("valid", 2))
    _write(
        store, _report("wrong-target", 2, target=SelfEvolveTargetRef("skill", "other"))
    )
    _write(store, _report("empty", 0))
    shared = _report("shared", 3)
    shared["self_improvement_disposition"] = {
        "kind": "repair_measurement",
        "scope": "shared_run",
    }
    original = deepcopy(shared)
    _write(store, shared)
    path = _write(store, _report("broken", 1))
    path.write_text("{")
    result = _load_prior_rejected_feedback(
        store,
        TARGET,
        current_run_id="next",
        limit=2,
        allowed_run_ids=("valid", "wrong-target", "empty", "shared", "broken"),
    )
    assert [item.variant_id for item in result] == [
        "valid-candidate-0",
        "valid-candidate-1",
    ]
    assert shared == original


def test_allowed_reports_keep_cross_workspace_target_matching(tmp_path):
    store = FilesystemSelfEvolveStore(tmp_path)
    _write(
        store,
        _report(
            "elsewhere",
            1,
            target=SelfEvolveTargetRef("skill", "demo", "/other/SKILL.md"),
        ),
    )
    assert (
        len(
            _load_prior_rejected_feedback(
                store, TARGET, current_run_id="next", allowed_run_ids=("elsewhere",)
            )
        )
        == 1
    )
    assert _load_prior_rejected_feedback(store, TARGET, current_run_id="next") == ()


def test_explicit_history_scan_bound_counts_missing_report_candidates(
    tmp_path, monkeypatch
):
    store = FilesystemSelfEvolveStore(tmp_path)
    _write(store, _report("valid", 1))
    original = store.run_path
    scanned = []

    def run_path(run_id):
        scanned.append(run_id)
        return original(run_id)

    monkeypatch.setattr(store, "run_path", run_path)
    result = _load_prior_rejected_feedback(
        store,
        TARGET,
        current_run_id="next",
        limit=1,
        allowed_run_ids=("valid", *(f"missing-{i}" for i in range(100))),
    )
    assert result == ()
    assert len(scanned) == 4


def test_default_history_scan_bounds_report_reads(tmp_path, monkeypatch):
    store = FilesystemSelfEvolveStore(tmp_path)
    for index in range(10):
        path = _write(store, _report(f"empty-{index}", 0))
        os.utime(path, (100 + index, 100 + index))
    original = history_module._load_json_mapping
    scanned = []

    def load(path):
        scanned.append(path.parent.name)
        return original(path)

    monkeypatch.setattr(history_module, "_load_json_mapping", load)
    assert (
        _load_prior_rejected_feedback(store, TARGET, current_run_id="next", limit=1)
        == ()
    )
    assert scanned == ["empty-9", "empty-8", "empty-7", "empty-6"]


def test_zero_limit_does_not_scan_history(tmp_path, monkeypatch):
    store = FilesystemSelfEvolveStore(tmp_path)
    _write(store, _report("valid", 1))
    monkeypatch.setattr(
        store, "run_path", lambda *_: pytest.fail("zero limit must not scan")
    )
    assert (
        _load_prior_rejected_feedback(
            store, TARGET, current_run_id="next", limit=0, allowed_run_ids=("valid",)
        )
        == ()
    )
