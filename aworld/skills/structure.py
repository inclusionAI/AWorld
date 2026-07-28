from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

import yaml

from aworld.skills.structure_types import (
    SkillStructuralEditAction,
    SkillStructuralEditIntent,
)


SKILL_STRUCTURE_SCHEMA_VERSION = "aworld.skill.structure.v1"
MAX_STRUCTURAL_SECTIONS = 256
MAX_STRUCTURAL_COMMANDS = 256
MIN_PROTECTED_SECTION_CHARS = 120
MIN_SECTION_RETAINED_CHARS = 48
MIN_SECTION_RETENTION_RATIO = 0.25
MAX_UNDECLARED_SECTION_DELETION_RATIO = 0.25
MAX_UNDECLARED_SECTION_WEIGHT_RATIO = 0.45
MAX_UNDECLARED_COMMAND_DELETION_RATIO = 0.50
MAX_UNDECLARED_ATOM_DELETION_RATIO = 0.65
MAX_UNDECLARED_FENCE_DELETION_RATIO = 0.50
MIN_NONTRIVIAL_FENCE_LINES = 2
MIN_NONTRIVIAL_FENCE_CHARS = 48
MIN_COMMAND_DENSE_SECTION_COMMANDS = 4
MIN_STRUCTURAL_ANCHOR_RETENTION_RATIO = 0.60

_FRONTMATTER_BOUNDARY = "---"
_HEADING_RE = re.compile(
    r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$"
)
_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_ARRAY_ELLIPSIS_RE = re.compile(r"^(?:\.{3}|\[…\]|\[\.\.\.\]|<\.\.\.>)$")
_SINGLE_TOKEN_OMISSION_RE = re.compile(r"^[A-Za-z0-9_./:-]{2,}\.\.\.$")
_TEXTUAL_OMISSION_RE = re.compile(
    r"(?i)\b(?:code|commands?|content|details?|rest|section|steps?)\b"
    r".{0,24}\b(?:omitted|truncated)\b"
)
_INTERNAL_PLACEHOLDERS = (
    "<REDACTED_SECRET>",
    "<LOCAL_PATH>",
    "<UNTRUSTED_INSTRUCTION>",
)
_INLINE_CODE_RE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
_SHELL_FENCE_LANGUAGES = frozenset(
    {
        "bash",
        "console",
        "fish",
        "shell",
        "sh",
        "terminal",
        "zsh",
    }
)
_PYTHON_FENCE_LANGUAGES = frozenset({"py", "python", "python3"})
_NORMALIZE_TOKEN_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class SkillSectionInventory:
    title: str
    path: tuple[str, ...]
    level: int
    body_chars: int
    content_fingerprint: str
    anchor_fingerprint: str
    command_signatures: tuple[str, ...]
    command_fingerprints: tuple[str, ...]
    substantive_atom_fingerprints: tuple[str, ...]


@dataclass(frozen=True)
class SkillFenceInventory:
    language: str
    section_path: tuple[str, ...]
    content_chars: int
    line_fingerprints: tuple[str, ...]
    command_fingerprints: tuple[str, ...]


@dataclass(frozen=True)
class SkillMarkdownInventory:
    front_matter: Mapping[str, Any]
    sections: tuple[SkillSectionInventory, ...]
    command_signatures: tuple[str, ...]
    fence_languages: tuple[str, ...]
    fences: tuple[SkillFenceInventory, ...]
    substantive_atom_fingerprints: tuple[str, ...]
    body_chars: int


@dataclass(frozen=True)
class SkillStructureValidation:
    passed: bool
    code: str
    reason: str
    field_path: str
    contract_fingerprint: str
    details: Mapping[str, Any]


@dataclass(frozen=True)
class _Heading:
    title: str
    path: tuple[str, ...]
    level: int
    line_index: int


@dataclass(frozen=True)
class _StructuralLine:
    kind: str
    value: str
    line_index: int


@dataclass(frozen=True)
class _EllipsisOccurrence:
    kind: str
    prefix: str
    line_index: int


@dataclass(frozen=True)
class _Inspection:
    inventory: SkillMarkdownInventory | None
    code: str | None = None
    reason: str | None = None
    field_path: str | None = None
    line: int | None = None
    structural_lines: tuple[_StructuralLine, ...] = ()
    ellipsis_occurrences: tuple[_EllipsisOccurrence, ...] = ()


