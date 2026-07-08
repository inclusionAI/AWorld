from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml


BLOCKED_SELF_EVOLVE_RELEASE_STATES = frozenset({"draft", "candidate", "rejected", "disabled"})
INTERNAL_RELEASE_PATTERNS = (
    re.compile(r"\bsource task ids?\b", re.IGNORECASE),
    re.compile(r"\b(candidate|baseline)_score\b", re.IGNORECASE),
    re.compile(r"\b(candidate_score|baseline_score)\s+exceeds\b", re.IGNORECASE),
    re.compile(r"\bA[1-4]_[A-Za-z0-9_]+\b"),
    re.compile(r"\bB[1-4]_[A-Za-z0-9_]+\b"),
    re.compile(r"\b(evidence_quality|score_improvement|held_out_verification|judge_only_signal|global_regression_benchmark)\b", re.IGNORECASE),
    re.compile(r"\b(harness_diagnostic|gate|evaluator rubric|evidence ids?)\b", re.IGNORECASE),
    re.compile(r"(?i)\b(secret|token|api[_-]?key|password|authorization|cookie)\s*[:=]"),
    re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/\-]+=*"),
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"(?<![\w.-])/(?:Users|private|var|tmp|home)/[^\s,;:'\")\]}]+"),
    re.compile(r"(?i)\b(ignore|disregard) (all )?(previous|prior|above) (instructions|messages)\b"),
    re.compile(r"(?i)\b(system prompt|developer message)\b"),
)


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


def normalize_verified_skill_release(
    content: str,
    *,
    run_id: str,
    candidate_id: str,
) -> tuple[str, Mapping[str, Any]]:
    """Return verified release content plus equivalence metrics.

    The normalizer removes obvious self-evolve internal body lines while
    requiring at least one runtime behavior constraint to survive. It is a
    conservative guard: when equivalence cannot be established, apply should be
    rejected before the runtime skill is written.
    """

    marked = mark_skill_content_verified(
        content,
        run_id=run_id,
        candidate_id=candidate_id,
    )
    normalized = _remove_internal_release_lines(marked)
    pre_constraints = _runtime_constraint_lines(content)
    normalized_constraints = _runtime_constraint_lines(normalized)
    equivalence_passed = bool(pre_constraints) and all(
        constraint in normalized_constraints for constraint in pre_constraints
    )
    return normalized, {
        "pre_normalization_fingerprint": _content_fingerprint(content),
        "normalized_release_fingerprint": _content_fingerprint(normalized),
        "normalization_equivalence_passed": equivalence_passed,
        "preserved_runtime_constraints": normalized_constraints,
        "removed_internal_line_count": _removed_internal_line_count(marked, normalized),
        "evaluator_mode": "release_normalization_equivalence",
    }


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


def _remove_internal_release_lines(content: str) -> str:
    lines = content.splitlines()
    front_matter, body_start = _extract_front_matter(lines)
    if body_start <= 0:
        prefix: list[str] = []
        body_lines = lines
    else:
        prefix = lines[:body_start]
        body_lines = lines[body_start:]
    filtered_body = [
        line for line in body_lines if not _is_internal_release_line(line)
    ]
    rendered_lines = prefix + filtered_body
    rendered = "\n".join(rendered_lines)
    if content.endswith("\n"):
        rendered += "\n"
    return rendered


def _runtime_constraint_lines(content: str) -> list[str]:
    lines = content.splitlines()
    _, body_start = _extract_front_matter(lines)
    body_lines = lines[body_start:] if body_start > 0 else lines
    constraints: list[str] = []
    for line in body_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped in {"---"}:
            continue
        if _is_internal_release_line(stripped):
            continue
        constraints.append(stripped)
    return constraints


def _is_internal_release_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    return any(pattern.search(stripped) for pattern in INTERNAL_RELEASE_PATTERNS)


def _removed_internal_line_count(original: str, normalized: str) -> int:
    return max(0, len(original.splitlines()) - len(normalized.splitlines()))


def _content_fingerprint(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


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
