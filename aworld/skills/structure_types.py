from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


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


def skill_structural_edit_intent_from_dict(
    value: Any,
) -> SkillStructuralEditIntent | None:
    """Load a persisted structural edit authorization without weakening it."""

    if not isinstance(value, Mapping):
        return None
    actions = value.get("actions")
    if not isinstance(actions, list):
        return None
    try:
        return SkillStructuralEditIntent(
            schema_version=str(value.get("schema_version") or ""),
            authority=str(value.get("authority") or ""),
            authorization=str(value.get("authorization") or ""),
            reason=str(value.get("reason") or ""),
            base_content_fingerprint=str(
                value.get("base_content_fingerprint") or ""
            ),
            candidate_content_fingerprint=str(
                value.get("candidate_content_fingerprint") or ""
            ),
            actions=tuple(
                SkillStructuralEditAction(
                    action=str(item.get("action") or ""),
                    section_path=tuple(
                        str(part)
                        for part in item.get("section_path", ())
                        if isinstance(part, str)
                    ),
                    base_section_fingerprint=(
                        str(item.get("base_section_fingerprint"))
                        if item.get("base_section_fingerprint") is not None
                        else None
                    ),
                    result_section_fingerprint=str(
                        item.get("result_section_fingerprint") or ""
                    ),
                )
                for item in actions
                if isinstance(item, Mapping)
            ),
        )
    except (TypeError, ValueError):
        return None