def validate_skill_markdown_structure(
    candidate_content: str,
    *,
    original_content: str | None = None,
    edit_intent: SkillStructuralEditIntent | None = None,
    require_exact_deletion_intent: bool = False,
) -> SkillStructureValidation:
    """Validate standalone Markdown and bounded preservation of published structure."""

    authoritative_edit_intent = _authoritative_edit_intent(
        edit_intent,
        original_content=original_content,
        candidate_content=candidate_content,
    )
    original_inspection = (
        inspect_skill_markdown(original_content)
        if isinstance(original_content, str) and original_content.strip()
        else None
    )
    original_inventory = (
        original_inspection.inventory
        if original_inspection is not None
        and original_inspection.inventory is not None
        else None
    )
    contract_fingerprint = skill_structure_contract_fingerprint(
        original_inventory,
        edit_intent=authoritative_edit_intent,
    )
    candidate_inspection = inspect_skill_markdown(candidate_content)
    if candidate_inspection.inventory is None:
        return _failed_validation(
            candidate_inspection.code or "skill_markdown_invalid",
            candidate_inspection.reason or "skill candidate markdown is invalid",
            candidate_inspection.field_path or "content",
            contract_fingerprint=contract_fingerprint,
            details={
                "line": candidate_inspection.line,
                "edit_mode": _edit_mode(authoritative_edit_intent),
                "edit_intent_authorized": bool(
                    authoritative_edit_intent
                ),
            },
        )

    candidate_inventory = candidate_inspection.inventory
    if original_inventory is None:
        return _passed_validation(
            candidate_inventory,
            contract_fingerprint=contract_fingerprint,
            edit_intent=authoritative_edit_intent,
        )

    truncated_ellipsis = _deleted_base_prefix_truncation(
        original_inspection,
        candidate_inspection,
    )
    if truncated_ellipsis is not None:
        return _failed_validation(
            "skill_truncation_marker",
            "skill candidate contains an ellipsis-truncated published line",
            "content",
            contract_fingerprint=contract_fingerprint,
            details={
                "line": truncated_ellipsis.line_index + 1,
                "line_kind": truncated_ellipsis.kind,
                "edit_mode": _edit_mode(authoritative_edit_intent),
            },
        )

    original_name = original_inventory.front_matter.get("name")
    candidate_name = candidate_inventory.front_matter.get("name")
    if (
        isinstance(original_name, str)
        and original_name.strip()
        and candidate_name != original_name
    ):
        return _failed_validation(
            "skill_frontmatter_identity_changed",
            "skill candidate must preserve the published frontmatter name",
            "frontmatter.name",
            contract_fingerprint=contract_fingerprint,
            details={
                "edit_mode": _edit_mode(authoritative_edit_intent),
            },
        )

    declared_rewrites = _declared_section_paths(
        authoritative_edit_intent,
        action="replace_section",
    )
    protected_edit_sections = declared_rewrites

    missing_fence_anchor = _missing_fence_anchor(
        original_inventory,
        candidate_inventory,
        declared_sections=protected_edit_sections,
    )
    if missing_fence_anchor is not None:
        return _failed_validation(
            "skill_fenced_block_deleted",
            "an undeclared non-trivial fenced block lost its structural anchor",
            "code_fences",
            contract_fingerprint=contract_fingerprint,
            details={
                "language": missing_fence_anchor.language,
                "section_path": [
                    _bounded_title(item)
                    for item in missing_fence_anchor.section_path
                ],
                "line_count": len(missing_fence_anchor.line_fingerprints),
                "command_count": len(
                    missing_fence_anchor.command_fingerprints
                ),
                "edit_mode": _edit_mode(authoritative_edit_intent),
            },
        )

    missing_command_section = _missing_command_dense_section_anchor(
        original_inventory,
        candidate_inventory,
        declared_sections=protected_edit_sections,
    )
    if missing_command_section is not None:
        return _failed_validation(
            "skill_command_dense_section_deleted",
            "an undeclared command-dense section lost its structural anchor",
            "sections[].commands",
            contract_fingerprint=contract_fingerprint,
            details={
                "section": _bounded_title(missing_command_section.title),
                "command_count": len(
                    missing_command_section.command_signatures
                ),
                "edit_mode": _edit_mode(authoritative_edit_intent),
            },
        )
    candidate_by_title: dict[str, list[SkillSectionInventory]] = {}
    for section in candidate_inventory.sections:
        candidate_by_title.setdefault(section.title, []).append(section)
    total_missing_section_count = sum(
        1
        for section in original_inventory.sections
        if section.level >= 2
        and section.title not in candidate_by_title
    )
    if require_exact_deletion_intent:
        strict_missing_sections = _missing_section_anchors(
            tuple(
                section
                for section in original_inventory.sections
                if section.level >= 2
                and not _section_declared(
                    section,
                    protected_edit_sections,
                )
            ),
            candidate_inventory.sections,
        )
        if strict_missing_sections:
            return _failed_validation(
                "skill_existing_sections_deleted",
                "auto-verified release requires an exact content-addressed intent for section deletion",
                "sections",
                contract_fingerprint=contract_fingerprint,
                details={
                    "missing_section_count": len(
                        strict_missing_sections
                    ),
                    "missing_sections": [
                        _bounded_title(item.title)
                        for item in strict_missing_sections[:16]
                    ],
                    "edit_mode": _edit_mode(
                        authoritative_edit_intent
                    ),
                    "exact_deletion_intent_required": True,
                },
            )

    for section in original_inventory.sections:
        if (
            section.level < 2
            or section.body_chars < MIN_PROTECTED_SECTION_CHARS
            or _section_declared(section, protected_edit_sections)
        ):
            continue
        matches = candidate_by_title.get(section.title, ())
        if not matches:
            continue
        retained_chars = max(item.body_chars for item in matches)
        minimum_retained = max(
            MIN_SECTION_RETAINED_CHARS,
            int(section.body_chars * MIN_SECTION_RETENTION_RATIO),
        )
        if retained_chars < minimum_retained:
            return _failed_validation(
                "skill_section_content_truncated",
                "an existing skill section lost most of its substantive content",
                "sections[].content",
                contract_fingerprint=contract_fingerprint,
                details={
                    "section": _bounded_title(section.title),
                    "original_chars": section.body_chars,
                    "candidate_chars": retained_chars,
                    "minimum_retained_chars": minimum_retained,
                    "edit_mode": _edit_mode(authoritative_edit_intent),
                },
            )

    protected_sections = tuple(
        section
        for section in original_inventory.sections
        if section.level >= 2
        and (
            section.body_chars >= MIN_PROTECTED_SECTION_CHARS
            or section.command_signatures
        )
        and not _section_declared(section, protected_edit_sections)
    )
    original_titles = {
        section.title for section in original_inventory.sections
    }
    unmatched_candidates = [
        section
        for section in candidate_inventory.sections
        if section.level >= 2 and section.title not in original_titles
    ]
    missing_section_items: list[SkillSectionInventory] = []
    for section in protected_sections:
        if section.title in candidate_by_title:
            continue
        renamed_index = next(
            (
                index
                for index, candidate_section in enumerate(
                    unmatched_candidates
                )
                if _section_content_preserved_after_rename(
                    section,
                    (candidate_section,),
                )
            ),
            None,
        )
        if renamed_index is None:
            missing_section_items.append(section)
        else:
            unmatched_candidates.pop(renamed_index)
    missing_sections = tuple(missing_section_items)
    protected_weight = sum(item.body_chars for item in protected_sections)
    missing_weight = sum(item.body_chars for item in missing_sections)
    allowed_missing_count = max(
        1,
        int(len(protected_sections) * MAX_UNDECLARED_SECTION_DELETION_RATIO),
    )
    missing_weight_ratio = (
        missing_weight / protected_weight if protected_weight else 0.0
    )
    if missing_sections and (
        require_exact_deletion_intent
        or len(missing_sections) > allowed_missing_count
        or missing_weight_ratio > MAX_UNDECLARED_SECTION_WEIGHT_RATIO
    ):
        return _failed_validation(
            "skill_existing_sections_deleted",
            "full skill replacement removed too much published section structure",
            "sections",
            contract_fingerprint=contract_fingerprint,
            details={
                "missing_section_count": len(missing_sections),
                "allowed_missing_section_count": allowed_missing_count,
                "missing_section_weight_ratio": round(missing_weight_ratio, 4),
                "missing_sections": [
                    _bounded_title(item.title)
                    for item in missing_sections[:16]
                ],
                "edit_mode": _edit_mode(authoritative_edit_intent),
                "exact_deletion_intent_required": (
                    require_exact_deletion_intent
                ),
            },
        )

    original_commands = set(original_inventory.command_signatures)
    candidate_commands = set(candidate_inventory.command_signatures)
    missing_commands = original_commands - candidate_commands
    allowed_command_loss = max(
        2,
        int(
            len(original_commands)
            * MAX_UNDECLARED_COMMAND_DELETION_RATIO
        ),
    )
    if (
        len(original_commands) >= 4
        and len(missing_commands) > allowed_command_loss
    ):
        return _failed_validation(
            "skill_command_inventory_deleted",
            "full skill replacement removed too much published command structure",
            "commands",
            contract_fingerprint=contract_fingerprint,
            details={
                "original_command_count": len(original_commands),
                "missing_command_count": len(missing_commands),
                "allowed_missing_command_count": allowed_command_loss,
                "missing_command_fingerprints": [
                    _identity_fingerprint(item)
                    for item in sorted(missing_commands)[:16]
                ],
                "edit_mode": _edit_mode(authoritative_edit_intent),
            },
        )

    if _edit_mode(authoritative_edit_intent) == "full_content":
        original_fences = Counter(original_inventory.fence_languages)
        candidate_fences = Counter(candidate_inventory.fence_languages)
        missing_fence_count = sum(
            max(0, count - candidate_fences.get(language, 0))
            for language, count in original_fences.items()
        )
        allowed_fence_loss = max(
            1,
            int(
                sum(original_fences.values())
                * MAX_UNDECLARED_FENCE_DELETION_RATIO
            ),
        )
        if (
            sum(original_fences.values()) >= 2
            and missing_fence_count > allowed_fence_loss
        ):
            return _failed_validation(
                "skill_code_fence_inventory_deleted",
                "full skill replacement removed too much published code-fence structure",
                "code_fences",
                contract_fingerprint=contract_fingerprint,
                details={
                    "original_fence_count": sum(original_fences.values()),
                    "missing_fence_count": missing_fence_count,
                    "allowed_missing_fence_count": allowed_fence_loss,
                    "edit_mode": "full_content",
                },
            )

        original_atoms = set(
            original_inventory.substantive_atom_fingerprints
        )
        candidate_atoms = set(
            candidate_inventory.substantive_atom_fingerprints
        )
        missing_atoms = original_atoms - candidate_atoms
        allowed_atom_loss = max(
            3,
            int(
                len(original_atoms)
                * MAX_UNDECLARED_ATOM_DELETION_RATIO
            ),
        )
        if (
            len(original_atoms) >= 6
            and len(missing_atoms) > allowed_atom_loss
        ):
            return _failed_validation(
                "skill_substantive_inventory_deleted",
                "full skill replacement removed too much published substantive structure",
                "content",
                contract_fingerprint=contract_fingerprint,
                details={
                    "original_atom_count": len(original_atoms),
                    "missing_atom_count": len(missing_atoms),
                    "allowed_missing_atom_count": allowed_atom_loss,
                    "edit_mode": "full_content",
                },
            )

    return _passed_validation(
        candidate_inventory,
        contract_fingerprint=contract_fingerprint,
        edit_intent=authoritative_edit_intent,
        original_inventory=original_inventory,
        missing_section_count=total_missing_section_count,
    )


