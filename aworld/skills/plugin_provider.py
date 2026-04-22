from __future__ import annotations

from pathlib import Path
from typing import Any

from aworld.skills.models import SkillContent, SkillDescriptor
from aworld.skills.providers import SkillProvider, read_front_matter_lines
from aworld.utils.skill_loader import (
    evaluate_skill_requirements,
    extract_front_matter,
    resolve_aworld_metadata,
)


class PluginSkillProvider(SkillProvider):
    def __init__(self, plugin: Any) -> None:
        self._plugin = plugin
        self._plugin_root = Path(plugin.manifest.plugin_root).resolve()
        self._skill_files: dict[str, Path] = {}

    def provider_id(self) -> str:
        return self._plugin.manifest.plugin_id

    def _resolve_skill_path(self, entrypoint) -> Path | None:
        target = (entrypoint.target or "").strip()
        if target:
            candidate = (self._plugin_root / target).resolve()
            if candidate.is_file():
                return candidate

        for candidate in (
            self._plugin_root / "skills" / entrypoint.entrypoint_id / "SKILL.md",
            self._plugin_root / entrypoint.entrypoint_id / "SKILL.md",
        ):
            if candidate.is_file():
                return candidate.resolve()
        return None

    def _load_front_matter(self, skill_file: Path) -> dict[str, Any]:
        content = read_front_matter_lines(skill_file)
        front_matter, _ = extract_front_matter(content)
        return front_matter

    def list_descriptors(self) -> list[SkillDescriptor]:
        descriptors: list[SkillDescriptor] = []
        for entrypoint in self._plugin.manifest.entrypoints.get("skills", ()):
            skill_file = self._resolve_skill_path(entrypoint)
            if skill_file is None:
                continue
            skill_id = f"{self.provider_id()}:{entrypoint.entrypoint_id}"
            self._skill_files[skill_id] = skill_file
            front_matter = self._load_front_matter(skill_file)
            aworld_meta = resolve_aworld_metadata(front_matter)
            requirements = {}
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
                    provider_id=self.provider_id(),
                    skill_name=entrypoint.entrypoint_id,
                    display_name=str(front_matter.get("name") or entrypoint.name or entrypoint.entrypoint_id),
                    description=str(
                        entrypoint.description
                        or front_matter.get("desc", front_matter.get("description", ""))
                    ),
                    source_type=f"plugin:{self._plugin.source}",
                    scope=str(entrypoint.scope or "workspace").strip().lower() or "workspace",
                    visibility=str(entrypoint.visibility or "public").strip().lower() or "public",
                    asset_root=str(skill_file.parent.resolve()),
                    skill_file=str(skill_file),
                    metadata=dict(entrypoint.metadata or {}),
                    requirements=requirements,
                )
            )
        return descriptors

    def load_content(self, skill_id: str) -> SkillContent:
        skill_file = self._skill_files.get(skill_id)
        if skill_file is None:
            raise KeyError(skill_id)
        content = skill_file.read_text(encoding="utf-8").splitlines()
        front_matter, body_start = extract_front_matter(content)
        usage = "\n".join(content[body_start:]).strip()
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
