from __future__ import annotations

from pathlib import Path
from typing import Any

from aworld.skills.models import SkillContent, SkillDescriptor
from aworld.skills.providers import SkillProvider
from aworld.utils.skill_loader import (
    evaluate_skill_requirements,
    extract_front_matter,
    resolve_aworld_metadata,
)


class FilesystemSkillProvider(SkillProvider):
    def __init__(self, provider_id: str, root: Path) -> None:
        self._provider_id = provider_id
        self._root = Path(root)
        self._skill_files: dict[str, Path] = {}

    def provider_id(self) -> str:
        return self._provider_id

    def _iter_skill_files(self):
        seen: set[Path] = set()
        for pattern in ("**/skill.md", "**/SKILL.md"):
            for skill_file in sorted(self._root.glob(pattern)):
                resolved = skill_file.resolve()
                if resolved in seen or not resolved.is_file():
                    continue
                seen.add(resolved)
                yield resolved

    def _read_front_matter(self, skill_file: Path) -> dict[str, Any]:
        content_lines: list[str] = []
        with skill_file.open("r", encoding="utf-8") as handle:
            for line in handle:
                content_lines.append(line.rstrip("\n"))
                if len(content_lines) > 1 and line.strip() == "---":
                    break
        front_matter, _ = extract_front_matter(content_lines)
        return front_matter

    def _load_full_skill(self, skill_file: Path) -> tuple[dict[str, Any], str]:
        content = skill_file.read_text(encoding="utf-8").splitlines()
        front_matter, body_start = extract_front_matter(content)
        usage = "\n".join(content[body_start:]).strip()
        return front_matter, usage

    def list_descriptors(self) -> list[SkillDescriptor]:
        descriptors: list[SkillDescriptor] = []
        for skill_file in self._iter_skill_files():
            front_matter = self._read_front_matter(skill_file)
            skill_name = skill_file.parent.name
            skill_id = f"{self._provider_id}:{skill_name}"
            if skill_id in self._skill_files:
                continue
            self._skill_files[skill_id] = skill_file

            requirements = {}
            aworld_meta = resolve_aworld_metadata(front_matter)
            if aworld_meta:
                eligible, missing = evaluate_skill_requirements(aworld_meta)
                requirements = {
                    "always": aworld_meta["always"],
                    "requires": aworld_meta["requires"],
                    "install": aworld_meta["install"],
                    "eligible": eligible,
                    "missing": missing,
                    "install_options": aworld_meta["install"],
                }

            descriptors.append(
                SkillDescriptor(
                    skill_id=skill_id,
                    provider_id=self._provider_id,
                    skill_name=skill_name,
                    display_name=str(front_matter.get("name") or skill_name),
                    description=str(front_matter.get("desc", front_matter.get("description", ""))),
                    source_type="filesystem",
                    scope="global",
                    visibility="public",
                    asset_root=str(skill_file.parent.resolve()),
                    skill_file=str(skill_file),
                    metadata={
                        "type": str(front_matter.get("type", "")),
                        "active": str(front_matter.get("active", "False")).lower() == "true",
                    },
                    requirements=requirements,
                )
            )
        return descriptors

    def load_content(self, skill_id: str) -> SkillContent:
        skill_file = self._skill_files.get(skill_id)
        if skill_file is None:
            for descriptor in self.list_descriptors():
                if descriptor.skill_id == skill_id:
                    skill_file = Path(descriptor.skill_file)
                    break
        if skill_file is None:
            raise KeyError(skill_id)

        front_matter, usage = self._load_full_skill(skill_file)
        tool_list = front_matter.get("tool_list", {})
        if isinstance(tool_list, str):
            tool_list = {}
        return SkillContent(
            skill_id=skill_id,
            usage=usage,
            tool_list=tool_list,
            raw_frontmatter=front_matter,
        )

    def resolve_asset_path(self, skill_id: str, relative_path: str) -> Path:
        skill_file = self._skill_files.get(skill_id)
        if skill_file is None:
            raise KeyError(skill_id)
        return (skill_file.parent / relative_path).resolve()