def inspect_skill_markdown(content: str) -> _Inspection:
    if not isinstance(content, str) or not content.strip():
        return _Inspection(
            None,
            "skill_content_empty",
            "skill candidate content must be non-empty",
            "content",
        )
    lines = content.splitlines()
    front_matter, body_start, error = _parse_front_matter(lines)
    if error is not None:
        return error
    assert front_matter is not None

    headings: list[_Heading] = []
    heading_stack: list[_Heading] = []
    commands: list[str] = []
    fence_languages: list[str] = []
    fences: list[SkillFenceInventory] = []
    commands_by_heading: dict[int, list[str]] = {}
    command_fingerprints_by_heading: dict[int, list[str]] = {}
    structural_lines: list[_StructuralLine] = []
    ellipsis_occurrences: list[_EllipsisOccurrence] = []
    open_fence: tuple[
        str,
        int,
        str,
        int,
        tuple[str, ...],
        list[str],
        list[str],
    ] | None = None
    for line_index, line in enumerate(lines[body_start:], start=body_start):
        if open_fence is not None:
            (
                marker,
                marker_length,
                language,
                opening_line,
                section_path,
                fence_line_fingerprints,
                fence_command_fingerprints,
            ) = open_fence
            if _is_fence_close(line, marker=marker, minimum=marker_length):
                fences.append(
                    SkillFenceInventory(
                        language=language or "plain",
                        section_path=section_path,
                        content_chars=sum(
                            len(item) for item in fence_line_fingerprints
                        ),
                        line_fingerprints=tuple(
                            _identity_fingerprint(item)
                            for item in fence_line_fingerprints[
                                :MAX_STRUCTURAL_COMMANDS
                            ]
                        ),
                        command_fingerprints=tuple(
                            fence_command_fingerprints[
                                :MAX_STRUCTURAL_COMMANDS
                            ]
                        ),
                    )
                )
                open_fence = None
                continue
            placeholder = _placeholder_kind(
                line,
                in_fence=True,
                fence_language=language,
            )
            if placeholder is not None:
                return _Inspection(
                    None,
                    "skill_truncation_marker",
                    "skill candidate contains an obvious truncation placeholder",
                    "content",
                    line=line_index + 1,
                )
            normalized_fence_line = _normalize_structural_line(line)
            if normalized_fence_line:
                fence_line_fingerprints.append(normalized_fence_line)
            if language in _SHELL_FENCE_LANGUAGES:
                signature = _command_signature(line)
                if signature is not None:
                    commands.append(signature)
                    command_fingerprint = _command_fingerprint(line)
                    fence_command_fingerprints.append(command_fingerprint)
                    structural_lines.append(
                        _StructuralLine(
                            kind="command",
                            value=_normalize_command_line(line),
                            line_index=line_index,
                        )
                    )
                    occurrence = _ellipsis_occurrence(
                        line,
                        kind="command",
                        line_index=line_index,
                    )
                    if occurrence is not None:
                        ellipsis_occurrences.append(occurrence)
                    if headings:
                        commands_by_heading.setdefault(
                            headings[-1].line_index,
                            [],
                        ).append(signature)
                        command_fingerprints_by_heading.setdefault(
                            headings[-1].line_index,
                            [],
                        ).append(command_fingerprint)
            continue

        fence_match = _FENCE_OPEN_RE.match(line)
        if fence_match is not None:
            marker_text = fence_match.group(1)
            marker = marker_text[0]
            info = fence_match.group(2).strip()
            if marker == "`" and "`" in info:
                return _Inspection(
                    None,
                    "skill_code_fence_invalid",
                    "backtick fence info must not contain a backtick",
                    "code_fences",
                    line=line_index + 1,
                )
            language = info.split(None, 1)[0].casefold() if info else ""
            fence_languages.append(language or "plain")
            open_fence = (
                marker,
                len(marker_text),
                language,
                line_index,
                headings[-1].path if headings else (),
                [],
                [],
            )
            continue

        heading_match = _HEADING_RE.match(line)
        is_heading = heading_match is not None
        placeholder = _placeholder_kind(
            line,
            in_fence=False,
            fence_language="",
        )
        if placeholder is not None:
            return _Inspection(
                None,
                "skill_truncation_marker",
                "skill candidate contains an obvious truncation placeholder",
                "content",
                line=line_index + 1,
            )
        if heading_match is not None:
            level = len(heading_match.group(1))
            title = _normalize_heading(heading_match.group(2))
            if not title:
                return _Inspection(
                    None,
                    "skill_heading_invalid",
                    "skill heading must include a stable title",
                    "headings",
                    line=line_index + 1,
                )
            while heading_stack and heading_stack[-1].level >= level:
                heading_stack.pop()
            path = tuple(
                [item.title for item in heading_stack]
                + [title]
            )
            heading = _Heading(
                title=title,
                path=path,
                level=level,
                line_index=line_index,
            )
            headings.append(heading)
            heading_stack.append(heading)
            structural_lines.append(
                _StructuralLine(
                    kind="heading",
                    value=_normalize_heading_line(line),
                    line_index=line_index,
                )
            )
            occurrence = _ellipsis_occurrence(
                line,
                kind="heading",
                line_index=line_index,
            )
            if occurrence is not None:
                ellipsis_occurrences.append(occurrence)
            continue
        normalized_prose = _normalize_structural_line(line)
        if normalized_prose:
            structural_lines.append(
                _StructuralLine(
                    kind="prose",
                    value=normalized_prose,
                    line_index=line_index,
                )
            )
            occurrence = _ellipsis_occurrence(
                line,
                kind="prose",
                line_index=line_index,
            )
            if occurrence is not None:
                ellipsis_occurrences.append(occurrence)
        for inline in _INLINE_CODE_RE.findall(line):
            signature = _command_signature(inline, require_command_signal=True)
            if signature is not None:
                commands.append(signature)
                command_fingerprint = _command_fingerprint(inline)
                if headings:
                    commands_by_heading.setdefault(
                        headings[-1].line_index,
                        [],
                    ).append(signature)
                    command_fingerprints_by_heading.setdefault(
                        headings[-1].line_index,
                        [],
                    ).append(command_fingerprint)

    if open_fence is not None:
        return _Inspection(
            None,
            "skill_code_fence_unclosed",
            "skill candidate contains an unclosed fenced code block",
            "code_fences",
            line=open_fence[3] + 1,
        )

    sections: list[SkillSectionInventory] = []
    bounded_headings = headings[:MAX_STRUCTURAL_SECTIONS]
    for heading_index, heading in enumerate(bounded_headings):
        end = len(lines)
        for next_heading in headings[heading_index + 1 :]:
            if next_heading.level <= heading.level:
                end = next_heading.line_index
                break
        section_lines = lines[heading.line_index + 1 : end]
        body_chars = len(
            "\n".join(
                item.strip()
                for item in section_lines
                if item.strip() and not _HEADING_RE.match(item)
            )
        )
        section_commands = tuple(
            commands_by_heading.get(heading.line_index, ())
        )[:MAX_STRUCTURAL_COMMANDS]
        section_command_fingerprints = tuple(
            command_fingerprints_by_heading.get(heading.line_index, ())
        )[:MAX_STRUCTURAL_COMMANDS]
        section_atoms = _substantive_atom_fingerprints(section_lines)
        sections.append(
            SkillSectionInventory(
                title=heading.title,
                path=heading.path,
                level=heading.level,
                body_chars=body_chars,
                content_fingerprint=_content_fingerprint(
                    "\n".join(
                        item.rstrip()
                        for item in lines[heading.line_index:end]
                    )
                ),
                anchor_fingerprint=_content_fingerprint(
                    "\n".join(
                        item.rstrip()
                        for item in lines[heading.line_index:end]
                    ).strip()
                ),
                command_signatures=section_commands,
                command_fingerprints=section_command_fingerprints,
                substantive_atom_fingerprints=section_atoms,
            )
        )
    inventory = SkillMarkdownInventory(
        front_matter=front_matter,
        sections=tuple(sections),
        command_signatures=tuple(dict.fromkeys(commands))[
            :MAX_STRUCTURAL_COMMANDS
        ],
        fence_languages=tuple(fence_languages)[:MAX_STRUCTURAL_SECTIONS],
        fences=tuple(fences)[:MAX_STRUCTURAL_SECTIONS],
        substantive_atom_fingerprints=_substantive_atom_fingerprints(
            lines[body_start:]
        ),
        body_chars=len("\n".join(lines[body_start:])),
    )
    return _Inspection(
        inventory,
        structural_lines=tuple(structural_lines),
        ellipsis_occurrences=tuple(ellipsis_occurrences),
    )


