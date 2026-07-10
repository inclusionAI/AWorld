from __future__ import annotations

import json
import os
from pathlib import Path

from aworld.self_evolve.lifecycle import (
    SelfEvolveArtifactRetentionPolicy,
    cleanup_self_evolve_artifacts,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, content: str = "artifact\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _touch_tree(path: Path, timestamp: float) -> None:
    for child in sorted(path.rglob("*"), reverse=True):
        os.utime(child, (timestamp, timestamp))
    os.utime(path, (timestamp, timestamp))


def test_cleanup_removes_only_expired_raw_artifacts_and_preserves_durable_run_files(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / ".aworld" / "self_evolve"
    old_run = artifact_root / "run-old"
    recent_run = artifact_root / "run-recent"
    for run_dir, status in ((old_run, "succeeded"), (recent_run, "rejected")):
        _write_json(run_dir / "run.json", {"run_id": run_dir.name, "status": status})
        _write_json(run_dir / "report.json", {"run_id": run_dir.name, "status": status})
        _write_text(run_dir / "candidates" / "cand-1.md", "# Candidate\n")
        _write_json(run_dir / "candidates" / "cand-1.json", {"candidate_id": "cand-1"})
        _write_text(run_dir / "lessons" / "lessons.jsonl", "{}\n")
        _write_json(run_dir / "optimizer_lineage" / "cand-1.json", {"candidate_id": "cand-1"})
        _write_text(run_dir / "manifest" / "evidence_manifest.jsonl", "{}\n")
        _write_json(run_dir / "apply" / "cand-1.journal.json", {"status": "applied"})
        _write_json(run_dir / "replay" / "cand-1" / "result.json", {"status": "succeeded"})
        _write_json(run_dir / "evidence" / "bundle.json", {"entries": []})
        _write_text(run_dir / "overlays" / "cand-1" / "skills" / "demo" / "SKILL.md")
        _write_text(run_dir / "stdout.txt", "duplicate stdout\n")
        _write_text(run_dir / "stderr.log", "duplicate stderr\n")
        _write_text(run_dir / "workspace_copy" / "tmp.txt")

    _write_json(artifact_root / "evaluator" / "run-old" / "baseline" / "report.json", {})
    _write_json(artifact_root / "evaluator" / "run-recent" / "baseline" / "report.json", {})
    _touch_tree(old_run, 1_000.0)
    _touch_tree(recent_run, 2_000.0)
    _touch_tree(artifact_root / "evaluator" / "run-old", 1_000.0)
    _touch_tree(artifact_root / "evaluator" / "run-recent", 2_000.0)

    cleanup = cleanup_self_evolve_artifacts(
        tmp_path,
        policy=SelfEvolveArtifactRetentionPolicy(
            keep_latest_runs=1,
            raw_artifact_retention_days=0,
        ),
        now=10_000.0,
    )

    assert cleanup["removed_run_count"] == 1
    assert not (old_run / "replay").exists()
    assert not (old_run / "evidence").exists()
    assert not (old_run / "overlays").exists()
    assert not (old_run / "stdout.txt").exists()
    assert not (old_run / "stderr.log").exists()
    assert not (old_run / "workspace_copy").exists()
    assert not (artifact_root / "evaluator" / "run-old").exists()

    assert (old_run / "report.json").exists()
    assert (old_run / "run.json").exists()
    assert (old_run / "candidates" / "cand-1.md").exists()
    assert (old_run / "lessons" / "lessons.jsonl").exists()
    assert (old_run / "optimizer_lineage" / "cand-1.json").exists()
    assert (old_run / "manifest" / "evidence_manifest.jsonl").exists()
    assert (old_run / "apply" / "cand-1.journal.json").exists()

    assert (recent_run / "replay").exists()
    assert (recent_run / "overlays").exists()
    assert (artifact_root / "evaluator" / "run-recent").exists()


def test_cleanup_skips_running_interrupted_apply_and_lineage_referenced_runs(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / ".aworld" / "self_evolve"
    protected_runs = {
        "run-running": ({"run_id": "run-running", "status": "running"}, None),
        "run-apply": ({"run_id": "run-apply", "status": "rejected"}, "applying"),
        "run-source": ({"run_id": "run-source", "status": "succeeded"}, None),
    }
    for run_id, (run_record, apply_status) in protected_runs.items():
        run_dir = artifact_root / run_id
        _write_json(run_dir / "run.json", run_record)
        _write_json(run_dir / "report.json", {"run_id": run_id, "status": run_record["status"]})
        _write_json(run_dir / "replay" / "cand-1" / "result.json", {"status": "succeeded"})
        if apply_status is not None:
            _write_json(run_dir / "apply" / "cand-1.journal.json", {"status": apply_status})
        _touch_tree(run_dir, 1_000.0)

    referencing_run = artifact_root / "run-rerun"
    _write_json(referencing_run / "run.json", {"run_id": "run-rerun", "status": "succeeded"})
    _write_json(
        referencing_run / "report.json",
        {
            "run_id": "run-rerun",
            "status": "succeeded",
            "optimizer_diagnostics": {
                "source": "stored_self_evolve_run",
                "source_run_id": "run-source",
            },
        },
    )
    _touch_tree(referencing_run, 2_000.0)

    cleanup = cleanup_self_evolve_artifacts(
        tmp_path,
        policy=SelfEvolveArtifactRetentionPolicy(
            keep_latest_runs=0,
            raw_artifact_retention_days=0,
        ),
        now=10_000.0,
    )

    assert cleanup["removed_run_count"] == 0
    assert (artifact_root / "run-running" / "replay").exists()
    assert (artifact_root / "run-apply" / "replay").exists()
    assert (artifact_root / "run-source" / "replay").exists()
    skipped = {item["run_id"]: item["reason"] for item in cleanup["skipped_runs"]}
    assert skipped["run-running"] == "run_not_terminal"
    assert skipped["run-apply"] == "apply_interrupted"
    assert skipped["run-source"] == "referenced_by_lineage"
