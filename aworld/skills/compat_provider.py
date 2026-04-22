from __future__ import annotations

from pathlib import Path

from aworld.skills.filesystem_provider import FilesystemSkillProvider
from aworld.utils.skill_loader import resolve_skill_path


def build_compat_provider(root_path, cache_dir=None) -> FilesystemSkillProvider:
    resolved_root = resolve_skill_path(root_path, cache_dir)
    return FilesystemSkillProvider(provider_id=str(resolved_root), root=Path(resolved_root))
