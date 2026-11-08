from __future__ import annotations

from pathlib import Path

from aworld.skills.filesystem_provider import FilesystemSkillProvider
from aworld.core.agent.skill import canonical_explicit_skill_root
from aworld.skills.registry import SkillRegistry
from aworld.utils.skill_loader import resolve_skill_path


def build_compat_provider(
    root_path,
    cache_dir=None,
    *,
    strict_paths: bool = True,
) -> FilesystemSkillProvider:
    raw_path = Path(root_path).expanduser()
    if strict_paths and raw_path.exists():
        canonical_explicit_skill_root(raw_path)
    resolved_root = resolve_skill_path(root_path, cache_dir)
    return FilesystemSkillProvider(
        provider_id=str(resolved_root),
        root=Path(resolved_root),
        strict_paths=strict_paths,
    )


def build_compat_registry(root_path, cache_dir=None) -> SkillRegistry:
    return SkillRegistry([build_compat_provider(root_path, cache_dir=cache_dir)])
