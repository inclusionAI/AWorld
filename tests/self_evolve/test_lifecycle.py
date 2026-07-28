from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import pytest

from aworld.self_evolve.lifecycle import (
    SelfEvolveArtifactRetentionPolicy,
    cleanup_self_evolve_artifacts,
)


def test_default_retention_bounds_large_replay_workspace_history() -> None:
    policy = SelfEvolveArtifactRetentionPolicy()

    assert policy.keep_latest_runs == 2
    assert policy.raw_artifact_retention_days == 0
    assert policy.stale_run_retention_hours == 24
    assert policy.unreferenced_ingestion_retention_days == 7
    assert policy.prune_unselected_candidate_materializations is True


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
        _write_text(run_dir / "candidates" / "cand-1" / "SKILL.md", "# Candidate\n")
        _write_text(run_dir / "lessons" / "lessons.jsonl", "{}\n")
        _write_json(run_dir / "optimizer_lineage" / "cand-1.json", {"candidate_id": "cand-1"})
        _write_text(run_dir / "manifest" / "evidence_manifest.jsonl", "{}\n")
        _write_json(run_dir / "apply" / "cand-1.journal.json", {"status": "applied"})
        _write_json(run_dir / "replay" / "cand-1" / "result.json", {"status": "succeeded"})
        _write_json(
            run_dir / "replay" / "cand-1" / "execution_request.json",
            {"run_id": run_dir.name},
        )
        _write_text(run_dir / "replay" / "cand-1" / "workspace" / "source.py")
        _write_json(
            run_dir / "replay_adaptation" / "dataset" / "capability" / "bundle.json",
            {"status": "compiled"},
        )
        _write_json(
            run_dir
            / "replay_adaptation"
            / "dataset"
            / "capability"
            / "workspace_seed"
            / "seed.json",
            {"status": "compiled"},
        )
        _write_text(
            run_dir
            / "repair_conformance"
            / "cand-1"
            / "replay_services"
            / "service-1"
            / "protocol_trace.log"
        )
        _write_json(run_dir / "evidence" / "bundle.json", {"entries": []})
        _write_text(run_dir / "overlays" / "cand-1" / "skills" / "demo" / "SKILL.md")
        _write_text(run_dir / "stdout.txt", "duplicate stdout\n")
        _write_text(run_dir / "stderr.log", "duplicate stderr\n")
        _write_text(run_dir / "workspace_copy" / "tmp.txt")

    for run_dir in (old_run, recent_run):
        evaluator_dir = (
            artifact_root
            / "evaluator"
            / run_dir.name
            / "baseline"
            / "validation"
        )
        evaluator_report = evaluator_dir / "report.json"
        _write_json(evaluator_report, {"status": "passed"})
        _write_text(evaluator_dir / "trajectory.log", "raw trajectory\n")
        _write_text(evaluator_dir / "logs" / "judge.log", "raw log\n")
        _write_json(evaluator_dir / "extracted" / "case.json", {"raw": True})
        report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
        report["evaluator_report_paths"] = [str(evaluator_report)]
        _write_json(run_dir / "report.json", report)
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

    assert cleanup["removed_run_count"] == 2
    assert (old_run / "replay" / "cand-1" / "result.json").exists()
    assert not (old_run / "replay" / "cand-1" / "workspace").exists()
    assert (
        old_run / "replay_adaptation" / "dataset" / "capability" / "bundle.json"
    ).exists()
    assert not (
        old_run
        / "replay_adaptation"
        / "dataset"
        / "capability"
        / "workspace_seed"
    ).exists()
    assert not (old_run / "repair_conformance").exists()
    assert not (old_run / "evidence").exists()
    assert not (old_run / "overlays").exists()
    assert not (old_run / "stdout.txt").exists()
    assert not (old_run / "stderr.log").exists()
    assert not (old_run / "workspace_copy").exists()
    old_evaluator = (
        artifact_root / "evaluator" / "run-old" / "baseline" / "validation"
    )
    assert (old_evaluator / "report.json").exists()
    assert not (old_evaluator / "trajectory.log").exists()
    assert not (old_evaluator / "logs").exists()
    assert not (old_evaluator / "extracted").exists()

    assert (old_run / "report.json").exists()
    assert (old_run / "run.json").exists()
    assert not (old_run / "candidates" / "cand-1.md").exists()
    assert not (old_run / "candidates" / "cand-1").exists()
    assert (old_run / "candidates" / "cand-1.json").exists()
    assert (old_run / "lessons" / "lessons.jsonl").exists()
    assert (old_run / "optimizer_lineage" / "cand-1.json").exists()
    assert (old_run / "manifest" / "evidence_manifest.jsonl").exists()
    assert (old_run / "apply" / "cand-1.journal.json").exists()

    assert (recent_run / "replay" / "cand-1" / "result.json").exists()
    assert not (recent_run / "replay" / "cand-1" / "workspace").exists()
    assert (
        recent_run / "replay_adaptation" / "dataset" / "capability" / "bundle.json"
    ).exists()
    assert not (
        recent_run
        / "replay_adaptation"
        / "dataset"
        / "capability"
        / "workspace_seed"
    ).exists()
    assert not (recent_run / "repair_conformance").exists()
    assert not (recent_run / "candidates" / "cand-1.md").exists()
    assert not (recent_run / "candidates" / "cand-1").exists()
    assert not (recent_run / "overlays").exists()
    recent_evaluator = (
        artifact_root / "evaluator" / "run-recent" / "baseline" / "validation"
    )
    assert (recent_evaluator / "report.json").exists()
    assert not (recent_evaluator / "trajectory.log").exists()
    assert not (recent_evaluator / "logs").exists()
    assert not (recent_evaluator / "extracted").exists()
    assert (recent_run / "report.json").exists()
    assert (recent_run / "run.json").exists()
    assert recent_run.name in cleanup["protected_run_ids"]


