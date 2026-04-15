"""
Installed skill manager for aworld-cli.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urlparse
from uuid import uuid4

from aworld.utils.skill_loader import collect_skill_docs

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

    def load_manifest(self) -> list[dict[str, str]]:
        if not self.manifest_path.exists():
            return []

        try:
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []

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

    def _build_install_id(self, source: str, name: Optional[str] = None) -> str:
        raw_name = name or Path(urlparse(source).path).stem or Path(source).name or "skill"
        slug = re.sub(r"[^a-z0-9._-]+", "-", raw_name.lower()).strip("-") or "skill"
        return f"{slug}-{uuid4().hex[:8]}"

    def import_entry(self, entry_path: Path, scope: SkillScope) -> dict[str, str]:
        entry_path = entry_path.expanduser()
        if self.installed_root.resolve() not in entry_path.absolute().parents:
            raise ValueError("Manual import path must already live under the installed root")

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

    def install(
        self,
        source: str,
        mode: InstallMode,
        scope: SkillScope,
        install_id: Optional[str] = None,
    ) -> dict[str, str]:
        install_id = install_id or self._build_install_id(source)
        target = self.installed_root / install_id
        if target.exists():
            raise ValueError(f"Install target already exists: {target}")

        if mode == "copy":
            shutil.copytree(Path(source).expanduser().resolve(), target)
        elif mode == "symlink":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(Path(source).expanduser().resolve(), target_is_directory=True)
        elif mode == "clone":
            subprocess.run(
                ["git", "clone", "--depth", "1", source, str(target)],
                check=True,
                capture_output=True,
                text=True,
            )
        else:
            raise ValueError(f"Unsupported install mode: {mode}")

        resolved_source = self.resolve_entry_source(target)
        record = InstalledSkillRecord(
            install_id=install_id,
            name=Path(install_id).name,
            source=source,
            installed_path=str(target),
            resolved_skill_source_path=str(resolved_source),
            install_mode=mode,
            scope=scope,
            installed_at=datetime.now(timezone.utc).isoformat(),
        )
        records = [item for item in self.load_manifest() if item.get("install_id") != install_id]
        records.append(asdict(record))
        self.save_manifest(records)
        return asdict(record)

    def list_installs(self) -> list[dict[str, str]]:
        installs: list[dict[str, str]] = []
        manifest_by_id = {
            item["install_id"]: item for item in self.load_manifest() if "install_id" in item
        }

        for entry_path in sorted(self.installed_root.iterdir()):
            if entry_path.name.startswith("."):
                continue

            manifest_record = manifest_by_id.get(entry_path.name)
            if manifest_record is None:
                try:
                    resolved_source = self.resolve_entry_source(entry_path)
                except ValueError:
                    continue

                discovered_record = InstalledSkillRecord(
                    install_id=entry_path.name,
                    name=entry_path.name,
                    source=str(entry_path),
                    installed_path=str(entry_path),
                    resolved_skill_source_path=str(resolved_source),
                    install_mode="manual",
                    scope="global",
                    installed_at=datetime.now(timezone.utc).isoformat(),
                )
                records = [
                    item
                    for item in self.load_manifest()
                    if item.get("install_id") != discovered_record.install_id
                ]
                records.append(asdict(discovered_record))
                self.save_manifest(records)
                manifest_record = asdict(discovered_record)

            discovered_skills = collect_skill_docs(
                manifest_record["resolved_skill_source_path"]
            )
            installs.append({**manifest_record, "skill_count": len(discovered_skills)})

        return installs

    def update_install(self, install_id_or_name: str) -> dict[str, str]:
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
        if target.get("install_mode") != "clone":
            raise ValueError("Only git-backed installs support update")

        installed_path = Path(target["installed_path"])
        subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=installed_path,
            check=True,
            capture_output=True,
            text=True,
        )
        target["resolved_skill_source_path"] = str(self.resolve_entry_source(installed_path))
        self.save_manifest(records)
        return target

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

        installed_path = Path(target["installed_path"])
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
