from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml


BLOCKED_SELF_EVOLVE_RELEASE_STATES = frozenset({"draft", "candidate", "rejected", "disabled"})


def extract_self_evolve_metadata(front_matter: Mapping[str, Any]) -> dict[str, Any]:
    raw = front_matter.get("self_evolve")
    if not isinstance(raw, Mapping):
        return {}
    return {str(key): value for key, value in raw.items()}


def is_self_evolve_release_visible(metadata: Mapping[str, Any]) -> bool:
    raw = metadata.get("self_evolve")
    if not isinstance(raw, Mapping):
        return True
    state = str(raw.get("release_state", "")).strip().lower()
    return state not in BLOCKED_SELF_EVOLVE_RELEASE_STATES


def is_self_evolve_draft_path(path: str | Path) -> bool:
    normalized_parts = tuple(part.lower() for part in Path(path).parts)
    marker = (".aworld", "self_evolve", "drafts", "skills")
    return any(
        normalized_parts[index : index + len(marker)] == marker
        for index in range(0, len(normalized_parts) - len(marker) + 1)
    )


def mark_skill_content_verified(
    content: str,
    *,
    run_id: str,
    candidate_id: str,
    verified_at: str | None = None,
) -> str:
    return _mark_skill_content_release_state(
        content,
        release_state="verified",
        metadata={
            "verified_run_id": run_id,
            "verified_candidate_id": candidate_id,
            "verified_at": verified_at or _utc_timestamp(),
        },
    )


def mark_skill_content_candidate(
    content: str,
    *,
    run_id: str,
    candidate_id: str,
) -> str:
    return _mark_skill_content_release_state(
        content,
        release_state="candidate",
        metadata={
            "run_id": run_id,
            "candidate_id": candidate_id,
        },
    )


def _mark_skill_content_release_state(
    content: str,
    *,
    release_state: str,
    metadata: Mapping[str, Any],
) -> str:
    lines = content.splitlines()
    front_matter, body_start = _extract_front_matter(lines)
    body_lines = lines[body_start:] if body_start > 0 else lines

    updated_front_matter = dict(front_matter)
    self_evolve = extract_self_evolve_metadata(updated_front_matter)
    self_evolve.update({"release_state": release_state, **dict(metadata)})
    updated_front_matter["self_evolve"] = self_evolve

    front_matter_text = yaml.safe_dump(
        updated_front_matter,
        sort_keys=False,
        allow_unicode=True,
    ).strip()
    body = "\n".join(body_lines).lstrip("\n")
    rendered = f"---\n{front_matter_text}\n---\n"
    if body:
        rendered += body
    if content.endswith("\n"):
        rendered += "\n"
    return rendered


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _extract_front_matter(lines: list[str]) -> tuple[dict[str, Any], int]:
    if not lines or lines[0].strip() != "---":
        return {}, 0
    end_index = 1
    while end_index < len(lines) and lines[end_index].strip() != "---":
        end_index += 1
    if end_index >= len(lines):
        return {}, 0
    front_matter_text = "\n".join(lines[1:end_index])
    try:
        parsed = yaml.safe_load(front_matter_text) or {}
    except yaml.YAMLError:
        return {}, 0
    if not isinstance(parsed, dict):
        return {}, end_index + 1
    return parsed, end_index + 1
