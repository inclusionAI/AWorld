from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from aworld.self_evolve.candidate_package import validate_candidate_files
from aworld.self_evolve.patch_intent import apply_skill_patch_intent
from aworld.self_evolve.types import CandidateFileDelta


CANDIDATE_SCHEMA_VERSION = "aworld.self_evolve.candidate.v1"
MAX_CANDIDATE_RESPONSE_CHARS = 2_000_000
MAX_SURROUNDING_PROSE_CHARS = 4_000
MAX_RATIONALE_CHARS = 8_000

CANDIDATE_OUTPUT_CONTRACT: Mapping[str, object] = {
    "schema_version": CANDIDATE_SCHEMA_VERSION,
    "content": "optional complete primary target content",
    "patch_intent": {
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
        decoded = _extract_one_surrounded_object(text)
    if not isinstance(decoded, Mapping):
        raise CandidateProtocolError(
            "candidate_response_not_object",
            "candidate response must contain one JSON object",
        )
    return dict(decoded)


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
    has_content = isinstance(content, str)
    has_patch_intent = isinstance(patch_intent, Mapping)
    if has_content and has_patch_intent:
        raise CandidateProtocolError(
            "ambiguous_candidate_body",
            "candidate must use exactly one of content or patch_intent",
            field_path="content|patch_intent",
        )
    if not has_content and not has_patch_intent:
        raise CandidateProtocolError(
            "missing_candidate_body",
            "candidate requires content or patch_intent",
            field_path="content|patch_intent",
        )
    if has_content and not content.strip():
        raise CandidateProtocolError(
            "empty_candidate_content",
            "candidate content must not be empty",
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
    else:
        normalized["patch_intent"] = dict(patch_intent)
    return normalized
