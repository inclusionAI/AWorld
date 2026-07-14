from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from pathlib import Path
from typing import Any, Mapping

from aworld.self_evolve.atomic_fs import atomic_exchange_paths
from aworld.self_evolve.provenance import TargetProvenance
from aworld.self_evolve.candidate_package import (
    candidate_package_fingerprint,
    validate_candidate_files,
)
from aworld.self_evolve.replay_adaptation import ReplayPreflightReport
from aworld.self_evolve.judge import JudgeRecord
from aworld.self_evolve.credit_assignment import TargetSelectionReport
from aworld.self_evolve.types import (
    CandidateVariant,
    DatasetRecipe,
    OptimizerLineage,
    SelfEvolveRun,
    to_json_dict,
)
from aworld.skills.release import mark_skill_content_candidate


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
        content = candidate.content
        if candidate.target.target_type == "skill":
            content = mark_skill_content_candidate(
                candidate.content,
                run_id=run_id,
                candidate_id=candidate.candidate_id,
            )
        content_path.write_text(content, encoding="utf-8")
        self._write_json(content_path.with_suffix(".json"), candidate)
        if candidate.target.target_type == "skill":
            package_dir = candidate_dir / candidate.candidate_id
            if package_dir.is_symlink() or package_dir.is_file():
                package_dir.unlink()
            elif package_dir.exists():
                shutil.rmtree(package_dir)
            package_dir.mkdir()
            (package_dir / "SKILL.md").write_text(content, encoding="utf-8")
            for item in validate_candidate_files(candidate.files):
                if item.operation != "upsert":
                    continue
                destination = package_dir.joinpath(*Path(item.path).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(item.content or "", encoding="utf-8")
                mode = destination.stat().st_mode
                destination.chmod(
                    (mode | 0o111) if item.executable else (mode & ~0o111)
                )
            self._write_json(package_dir / "candidate.json", candidate)
        return content_path

    def write_report(self, run_id: str, report: Mapping[str, Any]) -> Path:
        path = self.run_path(run_id) / "report.json"
        self._write_json(path, report)
        return path

    def write_dataset_recipe(self, run_id: str, recipe: DatasetRecipe) -> Path:
        path = self.run_path(run_id) / "dataset_recipe.json"
        self._write_json(path, recipe)
        return path

    def write_replay_requirements(
        self,
        run_id: str,
        report: ReplayPreflightReport,
    ) -> Path:
        path = self.run_path(run_id) / "replay_requirements.json"
        self._write_json(path, report)
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

    def write_lesson_records(self, run_id: str, lessons: tuple[Any, ...]) -> Path:
        lessons_dir = self.run_path(run_id) / "lessons"
        lessons_dir.mkdir(parents=True, exist_ok=True)
        path = lessons_dir / "lessons.jsonl"
        lines = [
            json.dumps(to_json_dict(lesson), ensure_ascii=False, sort_keys=True)
            for lesson in lessons
        ]
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return path

    def write_harness_diagnostics(self, run_id: str, diagnostics: tuple[Any, ...]) -> Path:
        diagnostics_dir = self.run_path(run_id) / "diagnostics"
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        path = diagnostics_dir / "harness_diagnostics.jsonl"
        lines = [
            json.dumps(to_json_dict(diagnostic), ensure_ascii=False, sort_keys=True)
            for diagnostic in diagnostics
        ]
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
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
        package_backup_path: Path | None = None
        target_root: Path | None = None
        target_root_existed: bool | None = None
        package_backup_fingerprint: str | None = None
        if (
            candidate.target.target_type == "skill"
            and candidate.files
            and target_path is not None
        ):
            target_root = Path(target_path).parent
            target_root_existed = target_root.exists()
            package_backup_path = apply_dir / f"{candidate.candidate_id}.backup.skill"
            if package_backup_path.is_symlink() or package_backup_path.is_file():
                package_backup_path.unlink()
            elif package_backup_path.exists():
                shutil.rmtree(package_backup_path)
            if target_root_existed:
                shutil.copytree(target_root, package_backup_path, symlinks=True)
                package_backup_fingerprint = _directory_fingerprint(
                    package_backup_path
                )
        self._write_json(
            journal_path,
            {
                "candidate_id": candidate.candidate_id,
                "target": candidate.target,
                "target_path": target_path,
                "backup_path": str(backup_path),
                "package_backup_path": (
                    str(package_backup_path)
                    if package_backup_path is not None
                    else None
                ),
                "target_root": str(target_root) if target_root is not None else None,
                "target_root_existed": target_root_existed,
                "package_backup_fingerprint": package_backup_fingerprint,
                "candidate_package_fingerprint": candidate_package_fingerprint(
                    candidate
                ),
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
        package_backup_value = payload.get("package_backup_path")
        if isinstance(package_backup_value, str) and package_backup_value:
            target_root = Path(str(payload.get("target_root") or target_path.parent))
            target_root_existed = payload.get("target_root_existed") is True
            package_backup_path = Path(package_backup_value)
            if target_root_existed and not package_backup_path.is_dir():
                return self._record_recovery_failure(
                    path,
                    payload,
                    reason="skill package backup is missing",
                )
            expected_backup_fingerprint = payload.get(
                "package_backup_fingerprint"
            )
            if (
                target_root_existed
                and isinstance(expected_backup_fingerprint, str)
                and _directory_fingerprint(package_backup_path)
                != expected_backup_fingerprint
            ):
                return self._record_recovery_failure(
                    path,
                    payload,
                    reason="skill package backup fingerprint mismatch",
                )
            if target_root_existed:
                target_root.parent.mkdir(parents=True, exist_ok=True)
                staging = target_root.parent / (
                    f".{target_root.name}.aworld-recovery-{uuid.uuid4().hex}"
                )
                try:
                    shutil.copytree(package_backup_path, staging, symlinks=True)
                    if target_root.exists() and target_root.is_dir() and not target_root.is_symlink():
                        atomic_exchange_paths(target_root, staging)
                        shutil.rmtree(staging)
                    elif target_root.exists() or target_root.is_symlink():
                        return self._record_recovery_failure(
                            path,
                            payload,
                            reason="skill package target is not a regular directory",
                        )
                    else:
                        staging.rename(target_root)
                finally:
                    if staging.exists():
                        shutil.rmtree(staging)
            elif target_root.exists() or target_root.is_symlink():
                trash = target_root.parent / (
                    f".{target_root.name}.aworld-trash-{uuid.uuid4().hex}"
                )
                target_root.rename(trash)
                if trash.is_symlink() or trash.is_file():
                    trash.unlink()
                else:
                    shutil.rmtree(trash)
            recovery = {
                "status": "recovered_rolled_back",
                "restored_from_backup": True,
                "target_path": str(target_path),
                "backup_path": str(package_backup_path),
                "package_restored": True,
            }
            payload["status"] = "recovered_rolled_back"
            payload["recovery"] = recovery
            self._write_json(path, payload)
            return recovery
        if not backup_path.exists() or not target_path.exists():
            return self._record_recovery_failure(
                path,
                payload,
                reason="backup or target path is missing",
            )

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

    def _record_recovery_failure(
        self,
        journal_path: Path,
        payload: dict[str, Any],
        *,
        reason: str,
    ) -> Mapping[str, Any]:
        recovery = {
            "status": "recovery_failed",
            "restored_from_backup": False,
            "reason": reason,
        }
        payload["status"] = "recovery_failed"
        payload["recovery"] = recovery
        self._write_json(journal_path, payload)
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


def _directory_fingerprint(root: Path) -> str:
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            entries.append(
                {"path": relative, "kind": "symlink", "target": path.readlink().as_posix()}
            )
        elif path.is_file():
            content = path.read_bytes()
            entries.append(
                {
                    "path": relative,
                    "kind": "file",
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "size": len(content),
                    "mode": path.stat().st_mode & 0o777,
                }
            )
    encoded = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
