"""Explicit skills: inline instructions or a discoverable SKILL.md location."""

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from aworld.core.tool.function import Tool


class SkillPackageValidationError(ValueError):
    """An explicit Skill package escaped its root or used mutable indirection."""


_PACKAGE_FINGERPRINT = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _lexical_absolute(path: str | Path) -> Path:
    return Path(os.path.abspath(Path(path).expanduser()))


def _contained_relative_path(root: Path, value: str | Path) -> Path:
    candidate = Path(value)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise SkillPackageValidationError("skill package path must be relative")
    lexical = _lexical_absolute(root / candidate)
    try:
        lexical.relative_to(root)
    except ValueError as exc:
        raise SkillPackageValidationError("skill package path escapes root") from exc
    return lexical


def validate_skill_package(
    skill_root: str | Path,
    *,
    skill_file: str | Path | None = None,
    declared_paths: tuple[str, ...] = (),
) -> tuple[Path, Path]:
    """Validate one explicit package without following symlinks or escapes."""

    root = _lexical_absolute(skill_root)
    if root.is_symlink() or not root.is_dir():
        raise SkillPackageValidationError("skill root must be a real directory")
    if skill_file is None:
        default_manifest = root / "SKILL.md"
        if not default_manifest.exists():
            default_manifest = root / "skill.md"
        skill_file = default_manifest
    manifest = _lexical_absolute(skill_file)
    try:
        manifest.relative_to(root)
    except ValueError as exc:
        raise SkillPackageValidationError("SKILL.md escapes skill root") from exc
    current = manifest
    while current != root:
        if current.is_symlink():
            raise SkillPackageValidationError("skill package cannot contain symlinks")
        current = current.parent
    if manifest.name not in {"SKILL.md", "skill.md"} or not manifest.is_file():
        raise SkillPackageValidationError("skill package is missing SKILL.md")
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise SkillPackageValidationError("skill package cannot contain symlinks")
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise SkillPackageValidationError("skill package entry escapes root") from exc
    for declared in declared_paths:
        target = _contained_relative_path(root, declared)
        if target.is_symlink() or not target.is_file():
            raise SkillPackageValidationError(
                "declared skill entrypoint must be a real file"
            )
    return root, manifest


def fingerprint_skill_package(skill_root: str | Path) -> str:
    """Return the canonical package identity shared by Rich and minimal CLIs."""

    root, _ = validate_skill_package(skill_root)
    package_entries: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        content = path.read_bytes()
        stat = path.stat()
        package_entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
                "size": stat.st_size,
                "mode": stat.st_mode & 0o777,
            }
        )
    encoded = json.dumps(
        {"files": package_entries},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    instructions: str = ""
    location: str | None = None
    tools: tuple[Tool, ...] = ()
    package_fingerprint: str | None = None

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Skill name must be non-empty")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("Skill description must be non-empty")
        if not isinstance(self.instructions, str):
            raise TypeError("Skill instructions must be text")
        if self.location is not None:
            object.__setattr__(self, "location", str(Path(self.location).expanduser().resolve()))
        if self.package_fingerprint is not None and _PACKAGE_FINGERPRINT.fullmatch(
            self.package_fingerprint
        ) is None:
            raise ValueError("package_fingerprint must be a canonical sha256")
        owned = tuple(self.tools)
        if not all(isinstance(tool, Tool) for tool in owned):
            raise TypeError("Skill tools must contain Tool values")
        object.__setattr__(self, "tools", owned)

    @classmethod
    def from_file(cls, path: str | Path) -> "Skill":
        """Load metadata only; the model reads the body with the ordinary read tool.

        YAML parsing is optional and imported only for this explicit file loader.
        This method never installs dependencies, syncs assets, or executes scripts.
        """
        try:
            import yaml
        except ImportError as exc:
            raise ImportError("Skill discovery requires the optional aworld[skills] dependency; use --no-skills to disable it") from exc

        location = _lexical_absolute(path)
        root, location = validate_skill_package(
            location.parent,
            skill_file=location,
        )
        text = location.read_text(encoding="utf-8")
        lines = text.splitlines()
        if not lines or lines[0] != "---":
            raise ValueError(f"Missing SKILL.md frontmatter: {location}")
        try:
            end = lines.index("---", 1)
        except ValueError:
            raise ValueError("Unclosed SKILL.md frontmatter") from None
        metadata = yaml.safe_load("\n".join(lines[1:end]))
        if not isinstance(metadata, dict):
            raise ValueError("Skill frontmatter must be a mapping")
        declared_entrypoint = metadata.get("entrypoint")
        nested_metadata = metadata.get("metadata")
        if not declared_entrypoint and isinstance(nested_metadata, dict):
            declared_entrypoint = nested_metadata.get("entrypoint")
        if declared_entrypoint:
            validate_skill_package(
                root,
                skill_file=location,
                declared_paths=(str(declared_entrypoint),),
            )
        return cls(
            metadata.get("name"),
            metadata.get("description"),
            location=str(location),
            package_fingerprint=fingerprint_skill_package(root),
        )


def load_skills(*directories: str | Path) -> tuple[Skill, ...]:
    """Discover one directory level under explicit roots, without global scanning."""
    skills = []
    for directory in directories:
        root = _lexical_absolute(directory)
        if root.is_symlink() or not root.is_dir():
            raise NotADirectoryError(str(root))
        direct = root / "SKILL.md"
        if direct.is_symlink():
            raise SkillPackageValidationError("SKILL.md cannot be a symlink")
        if direct.is_file():
            files = [direct]
        else:
            files = []
            for child in sorted(root.iterdir()):
                if child.is_symlink():
                    raise SkillPackageValidationError(
                        "skill directory cannot be a symlink"
                    )
                manifest = child / "SKILL.md"
                if manifest.is_symlink():
                    raise SkillPackageValidationError("SKILL.md cannot be a symlink")
                if child.is_dir() and manifest.is_file():
                    files.append(manifest)
        skills.extend(Skill.from_file(path) for path in files)
    names = [skill.name for skill in skills]
    if len(set(names)) != len(names):
        raise ValueError("Skill names must be unique")
    return tuple(skills)
