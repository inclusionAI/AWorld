from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SkillStructuralEditAction:
    action: str
    section_path: tuple[str, ...]
    base_section_fingerprint: str | None
    result_section_fingerprint: str


@dataclass(frozen=True)
class SkillStructuralEditIntent:
    schema_version: str
    authority: str
    authorization: str
    reason: str
    base_content_fingerprint: str
    candidate_content_fingerprint: str
    actions: tuple[SkillStructuralEditAction, ...]