def skill_structure_contract_fingerprint(
    original_inventory: SkillMarkdownInventory | None,
    *,
    edit_intent: SkillStructuralEditIntent | None = None,
) -> str:
    payload = {
        "schema_version": SKILL_STRUCTURE_SCHEMA_VERSION,
        "policy": {
            "min_protected_section_chars": MIN_PROTECTED_SECTION_CHARS,
            "min_section_retention_ratio": MIN_SECTION_RETENTION_RATIO,
            "max_section_deletion_ratio": (
                MAX_UNDECLARED_SECTION_DELETION_RATIO
            ),
            "max_section_weight_ratio": (
                MAX_UNDECLARED_SECTION_WEIGHT_RATIO
            ),
            "max_command_deletion_ratio": (
                MAX_UNDECLARED_COMMAND_DELETION_RATIO
            ),
            "max_atom_deletion_ratio": (
                MAX_UNDECLARED_ATOM_DELETION_RATIO
            ),
            "max_fence_deletion_ratio": (
                MAX_UNDECLARED_FENCE_DELETION_RATIO
            ),
            "min_structural_anchor_retention_ratio": (
                MIN_STRUCTURAL_ANCHOR_RETENTION_RATIO
            ),
            "min_command_dense_section_commands": (
                MIN_COMMAND_DENSE_SECTION_COMMANDS
            ),
        },
        "original": (
            {
                "name": original_inventory.front_matter.get("name"),
                "sections": [
                    {
                        "title": item.title,
                        "path": list(item.path),
                        "level": item.level,
                        "body_chars": item.body_chars,
                        "content_fingerprint": item.content_fingerprint,
                        "anchor_fingerprint": item.anchor_fingerprint,
                        "commands": list(item.command_signatures),
                        "command_fingerprints": list(
                            item.command_fingerprints
                        ),
                        "substantive_atoms": list(
                            item.substantive_atom_fingerprints
                        ),
                    }
                    for item in original_inventory.sections
                ],
                "commands": list(original_inventory.command_signatures),
                "fences": list(original_inventory.fence_languages),
                "fence_anchors": [
                    {
                        "language": item.language,
                        "section_path": list(item.section_path),
                        "content_chars": item.content_chars,
                        "lines": list(item.line_fingerprints),
                        "commands": list(item.command_fingerprints),
                    }
                    for item in original_inventory.fences
                ],
                "substantive_atoms": list(
                    original_inventory.substantive_atom_fingerprints
                ),
            }
            if original_inventory is not None
            else None
        ),
        "edit_intent": _bounded_edit_intent(edit_intent),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def build_skill_structural_edit_intent(
    *,
    original_content: str,
    candidate_content: str,
    patch_intent: Mapping[str, Any],
) -> SkillStructuralEditIntent:
    """Bind framework-materialized patch actions to exact base/result sections."""

    original = inspect_skill_markdown(original_content).inventory
    candidate = inspect_skill_markdown(candidate_content).inventory
    operations = patch_intent.get("operations")
    if (
        original is None
        or candidate is None
        or not isinstance(operations, list)
        or not operations
    ):
        raise ValueError(
            "structural edit intent requires valid materialized skill markdown"
        )
    actions: list[SkillStructuralEditAction] = []
    consumed_result_indexes: set[int] = set()
    for operation in operations:
        if not isinstance(operation, Mapping):
            raise ValueError("structural edit operation must be an object")
        action = str(operation.get("op") or "")
        heading = operation.get("heading")
        if (
            action not in {"replace_section", "append_section"}
            or not isinstance(heading, str)
        ):
            raise ValueError("structural edit operation is invalid")
        title = _normalize_heading(heading.lstrip("#").strip())
        if action == "replace_section":
            base_section = next(
                (item for item in original.sections if item.title == title),
                None,
            )
            if base_section is None:
                raise ValueError("structural edit base section is missing")
            result_index, result_section = next(
                (
                    (index, item)
                    for index, item in enumerate(candidate.sections)
                    if index not in consumed_result_indexes
                    and item.path == base_section.path
                ),
                (None, None),
            )
            if result_section is None or result_index is None:
                raise ValueError("structural edit result section is missing")
            consumed_result_indexes.add(result_index)
            section_path = base_section.path
            base_section_fingerprint = base_section.content_fingerprint
        else:
            result_index, result_section = next(
                (
                    (index, item)
                    for index, item in reversed(
                        tuple(enumerate(candidate.sections))
                    )
                    if index not in consumed_result_indexes
                    and item.title == title
                ),
                (None, None),
            )
            if result_section is None or result_index is None:
                raise ValueError("appended structural edit section is missing")
            consumed_result_indexes.add(result_index)
            section_path = result_section.path
            base_section_fingerprint = None
        actions.append(
            SkillStructuralEditAction(
                action=action,
                section_path=section_path,
                base_section_fingerprint=base_section_fingerprint,
                result_section_fingerprint=(
                    result_section.content_fingerprint
                ),
            )
        )
    intent = SkillStructuralEditIntent(
        schema_version="aworld.skill.edit_intent.v2",
        authority="framework",
        authorization="",
        reason="candidate_protocol.patch_intent",
        base_content_fingerprint=_content_fingerprint(original_content),
        candidate_content_fingerprint=_content_fingerprint(
            candidate_content
        ),
        actions=tuple(actions),
    )
    return replace(
        intent,
        authorization=_edit_intent_authorization(intent),
    )


def rebind_skill_structural_edit_intent(
    edit_intent: SkillStructuralEditIntent,
    *,
    original_content: str,
    previous_candidate_content: str,
    candidate_content: str,
) -> SkillStructuralEditIntent:
    """Rebind an exact intent across a structure-preserving release transform."""

    authoritative = _authoritative_edit_intent(
        edit_intent,
        original_content=original_content,
        candidate_content=previous_candidate_content,
    )
    candidate_inventory = inspect_skill_markdown(
        candidate_content
    ).inventory
    if authoritative is None or candidate_inventory is None:
        raise ValueError("structural edit intent cannot be rebound")
    for action in authoritative.actions:
        if not any(
            section.path == action.section_path
            and section.content_fingerprint
            == action.result_section_fingerprint
            for section in candidate_inventory.sections
        ):
            raise ValueError(
                "release transform changed an authorized section"
            )
    rebound = SkillStructuralEditIntent(
        schema_version=authoritative.schema_version,
        authority=authoritative.authority,
        authorization="",
        reason=authoritative.reason,
        base_content_fingerprint=(
            authoritative.base_content_fingerprint
        ),
        candidate_content_fingerprint=_content_fingerprint(
            candidate_content
        ),
        actions=authoritative.actions,
    )
    return replace(
        rebound,
        authorization=_edit_intent_authorization(rebound),
    )


def _parse_front_matter(
    lines: Sequence[str],
) -> tuple[Mapping[str, Any] | None, int, _Inspection | None]:
    if not lines or lines[0].strip() != _FRONTMATTER_BOUNDARY:
        return (
            None,
            0,
            _Inspection(
                None,
                "skill_frontmatter_missing",
                "skill candidate must preserve YAML frontmatter",
                "frontmatter",
                line=1,
            ),
        )
    end_index = next(
        (
            index
            for index in range(1, len(lines))
            if lines[index].strip() == _FRONTMATTER_BOUNDARY
        ),
        None,
    )
    if end_index is None:
        return (
            None,
            0,
            _Inspection(
                None,
                "skill_frontmatter_unclosed",
                "skill candidate YAML frontmatter is unclosed",
                "frontmatter",
                line=1,
            ),
        )
    try:
        parsed = yaml.safe_load("\n".join(lines[1:end_index])) or {}
    except yaml.YAMLError:
        return (
            None,
            0,
            _Inspection(
                None,
                "skill_frontmatter_invalid",
                "skill candidate YAML frontmatter is invalid",
                "frontmatter",
                line=1,
            ),
        )
    if not isinstance(parsed, Mapping):
        return (
            None,
            0,
            _Inspection(
                None,
                "skill_frontmatter_invalid",
                "skill candidate YAML frontmatter must be a mapping",
                "frontmatter",
                line=1,
            ),
        )
    name = parsed.get("name")
    if not isinstance(name, str) or not name.strip():
        return (
            None,
            0,
            _Inspection(
                None,
                "skill_frontmatter_name_missing",
                "skill candidate YAML frontmatter requires a name",
                "frontmatter.name",
                line=1,
            ),
        )
    return dict(parsed), end_index + 1, None


def _is_fence_close(line: str, *, marker: str, minimum: int) -> bool:
    stripped = line.lstrip(" ")
    if len(line) - len(stripped) > 3:
        return False
    run_length = len(stripped) - len(stripped.lstrip(marker))
    return run_length >= minimum and not stripped[run_length:].strip()


def _placeholder_kind(
    line: str,
    *,
    in_fence: bool,
    fence_language: str,
) -> str | None:
    stripped = line.strip()
    if not stripped:
        return None
    if any(marker in stripped for marker in _INTERNAL_PLACEHOLDERS):
        return "internal_redaction_placeholder"
    if _TEXTUAL_OMISSION_RE.search(stripped):
        return "textual_omission"
    if _ARRAY_ELLIPSIS_RE.fullmatch(stripped):
        if (
            in_fence
            and fence_language in _PYTHON_FENCE_LANGUAGES
            and stripped == "..."
        ):
            return None
        return "standalone_ellipsis"
    if (
        in_fence
        and fence_language in _SHELL_FENCE_LANGUAGES
        and _SINGLE_TOKEN_OMISSION_RE.fullmatch(stripped)
    ):
        return "token_ellipsis"
    return None


def _normalize_heading(value: str) -> str:
    normalized = _NORMALIZE_TOKEN_RE.sub(
        " ",
        value.casefold().replace("`", ""),
    )
    return " ".join(normalized.split())[:160]


def _command_signature(
    value: str,
    *,
    require_command_signal: bool = False,
) -> str | None:
    stripped = value.strip()
    if not stripped or stripped.startswith("#"):
        return None
    stripped = re.sub(r"^[>$]\s*", "", stripped)
    if require_command_signal and not any(
        marker in stripped
        for marker in (" ", "--", "/", "./", "=", "|")
    ):
        return None
    try:
        tokens = re.findall(r"[^\s]+", stripped)
    except (TypeError, ValueError):
        return None
    if not tokens:
        return None
    executable = tokens[0].casefold()
    if executable in {"...", "and", "or", "then"}:
        return None
    semantic_tokens = [executable]
    for token in tokens[1:]:
        clean = token.strip(";,()[]{}").casefold()
        if not clean or clean.startswith("-") or "=" in clean:
            continue
        if clean in {"&&", "||", "|", "\\", "then", "do"}:
            continue
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,40}", clean):
            continue
        semantic_tokens.append(clean)
        if len(semantic_tokens) >= 2:
            break
    return " ".join(semantic_tokens)[:160]


