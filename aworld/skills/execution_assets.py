from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any


_DEFAULT_EXECUTION_SUFFIXES = {
    ".bash",
    ".cfg",
    ".conf",
    ".config",
    ".csv",
    ".ini",
    ".jinja",
    ".j2",
    ".json",
    ".py",
    ".sh",
    ".sql",
    ".template",
    ".tmpl",
    ".toml",
    ".tsv",
    ".txt",
    ".yaml",
    ".yml",
    ".zsh",
}

_UNDERSTANDING_FILES = {"SKILL.md", "skill.md"}
_SCRIPT_REFERENCE_RE = re.compile(r"(?P<path>scripts/[A-Za-z0-9_./-]+)")
_SKILL_VIRTUAL_REFERENCE_RE = re.compile(
    r"/skills/(?P<skill_name>[A-Za-z0-9_.-]+)/(?P<relative_path>[A-Za-z0-9_./-]+)"
)


@dataclass(frozen=True)
class ExecutionAssetManifest:
    root: Path
    relative_paths: tuple[str, ...]


def parse_declared_execution_assets(raw: Any) -> list[str] | None:
    if raw in (None, "", [], (), set()):
        return None

    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return [text]
        return parse_declared_execution_assets(parsed)

    if isinstance(raw, dict):
        for key in ("relative_paths", "paths", "include"):
            if key in raw:
                return parse_declared_execution_assets(raw.get(key))
        enabled = raw.get("enabled")
        if enabled is False:
            return []
        return None

    if isinstance(raw, (list, tuple, set)):
        return [str(item).strip() for item in raw if str(item).strip()]

    return [str(raw).strip()]


def build_execution_asset_manifest(
    root: Path,
    declared_assets: list[str] | None,
) -> ExecutionAssetManifest:
    root = Path(root).resolve()
    if declared_assets:
        relative_paths = tuple(_normalize_declared_paths(root, declared_assets))
    else:
        relative_paths = tuple(_collect_default_execution_assets(root))
    return ExecutionAssetManifest(root=root, relative_paths=relative_paths)


