from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

import json_repair

from aworld.self_evolve.candidate_package import validate_candidate_files
from aworld.self_evolve.patch_intent import apply_skill_patch_intent
from aworld.self_evolve.types import CandidateFileDelta


CANDIDATE_SCHEMA_VERSION = "aworld.self_evolve.candidate.v1"
MAX_CANDIDATE_RESPONSE_CHARS = 2_000_000
MAX_SURROUNDING_PROSE_CHARS = 4_000
MAX_RATIONALE_CHARS = 8_000
LARGE_TARGET_CONTENT_CHARS = 16_000
MIN_FULL_REPLACEMENT_RETENTION_RATIO = 0.8

CANDIDATE_OUTPUT_CONTRACT: Mapping[str, object] = {
    "schema_version": CANDIDATE_SCHEMA_VERSION,
    "content": (
        "optional complete primary target content for a deliberate full rewrite; "
        "when the current content starts with YAML frontmatter, preserve that "
        "frontmatter; do not use for a small delta to a large existing target; "
        "may be omitted with patch_intent, or when files themselves implement "
        "the reusable skill-package behavior delta"
    ),
    "patch_intent": {
        "preferred_when": (
            "preserving a large existing target while adding or replacing bounded "
            "Markdown sections"
        ),
        "operations": [
            {
                "op": "replace_section or append_section",
                "heading": "existing or new Markdown heading",
                "content": "replacement section body",
            }
        ]
    },
    "rationale": "bounded explanation of the reusable behavior delta",
    "files": [
        {
            "path": "replay/<relative-path>",
            "operation": "upsert or delete",
            "content": "UTF-8 text for upsert",
            "executable": False,
        }
    ],
}

_CANDIDATE_FIELDS = frozenset(
    {"schema_version", "content", "patch_intent", "rationale", "files"}
)
_JSON_FENCE = re.compile(
    r"```(?:json)?\s*(.*?)\s*```",
    flags=re.IGNORECASE | re.DOTALL,
)
_YAML_FRONTMATTER = re.compile(r"^---\s*\n.*?\n---\s*\n", flags=re.DOTALL)