def _normalize_structural_line(value: str) -> str:
    return " ".join(value.strip().casefold().split())[:2_000]


def _normalize_heading_line(value: str) -> str:
    stripped = value.strip().lstrip("#").strip()
    stripped = re.sub(r"\s+#+\s*$", "", stripped)
    return _normalize_structural_line(stripped)


def _normalize_command_line(value: str) -> str:
    stripped = re.sub(r"^[>$]\s*", "", value.strip())
    return _normalize_structural_line(stripped)


def _command_fingerprint(value: str) -> str:
    return _identity_fingerprint(_normalize_command_line(value))


def _ellipsis_occurrence(
    line: str,
    *,
    kind: str,
    line_index: int,
) -> _EllipsisOccurrence | None:
    value = (
        _normalize_heading_line(line)
        if kind == "heading"
        else _normalize_command_line(line)
        if kind == "command"
        else _normalize_structural_line(line)
    )
    positions = [
        index
        for marker in ("…", "...")
        if (index := value.find(marker)) >= 0
    ]
    if not positions:
        return None
    position = min(positions)
    marker_length = 1 if value[position] == "…" else 3
    if value[position + marker_length :].strip():
        return None
    if (
        kind == "prose"
        and value[:position].count("`") % 2 == 1
        and "`" in value[position + marker_length :]
    ):
        return None
    prefix = value[:position].rstrip(" `")
    if len(prefix) < 4:
        return None
    return _EllipsisOccurrence(
        kind=kind,
        prefix=prefix,
        line_index=line_index,
    )


