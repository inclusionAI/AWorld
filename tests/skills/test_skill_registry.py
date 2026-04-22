from dataclasses import dataclass
from pathlib import Path

from aworld.skills.models import SkillContent, SkillDescriptor
from aworld.skills.providers import SkillProvider
from aworld.skills.registry import SkillRegistry


@dataclass
class DummyProvider(SkillProvider):
    provider_name: str = "dummy"

    def provider_id(self) -> str:
        return self.provider_name

    def list_descriptors(self):
        return [
            SkillDescriptor(
                skill_id=f"{self.provider_name}:browser-use",
                provider_id=self.provider_name,
                skill_name="browser-use",
                display_name="browser-use",
                description="Browser automation",
                source_type="dummy",
                scope="workspace",
                visibility="public",
                asset_root="/tmp/browser-use",
                skill_file="/tmp/browser-use/SKILL.md",
                metadata={},
                requirements={},
            )
        ]

    def load_content(self, skill_id: str) -> SkillContent:
        return SkillContent(
            skill_id=skill_id,
            usage="# Browser skill",
            tool_list={},
            raw_frontmatter={},
        )

    def resolve_asset_path(self, skill_id: str, relative_path: str) -> Path:
        return Path("/tmp/browser-use") / relative_path


def test_registry_indexes_descriptor_by_skill_id():
    registry = SkillRegistry([DummyProvider()])

    descriptor = registry.get_descriptor("dummy:browser-use")

    assert descriptor is not None
    assert descriptor.skill_name == "browser-use"


def test_registry_loads_content_lazily():
    provider = DummyProvider()
    registry = SkillRegistry([provider])

    first = registry.load_content("dummy:browser-use")
    second = registry.load_content("dummy:browser-use")

    assert first.usage == "# Browser skill"
    assert second is first
