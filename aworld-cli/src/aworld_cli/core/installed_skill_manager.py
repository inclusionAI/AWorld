"""
Installed skill manager for aworld-cli.
"""

from __future__ import annotations

import json
import logging
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
        records = [
            item for item in self.load_manifest() if item.get("install_id") != record.install_id
        ]
        records.append(asdict(record))
        self.save_manifest(records)
        return asdict(record)

    def remove_install(self, install_id_or_name: str) -> None:
        records = self.load_manifest()
        target = next(
            (
                item
                for item in records
                if item.get("install_id") == install_id_or_name
                or item.get("name") == install_id_or_name
            ),
            None,
        )
        if target is None:
            raise ValueError(f"Unknown installed skill entry: {install_id_or_name}")

        installed_path_value = target.get("installed_path")
        if not installed_path_value:
            raise ValueError(f"Unknown installed skill entry: {install_id_or_name}")

        installed_path = self._normalize_entry_path(Path(installed_path_value))
        if not self._is_managed_entry_path(installed_path):
            raise ValueError(
                f"Installed path resolves outside the installed root: {installed_path}"
            )

        if installed_path.is_symlink():
            installed_path.unlink()
        elif installed_path.is_dir():
            shutil.rmtree(installed_path)
        elif installed_path.exists():
            installed_path.unlink()

        self.save_manifest(
            [
                item
                for item in records
                if item.get("install_id") != target.get("install_id")
            ]
        )
