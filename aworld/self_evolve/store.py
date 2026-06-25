from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from aworld.self_evolve.provenance import TargetProvenance
from aworld.self_evolve.judge import JudgeRecord
from aworld.self_evolve.credit_assignment import TargetSelectionReport
from aworld.self_evolve.types import (
    CandidateVariant,
    DatasetRecipe,
    OptimizerLineage,
    SelfEvolveRun,
    to_json_dict,
)


class FilesystemSelfEvolveStore:
    """Filesystem artifact store under `.aworld/self_evolve/<run_id>/`."""

    def __init__(self, workspace_root: str | Path, artifact_root: str | Path | None = None) -> None:
        self.workspace_root = Path(workspace_root)
        self.artifact_root = (
            Path(artifact_root)
            if artifact_root is not None
            else self.workspace_root / ".aworld" / "self_evolve"
        )

    def run_path(self, run_id: str) -> Path:
        self._validate_id(run_id, "run_id")
        return self.artifact_root / run_id

    def create_run(self, run: SelfEvolveRun) -> Path:
        run_dir = self.run_path(run.run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        self._write_json(run_dir / "run.json", run)
        return run_dir

    def write_candidate(self, run_id: str, candidate: CandidateVariant) -> Path:
        self._validate_id(candidate.candidate_id, "candidate_id")
        candidate_dir = self.run_path(run_id) / "candidates"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        content_path = candidate_dir / f"{candidate.candidate_id}.md"
        content_path.write_text(candidate.content, encoding="utf-8")
        self._write_json(content_path.with_suffix(".json"), candidate)
        return content_path

    def write_report(self, run_id: str, report: Mapping[str, Any]) -> Path:
        path = self.run_path(run_id) / "report.json"
        self._write_json(path, report)
        return path

    def write_dataset_recipe(self, run_id: str, recipe: DatasetRecipe) -> Path:
        path = self.run_path(run_id) / "dataset_recipe.json"
        self._write_json(path, recipe)
        return path

    def write_target_provenance(self, run_id: str, provenance: TargetProvenance) -> Path:
        path = self.run_path(run_id) / "target_provenance.json"
        self._write_json(path, provenance)
        return path

    def write_target_selection_report(
        self,
        run_id: str,
        report: TargetSelectionReport,
    ) -> Path:
        path = self.run_path(run_id) / "target_selection.json"
        self._write_json(path, report)
        return path

    def write_optimizer_lineage(self, run_id: str, lineage: OptimizerLineage) -> Path:
        self._validate_id(lineage.candidate_id, "candidate_id")
        lineage_dir = self.run_path(run_id) / "optimizer_lineage"
        lineage_dir.mkdir(parents=True, exist_ok=True)
        path = lineage_dir / f"{lineage.candidate_id}.json"
        self._write_json(path, lineage)
        return path

    def write_judge_record(self, run_id: str, record: JudgeRecord) -> Path:
        self._validate_id(record.backend_id, "backend_id")
        judge_dir = self.run_path(run_id) / "judges"
        judge_dir.mkdir(parents=True, exist_ok=True)
        path = judge_dir / f"{record.backend_id}.json"
        self._write_json(path, record)
        return path

    def write_apply_backup(
        self,
        run_id: str,
        *,
        candidate: CandidateVariant,
        original_content: str,
        target_path: str | None,
    ) -> tuple[Path, Path]:
        self._validate_id(candidate.candidate_id, "candidate_id")
        apply_dir = self.run_path(run_id) / "apply"
        apply_dir.mkdir(parents=True, exist_ok=True)
        backup_path = apply_dir / f"{candidate.candidate_id}.backup.md"
        backup_path.write_text(original_content, encoding="utf-8")
        journal_path = apply_dir / f"{candidate.candidate_id}.journal.json"
        self._write_json(
            journal_path,
            {
                "candidate_id": candidate.candidate_id,
                "target": candidate.target,
                "target_path": target_path,
                "backup_path": str(backup_path),
                "status": "backup_written",
            },
        )
        return backup_path, journal_path

    def update_apply_journal(
        self,
        journal_path: str | Path,
        *,
        status: str,
        details: Mapping[str, Any] | None = None,
    ) -> Path:
        path = Path(journal_path)
        payload = self._read_json(path)
        payload["status"] = status
        if details:
            payload.setdefault("details", {}).update(dict(details))
        self._write_json(path, payload)
        return path

    def recover_interrupted_apply(self, journal_path: str | Path) -> Mapping[str, Any]:
        path = Path(journal_path)
        payload = self._read_json(path)
        status = payload.get("status")
        if status not in {"backup_written", "applying"}:
            return {
                "status": "skipped",
                "reason": "apply journal is not in an interrupted state",
            }
        backup_path = Path(str(payload.get("backup_path") or ""))
        target_path = Path(str(payload.get("target_path") or ""))
        if not backup_path.exists() or not target_path.exists():
            recovery = {
                "status": "recovery_failed",
                "restored_from_backup": False,
                "reason": "backup or target path is missing",
            }
            payload["status"] = "recovery_failed"
            payload["recovery"] = recovery
            self._write_json(path, payload)
            return recovery

        target_path.write_text(backup_path.read_text(encoding="utf-8"), encoding="utf-8")
        recovery = {
            "status": "recovered_rolled_back",
            "restored_from_backup": True,
            "target_path": str(target_path),
            "backup_path": str(backup_path),
        }
        payload["status"] = "recovered_rolled_back"
        payload["recovery"] = recovery
        self._write_json(path, payload)
        return recovery

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(to_json_dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _read_json(self, path: Path) -> dict[str, Any]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"expected JSON object in {path}")
        return payload

    def _validate_id(self, value: str, field_name: str) -> None:
        if not value or "/" in value or "\\" in value or value in {".", ".."}:
            raise ValueError(f"invalid {field_name}: {value!r}")
