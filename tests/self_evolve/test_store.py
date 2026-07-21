from __future__ import annotations

import json
import os
import socket

from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.types import (
    CandidateFileDelta,
    CandidateVariant,
    DatasetRecipe,
    EvaluationSummary,
    GateResult,
    OptimizerLineage,
    SelfEvolveRun,
    SelfEvolveRunStatus,
    SelfEvolveTargetRef,
)


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_store_creates_stable_run_directory_and_record(tmp_path) -> None:
    target = SelfEvolveTargetRef(target_type="skill", target_id="demo")
    run = SelfEvolveRun(
        run_id="run-001",
        target=target,
        status=SelfEvolveRunStatus.PENDING,
    )

    store = FilesystemSelfEvolveStore(workspace_root=tmp_path)
    run_dir = store.create_run(run)

    assert run_dir == tmp_path / ".aworld" / "self_evolve" / "run-001"
    assert store.run_path("run-001") == run_dir
    assert _read_json(run_dir / "run.json") == {
        "run_id": "run-001",
        "target": {"target_type": "skill", "target_id": "demo", "path": None},
        "status": "pending",
        "selected_candidate_id": None,
        "metrics": [],
        "gate_results": [],
    }


def test_store_tracks_active_run_lease_until_terminal_status(tmp_path) -> None:
    target = SelfEvolveTargetRef(target_type="skill", target_id="demo")
    store = FilesystemSelfEvolveStore(workspace_root=tmp_path)

    run_dir = store.create_run(
        SelfEvolveRun(
            run_id="run-active",
            target=target,
            status=SelfEvolveRunStatus.RUNNING,
        )
    )

    lease = _read_json(run_dir / ".active.json")
    assert lease["hostname"] == socket.gethostname()
    assert lease["pid"] == os.getpid()
    assert lease["started_at"] > 0

    store.create_run(
        SelfEvolveRun(
            run_id="run-active",
            target=target,
            status=SelfEvolveRunStatus.SUCCEEDED,
        )
    )

    assert not (run_dir / ".active.json").exists()


def test_store_persists_candidate_report_recipe_and_lineage(tmp_path) -> None:
    target = SelfEvolveTargetRef(target_type="skill", target_id="demo")
    run = SelfEvolveRun(run_id="run-002", target=target)
    candidate = CandidateVariant(
        candidate_id="cand-1",
        target=target,
        content="# Demo\n\nUpdated skill text.\n",
        rationale="Clarify failed browser login guidance.",
        parent_candidate_ids=("base",),
        target_fingerprint="sha256:old",
    )
    report = {
        "run_id": run.run_id,
        "best_candidate_id": candidate.candidate_id,
        "summary": "proposal only",
    }
    recipe = DatasetRecipe(
        source={"kind": "jsonl", "path": "eval.jsonl"},
        split_seed="seed-1",
        splits={"train": ["case-1"], "validation": ["case-2"], "held_out": ["case-3"]},
        synthetic_generation_policy="disabled",
        trainable_case_ids=("case-1", "case-2"),
        held_out_case_ids=("case-3",),
    )
    lineage = OptimizerLineage(
        candidate_id="cand-1",
        optimizer_name="llm-mutator",
        optimizer_version="0",
        parent_candidate_ids=("base",),
        trainable_case_ids=("case-1",),
        rationale="seed candidate",
    )

    store = FilesystemSelfEvolveStore(workspace_root=tmp_path)
    store.create_run(run)
    content_path = store.write_candidate(run.run_id, candidate)
    report_path = store.write_report(run.run_id, report)
    recipe_path = store.write_dataset_recipe(run.run_id, recipe)
    lineage_path = store.write_optimizer_lineage(run.run_id, lineage)

    assert content_path == tmp_path / ".aworld" / "self_evolve" / "run-002" / "candidates" / "cand-1.md"
    candidate_artifact = content_path.read_text(encoding="utf-8")
    assert "release_state: candidate" in candidate_artifact
    assert "run_id: run-002" in candidate_artifact
    assert "candidate_id: cand-1" in candidate_artifact
    assert "# Demo\n\nUpdated skill text.\n" in candidate_artifact
    assert _read_json(content_path.with_suffix(".json"))["rationale"] == candidate.rationale
    assert _read_json(report_path) == report
    assert _read_json(recipe_path)["held_out_case_ids"] == ["case-3"]
    assert _read_json(lineage_path)["optimizer_name"] == "llm-mutator"