class CandidateProtocolError(ValueError):
    """Bounded, attributable validation failure for one model candidate."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        field_path: str | None = None,
        repairable: bool = True,
    ) -> None:
        self.code = str(code)
        self.field_path = field_path
        self.repairable = bool(repairable)
        super().__init__(str(message)[:512])

    def to_diagnostic(self) -> dict[str, object]:
        diagnostic: dict[str, object] = {
            "code": self.code,
            "stage": "candidate_protocol",
            "failure_class": "candidate",
            "repairable": self.repairable,
        }
        if self.field_path is not None:
            diagnostic["field_path"] = self.field_path
        return diagnostic


def normalize_candidate_output(
    raw_output: Any,
    *,
    current_content: str,
) -> dict[str, Any]:
    """Normalize safe response variants into the canonical candidate protocol."""

    payload = _decode_single_json_object(raw_output)
    envelope = payload.get("candidate_output_contract")
    if envelope is not None:
        if not isinstance(envelope, Mapping):
            raise CandidateProtocolError(
                "invalid_candidate_envelope",
                "candidate_output_contract must be an object",
                field_path="candidate_output_contract",
            )
        direct = {
            key: payload[key]
            for key in _CANDIDATE_FIELDS
            if key in payload
        }
        for key, value in direct.items():
            if key in envelope and envelope[key] != value:
                raise CandidateProtocolError(
                    "conflicting_candidate_envelope",
                    "direct and envelope candidate fields conflict",
                    field_path=key,
                    repairable=False,
                )
        payload = {**dict(envelope), **direct}
    return _validate_candidate_payload(payload, current_content=current_content)


def merge_candidate_repair_output(
    invalid_output: str,
    repaired_output: str,
    error: ValueError,
) -> str:
    """Merge representation-only repair fields without dropping valid package files.

    A large initial package may fail only because its primary body is absent or
    malformed. The repair Task should not have to reproduce every already-valid
    candidate-owned file through a bounded repair prompt. Semantic validation still
    runs on the merged object immediately afterwards.
    """

    del error
    initial = _candidate_payload_fields(_decode_single_json_object(invalid_output))
    repaired = _candidate_payload_fields(_decode_single_json_object(repaired_output))
    merged = {**initial, **repaired}
    initial_files = initial.get("files")
    repaired_files = repaired.get("files")
    if (
        isinstance(initial_files, list)
        and initial_files
        and (not isinstance(repaired_files, list) or not repaired_files)
    ):
        merged["files"] = initial_files
    if not repaired.get("rationale") and isinstance(initial.get("rationale"), str):
        merged["rationale"] = initial["rationale"]
    return json.dumps(merged, ensure_ascii=False, separators=(",", ":"))


def _candidate_payload_fields(payload: Mapping[str, Any]) -> dict[str, Any]:
    envelope = payload.get("candidate_output_contract")
    if not isinstance(envelope, Mapping):
        return {
            key: payload[key]
            for key in _CANDIDATE_FIELDS
            if key in payload
        }
    fields = {
        key: envelope[key]
        for key in _CANDIDATE_FIELDS
        if key in envelope
    }
    fields.update(
        {
            key: payload[key]
            for key in _CANDIDATE_FIELDS
            if key in payload
        }
    )
    return fields


def _decode_single_json_object(raw_output: Any) -> dict[str, Any]:
    if isinstance(raw_output, Mapping):
        return dict(raw_output)
    if not isinstance(raw_output, str):
        raise CandidateProtocolError(
            "invalid_candidate_response_type",
            "candidate response must be text or an object",
        )
    text = raw_output.strip()
    if not text:
        raise CandidateProtocolError(
            "empty_candidate_response",
            "candidate response must not be empty",
        )
    if len(text) > MAX_CANDIDATE_RESPONSE_CHARS:
        raise CandidateProtocolError(
            "candidate_response_too_large",
            "candidate response exceeds the protocol size limit",
            repairable=False,
        )
    fenced = _JSON_FENCE.fullmatch(text)
    if fenced is not None:
        text = fenced.group(1).strip()

    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        if text.startswith("{"):
            if _has_multiple_top_level_objects(text):
                raise CandidateProtocolError(
                    "multiple_json_objects",
                    "candidate response must contain exactly one JSON object",
                    repairable=False,
                )
            try:
                decoded = json_repair.repair_json(text, return_objects=True)
            except Exception:
                decoded = _extract_one_surrounded_object(text)
        else:
            decoded = _extract_one_surrounded_object(text)
    if not isinstance(decoded, Mapping):
        raise CandidateProtocolError(
            "candidate_response_not_object",
            "candidate response must contain one JSON object",
        )
    return dict(decoded)


def _has_multiple_top_level_objects(text: str) -> bool:
    """Detect concatenated objects without mistaking nested candidate fields."""

    depth = 0
    completed = 0
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                completed += 1
                if completed > 1:
                    return True
    return False


def _extract_one_surrounded_object(text: str) -> Mapping[str, Any]:
    decoder = json.JSONDecoder()
    matches: list[tuple[int, int, Mapping[str, Any]]] = []
    cursor = 0
    while cursor < len(text):
        start = text.find("{", cursor)
        if start < 0:
            break
        try:
            value, relative_end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            cursor = start + 1
            continue
        end = start + relative_end
        if isinstance(value, Mapping):
            matches.append((start, end, value))
            cursor = end
        else:
            cursor = start + 1

    if not matches:
        raise CandidateProtocolError(
            "invalid_candidate_json",
            "candidate response does not contain a valid JSON object",
        )
    if len(matches) != 1:
        raise CandidateProtocolError(
            "multiple_json_objects",
            "candidate response must contain exactly one JSON object",
            repairable=False,
        )
    start, end, value = matches[0]
    prefix = text[:start].strip()
    suffix = text[end:].strip()
    if (
        len(prefix) > MAX_SURROUNDING_PROSE_CHARS
        or len(suffix) > MAX_SURROUNDING_PROSE_CHARS
    ):
        raise CandidateProtocolError(
            "candidate_surrounding_text_too_large",
            "text surrounding the candidate JSON exceeds the protocol limit",
        )
    return value


def _validate_candidate_payload(
    payload: Mapping[str, Any],
    *,
    current_content: str,
) -> dict[str, Any]:
    schema_version = payload.get("schema_version", CANDIDATE_SCHEMA_VERSION)
    if schema_version != CANDIDATE_SCHEMA_VERSION:
        raise CandidateProtocolError(
            "unsupported_candidate_schema",
            "candidate schema_version is unsupported",
            field_path="schema_version",
            repairable=False,
        )

    content = payload.get("content")
    patch_intent = payload.get("patch_intent")
    raw_files_for_body = payload.get("files", [])
    has_package_file_delta = (
        isinstance(raw_files_for_body, list)
        and any(isinstance(item, Mapping) for item in raw_files_for_body)
    )
    has_content = isinstance(content, str)
    has_patch_intent = isinstance(patch_intent, Mapping)
    if has_content and has_patch_intent:
        raise CandidateProtocolError(
            "ambiguous_candidate_body",
            "candidate must use exactly one of content or patch_intent",
            field_path="content|patch_intent",
        )
    if not has_content and not has_patch_intent and not has_package_file_delta:
        raise CandidateProtocolError(
            "missing_candidate_body",
            "candidate requires content, patch_intent, or package file deltas",
            field_path="content|patch_intent|files",
        )
    if has_content and not content.strip():
        raise CandidateProtocolError(
            "empty_candidate_content",
            "candidate content must not be empty",
            field_path="content",
        )
    if (
        has_content
        and _YAML_FRONTMATTER.match(current_content)
        and not _YAML_FRONTMATTER.match(content)
    ):
        raise CandidateProtocolError(
            "missing_candidate_frontmatter",
            "complete skill content must preserve YAML frontmatter",
            field_path="content",
            repairable=False,
        )
    if (
        has_content
        and len(current_content) >= LARGE_TARGET_CONTENT_CHARS
        and len(content)
        < int(len(current_content) * MIN_FULL_REPLACEMENT_RETENTION_RATIO)
    ):
        raise CandidateProtocolError(
            "destructive_full_content_replacement",
            "large existing targets require patch_intent for a bounded delta",
            field_path="content",
        )
    if has_patch_intent:
        try:
            apply_skill_patch_intent(current_content, patch_intent)
        except ValueError as exc:
            raise CandidateProtocolError(
                "invalid_patch_intent",
                str(exc),
                field_path="patch_intent",
            ) from exc

    rationale = payload.get("rationale", "")
    if not isinstance(rationale, str):
        raise CandidateProtocolError(
            "invalid_candidate_rationale",
            "candidate rationale must be text",
            field_path="rationale",
        )
    if len(rationale) > MAX_RATIONALE_CHARS:
        raise CandidateProtocolError(
            "candidate_rationale_too_large",
            "candidate rationale exceeds the protocol limit",
            field_path="rationale",
        )

    raw_files = payload.get("files", [])
    if not isinstance(raw_files, list) or any(
        not isinstance(item, Mapping) for item in raw_files
    ):
        raise CandidateProtocolError(
            "invalid_candidate_files",
            "candidate files must be an array of objects",
            field_path="files",
        )
    file_deltas: list[CandidateFileDelta] = []
    for index, item in enumerate(raw_files):
        raw_content = item.get("content")
        if raw_content is not None and not isinstance(raw_content, str):
            raise CandidateProtocolError(
                "invalid_candidate_file_content",
                "candidate file content must be text or null",
                field_path=f"files[{index}].content",
            )
        file_deltas.append(
            CandidateFileDelta(
                path=str(item.get("path") or ""),
                operation=str(item.get("operation") or "upsert"),
                content=raw_content,
                executable=bool(item.get("executable", False)),
            )
        )
    try:
        normalized_files = validate_candidate_files(file_deltas)
    except ValueError as exc:
        raise CandidateProtocolError(
            "invalid_candidate_files",
            str(exc),
            field_path="files",
        ) from exc

    normalized: dict[str, Any] = {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "rationale": rationale,
        "files": [
            {
                "path": item.path,
                "operation": item.operation,
                "content": item.content,
                "executable": item.executable,
            }
            for item in normalized_files
        ],
    }
    if has_content:
        normalized["content"] = content
    elif has_patch_intent:
        normalized["patch_intent"] = dict(patch_intent)
    return normalized