def _deleted_base_prefix_truncation(
    original: _Inspection | None,
    candidate: _Inspection,
) -> _EllipsisOccurrence | None:
    if original is None:
        return None
    candidate_values = {
        (item.kind, item.value)
        for item in candidate.structural_lines
    }
    for occurrence in candidate.ellipsis_occurrences:
        for base_line in original.structural_lines:
            if base_line.kind != occurrence.kind:
                continue
            if not base_line.value.startswith(occurrence.prefix):
                continue
            if (
                base_line.value == occurrence.prefix
                and occurrence.kind != "command"
            ):
                continue
            if (base_line.kind, base_line.value) in candidate_values:
                continue
            return occurrence
    return None


def _counter_retention_ratio(
    original: Sequence[str],
    candidate: Sequence[str],
) -> float:
    original_counter = Counter(original)
    candidate_counter = Counter(candidate)
    retained = sum(
        min(count, candidate_counter.get(item, 0))
        for item, count in original_counter.items()
    )
    return retained / len(original) if original else 1.0


def _nontrivial_fence(fence: SkillFenceInventory) -> bool:
    return (
        len(fence.line_fingerprints) >= MIN_NONTRIVIAL_FENCE_LINES
        or fence.content_chars >= MIN_NONTRIVIAL_FENCE_CHARS
        or len(fence.command_fingerprints) >= 2
    )


