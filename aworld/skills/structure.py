from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import yaml


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
    command_signatures: tuple[str, ...]
    substantive_atom_fingerprints: tuple[str, ...]


@dataclass(frozen=True)
class SkillMarkdownInventory:
    front_matter: Mapping[str, Any]
    sections: tuple[SkillSectionInventory, ...]
    command_signatures: tuple[str, ...]
    fence_languages: tuple[str, ...]
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
class _Inspection:
    inventory: SkillMarkdownInventory | None
    code: str | None = None
    reason: str | None = None
    field_path: str | None = None
    line: int | None = None


def validate_skill_markdown_structure(
    candidate_content: str,
    *,
    original_content: str | None = None,
    edit_intent: Mapping[str, Any] | None = None,
) -> SkillStructureValidation:
    """Validate standalone Markdown and bounded preservation of published structure."""

    authoritative_edit_intent = _authoritative_edit_intent(
        edit_intent,
        original_content=original_content,
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

    declared_rewrites = _declared_section_titles(
        authoritative_edit_intent,
        "rewritten_sections",
    )
    declared_removals = _declared_section_titles(
        authoritative_edit_intent,
        "removed_sections",
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

    for section in original_inventory.sections:
        if (
            section.level < 2
            or section.body_chars < MIN_PROTECTED_SECTION_CHARS
            or _section_declared(section, declared_rewrites | declared_removals)
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
        and not _section_declared(section, declared_removals)
        and not _section_declared(section, declared_rewrites)
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
        len(missing_sections) > allowed_missing_count
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
    commands_by_heading: dict[int, list[str]] = {}
    open_fence: tuple[str, int, str, int] | None = None
    for line_index, line in enumerate(lines[body_start:], start=body_start):
        if open_fence is not None:
            marker, marker_length, language, opening_line = open_fence
            if _is_fence_close(line, marker=marker, minimum=marker_length):
                open_fence = None
                continue
            placeholder = _placeholder_kind(
                line,
                in_fence=True,
                fence_language=language,
                is_heading=False,
            )
            if placeholder is not None:
                return _Inspection(
                    None,
                    "skill_truncation_marker",
                    "skill candidate contains an obvious truncation placeholder",
                    "content",
                    line=line_index + 1,
                )
            if language in _SHELL_FENCE_LANGUAGES:
                signature = _command_signature(line)
                if signature is not None:
                    commands.append(signature)
                    if headings:
                        commands_by_heading.setdefault(
                            headings[-1].line_index,
                            [],
                        ).append(signature)
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
            )
            continue

        heading_match = _HEADING_RE.match(line)
        is_heading = heading_match is not None
        placeholder = _placeholder_kind(
            line,
            in_fence=False,
            fence_language="",
            is_heading=is_heading,
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
            continue
        for inline in _INLINE_CODE_RE.findall(line):
            signature = _command_signature(inline, require_command_signal=True)
            if signature is not None:
                commands.append(signature)
                if headings:
                    commands_by_heading.setdefault(
                        headings[-1].line_index,
                        [],
                    ).append(signature)

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
            dict.fromkeys(commands_by_heading.get(heading.line_index, ()))
        )[:MAX_STRUCTURAL_COMMANDS]
        section_atoms = _substantive_atom_fingerprints(section_lines)
        sections.append(
            SkillSectionInventory(
                title=heading.title,
                path=heading.path,
                level=heading.level,
                body_chars=body_chars,
                command_signatures=section_commands,
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
        substantive_atom_fingerprints=_substantive_atom_fingerprints(
            lines[body_start:]
        ),
        body_chars=len("\n".join(lines[body_start:])),
    )
    return _Inspection(inventory)


def skill_structure_contract_fingerprint(
    original_inventory: SkillMarkdownInventory | None,
    *,
    edit_intent: Mapping[str, Any] | None = None,
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
                        "commands": list(item.command_signatures),
                        "substantive_atoms": list(
                            item.substantive_atom_fingerprints
                        ),
                    }
                    for item in original_inventory.sections
                ],
                "commands": list(original_inventory.command_signatures),
                "fences": list(original_inventory.fence_languages),
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
    is_heading: bool,
) -> str | None:
    stripped = line.strip()
    if not stripped:
        return None
    if any(marker in stripped for marker in _INTERNAL_PLACEHOLDERS):
        return "internal_redaction_placeholder"
    if "…" in stripped:
        return "unicode_ellipsis"
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
    if is_heading and stripped.endswith("..."):
        return "heading_ellipsis"
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


def _declared_section_titles(
    edit_intent: Mapping[str, Any] | None,
    key: str,
) -> set[str]:
    if not isinstance(edit_intent, Mapping):
        return set()
    raw = edit_intent.get(key)
    if not isinstance(raw, (list, tuple)):
        return set()
    return {
        normalized
        for item in raw[:32]
        if isinstance(item, str)
        and (normalized := _normalize_heading(item))
    }


def _section_declared(
    section: SkillSectionInventory,
    declared_titles: set[str],
) -> bool:
    return bool(declared_titles.intersection(section.path))


def _section_content_preserved_after_rename(
    original: SkillSectionInventory,
    candidates: Sequence[SkillSectionInventory],
) -> bool:
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
    edit_intent: Mapping[str, Any] | None,
    *,
    original_content: str | None,
) -> Mapping[str, Any] | None:
    """Accept deletion/rewrite authority only from the framework patch boundary."""

    if (
        not isinstance(edit_intent, Mapping)
        or not isinstance(original_content, str)
        or edit_intent.get("schema_version")
        != "aworld.skill.edit_intent.v1"
        or edit_intent.get("mode") != "patch_intent"
        or edit_intent.get("framework_anchor")
        != "candidate_protocol.patch_intent"
        or edit_intent.get("base_content_fingerprint")
        != _content_fingerprint(original_content)
    ):
        return None
    for key in (
        "rewritten_sections",
        "removed_sections",
        "added_sections",
    ):
        value = edit_intent.get(key, ())
        if (
            not isinstance(value, (list, tuple))
            or len(value) > 32
            or any(
                not isinstance(item, str)
                or not item.strip()
                or len(item) > 240
                for item in value
            )
        ):
            return None
    return {
        "schema_version": "aworld.skill.edit_intent.v1",
        "mode": "patch_intent",
        "base_content_fingerprint": _content_fingerprint(
            original_content
        ),
        "framework_anchor": "candidate_protocol.patch_intent",
        "rewritten_sections": list(
            edit_intent.get("rewritten_sections", ())
        ),
        "removed_sections": list(
            edit_intent.get("removed_sections", ())
        ),
        "added_sections": list(
            edit_intent.get("added_sections", ())
        ),
    }


def _bounded_edit_intent(
    edit_intent: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    return {
        "mode": _edit_mode(edit_intent),
        "rewritten_sections": sorted(
            _declared_section_titles(edit_intent, "rewritten_sections")
        ),
        "removed_sections": sorted(
            _declared_section_titles(edit_intent, "removed_sections")
        ),
        "added_sections": sorted(
            _declared_section_titles(edit_intent, "added_sections")
        ),
    }


def _edit_mode(edit_intent: Mapping[str, Any] | None) -> str:
    if not isinstance(edit_intent, Mapping):
        return "full_content"
    mode = str(edit_intent.get("mode") or "").strip()
    return mode if mode in {"full_content", "patch_intent"} else "full_content"


def _passed_validation(
    candidate: SkillMarkdownInventory,
    *,
    contract_fingerprint: str,
    edit_intent: Mapping[str, Any] | None,
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
