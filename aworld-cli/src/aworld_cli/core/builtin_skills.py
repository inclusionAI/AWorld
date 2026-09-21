"""Framework skills shipped with the CLI, independent of user skill sources."""

from __future__ import annotations

import json
from pathlib import Path

from aworld.skills.filesystem_provider import FilesystemSkillProvider


AWORLD_DEFAULT_SKILL_NAMES = ("filex",)

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_MANIFEST_PATH = _PACKAGE_ROOT / "builtin_skills" / "manifest.json"


def get_builtin_skills_path() -> Path:
    """Return packaged resources, or their single canonical source in a checkout."""
    packaged = _PACKAGE_ROOT / "builtin_skills"
    manifest = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    if all(
        (packaged / item["skill_file"]).is_file()
        for item in manifest["skills"].values()
    ):
        return packaged

    # Editable installs use the repository files directly; only distributions
    # contain a build-time copy under aworld_cli/builtin_skills.
    source = _PACKAGE_ROOT.parents[2] / "aworld-skills"
    if all(
        (source / item["skill_file"]).is_file()
        for item in manifest["skills"].values()
    ):
        return source
    raise FileNotFoundError(f"Built-in skill resources are missing: {packaged}")


def build_builtin_skill_providers() -> list[FilesystemSkillProvider]:
    """Provide only the declared built-ins, without overriding user sources."""
    root = get_builtin_skills_path()
    manifest = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    return [
        FilesystemSkillProvider(f"builtin:{name}", root / name)
        for name in manifest["skills"]
    ]