def _missing_fence_anchor(
    original: SkillMarkdownInventory,
    candidate: SkillMarkdownInventory,
    *,
    declared_sections: set[tuple[str, ...]],
) -> SkillFenceInventory | None:
    candidates = list(candidate.fences)
    for fence in original.fences:
        if (
            not _nontrivial_fence(fence)
            or fence.section_path in declared_sections
        ):
            continue
        matched_index = next(
            (
                index
                for index, item in enumerate(candidates)
                if item.language == fence.language
                and _counter_retention_ratio(
                    fence.line_fingerprints,
                    item.line_fingerprints,
                )
                >= MIN_STRUCTURAL_ANCHOR_RETENTION_RATIO
                and (
                    not fence.command_fingerprints
                    or _counter_retention_ratio(
                        fence.command_fingerprints,
                        item.command_fingerprints,
                    )
                    >= MIN_STRUCTURAL_ANCHOR_RETENTION_RATIO
                )
            ),
            None,
        )
        if matched_index is None:
            return fence
        candidates.pop(matched_index)
    return None


def _missing_command_dense_section_anchor(
    original: SkillMarkdownInventory,
    candidate: SkillMarkdownInventory,
    *,
    declared_sections: set[tuple[str, ...]],
) -> SkillSectionInventory | None:
    candidates = list(candidate.sections)
    for section in original.sections:
        if (
            len(section.command_fingerprints)
            < MIN_COMMAND_DENSE_SECTION_COMMANDS
            or section.path in declared_sections
        ):
            continue
        matched_index = next(
            (
                index
                for index, item in enumerate(candidates)
                if _counter_retention_ratio(
                    section.command_fingerprints,
                    item.command_fingerprints,
                )
                >= MIN_STRUCTURAL_ANCHOR_RETENTION_RATIO
            ),
            None,
        )
        if matched_index is None:
            return section
        candidates.pop(matched_index)
    return None


def _declared_section_paths(
    edit_intent: SkillStructuralEditIntent | None,
    *,
    action: str,
) -> set[tuple[str, ...]]:
    if edit_intent is None:
        return set()
    return {
        item.section_path
        for item in edit_intent.actions
        if item.action == action
    }


def _section_declared(
    section: SkillSectionInventory,
    declared_paths: set[tuple[str, ...]],
) -> bool:
    return section.path in declared_paths


def _missing_section_anchors(
    original_sections: Sequence[SkillSectionInventory],
    candidate_sections: Sequence[SkillSectionInventory],
) -> tuple[SkillSectionInventory, ...]:
    candidates = [
        item
        for item in candidate_sections
        if item.level >= 2
    ]
    candidate_match: dict[int, int] = {}

    def matching_candidates(
        section: SkillSectionInventory,
    ) -> tuple[int, ...]:
        # An occurrence stays anchored either at its exact hierarchy path or
        # by carrying its exact content to a new path. Matching remains
        # one-to-one so duplicate headings and content preserve multiplicity.
        matches = [
            (
                0
                if candidate.path == section.path
                and candidate.anchor_fingerprint
                == section.anchor_fingerprint
                else 1
                if candidate.anchor_fingerprint
                == section.anchor_fingerprint
                else 2,
                index,
            )
            for index, candidate in enumerate(candidates)
            if candidate.path == section.path
            or candidate.anchor_fingerprint
            == section.anchor_fingerprint
        ]
        return tuple(
            index for _, index in sorted(matches)
        )

    def assign(
        original_index: int,
        visited_candidates: set[int],
    ) -> bool:
        section = original_sections[original_index]
        for candidate_index in matching_candidates(section):
            if candidate_index in visited_candidates:
                continue
            visited_candidates.add(candidate_index)
            previous_original = candidate_match.get(candidate_index)
            if previous_original is None or assign(
                previous_original,
                visited_candidates,
            ):
                candidate_match[candidate_index] = original_index
                return True
        return False

    missing: list[SkillSectionInventory] = []
    for original_index, section in enumerate(original_sections):
        if not assign(original_index, set()):
            missing.append(section)
    return tuple(missing)


def _section_content_preserved_after_rename(
    original: SkillSectionInventory,
    candidates: Sequence[SkillSectionInventory],
) -> bool:
    if original.command_fingerprints and any(
        _counter_retention_ratio(
            original.command_fingerprints,
            candidate.command_fingerprints,
        )
        >= MIN_STRUCTURAL_ANCHOR_RETENTION_RATIO
        for candidate in candidates
    ):
        return True
    original_commands = set(original.command_signatures)
    if original_commands and any(
        original_commands.issubset(set(candidate.command_signatures))
        for candidate in candidates
    ):
        return True
    original_atoms = set(original.substantive_atom_fingerprints)
    if original_atoms:
        for candidate in candidates:
            candidate_atoms = set(
                candidate.substantive_atom_fingerprints
            )
            union = original_atoms | candidate_atoms
            if union and len(original_atoms & candidate_atoms) / len(union) >= 0.45:
                return True
    return any(
        candidate.level == original.level
        and original.body_chars > 0
        and not original_atoms
        and 0.85
        <= candidate.body_chars / original.body_chars
        <= 1.15
        for candidate in candidates
    )


