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
    heading_title = _heading_title(heading)
    start = _find_heading_index(lines, heading_title)
    if start is None:
        raise CandidateMaterializationError(
            CandidateMaterializationCode.PATCH_SECTION_NOT_FOUND,
            f"section not found: {heading}",
            field_path=CandidateFailureField.PATCH_HEADING,
        )
    level = _heading_level(lines[start])
    end = start + 1
    while end < len(lines):
        current_level = _heading_level(lines[end])
        if current_level is not None and current_level <= level:
            break
        end += 1
    replacement = [
        lines[start],
        "",
        *_body_lines(body, heading_title=heading_title),
    ]
    return "\n".join([*lines[:start], *replacement, *lines[end:]])


def _append_section(content: str, *, heading: str, body: str) -> str:
    heading_title = _heading_title(heading)
    rendered = content.rstrip() + "\n\n"
    rendered += f"## {heading_title}\n\n"
    rendered += "\n".join(_body_lines(body, heading_title=heading_title))
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
