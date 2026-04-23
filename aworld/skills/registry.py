from __future__ import annotations

from typing import Any

from aworld.skills.models import SkillContent, SkillDescriptor


class SkillRegistry:
    def __init__(self, providers):
        self._providers = list(providers)
        self._descriptors: dict[str, SkillDescriptor] = {}
        self._content_cache: dict[str, SkillContent] = {}
        self._provider_by_skill_id = {}

        for provider in self._providers:
            for descriptor in provider.list_descriptors():
                self._descriptors.setdefault(descriptor.skill_id, descriptor)
                self._provider_by_skill_id.setdefault(descriptor.skill_id, provider)

    def get_descriptor(self, skill_id: str) -> SkillDescriptor | None:
        return self._descriptors.get(skill_id)

    def list_descriptors(self) -> list[SkillDescriptor]:
        return list(self._descriptors.values())

    def load_content(self, skill_id: str) -> SkillContent:
        if skill_id not in self._content_cache:
            provider = self._provider_by_skill_id[skill_id]
            self._content_cache[skill_id] = provider.load_content(skill_id)
        return self._content_cache[skill_id]

    def build_skill_config(
        self,
        skill_id: str,
        *,
        active_override: bool | None = None,
    ) -> dict[str, Any]:
        descriptor = self._descriptors[skill_id]
        content = self.load_content(skill_id)
        metadata = dict(descriptor.metadata or {})
        skill_config = {
            "name": descriptor.skill_name,
            "description": descriptor.description,
            "tool_list": dict(content.tool_list),
            "usage": content.usage,
            "type": str(metadata.get("type", "")),
            "active": (
                bool(active_override)
                if active_override is not None
                else bool(metadata.get("active", False))
            ),
            "skill_path": descriptor.skill_file,
            "asset_root": descriptor.asset_root,
        }
        if descriptor.requirements:
            skill_config["aworld_metadata"] = dict(descriptor.requirements)
        return skill_config
