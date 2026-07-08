from __future__ import annotations

import re
from typing import Any, Mapping


_PROTECTED_REFERENCE_PATTERNS = (
    re.compile(r"(?<![\w.-])/(?:Users|private|var|tmp|home)/[^\s,;:'\")\]}]+"),
    re.compile(r"(?i)\b(secret|token|api[_-]?key|password|authorization|cookie)\b"),
    re.compile(r"(?i)\b(ignore|disregard) (all )?(previous|prior|above) (instructions|messages)\b"),
)


def apply_skill_patch_intent(
    content: str,
    patch_intent: Mapping[str, Any],
    *,
    max_chars: int = 500_000,
) -> str:
    """Apply a bounded skill markdown patch intent to full SKILL.md content."""

    operations = patch_intent.get("operations")
    if not isinstance(operations, list) or not operations:
        raise ValueError("patch_intent.operations must be a non-empty list")
    updated = content
    for index, operation in enumerate(operations):
        if not isinstance(operation, Mapping):
            raise ValueError(f"patch operation {index} must be an object")
        op = operation.get("op")
        heading = _required_text(operation.get("heading"), field=f"operations[{index}].heading")
        body = _required_text(operation.get("content"), field=f"operations[{index}].content")
        _reject_protected_references(body)
        if op == "replace_section":
            updated = _replace_section(updated, heading=heading, body=body)
        elif op == "append_section":
            updated = _append_section(updated, heading=heading, body=body)
        else:
            raise ValueError(f"unsupported patch operation: {op!r}")
    updated = _ensure_trailing_newline(updated)
    if len(updated) > max_chars:
        raise ValueError("materialized skill exceeds size limit")
    return updated


def _replace_section(content: str, *, heading: str, body: str) -> str:
    lines = content.splitlines()
    start = _find_heading_index(lines, heading)
    if start is None:
        raise ValueError(f"section not found: {heading}")
    level = _heading_level(lines[start])
    end = start + 1
    while end < len(lines):
        current_level = _heading_level(lines[end])
        if current_level is not None and current_level <= level:
            break
        end += 1
    replacement = [lines[start], "", *_body_lines(body)]
    return "\n".join([*lines[:start], *replacement, *lines[end:]])


def _append_section(content: str, *, heading: str, body: str) -> str:
    rendered = content.rstrip() + "\n\n"
    rendered += f"## {heading.strip()}\n\n"
    rendered += "\n".join(_body_lines(body))
    return rendered


def _find_heading_index(lines: list[str], heading: str) -> int | None:
    normalized = heading.strip().lower()
    for index, line in enumerate(lines):
        level = _heading_level(line)
        if level is None:
            continue
        title = line.lstrip("#").strip().lower()
        if title == normalized:
            return index
    return None


def _heading_level(line: str) -> int | None:
    stripped = line.lstrip()
    if not stripped.startswith("#"):
        return None
    level = len(stripped) - len(stripped.lstrip("#"))
    if level <= 0 or level > 6:
        return None
    return level


def _body_lines(body: str) -> list[str]:
    return body.strip("\n").splitlines()


def _required_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _reject_protected_references(value: str) -> None:
    if any(pattern.search(value) for pattern in _PROTECTED_REFERENCE_PATTERNS):
        raise ValueError("patch intent contains a protected reference")


def _ensure_trailing_newline(value: str) -> str:
    return value if value.endswith("\n") else value + "\n"
