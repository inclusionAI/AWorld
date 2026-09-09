from __future__ import annotations

import re
from typing import Any, Mapping

from aworld.secret_detection import contains_sensitive_literal
from aworld.self_evolve.candidate_errors import (
    CandidateFailureField,
    CandidateMaterializationCode,
    CandidateMaterializationError,
)


_PROTECTED_REFERENCE_PATTERNS = (
    re.compile(r"(?<![\w.-])/(?:Users|private|var|tmp|home)/[^\s,;:'\")\]}]+"),
    re.compile(r"(?i)\b(ignore|disregard) (all )?(previous|prior|above) (instructions|messages)\b"),
)
_MARKDOWN_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def apply_skill_patch_intent(
    content: str,
    patch_intent: Mapping[str, Any],
    *,
    max_chars: int = 500_000,
) -> str:
    """Apply a bounded skill markdown patch intent to full SKILL.md content."""

    validate_skill_patch_intent(patch_intent)
    operations = patch_intent.get("operations")
    assert isinstance(operations, list)
    updated = content
    for index, operation in enumerate(operations):
        assert isinstance(operation, Mapping)
        op = operation.get("op")
        heading = _required_text(
            operation.get("heading"),
            field=f"operations[{index}].heading",
            code=CandidateMaterializationCode.PATCH_HEADING_INVALID,
            field_path=CandidateFailureField.PATCH_HEADING,
        )
        body = _required_text(
            operation.get("content"),
            field=f"operations[{index}].content",
            code=CandidateMaterializationCode.PATCH_CONTENT_INVALID,
            field_path=CandidateFailureField.PATCH_CONTENT,
        )
        if op == "replace_section":
            updated = _replace_section(updated, heading=heading, body=body)
        elif op == "append_section":
            updated = _append_section(updated, heading=heading, body=body)
    updated = _ensure_trailing_newline(updated)
    if len(updated) > max_chars:
        raise CandidateMaterializationError(
            CandidateMaterializationCode.CONTENT_TOO_LARGE,
            "materialized skill exceeds size limit",
            field_path=CandidateFailureField.CONTENT,
        )
    return updated


def validate_skill_patch_intent(patch_intent: Mapping[str, Any]) -> None:
    """Validate patch syntax and safety without assuming a materialization base."""

    operations = patch_intent.get("operations")
    if not isinstance(operations, list) or not operations:
        raise CandidateMaterializationError(
            CandidateMaterializationCode.PATCH_OPERATIONS_INVALID,
            "patch_intent.operations must be a non-empty list",
            field_path=CandidateFailureField.PATCH_OPERATIONS,
        )
    for index, operation in enumerate(operations):
        if not isinstance(operation, Mapping):
            raise CandidateMaterializationError(
                CandidateMaterializationCode.PATCH_OPERATION_INVALID,
                f"patch operation {index} must be an object",
                field_path=CandidateFailureField.PATCH_OPERATION,
            )
        op = operation.get("op")
        if op not in {"replace_section", "append_section"}:
            raise CandidateMaterializationError(
                CandidateMaterializationCode.PATCH_OPERATION_KIND_INVALID,
                f"unsupported patch operation: {op!r}",
                field_path=CandidateFailureField.PATCH_OPERATION_KIND,
            )
        _required_text(
            operation.get("heading"),
            field=f"operations[{index}].heading",
            code=CandidateMaterializationCode.PATCH_HEADING_INVALID,
            field_path=CandidateFailureField.PATCH_HEADING,
        )
        body = _required_text(
            operation.get("content"),
            field=f"operations[{index}].content",
            code=CandidateMaterializationCode.PATCH_CONTENT_INVALID,
            field_path=CandidateFailureField.PATCH_CONTENT,
        )
        _reject_protected_references(body)


def _replace_section(content: str, *, heading: str, body: str) -> str:
    lines = content.splitlines()
    heading_levels = _markdown_heading_levels(lines)
    heading_title = _heading_title(heading)
    start = _find_heading_index(lines, heading_title, heading_levels=heading_levels)
    if start is None:
        raise CandidateMaterializationError(
            CandidateMaterializationCode.PATCH_SECTION_NOT_FOUND,
            f"section not found: {heading}",
            field_path=CandidateFailureField.PATCH_HEADING,
        )
    level = heading_levels[start]
    assert level is not None
    end = start + 1
    while end < len(lines):
        current_level = heading_levels[end]
        if current_level is not None and current_level <= level:
            break
        end += 1
    replacement = [
        lines[start],
        "",
        *_body_lines(body, heading_title=heading_title),
    ]
    # Focused repair candidates are patched on top of the previously judged
    # candidate.  A model can therefore encounter an already duplicated
    # section and legitimately ask to replace/consolidate it.  Replacing only
    # the first occurrence retained every stale copy and made each repair grow
    # the target further.  Treat same-level, same-title sections as one logical
    # patch target: keep the first position, replace its body, and remove later
    # duplicates while preserving all intervening sections.
    duplicate_ranges: list[tuple[int, int]] = []
    normalized_title = heading_title.strip().lower()
    for duplicate_start in range(end, len(lines)):
        if heading_levels[duplicate_start] != level:
            continue
        title = lines[duplicate_start].lstrip("#").strip().lower()
        if title != normalized_title:
            continue
        duplicate_end = duplicate_start + 1
        while duplicate_end < len(lines):
            current_level = heading_levels[duplicate_end]
            if current_level is not None and current_level <= level:
                break
            duplicate_end += 1
        duplicate_ranges.append((duplicate_start, duplicate_end))

    rendered = [*lines[:start], *replacement]
    cursor = end
    for duplicate_start, duplicate_end in duplicate_ranges:
        rendered.extend(lines[cursor:duplicate_start])
        cursor = duplicate_end
    rendered.extend(lines[cursor:])
    return "\n".join(rendered)