def _substantive_atom_fingerprints(
    lines: Sequence[str],
) -> tuple[str, ...]:
    atoms: list[str] = []
    paragraph: list[str] = []
    open_fence: tuple[str, int] | None = None

    def flush() -> None:
        if not paragraph:
            return
        normalized = " ".join(" ".join(paragraph).casefold().split())
        paragraph.clear()
        if len(normalized) < 32:
            return
        atoms.append(_identity_fingerprint(normalized))

    for line in lines:
        if open_fence is not None:
            marker, minimum = open_fence
            if _is_fence_close(line, marker=marker, minimum=minimum):
                open_fence = None
            continue
        fence_match = _FENCE_OPEN_RE.match(line)
        if fence_match is not None:
            flush()
            marker_text = fence_match.group(1)
            open_fence = (marker_text[0], len(marker_text))
            continue
        if _HEADING_RE.match(line):
            flush()
            continue
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        paragraph.append(stripped)
    flush()
    return tuple(dict.fromkeys(atoms))[:MAX_STRUCTURAL_SECTIONS]


def _authoritative_edit_intent(
    edit_intent: SkillStructuralEditIntent | None,
    *,
    original_content: str | None,
    candidate_content: str,
) -> SkillStructuralEditIntent | None:
    """Accept deletion/rewrite authority only from the framework patch boundary."""

    if (
        not isinstance(edit_intent, SkillStructuralEditIntent)
        or not isinstance(original_content, str)
        or edit_intent.schema_version
        != "aworld.skill.edit_intent.v2"
        or edit_intent.authority != "framework"
        or edit_intent.reason != "candidate_protocol.patch_intent"
        or edit_intent.base_content_fingerprint
        != _content_fingerprint(original_content)
        or edit_intent.candidate_content_fingerprint
        != _content_fingerprint(candidate_content)
        or edit_intent.authorization
        != _edit_intent_authorization(edit_intent)
        or not edit_intent.actions
        or len(edit_intent.actions) > 32
    ):
        return None
    original_inventory = inspect_skill_markdown(original_content).inventory
    candidate_inventory = inspect_skill_markdown(candidate_content).inventory
    if original_inventory is None or candidate_inventory is None:
        return None
    for action in edit_intent.actions:
        if (
            action.action not in {"replace_section", "append_section"}
            or not action.section_path
            or len(action.section_path) > 6
            or any(
                not isinstance(item, str)
                or not item
                or len(item) > 160
                for item in action.section_path
            )
        ):
            return None
        result_section = next(
            (
                item
                for item in candidate_inventory.sections
                if item.path == action.section_path
                and item.content_fingerprint
                == action.result_section_fingerprint
            ),
            None,
        )
        if result_section is None:
            return None
        if action.action == "replace_section":
            base_section = next(
                (
                    item
                    for item in original_inventory.sections
                    if item.path == action.section_path
                    and item.content_fingerprint
                    == action.base_section_fingerprint
                ),
                None,
            )
            if base_section is None:
                return None
        elif action.base_section_fingerprint is not None:
            return None
    return edit_intent


def _bounded_edit_intent(
    edit_intent: SkillStructuralEditIntent | None,
) -> Mapping[str, Any]:
    if edit_intent is None:
        return {"mode": "full_content", "actions": []}
    return {
        "mode": "patch_intent",
        "authorization": edit_intent.authorization,
        "actions": [
            {
                "action": item.action,
                "section_path": list(item.section_path),
                "base_section_fingerprint": (
                    item.base_section_fingerprint
                ),
                "result_section_fingerprint": (
                    item.result_section_fingerprint
                ),
            }
            for item in edit_intent.actions
        ],
    }


def _edit_intent_authorization(
    edit_intent: SkillStructuralEditIntent,
) -> str:
    payload = {
        "schema_version": edit_intent.schema_version,
        "authority": edit_intent.authority,
        "reason": edit_intent.reason,
        "base_content_fingerprint": (
            edit_intent.base_content_fingerprint
        ),
        "candidate_content_fingerprint": (
            edit_intent.candidate_content_fingerprint
        ),
        "actions": [
            {
                "action": item.action,
                "section_path": list(item.section_path),
                "base_section_fingerprint": (
                    item.base_section_fingerprint
                ),
                "result_section_fingerprint": (
                    item.result_section_fingerprint
                ),
            }
            for item in edit_intent.actions
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _edit_mode(
    edit_intent: SkillStructuralEditIntent | None,
) -> str:
    return "patch_intent" if edit_intent is not None else "full_content"


def _passed_validation(
    candidate: SkillMarkdownInventory,
    *,
    contract_fingerprint: str,
    edit_intent: SkillStructuralEditIntent | None,
    original_inventory: SkillMarkdownInventory | None = None,
    missing_section_count: int = 0,
) -> SkillStructureValidation:
    original_titles = (
        {item.title for item in original_inventory.sections}
        if original_inventory is not None
        else set()
    )
    candidate_titles = {item.title for item in candidate.sections}
    return SkillStructureValidation(
        passed=True,
        code="skill_markdown_valid",
        reason="skill candidate markdown structure is valid",
        field_path="content",
        contract_fingerprint=contract_fingerprint,
        details={
            "section_count": len(candidate.sections),
            "command_count": len(candidate.command_signatures),
            "added_section_count": len(candidate_titles - original_titles),
            "missing_section_count": missing_section_count,
            "edit_mode": _edit_mode(edit_intent),
        },
    )


def _failed_validation(
    code: str,
    reason: str,
    field_path: str,
    *,
    contract_fingerprint: str,
    details: Mapping[str, Any],
) -> SkillStructureValidation:
    return SkillStructureValidation(
        passed=False,
        code=code,
        reason=reason,
        field_path=field_path,
        contract_fingerprint=contract_fingerprint,
        details=dict(details),
    )


def _bounded_title(value: str) -> str:
    return value[:160]


def _identity_fingerprint(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _content_fingerprint(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()
