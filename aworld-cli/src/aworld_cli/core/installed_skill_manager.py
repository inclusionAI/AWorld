"""
Installed skill manager for aworld-cli.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import shutil
import tempfile
from copy import deepcopy
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from aworld.plugins.discovery import discover_plugins
from aworld.skills.compat_provider import build_compat_registry
from aworld_cli.core.plugin_manager import PluginManager, list_builtin_plugins

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
        self.plugin_dir = self.manifest_path.parent.parent / "plugins"
        self.plugin_manager = PluginManager(plugin_dir=self.plugin_dir)
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
        self._ensure_no_unmanaged_plugin_dir_collision(record["install_id"])
        self._ensure_no_reserved_plugin_id_collision(record)
        existing_record = self.plugin_manager._manifest.get(record["install_id"])
        if isinstance(existing_record, Mapping):
            existing_package_kind = str(existing_record.get("package_kind", "plugin"))
            existing_managed_by = str(existing_record.get("managed_by", "plugin"))
            is_skill_managed = (
                existing_package_kind == "skill" and existing_managed_by == "skill"
            )
            if not is_skill_managed:
                raise ValueError(
                    f"Skill install id '{record['install_id']}' conflicts with an existing non-skill plugin manifest record"
                )

        metadata = {
            "install_id": record["install_id"],
            "name": record["name"],
            "installed_path": record["installed_path"],
            "resolved_skill_source_path": record["resolved_skill_source_path"],
            "install_mode": record["install_mode"],
            "scope": record["scope"],
            "source": record["source"],
            "installed_at": record["installed_at"],
        }
        self.plugin_manager.upsert_manifest_record(
            record["install_id"],
            plugin_path=Path(record["installed_path"]),
            source=record["source"],
            enabled=True,
            package_kind="skill",
            managed_by="skill",
            activation_scope="global",
            metadata=metadata,
            installed_at=record["installed_at"],
        )
        return record

    def _ensure_no_unmanaged_plugin_dir_collision(self, install_id: str) -> None:
        existing_record = self.plugin_manager._manifest.get(install_id)
        if isinstance(existing_record, Mapping):
            return

        unmanaged_path = self.plugin_manager.plugin_dir / install_id
        if unmanaged_path.exists() or unmanaged_path.is_symlink():
            raise ValueError(
                f"Skill install id '{install_id}' conflicts with an unmanaged plugin directory: {unmanaged_path}"
                )

    def _is_same_skill_package(self, record: dict[str, str], plugin: Mapping[str, object]) -> bool:
        if str(plugin.get("name", "")) != record["install_id"]:
            return False

        if str(plugin.get("managed_by", "")) != "skill":
            return False

        recorded_path = record.get("installed_path")
        plugin_path = plugin.get("path")
        if not isinstance(recorded_path, str) or not isinstance(plugin_path, str):
            return False

        try:
            return self._normalize_entry_path(Path(recorded_path)) == self._normalize_entry_path(
                Path(plugin_path)
            )
        except ValueError:
            return False

    def _ensure_no_reserved_plugin_id_collision(self, record: dict[str, str]) -> None:
        candidate_ids: list[tuple[str, str]] = [("install id", record["install_id"])]
        embedded_plugin_id = self._extract_embedded_manifest_plugin_id(record)
        if embedded_plugin_id and embedded_plugin_id != record["install_id"]:
            candidate_ids.append(("embedded plugin id", embedded_plugin_id))

        for candidate_label, candidate_plugin_id in candidate_ids:
            self._ensure_no_framework_plugin_id_collision(
                record=record,
                candidate_plugin_id=candidate_plugin_id,
                candidate_label=candidate_label,
            )

    def _extract_embedded_manifest_plugin_id(self, record: Mapping[str, str]) -> str | None:
        installed_path_value = record.get("installed_path")
        if not installed_path_value:
            return None

        manifest_path = Path(installed_path_value) / ".aworld-plugin" / "plugin.json"
        if not manifest_path.is_file():
            return None

        try:
            manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        if not isinstance(manifest_data, Mapping):
            return None

        raw_plugin_id = manifest_data.get("id", manifest_data.get("plugin_id"))
        if not isinstance(raw_plugin_id, str):
            return None

        normalized_plugin_id = raw_plugin_id.strip()
        return normalized_plugin_id or None

    def _ensure_no_framework_plugin_id_collision(
        self,
        *,
        record: dict[str, str],
        candidate_plugin_id: str,
        candidate_label: str,
    ) -> None:
        if any(
            str(builtin_plugin.get("plugin_id", "")) == candidate_plugin_id
            for builtin_plugin in list_builtin_plugins()
        ):
            raise ValueError(
                f"Skill {candidate_label} '{candidate_plugin_id}' conflicts with reserved framework plugin id '{candidate_plugin_id}'"
            )

        seen_paths: set[Path] = set()
        for plugin in self.plugin_manager._iter_plugin_records():
            plugin_path_value = plugin.get("path")
            if not isinstance(plugin_path_value, str):
                continue
            plugin_path = Path(plugin_path_value)
            if not plugin_path.exists() or not plugin_path.is_dir():
                continue
            resolved_plugin_path = plugin_path.resolve(strict=False)
            if resolved_plugin_path in seen_paths:
                continue
            seen_paths.add(resolved_plugin_path)

            discovered = discover_plugins([plugin_path])
            if not discovered:
                continue
            if discovered[0].manifest.plugin_id != candidate_plugin_id:
                continue
            if self._is_same_skill_package(record, plugin):
                continue
            raise ValueError(
                f"Skill {candidate_label} '{candidate_plugin_id}' conflicts with existing framework plugin id '{candidate_plugin_id}'"
            )

    def _load_plugin_manifest_records(
        self,
        *,
        include_disabled: bool = True,
        include_enabled_field: bool = False,
    ) -> list[dict[str, str | bool]]:
        records: list[dict[str, str | bool]] = []
        now_iso = datetime.now(timezone.utc).isoformat()
        manifest_items = sorted(
            self.plugin_manager._manifest.items(), key=lambda item: str(item[0])
        )
        for plugin_name, plugin_info in manifest_items:
            if not isinstance(plugin_info, Mapping):
                continue

            package_kind = str(plugin_info.get("package_kind", "plugin"))
            managed_by = str(plugin_info.get("managed_by", "plugin"))
            metadata = plugin_info.get("metadata")
            if isinstance(metadata, Mapping):
                metadata_map = metadata
            else:
                metadata_map = {}

            has_skill_metadata = "install_id" in metadata_map and "installed_path" in metadata_map
            if not ((package_kind == "skill" and managed_by == "skill") or has_skill_metadata):
                continue

            enabled = bool(plugin_info.get("enabled", True))
            if not include_disabled and not enabled:
                continue

            installed_path = str(
                metadata_map.get("installed_path")
                or plugin_info.get("path")
                or (self.plugin_manager.plugin_dir / plugin_name)
            )
            install_id = str(metadata_map.get("install_id") or plugin_name)
            resolved_skill_source_path = str(
                metadata_map.get("resolved_skill_source_path") or installed_path
            )
            try:
                resolved_skill_source_path = str(
                    self.resolve_entry_source(Path(installed_path))
                )
            except ValueError:
                pass

            record: dict[str, str | bool] = {
                "install_id": install_id,
                "name": str(metadata_map.get("name") or install_id),
                "source": str(
                    metadata_map.get("source")
                    or plugin_info.get("source")
                    or installed_path
                ),
                "installed_path": installed_path,
                "resolved_skill_source_path": resolved_skill_source_path,
                "install_mode": str(metadata_map.get("install_mode") or "manual"),
                "scope": str(
                    metadata_map.get("scope")
                    or plugin_info.get("activation_scope")
                    or "global"
                ),
                "installed_at": str(
                    metadata_map.get("installed_at")
                    or plugin_info.get("installed_at")
                    or now_iso
                ),
            }
            if include_enabled_field:
                record["enabled"] = enabled
            records.append(record)

        return records

    def _find_manifest_record(
        self, install_id_or_name: str
    ) -> tuple[str, int, dict[str, str], list[dict[str, str]]]:
        plugin_records = self._load_plugin_manifest_records(include_disabled=True)
        for index, record in enumerate(plugin_records):
            if (
                record.get("install_id") == install_id_or_name
                or record.get("name") == install_id_or_name
            ):
                return "plugin", index, record, plugin_records

        legacy_records = self._load_legacy_manifest()
        for index, record in enumerate(legacy_records):
            if (
                record.get("install_id") == install_id_or_name
                or record.get("name") == install_id_or_name
            ):
                return "legacy", index, record, legacy_records

        raise ValueError(f"Unknown installed skill entry: {install_id_or_name}")

    def _count_skills(self, source_path: Path) -> int:
        if not source_path.exists():
            return 0
        return len(build_compat_registry(source_path).list_descriptors())

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

    def _load_legacy_manifest(self) -> list[dict[str, str]]:
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

    def load_manifest(self) -> list[dict[str, str]]:
        plugin_records = self._load_plugin_manifest_records(include_disabled=True)
        if not self.manifest_path.exists():
            return plugin_records

        legacy_records = self._load_legacy_manifest()
        if not plugin_records:
            return legacy_records

        merged_records: dict[str, dict[str, str]] = {
            item["install_id"]: item for item in plugin_records
        }
        plugin_names = {item["name"] for item in plugin_records}
        for legacy_record in legacy_records:
            legacy_install_id = legacy_record["install_id"]
            if legacy_install_id in merged_records:
                continue
            if legacy_record["name"] in plugin_names:
                continue
            merged_records[legacy_install_id] = legacy_record

        return sorted(
            merged_records.values(), key=lambda item: (item["name"], item["install_id"])
        )

    def save_manifest(self, records: list[dict[str, str]]) -> None:
        sanitized_records: list[dict[str, str]] = []
        for index, item in enumerate(records):
            if not isinstance(item, Mapping):
                logger.warning(
                    "Skipping invalid installed skill manifest record %s in save_manifest: not a mapping",
                    index,
                )
                continue
            sanitized = self._sanitize_manifest_record(item, index)
            if sanitized is not None:
                sanitized_records.append(sanitized)

        serialized = json.dumps(sanitized_records, indent=2, ensure_ascii=False)
        legacy_state = self._capture_text_file_state(self.manifest_path)
        plugin_manifest_state = self._capture_text_file_state(
            self.plugin_manager.manifest_file
        )
        plugin_memory_state = deepcopy(self.plugin_manager._manifest)
        managed_skill_records = [
            item
            for item in self.plugin_manager.list_skill_packages(include_disabled=True)
            if str(item.get("managed_by")) == "skill"
        ]
        expected_install_ids = {item["install_id"] for item in sanitized_records}
        try:
            for plugin_record in managed_skill_records:
                plugin_name = str(plugin_record.get("name", ""))
                if plugin_name and plugin_name not in expected_install_ids:
                    self.plugin_manager.remove_manifest_record(plugin_name)

            for record in sanitized_records:
                self._upsert_manifest_record(record)

            self._write_text_file_atomic(self.manifest_path, serialized)
        except Exception:
            self.plugin_manager._manifest = plugin_memory_state
            self._restore_text_file_state(
                self.plugin_manager.manifest_file, plugin_manifest_state
            )
            self._restore_text_file_state(self.manifest_path, legacy_state)
            raise

    def _write_text_file_atomic(
        self, path: Path, content: str, *, mode: int | None = None
    ) -> None:
        target_path = path.resolve(strict=False)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        target_mode = mode
        if target_mode is None:
            if target_path.exists():
                target_mode = target_path.stat().st_mode & 0o777
            else:
                current_umask = os.umask(0)
                os.umask(current_umask)
                target_mode = 0o666 & ~current_umask

        temp_manifest_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=target_path.parent,
                prefix=f"{target_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                temp_file.write(content)
                temp_manifest_path = Path(temp_file.name)
            if target_mode is not None:
                temp_manifest_path.chmod(target_mode)
            temp_manifest_path.replace(target_path)
        except Exception:
            if temp_manifest_path and (
                temp_manifest_path.is_file() or temp_manifest_path.is_symlink()
            ):
                temp_manifest_path.unlink()
            raise

    def _capture_text_file_state(
        self, path: Path
    ) -> tuple[bool, str | None, int | None]:
        target_path = path.resolve(strict=False)
        if not (target_path.is_file() or target_path.is_symlink()):
            return (False, None, None)
        return (
            True,
            target_path.read_text(encoding="utf-8"),
            target_path.stat().st_mode & 0o777,
        )

    def _restore_text_file_state(
        self,
        path: Path,
        state: tuple[bool, str | None, int | None],
    ) -> None:
        existed, content, mode = state
        target_path = path.resolve(strict=False)
        if not existed:
            if target_path.is_file() or target_path.is_symlink():
                target_path.unlink()
            return
        self._write_text_file_atomic(target_path, content or "", mode=mode)

    def resolve_entry_source(self, entry_path: Path) -> Path:
        entry_path = entry_path.expanduser()
        nested_skills_dir = entry_path / "skills"
        if nested_skills_dir.is_dir() and self._count_skills(nested_skills_dir):
            return nested_skills_dir.resolve()
        if entry_path.is_dir() and self._count_skills(entry_path):
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
        plugin_manifest_state = self._capture_text_file_state(
            self.plugin_manager.manifest_file
        )
        plugin_memory_state = deepcopy(self.plugin_manager._manifest)
        try:
            return self._upsert_manifest_record(asdict(record))
        except Exception:
            self.plugin_manager._manifest = plugin_memory_state
            self._restore_text_file_state(
                self.plugin_manager.manifest_file, plugin_manifest_state
            )
            self._cleanup_installed_entry(target_path)
            raise

    def list_installs(
        self, *, include_disabled: bool = True
    ) -> list[dict[str, str | int | bool]]:
        self._migrate_legacy_manifest_once()
        all_records = self._load_plugin_manifest_records(
            include_disabled=True,
            include_enabled_field=True,
        )
        records = (
            all_records
            if include_disabled
            else [
                item
                for item in all_records
                if bool(item.get("enabled", True))
            ]
        )
        managed_ids = {item["install_id"] for item in all_records}
        managed_paths: set[Path] = set()
        for item in all_records:
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
            records = self._load_plugin_manifest_records(
                include_disabled=include_disabled,
                include_enabled_field=True,
            )

        installs: list[dict[str, str | int | bool]] = []
        for item in records:
            record_with_count: dict[str, str | int | bool] = dict(item)
            record_with_count["skill_count"] = self._count_skills(
                Path(item["resolved_skill_source_path"])
            )
            installs.append(record_with_count)
        return installs

    def _migrate_legacy_manifest_once(self) -> None:
        if not self.manifest_path.exists():
            return

        legacy_records = self._load_legacy_manifest()
        if not legacy_records:
            return

        plugin_manifest_state = self._capture_text_file_state(
            self.plugin_manager.manifest_file
        )
        plugin_memory_state = deepcopy(self.plugin_manager._manifest)

        try:
            for record in legacy_records:
                self._upsert_manifest_record(record)

            migrated_path = self.manifest_path.with_suffix(".json.migrated")
            if migrated_path.exists():
                migrated_path.unlink()
            self.manifest_path.replace(migrated_path)
        except Exception:
            self.plugin_manager._manifest = plugin_memory_state
            self._restore_text_file_state(
                self.plugin_manager.manifest_file, plugin_manifest_state
            )
            raise

    def update_install(self, install_id_or_name: str) -> dict[str, str]:
        record_source, index, target, records = self._find_manifest_record(install_id_or_name)
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
        if installed_path.is_symlink():
            raise ValueError(
                "symlink-backed installed entries cannot be updated"
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
        if record_source == "plugin":
            self._upsert_manifest_record(updated_record)
        else:
            records[index] = updated_record
            self.save_manifest(records)
        return updated_record

    def enable_install(self, install_id_or_name: str) -> dict[str, str | int | bool]:
        self._migrate_legacy_manifest_once()
        _, _, target, _ = self._find_manifest_record(install_id_or_name)
        install_id = str(target["install_id"])
        self.plugin_manager.enable(install_id)
        installs = self.list_installs(include_disabled=True)
        for item in installs:
            if item.get("install_id") == install_id:
                return item
        raise ValueError(f"Unknown installed skill entry: {install_id_or_name}")

    def disable_install(self, install_id_or_name: str) -> dict[str, str | int | bool]:
        self._migrate_legacy_manifest_once()
        _, _, target, _ = self._find_manifest_record(install_id_or_name)
        install_id = str(target["install_id"])
        self.plugin_manager.disable(install_id)
        installs = self.list_installs(include_disabled=True)
        for item in installs:
            if item.get("install_id") == install_id:
                return item
        raise ValueError(f"Unknown installed skill entry: {install_id_or_name}")

    def remove_install(self, install_id_or_name: str) -> None:
        record_source, _, target, records = self._find_manifest_record(install_id_or_name)

        installed_path_value = target.get("installed_path")
        if not installed_path_value:
            raise ValueError(f"Unknown installed skill entry: {install_id_or_name}")

        installed_path = self._normalize_entry_path(Path(installed_path_value))
        if not self._is_managed_entry_path(installed_path):
            raise ValueError(
                f"Installed path resolves outside the installed root: {installed_path}"
            )

        self._cleanup_installed_entry(installed_path)

        if record_source == "plugin":
            self.plugin_manager.remove_manifest_record(str(target["install_id"]))
            return

        self.save_manifest(
            [
                item
                for item in records
                if item.get("install_id") != target.get("install_id")
            ]
        )