def _append_section(content: str, *, heading: str, body: str) -> str:
    heading_title = _heading_title(heading)
    lines = content.splitlines()
    heading_levels = _markdown_heading_levels(lines)
    if _find_heading_index(
        lines,
        heading_title,
        heading_levels=heading_levels,
    ) is not None:
        # An append operation emitted during focused repair commonly means
        # "publish this section" even though the parent candidate already has
        # a version of it.  Upsert the logical section instead of silently
        # manufacturing duplicate instructions.
        return _replace_section(content, heading=heading_title, body=body)
    rendered = content.rstrip() + "\n\n"
    rendered += f"## {heading_title}\n\n"
    rendered += "\n".join(_body_lines(body, heading_title=heading_title))
    return rendered


def _find_heading_index(
    lines: list[str],
    heading: str,
    *,
    heading_levels: list[int | None] | None = None,
) -> int | None:
    normalized = heading.strip().lower()
    levels = heading_levels or _markdown_heading_levels(lines)
    for index, (line, level) in enumerate(zip(lines, levels, strict=True)):
        if level is None:
            continue
        title = line.lstrip("#").strip().lower()
        if title == normalized:
            return index
    return None


def _markdown_heading_levels(lines: list[str]) -> list[int | None]:
    """Return heading levels while ignoring heading-like text in code fences."""

    levels: list[int | None] = []
    fence_character: str | None = None
    fence_length = 0
    for line in lines:
        if fence_character is not None:
            stripped = line.lstrip(" ")
            indentation = len(line) - len(stripped)
            marker_length = len(stripped) - len(stripped.lstrip(fence_character))
            if (
                indentation <= 3
                and marker_length >= fence_length
                and not stripped[marker_length:].strip()
            ):
                fence_character = None
                fence_length = 0
            levels.append(None)
            continue

        fence_match = _MARKDOWN_FENCE_OPEN.match(line)
        if fence_match is not None:
            marker = fence_match.group(1)
            info = fence_match.group(2)
            if marker[0] == "~" or "`" not in info:
                fence_character = marker[0]
                fence_length = len(marker)
                levels.append(None)
                continue

        levels.append(_heading_level(line))
    return levels


def _heading_level(line: str) -> int | None:
    stripped = line.lstrip()
    if not stripped.startswith("#"):
        return None
    level = len(stripped) - len(stripped.lstrip("#"))
    if level <= 0 or level > 6:
        return None
    return level


def _body_lines(body: str, *, heading_title: str) -> list[str]:
    lines = body.strip("\n").splitlines()
    if lines and _heading_level(lines[0]) is not None:
        first_title = _heading_title(lines[0])
        if first_title.lower() == heading_title.lower():
            lines = lines[1:]
            while lines and not lines[0].strip():
                lines.pop(0)
    return lines


def _heading_title(value: str) -> str:
    stripped = value.strip()
    if _heading_level(stripped) is not None:
        stripped = stripped.lstrip("#").strip()
    if not stripped:
        raise CandidateMaterializationError(
            CandidateMaterializationCode.PATCH_HEADING_INVALID,
            "heading must include a Markdown title",
            field_path=CandidateFailureField.PATCH_HEADING,
        )
    return stripped


def _required_text(
    value: Any,
    *,
    field: str,
    code: CandidateMaterializationCode,
    field_path: CandidateFailureField,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CandidateMaterializationError(
            code,
            f"{field} must be a non-empty string",
            field_path=field_path,
        )
    return value


def _reject_protected_references(value: str) -> None:
    if contains_sensitive_literal(value) or any(
        pattern.search(value)
        for pattern in _PROTECTED_REFERENCE_PATTERNS
    ):
        raise CandidateMaterializationError(
            CandidateMaterializationCode.PATCH_CONTENT_PROTECTED_REFERENCE,
            "patch intent contains a protected reference",
            field_path=CandidateFailureField.PATCH_CONTENT,
        )


def _ensure_trailing_newline(value: str) -> str:
    return value if value.endswith("\n") else value + "\n"
