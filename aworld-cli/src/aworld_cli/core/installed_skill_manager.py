"""
Installed skill manager for aworld-cli.
"""

from __future__ import annotations

import json
import logging
import subprocess
import shutil
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from aworld.utils.skill_loader import collect_skill_docs

logger = logging.getLogger(__name__)

InstallMode = Literal["clone", "copy", "symlink", "manual"]
SkillScope = str


def default_skill_home() -> Path:
    return Path.home() / ".aworld" / "skills"


def default_installed_skill_root() -> Path:
    return default_skill_home() / "installed"


def default_skill_manifest_path() -> Path:
    return default_skill_home() / ".manifest.json"


@dataclass
class InstalledSkillRecord:
    install_id: str
    name: str
    source: str
    installed_path: str
    resolved_skill_source_path: str
    install_mode: InstallMode
    scope: SkillScope
    installed_at: str


class InstalledSkillManager:
    def __init__(
        self,
        installed_root: Optional[Path] = None,
        manifest_path: Optional[Path] = None,
    ) -> None:
        self.installed_root = (installed_root or default_installed_skill_root()).expanduser()
        self.manifest_path = (manifest_path or default_skill_manifest_path()).expanduser()
        self.installed_root.mkdir(parents=True, exist_ok=True)
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)

    def _derive_install_id(self, source: str | Path) -> str:
        if isinstance(source, Path):
            return source.expanduser().name

        source_value = str(source).rstrip("/")
        if "://" in source_value or source_value.startswith("git@"):
            candidate = source_value.rsplit("/", maxsplit=1)[-1]
            if ":" in candidate and "/" not in candidate:
                candidate = candidate.split(":", maxsplit=1)[-1]
            return candidate.removesuffix(".git") or "installed-skill"

        return Path(source_value).expanduser().name

    def _prepare_install_target(self, install_name: str) -> Path:
        target_path = self._normalize_entry_path(self.installed_root / install_name)
        if not self._is_managed_entry_path(target_path):
            raise ValueError(
                f"Installed skill target must live under the installed root: {target_path}"
            )
        if target_path.parent != self.installed_root.resolve(strict=False):
            raise ValueError(
                f"Installed skill target must be a direct child of the installed root: {target_path}"
            )
        if target_path.exists() or target_path.is_symlink():
            raise ValueError(f"Installed skill target already exists: {target_path}")
        return target_path

    def _cleanup_installed_entry(self, entry_path: Path) -> None:
        if entry_path.is_symlink():
            entry_path.unlink()
        elif entry_path.is_dir():
            shutil.rmtree(entry_path)
        elif entry_path.exists():
            entry_path.unlink()

    def _upsert_manifest_record(self, record: dict[str, str]) -> dict[str, str]:
        records = [
            item for item in self.load_manifest() if item.get("install_id") != record["install_id"]
        ]
        records.append(record)
        self.save_manifest(records)
        return record

    def _find_manifest_record(
        self, install_id_or_name: str
    ) -> tuple[int, dict[str, str], list[dict[str, str]]]:
        records = self.load_manifest()
        for index, record in enumerate(records):
            if (
                record.get("install_id") == install_id_or_name
                or record.get("name") == install_id_or_name
            ):
                return index, record, records
        raise ValueError(f"Unknown installed skill entry: {install_id_or_name}")

    def _count_skills(self, source_path: Path) -> int:
        if not source_path.exists():
            return 0
        return len(collect_skill_docs(source_path))

    def _normalize_entry_path(self, path: Path) -> Path:
        expanded_path = path.expanduser()
        if expanded_path.name in {".", ".."}:
            raise ValueError(
                "Installed skill entries cannot use '.' or '..' as the entry name or resolve to the installed root itself"
            )

        normalized_path = expanded_path.parent.resolve(strict=False) / expanded_path.name
        if normalized_path.is_symlink():
            return normalized_path
        return normalized_path.resolve(strict=False)

    def _sanitize_manifest_record(
        self, record: Mapping[str, object], index: int
    ) -> dict[str, str] | None:
        required_fields = (
            "install_id",
            "name",
            "source",
            "installed_path",
            "resolved_skill_source_path",
            "install_mode",
            "scope",
            "installed_at",
        )
        sanitized: dict[str, str] = {}
        for field in required_fields:
            value = record.get(field)
            if not isinstance(value, str):
                logger.warning(
                    "Skipping invalid installed skill manifest record %s in %s: bad field %s",
                    index,
                    self.manifest_path,
                    field,
                )
                return None
            sanitized[field] = value
        return sanitized

    def _is_managed_entry_path(self, path: Path) -> bool:
        canonical_root = self.installed_root.resolve(strict=False)
        normalized_path = self._normalize_entry_path(path)
        return normalized_path != canonical_root and canonical_root in normalized_path.parents

    def load_manifest(self) -> list[dict[str, str]]:
        if not self.manifest_path.exists():
            return []

        try:
            raw_manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("Failed to parse installed skill manifest: %s", self.manifest_path)
            return []

        if not isinstance(raw_manifest, list) or any(
            not isinstance(item, Mapping) for item in raw_manifest
        ):
            logger.warning(
                "Installed skill manifest has invalid structure: %s", self.manifest_path
            )
            return []

        sanitized_records: list[dict[str, str]] = []
        for index, item in enumerate(raw_manifest):
            sanitized_record = self._sanitize_manifest_record(item, index)
            if sanitized_record is not None:
                sanitized_records.append(sanitized_record)

        return sanitized_records

    def save_manifest(self, records: list[dict[str, str]]) -> None:
        self.manifest_path.write_text(
            json.dumps(records, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def resolve_entry_source(self, entry_path: Path) -> Path:
        entry_path = entry_path.expanduser()
        nested_skills_dir = entry_path / "skills"
        if nested_skills_dir.is_dir() and collect_skill_docs(nested_skills_dir):
            return nested_skills_dir.resolve()
        if entry_path.is_dir() and collect_skill_docs(entry_path):
            return entry_path.resolve()
        raise ValueError(f"No skill directories found under {entry_path}")

    def import_entry(self, entry_path: Path, scope: SkillScope) -> dict[str, str]:
        entry_path = self._normalize_entry_path(entry_path)
        if not self._is_managed_entry_path(entry_path):
            raise ValueError(
                "Manual import path must already live under the installed root and cannot be the installed root itself"
            )

        resolved_source = self.resolve_entry_source(entry_path)
        record = InstalledSkillRecord(
            install_id=entry_path.name,
            name=entry_path.name,
            source=str(entry_path),
            installed_path=str(entry_path),
            resolved_skill_source_path=str(resolved_source),
            install_mode="manual",
            scope=scope,
            installed_at=datetime.now(timezone.utc).isoformat(),
        )
        return self._upsert_manifest_record(asdict(record))

    def install(
        self,
        source: str | Path,
        mode: InstallMode,
        scope: SkillScope,
        install_id: Optional[str] = None,
    ) -> dict[str, str]:
        install_name = install_id or self._derive_install_id(source)
        target_path = self._prepare_install_target(install_name)
        source_value = str(source)
        source_path = Path(source).expanduser() if isinstance(source, Path) else Path(source_value).expanduser()

        try:
            if mode == "copy":
                if not source_path.exists() or not source_path.is_dir():
                    raise ValueError(f"Local skill source must be an existing directory: {source}")
                shutil.copytree(source_path, target_path, symlinks=True)
            elif mode == "symlink":
                if not source_path.exists() or not source_path.is_dir():
                    raise ValueError(f"Local skill source must be an existing directory: {source}")
                target_path.symlink_to(source_path.resolve(strict=False), target_is_directory=True)
            elif mode == "clone":
                subprocess.run(
                    ["git", "clone", source_value, str(target_path)],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
            else:
                raise ValueError(f"Unsupported install mode: {mode}")

            resolved_source = self.resolve_entry_source(target_path)
        except Exception:
            self._cleanup_installed_entry(target_path)
            raise

        record = InstalledSkillRecord(
            install_id=install_name,
            name=install_name,
            source=source_value,
            installed_path=str(target_path),
            resolved_skill_source_path=str(resolved_source),
            install_mode=mode,
            scope=scope,
            installed_at=datetime.now(timezone.utc).isoformat(),
        )
        return self._upsert_manifest_record(asdict(record))

    def list_installs(self) -> list[dict[str, str | int]]:
        records = self.load_manifest()
        managed_ids = {item["install_id"] for item in records}
        managed_paths: set[Path] = set()
        for item in records:
            installed_path_value = item.get("installed_path")
            if not installed_path_value:
                continue
            try:
                managed_paths.add(self._normalize_entry_path(Path(installed_path_value)))
            except ValueError:
                continue

        adopted = False
        for entry in sorted(self.installed_root.iterdir(), key=lambda item: item.name):
            try:
                normalized_entry = self._normalize_entry_path(entry)
            except ValueError:
                continue
            if not self._is_managed_entry_path(normalized_entry):
                continue
            if normalized_entry in managed_paths or entry.name in managed_ids:
                continue
            try:
                adopted_record = self.import_entry(entry, scope="global")
            except ValueError:
                continue
            records.append(adopted_record)
            managed_ids.add(adopted_record["install_id"])
            managed_paths.add(self._normalize_entry_path(Path(adopted_record["installed_path"])))
            adopted = True

        if adopted:
            records = self.load_manifest()

        installs: list[dict[str, str | int]] = []
        for item in records:
            record_with_count: dict[str, str | int] = dict(item)
            record_with_count["skill_count"] = self._count_skills(
                Path(item["resolved_skill_source_path"])
            )
            installs.append(record_with_count)
        return installs

    def update_install(self, install_id_or_name: str) -> dict[str, str]:
        index, target, records = self._find_manifest_record(install_id_or_name)
        if target.get("install_mode") != "clone":
            raise ValueError(
                f"Only git-backed installed skill entries can be updated: {install_id_or_name}"
            )

        installed_path_value = target.get("installed_path")
        if not installed_path_value:
            raise ValueError(f"Unknown installed skill entry: {install_id_or_name}")

        installed_path = self._normalize_entry_path(Path(installed_path_value))
        if not self._is_managed_entry_path(installed_path):
            raise ValueError(
                f"Installed path resolves outside the installed root: {installed_path}"
            )
        if not installed_path.exists():
            raise ValueError(f"Installed path does not exist: {installed_path}")

        subprocess.run(
            ["git", "-C", str(installed_path), "pull", "--ff-only"],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )

        updated_record = dict(target)
        updated_record["resolved_skill_source_path"] = str(
            self.resolve_entry_source(installed_path)
        )
        records[index] = updated_record
        self.save_manifest(records)
        return updated_record

    def remove_install(self, install_id_or_name: str) -> None:
        _, target, records = self._find_manifest_record(install_id_or_name)

        installed_path_value = target.get("installed_path")
        if not installed_path_value:
            raise ValueError(f"Unknown installed skill entry: {install_id_or_name}")

        installed_path = self._normalize_entry_path(Path(installed_path_value))
        if not self._is_managed_entry_path(installed_path):
            raise ValueError(
                f"Installed path resolves outside the installed root: {installed_path}"
            )

        self._cleanup_installed_entry(installed_path)

        self.save_manifest(
            [
                item
                for item in records
                if item.get("install_id") != target.get("install_id")
            ]
        )