def test_run_record_serializes_metrics_and_gate_results(tmp_path) -> None:
    target = SelfEvolveTargetRef(target_type="skill", target_id="demo")
    run = SelfEvolveRun(
        run_id="run-003",
        target=target,
        status=SelfEvolveRunStatus.SUCCEEDED,
        selected_candidate_id="cand-1",
        metrics=(
            EvaluationSummary(
                variant_id="baseline",
                metrics={"score": 0.5},
                dataset_split="validation",
            ),
        ),
        gate_results=(
            GateResult(
                gate_name="score_improvement",
                passed=True,
                reason="candidate improved score",
                details={"delta": 0.2},
            ),
        ),
    )

    store = FilesystemSelfEvolveStore(workspace_root=tmp_path)
    run_dir = store.create_run(run)

    saved = _read_json(run_dir / "run.json")
    assert saved["status"] == "succeeded"
    assert saved["metrics"][0]["metrics"] == {"score": 0.5}
    assert saved["gate_results"][0]["passed"] is True


def test_store_recovers_interrupted_apply_from_backup_journal(tmp_path) -> None:
    target_path = tmp_path / "skills" / "demo" / "SKILL.md"
    target_path.parent.mkdir(parents=True)
    original = "---\nname: demo\n---\n# Demo\n\nOriginal.\n"
    candidate_content = "---\nname: demo\n---\n# Demo\n\nCandidate.\n"
    target_path.write_text(original, encoding="utf-8")
    target = SelfEvolveTargetRef(
        target_type="skill",
        target_id="demo",
        path=str(target_path),
    )
    candidate = CandidateVariant(
        candidate_id="cand-1",
        target=target,
        content=candidate_content,
        rationale="candidate",
    )
    store = FilesystemSelfEvolveStore(workspace_root=tmp_path)
    store.create_run(SelfEvolveRun(run_id="run-apply", target=target))
    _backup_path, journal_path = store.write_apply_backup(
        "run-apply",
        candidate=candidate,
        original_content=original,
        target_path=str(target_path),
    )
    store.update_apply_journal(
        journal_path,
        status="applying",
        details={"candidate_written": True},
    )
    target_path.write_text(candidate_content, encoding="utf-8")

    recovery = store.recover_interrupted_apply(journal_path)

    assert recovery["status"] == "recovered_rolled_back"
    assert target_path.read_text(encoding="utf-8") == original
    saved_journal = _read_json(journal_path)
    assert saved_journal["status"] == "recovered_rolled_back"
    assert saved_journal["recovery"]["restored_from_backup"] is True


def test_store_persists_and_recovers_multi_file_skill_candidate(tmp_path) -> None:
    target_path = tmp_path / "skills" / "demo" / "SKILL.md"
    replay_path = target_path.parent / "replay" / "runtime.py"
    replay_path.parent.mkdir(parents=True)
    target_path.write_text("# Original\n", encoding="utf-8")
    replay_path.write_text("old runtime\n", encoding="utf-8")
    target = SelfEvolveTargetRef(
        target_type="skill",
        target_id="demo",
        path=str(target_path),
    )
    candidate = CandidateVariant(
        candidate_id="cand-package",
        target=target,
        content="# Candidate\n",
        rationale="package candidate",
        files=(
            CandidateFileDelta(
                path="replay/runtime.py",
                content="new runtime\n",
            ),
            CandidateFileDelta(
                path="replay/compiler.py",
                content="print('compile')\n",
            ),
        ),
    )
    store = FilesystemSelfEvolveStore(tmp_path)
    store.create_run(SelfEvolveRun(run_id="run-package", target=target))

    store.write_candidate("run-package", candidate)
    package_root = store.run_path("run-package") / "candidates" / "cand-package"
    assert (package_root / "SKILL.md").is_file()
    assert (package_root / "replay/runtime.py").read_text(encoding="utf-8") == (
        "new runtime\n"
    )
    assert (package_root / "replay/compiler.py").is_file()

    _backup_path, journal_path = store.write_apply_backup(
        "run-package",
        candidate=candidate,
        original_content="# Original\n",
        target_path=str(target_path),
    )
    store.update_apply_journal(journal_path, status="applying")
    target_path.write_text("# Candidate\n", encoding="utf-8")
    replay_path.write_text("new runtime\n", encoding="utf-8")
    (replay_path.parent / "compiler.py").write_text(
        "print('compile')\n",
        encoding="utf-8",
    )

    recovery = store.recover_interrupted_apply(journal_path)

    assert recovery["status"] == "recovered_rolled_back"
    assert target_path.read_text(encoding="utf-8") == "# Original\n"
    assert replay_path.read_text(encoding="utf-8") == "old runtime\n"
    assert not (replay_path.parent / "compiler.py").exists()
