from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class SkillDescriptor:
    skill_id: str
    provider_id: str
    skill_name: str
    display_name: str
    description: str
    source_type: str
    scope: str
    visibility: str
    asset_root: str
    skill_file: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    requirements: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SkillContent:
    skill_id: str
    usage: str
    tool_list: Mapping[str, Any]
    raw_frontmatter: Mapping[str, Any]