def test_cleanup_skips_running_and_interrupted_apply_but_prunes_referenced_terminal_runs(
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
        _write_json(run_dir / "replay" / "cand-1" / "execution_request.json", {})
        _write_text(run_dir / "replay" / "cand-1" / "workspace" / "source.py")
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

    assert cleanup["removed_run_count"] == 1
    assert (artifact_root / "run-running" / "replay").exists()
    assert (artifact_root / "run-apply" / "replay").exists()
    assert (artifact_root / "run-source" / "replay" / "cand-1" / "result.json").exists()
    assert not (
        artifact_root / "run-source" / "replay" / "cand-1" / "workspace"
    ).exists()
    skipped = {item["run_id"]: item["reason"] for item in cleanup["skipped_runs"]}
    assert skipped["run-running"] == "run_not_terminal"
    assert skipped["run-apply"] == "apply_interrupted"
    assert "run-source" not in skipped
    assert "run-source" in cleanup["protected_run_ids"]


def test_cleanup_preserves_active_campaign_run_records_but_prunes_raw_artifacts(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / ".aworld" / "self_evolve"
    run_dir = artifact_root / "campaign-generic-cycle-001"
    _write_json(
        run_dir / "run.json",
        {"run_id": run_dir.name, "status": "rejected"},
    )
    _write_json(
        run_dir / "report.json",
        {"run_id": run_dir.name, "status": "rejected"},
    )
    _write_json(run_dir / "replay" / "candidate" / "result.json", {})
    _write_json(run_dir / "replay" / "candidate" / "execution_request.json", {})
    _write_text(run_dir / "replay" / "candidate" / "workspace" / "source.py")
    _write_json(
        artifact_root / "campaigns" / "campaign-generic" / "campaign.json",
        {
            "schema_version": "aworld.self_evolve.campaign.v1",
            "status": "active",
            "run_ids": [run_dir.name],
        },
    )
    _touch_tree(run_dir, 1_000.0)

    cleanup = cleanup_self_evolve_artifacts(
        tmp_path,
        policy=SelfEvolveArtifactRetentionPolicy(keep_latest_runs=0),
        now=10_000.0,
    )

    assert (run_dir / "replay" / "candidate" / "result.json").exists()
    assert not (run_dir / "replay" / "candidate" / "workspace").exists()
    assert (run_dir / "run.json").exists()
    assert (run_dir / "report.json").exists()
    assert run_dir.name in cleanup["protected_run_ids"]
    assert cleanup["skipped_runs"] == []


def test_cleanup_reclaims_only_unreferenced_expired_ingestions(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / ".aworld" / "self_evolve"
    ingestion_root = artifact_root / "ingestions"
    run_ingestion = ingestion_root / "ingestion-run"
    campaign_ingestion = ingestion_root / "ingestion-campaign"
    stale_ingestion = ingestion_root / "ingestion-stale"
    for path in (run_ingestion, campaign_ingestion, stale_ingestion):
        _write_json(
            path / "ingestion.json",
            {"ingestion_id": path.name},
        )
        _touch_tree(path, 1_000.0)

    run_dir = artifact_root / "run-retained"
    _write_json(run_dir / "run.json", {"run_id": run_dir.name, "status": "rejected"})
    _write_json(
        run_dir / "ingestion_ref.json",
        {"ingestion_id": run_ingestion.name},
    )
    _write_json(
        artifact_root / "campaigns" / "campaign-generic" / "campaign.json",
        {
            "schema_version": "aworld.self_evolve.campaign.v1",
            "status": "paused",
            "run_ids": [],
            "source_snapshot": {
                "ingestion_id": campaign_ingestion.name,
            },
        },
    )

    cleanup = cleanup_self_evolve_artifacts(
        tmp_path,
        policy=SelfEvolveArtifactRetentionPolicy(
            keep_latest_runs=0,
            unreferenced_ingestion_retention_days=0,
        ),
        now=10_000.0,
    )

    assert run_ingestion.is_dir()
    assert campaign_ingestion.is_dir()
    assert not stale_ingestion.exists()
    assert cleanup["removed_ingestion_ids"] == ["ingestion-stale"]
    assert cleanup["protected_ingestion_ids"] == [
        "ingestion-campaign",
        "ingestion-run",
    ]


def test_cleanup_prunes_only_unselected_candidate_materializations(tmp_path: Path) -> None:
    artifact_root = tmp_path / ".aworld" / "self_evolve"
    run_dir = artifact_root / "run-old"
    _write_json(
        run_dir / "run.json",
        {
            "run_id": "run-old",
            "status": "succeeded",
            "selected_candidate_id": "cand-selected",
        },
    )
    _write_json(
        run_dir / "report.json",
        {
            "run_id": "run-old",
            "status": "succeeded",
            "selected_candidate_id": "cand-selected",
        },
    )
    _write_json(
        run_dir / "apply" / "cand-applied.journal.json",
        {"candidate_id": "cand-applied", "status": "applied"},
    )
    _write_json(
        run_dir / "optimizer_lineage" / "cand-child.json",
        {
            "candidate_id": "cand-child",
            "parent_candidate_ids": ["cand-parent"],
        },
    )
    for candidate_id in (
        "cand-selected",
        "cand-applied",
        "cand-parent",
        "cand-discarded",
    ):
        _write_json(
            run_dir / "candidates" / f"{candidate_id}.json",
            {"candidate_id": candidate_id},
        )
        _write_text(run_dir / "candidates" / f"{candidate_id}.md")
        _write_text(run_dir / "candidates" / f"{candidate_id}.diff")
        _write_text(run_dir / "candidates" / candidate_id / "SKILL.md")
    _touch_tree(run_dir, 1_000.0)

    cleanup = cleanup_self_evolve_artifacts(
        tmp_path,
        policy=SelfEvolveArtifactRetentionPolicy(keep_latest_runs=1),
        now=10_000.0,
    )

    for candidate_id in ("cand-selected", "cand-applied", "cand-parent"):
        assert (run_dir / "candidates" / f"{candidate_id}.md").exists()
        assert (run_dir / "candidates" / f"{candidate_id}.diff").exists()
        assert (run_dir / "candidates" / candidate_id).exists()
    assert not (run_dir / "candidates" / "cand-discarded.md").exists()
    assert not (run_dir / "candidates" / "cand-discarded.diff").exists()
    assert not (run_dir / "candidates" / "cand-discarded").exists()
    assert (run_dir / "candidates" / "cand-discarded.json").exists()
    assert any("cand-discarded" in path for path in cleanup["removed_paths"])


def test_raw_retention_and_candidate_pruning_compose_for_recent_terminal_run(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / ".aworld" / "self_evolve"
    run_dir = artifact_root / "run-recent"
    _write_json(
        run_dir / "run.json",
        {
            "run_id": run_dir.name,
            "status": "rejected",
            "selected_candidate_id": "cand-selected",
        },
    )
    _write_json(
        run_dir / "report.json",
        {
            "run_id": run_dir.name,
            "status": "rejected",
            "selected_candidate_id": "cand-selected",
        },
    )
    _write_json(run_dir / "replay" / "candidate" / "result.json", {})
    _write_json(run_dir / "replay" / "candidate" / "execution_request.json", {})
    _write_text(run_dir / "replay" / "candidate" / "workspace" / "source.py")
    for candidate_id in ("cand-selected", "cand-discarded"):
        _write_json(
            run_dir / "candidates" / f"{candidate_id}.json",
            {"candidate_id": candidate_id},
        )
        _write_text(run_dir / "candidates" / f"{candidate_id}.md")
    _touch_tree(run_dir, 9_000.0)

    cleanup = cleanup_self_evolve_artifacts(
        tmp_path,
        policy=SelfEvolveArtifactRetentionPolicy(
            keep_latest_runs=1,
            raw_artifact_retention_days=1,
        ),
        now=10_000.0,
    )

    assert (run_dir / "replay" / "candidate" / "workspace").exists()
    assert (run_dir / "candidates" / "cand-selected.md").exists()
    assert not (run_dir / "candidates" / "cand-discarded.md").exists()
    assert run_dir.name in cleanup["protected_run_ids"]


def test_cleanup_archives_only_stale_nonterminal_runs_with_proven_dead_lease(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / ".aworld" / "self_evolve"
    no_lease_run = artifact_root / "run-no-lease"
    live_run = artifact_root / "run-live"
    dead_run = artifact_root / "run-dead"
    dead_grace_run = artifact_root / "run-dead-grace"
    foreign_run = artifact_root / "run-foreign"
    malformed_run = artifact_root / "run-malformed"
    for run_dir in (
        no_lease_run,
        live_run,
        dead_run,
        dead_grace_run,
        foreign_run,
        malformed_run,
    ):
        _write_json(
            run_dir / "run.json",
            {"run_id": run_dir.name, "status": "running"},
        )
        _write_json(run_dir / "replay" / "cand-1" / "execution_request.json", {})
        _write_text(run_dir / "replay" / "cand-1" / "workspace" / "source.py")
    _write_json(
        live_run / ".active.json",
        {"hostname": socket.gethostname(), "pid": os.getpid()},
    )
    _write_json(
        dead_run / ".active.json",
        {
            "hostname": socket.gethostname(),
            "pid": 2_147_483_647,
            "started_at": 1.0,
        },
    )
    _write_json(
        dead_grace_run / ".active.json",
        {
            "hostname": socket.gethostname(),
            "pid": 2_147_483_647,
            "started_at": 1.0,
        },
    )
    _write_json(
        foreign_run / ".active.json",
        {"hostname": "another-host", "pid": 2_147_483_647, "started_at": 1.0},
    )
    _write_json(
        malformed_run / ".active.json",
        {"hostname": socket.gethostname(), "pid": "not-a-pid", "started_at": 1.0},
    )
    _touch_tree(no_lease_run, 1_000.0)
    _touch_tree(live_run, 1_000.0)
    _touch_tree(dead_run, 1_000.0)
    _touch_tree(dead_grace_run, 9_000.0)
    _touch_tree(foreign_run, 1_000.0)
    _touch_tree(malformed_run, 1_000.0)

    cleanup = cleanup_self_evolve_artifacts(
        tmp_path,
        policy=SelfEvolveArtifactRetentionPolicy(
            keep_latest_runs=0,
            stale_run_retention_hours=1,
        ),
        now=10_000.0,
    )

    assert (no_lease_run / "replay" / "cand-1" / "workspace").exists()
    assert (live_run / "replay" / "cand-1" / "workspace").exists()
    assert not (dead_run / "replay" / "cand-1" / "workspace").exists()
    assert not (dead_run / ".active.json").exists()
    archive = json.loads(
        (dead_run / "artifact_retention_archive.json").read_text(encoding="utf-8")
    )
    assert archive["reason"] == "stale_dead_lease"
    assert archive["prior_status"] == "running"
    assert (dead_grace_run / "replay" / "cand-1" / "workspace").exists()
    assert (foreign_run / "replay" / "cand-1" / "workspace").exists()
    assert (malformed_run / "replay" / "cand-1" / "workspace").exists()
    assert cleanup["archived_run_ids"] == ["run-dead"]
    skipped = {item["run_id"]: item["reason"] for item in cleanup["skipped_runs"]}
    assert skipped["run-no-lease"] == "run_not_terminal"
    assert skipped["run-live"] == "run_active"
    assert skipped["run-dead-grace"] == "run_not_terminal"
    assert skipped["run-foreign"] == "run_active"
    assert skipped["run-malformed"] == "run_active"


def test_cleanup_ignores_retention_telemetry_as_lineage(tmp_path: Path) -> None:
    artifact_root = tmp_path / ".aworld" / "self_evolve"
    source_run = artifact_root / "run-source"
    owner_run = artifact_root / "run-owner"
    for run_dir in (source_run, owner_run):
        _write_json(
            run_dir / "run.json",
            {"run_id": run_dir.name, "status": "rejected"},
        )
        _write_json(run_dir / "replay" / "candidate" / "result.json", {})
        _write_json(run_dir / "replay" / "candidate" / "execution_request.json", {})
        _write_text(run_dir / "replay" / "candidate" / "workspace" / "source.py")
    _write_json(
        source_run / "report.json",
        {
            "run_id": source_run.name,
            "status": "rejected",
            "artifact_retention": {
                "protected_run_ids": [source_run.name],
                "removed_run_ids": [],
            },
        },
    )
    _write_json(
        owner_run / "report.json",
        {
            "run_id": owner_run.name,
            "status": "rejected",
            "artifact_retention": {
                "protected_run_ids": [source_run.name, owner_run.name],
            },
        },
    )
    _touch_tree(source_run, 1_000.0)
    _touch_tree(owner_run, 2_000.0)

    cleanup = cleanup_self_evolve_artifacts(
        tmp_path,
        policy=SelfEvolveArtifactRetentionPolicy(keep_latest_runs=0),
        now=10_000.0,
    )

    assert source_run.name not in cleanup["protected_run_ids"]
    assert (source_run / "replay" / "candidate" / "result.json").exists()
    assert not (source_run / "replay" / "candidate" / "workspace").exists()


def test_cleanup_retains_exact_prior_parent_and_campaign_lineage(tmp_path: Path) -> None:
    artifact_root = tmp_path / ".aworld" / "self_evolve"
    referenced_ids = {
        "run-source",
        "run-prior",
        "run-parent",
        "run-campaign",
    }
    for run_id in (*sorted(referenced_ids), "run-owner"):
        run_dir = artifact_root / run_id
        _write_json(
            run_dir / "run.json",
            {"run_id": run_id, "status": "rejected"},
        )
        _write_json(run_dir / "report.json", {"run_id": run_id, "status": "rejected"})
    _write_json(
        artifact_root / "run-owner" / "optimizer_lineage" / "candidate.json",
        {
            "source_run_id": "run-source",
            "prior_run_ids": ["run-prior"],
            "parent_run_id": "run-parent",
            "campaign_prior_run_ids": ["run-campaign"],
            "protected_run_ids": ["run-owner"],
        },
    )

    cleanup = cleanup_self_evolve_artifacts(
        tmp_path,
        policy=SelfEvolveArtifactRetentionPolicy(keep_latest_runs=0),
        now=10_000.0,
    )

    assert referenced_ids <= set(cleanup["protected_run_ids"])
    assert "run-owner" not in cleanup["protected_run_ids"]


def test_cleanup_terminal_run_requires_released_active_lease(tmp_path: Path) -> None:
    artifact_root = tmp_path / ".aworld" / "self_evolve"
    run_dir = artifact_root / "run-terminal"
    _write_json(
        run_dir / "run.json",
        {"run_id": run_dir.name, "status": "rejected"},
    )
    _write_json(
        run_dir / "report.json",
        {"run_id": run_dir.name, "status": "rejected"},
    )
    _write_json(run_dir / "replay" / "candidate" / "result.json", {})
    _write_json(run_dir / "replay" / "candidate" / "execution_request.json", {})
    _write_text(run_dir / "replay" / "candidate" / "workspace" / "source.py")
    _write_json(
        run_dir / ".active.json",
        {"hostname": socket.gethostname(), "pid": os.getpid()},
    )
    _touch_tree(run_dir, 1_000.0)

    active_cleanup = cleanup_self_evolve_artifacts(
        tmp_path,
        policy=SelfEvolveArtifactRetentionPolicy(keep_latest_runs=1),
        now=10_000.0,
    )

    assert (run_dir / "replay" / "candidate" / "workspace").exists()
    assert active_cleanup["skipped_runs"] == [
        {"run_id": run_dir.name, "reason": "run_active"}
    ]

    (run_dir / ".active.json").unlink()
    terminal_cleanup = cleanup_self_evolve_artifacts(
        tmp_path,
        policy=SelfEvolveArtifactRetentionPolicy(keep_latest_runs=1),
        now=10_000.0,
    )

    assert (run_dir / "replay" / "candidate" / "result.json").exists()
    assert not (run_dir / "replay" / "candidate" / "workspace").exists()
    assert terminal_cleanup["skipped_runs"] == []


def test_cleanup_ignores_symlinked_run_directories(tmp_path: Path) -> None:
    artifact_root = tmp_path / ".aworld" / "self_evolve"
    outside = tmp_path / "outside-run"
    _write_json(
        outside / "run.json",
        {"run_id": "run-alias", "status": "rejected"},
    )
    _write_json(outside / "replay" / "candidate" / "result.json", {})
    artifact_root.mkdir(parents=True)
    (artifact_root / "run-alias").symlink_to(outside, target_is_directory=True)

    cleanup = cleanup_self_evolve_artifacts(
        tmp_path,
        policy=SelfEvolveArtifactRetentionPolicy(keep_latest_runs=0),
        now=10_000.0,
    )

    assert cleanup["removed_run_count"] == 0
    assert (outside / "replay" / "candidate" / "result.json").exists()


def test_cleanup_unlinks_symlinked_raw_path_without_touching_target(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / ".aworld" / "self_evolve"
    run_dir = artifact_root / "run-terminal"
    outside = tmp_path / "outside-replay"
    _write_json(
        run_dir / "run.json",
        {"run_id": run_dir.name, "status": "rejected"},
    )
    _write_json(
        run_dir / "report.json",
        {"run_id": run_dir.name, "status": "rejected"},
    )
    _write_json(outside / "source.py", {})
    _write_json(
        run_dir / "replay" / "candidate" / "execution_request.json",
        {},
    )
    (run_dir / "replay" / "candidate" / "workspace").symlink_to(
        outside,
        target_is_directory=True,
    )
    _touch_tree(outside, 1_000.0)

    cleanup_self_evolve_artifacts(
        tmp_path,
        policy=SelfEvolveArtifactRetentionPolicy(keep_latest_runs=0),
        now=10_000.0,
    )

    assert not (run_dir / "replay" / "candidate" / "workspace").exists()
    assert (outside / "source.py").exists()


def test_cleanup_rejects_symlinked_artifact_ancestor_without_touching_target(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    run_dir = outside / "self_evolve" / "run-terminal"
    _write_json(
        run_dir / "run.json",
        {"run_id": run_dir.name, "status": "rejected"},
    )
    _write_json(run_dir / "replay" / "candidate" / "execution_request.json", {})
    _write_text(run_dir / "replay" / "candidate" / "workspace" / "source.py")
    (tmp_path / ".aworld").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        cleanup_self_evolve_artifacts(tmp_path)

    assert (run_dir / "replay" / "candidate" / "workspace" / "source.py").exists()


def test_cleanup_rejects_explicit_artifact_root_outside_workspace_boundary(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    artifact_root = tmp_path / "external-artifacts"
    run_dir = artifact_root / "run-terminal"
    _write_json(
        run_dir / "run.json",
        {"run_id": run_dir.name, "status": "rejected"},
    )
    _write_json(run_dir / "replay" / "candidate" / "execution_request.json", {})
    _write_text(run_dir / "replay" / "candidate" / "workspace" / "source.py")

    with pytest.raises(ValueError, match="workspace"):
        cleanup_self_evolve_artifacts(
            workspace,
            artifact_root=artifact_root,
        )

    assert (run_dir / "replay" / "candidate" / "workspace" / "source.py").exists()


def test_cleanup_recovers_atomically_quarantined_artifacts(tmp_path: Path) -> None:
    artifact_root = tmp_path / ".aworld" / "self_evolve"
    operation = artifact_root / ".artifact-retention-trash" / "orphan"
    _write_json(
        operation / "owner.json",
        {
            "schema_version": "aworld.self_evolve.cleanup_quarantine.v1",
            "hostname": socket.gethostname(),
            "pid": 2_147_483_647,
            "started_at": 1.0,
        },
    )
    _write_text(operation / "artifact" / "workspace" / "source.py")

    cleanup = cleanup_self_evolve_artifacts(
        tmp_path,
        policy=SelfEvolveArtifactRetentionPolicy(stale_run_retention_hours=1),
        now=10_000.0,
    )

    assert not operation.parent.exists()
    assert str(operation) in cleanup["removed_paths"]


def test_cleanup_does_not_recover_live_quarantine_operation(tmp_path: Path) -> None:
    artifact_root = tmp_path / ".aworld" / "self_evolve"
    operation = artifact_root / ".artifact-retention-trash" / "in-progress"
    _write_json(
        operation / "owner.json",
        {
            "schema_version": "aworld.self_evolve.cleanup_quarantine.v1",
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
            "started_at": 1.0,
        },
    )
    _write_text(operation / "artifact" / "workspace" / "source.py")

    cleanup = cleanup_self_evolve_artifacts(
        tmp_path,
        policy=SelfEvolveArtifactRetentionPolicy(stale_run_retention_hours=1),
        now=10_000.0,
    )

    assert operation.exists()
    assert str(operation) not in cleanup["removed_paths"]