def compute_execution_asset_digest(manifest: ExecutionAssetManifest) -> str:
    hasher = sha256()
    for rel_path in manifest.relative_paths:
        asset_path = manifest.root / rel_path
        hasher.update(rel_path.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(asset_path.read_bytes())
        hasher.update(b"\0")
    return hasher.hexdigest()[:16]


def build_execution_assets_config(
    root: Path,
    declared_assets: Any = None,
    *,
    usage_text: str = "",
    skill_name: str = "",
    entrypoint: str | None = None,
    metadata: Any = None,
) -> dict[str, Any]:
    declared_entrypoint = resolve_execution_entrypoint(
        entrypoint=entrypoint,
        metadata=metadata,
    )
    referenced_paths = discover_execution_asset_references(
        usage_text=usage_text,
        skill_name=skill_name,
    )
    declared_relative_paths = parse_declared_execution_assets(declared_assets) or []
    merged_declared_paths = _merge_relative_paths(
        declared_relative_paths,
        referenced_paths,
        [declared_entrypoint] if declared_entrypoint else [],
    )
    manifest = build_execution_asset_manifest(
        root,
        merged_declared_paths or None,
    )
    if not manifest.relative_paths:
        return {
            "enabled": False,
            "relative_paths": [],
            "digest": "",
        }
    config = {
        "enabled": True,
        "relative_paths": list(manifest.relative_paths),
        "digest": compute_execution_asset_digest(manifest),
    }
    normalized_entrypoint = _resolve_entrypoint_for_manifest(
        root=manifest.root,
        manifest=manifest,
        declared_entrypoint=declared_entrypoint,
        referenced_paths=referenced_paths,
    )
    if normalized_entrypoint:
        config["entrypoint"] = normalized_entrypoint
    return config


def merge_execution_assets_configs(
    root: Path,
    *configs: dict[str, Any],
) -> dict[str, Any]:
    enabled = False
    relative_paths: list[str] = []
    entrypoint: str | None = None
    digest: str = ""

    for config in configs:
        if not isinstance(config, dict):
            continue
        enabled = enabled or bool(config.get("enabled"))
        relative_paths = _merge_relative_paths(
            relative_paths,
            [str(path).strip() for path in config.get("relative_paths", []) or [] if str(path).strip()],
        )
        if not entrypoint:
            candidate = config.get("entrypoint")
            if isinstance(candidate, str) and candidate.strip():
                entrypoint = candidate.strip()
        if not digest:
            candidate_digest = str(config.get("digest", "") or "").strip()
            if candidate_digest:
                digest = candidate_digest

    if not enabled and not relative_paths:
        return {
            "enabled": False,
            "relative_paths": [],
            "digest": "",
        }

    root_path = Path(root)
    if not root_path.exists():
        merged = {
            "enabled": True,
            "relative_paths": relative_paths,
            "digest": digest,
        }
        if entrypoint:
            merged["entrypoint"] = entrypoint
        return merged

    return build_execution_assets_config(
        root_path,
        declared_assets=relative_paths,
        entrypoint=entrypoint,
    )


def resolve_execution_entrypoint(
    *,
    entrypoint: str | None = None,
    metadata: Any = None,
) -> str | None:
    for candidate in (
        entrypoint,
        metadata.get("entrypoint") if isinstance(metadata, dict) else None,
    ):
        if isinstance(candidate, str) and candidate.strip():
            return str(Path(candidate.strip()))
    return None


def discover_execution_asset_references(
    *,
    usage_text: str,
    skill_name: str = "",
) -> list[str]:
    if not usage_text:
        return []

    references: list[str] = []
    seen: set[str] = set()
    normalized_skill_name = skill_name.strip()

    for match in _SCRIPT_REFERENCE_RE.finditer(usage_text):
        candidate = _normalize_reference_path(match.group("path"))
        if candidate and candidate not in seen:
            references.append(candidate)
            seen.add(candidate)

    for match in _SKILL_VIRTUAL_REFERENCE_RE.finditer(usage_text):
        if normalized_skill_name and match.group("skill_name") != normalized_skill_name:
            continue
        candidate = _normalize_reference_path(match.group("relative_path"))
        if candidate and candidate not in seen:
            references.append(candidate)
            seen.add(candidate)

    return references


def _normalize_declared_paths(root: Path, declared_assets: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_path in declared_assets:
        rel_path = str(Path(raw_path)).strip()
        if not rel_path:
            continue
        asset_path = (root / rel_path).resolve()
        try:
            normalized_rel_path = str(asset_path.relative_to(root))
        except ValueError as exc:
            raise ValueError(f"Execution asset path escapes skill root: {raw_path}") from exc
        if not asset_path.is_file():
            raise FileNotFoundError(f"Execution asset file not found: {raw_path}")
        if normalized_rel_path in seen:
            continue
        normalized.append(normalized_rel_path)
        seen.add(normalized_rel_path)
    normalized.sort()
    return normalized


def _collect_default_execution_assets(root: Path) -> list[str]:
    candidates: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name in _UNDERSTANDING_FILES:
            continue
        relative_path = path.relative_to(root)
        if _is_default_script_path(relative_path):
            candidates.append(str(relative_path))
            continue
        if path.suffix.lower() not in _DEFAULT_EXECUTION_SUFFIXES:
            continue
        candidates.append(str(relative_path))
    return candidates


def _is_default_script_path(relative_path: Path) -> bool:
    parts = relative_path.parts
    return bool(parts) and parts[0] == "scripts"


def _merge_relative_paths(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for value in group:
            candidate = str(Path(value)).strip()
            if not candidate or candidate in seen:
                continue
            merged.append(candidate)
            seen.add(candidate)
    return merged


def _normalize_reference_path(raw_path: str) -> str | None:
    candidate = raw_path.strip().rstrip("`'\"),.:;!?")
    if not candidate:
        return None
    return str(Path(candidate))


def _resolve_entrypoint_for_manifest(
    *,
    root: Path,
    manifest: ExecutionAssetManifest,
    declared_entrypoint: str | None,
    referenced_paths: list[str],
) -> str | None:
    if declared_entrypoint:
        normalized = _normalize_declared_paths(root, [declared_entrypoint])[0]
        if normalized not in manifest.relative_paths:
            raise FileNotFoundError(f"Execution entrypoint file not found: {declared_entrypoint}")
        return normalized

    referenced_script_paths = [
        reference
        for reference in referenced_paths
        if reference.startswith("scripts/")
    ]
    existing = [
        normalized
        for normalized in _existing_relative_paths(root, referenced_script_paths)
        if normalized in manifest.relative_paths
    ]
    if len(existing) == 1:
        return existing[0]
    return None


def _existing_relative_paths(root: Path, candidates: list[str]) -> list[str]:
    existing: list[str] = []
    for candidate in candidates:
        path = (root / candidate).resolve()
        try:
            normalized = str(path.relative_to(root))
        except ValueError:
            continue
        if path.is_file():
            existing.append(normalized)
    return existing
