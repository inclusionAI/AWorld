from __future__ import annotations

import re
from enum import Enum
from typing import Any


class CandidateMaterializationCode(str, Enum):
    """Stable, bounded candidate materialization failure taxonomy."""

    INVALID = "candidate_materialization_invalid"
    UNEXPOSED_IMPROVEMENT_SIGNAL_IDS = "unexposed_improvement_signal_ids"
    CONTENT_REQUIRED = "candidate_content_required"
    CONTENT_TOO_LARGE = "candidate_content_too_large"
    PATCH_OPERATIONS_INVALID = "patch_operations_invalid"
    PATCH_OPERATION_INVALID = "patch_operation_invalid"
    PATCH_OPERATION_KIND_INVALID = "patch_operation_kind_invalid"
    PATCH_HEADING_INVALID = "patch_heading_invalid"
    PATCH_CONTENT_INVALID = "patch_content_invalid"
    PATCH_CONTENT_PROTECTED_REFERENCE = "patch_content_protected_reference"
    PATCH_SECTION_NOT_FOUND = "patch_section_not_found"
    FILES_TYPE_INVALID = "candidate_files_type_invalid"
    FILE_PATH_INVALID = "candidate_file_path_invalid"
    FILE_PATH_DUPLICATE = "candidate_file_path_duplicate"
    FILE_OPERATION_INVALID = "candidate_file_operation_invalid"
    FILE_CONTENT_REQUIRED = "candidate_file_content_required"
    FILE_CONTENT_TOO_LARGE = "candidate_file_content_too_large"
    FILE_DELETE_CONTENT_INVALID = "candidate_file_delete_content_invalid"
    FILE_DELETE_EXECUTABLE_INVALID = "candidate_file_delete_executable_invalid"
    FILE_COUNT_EXCEEDED = "candidate_file_count_exceeded"
    PACKAGE_BYTES_EXCEEDED = "candidate_package_bytes_exceeded"
    FILES_ONLY_DELTA_REQUIRED = "candidate_files_only_delta_required"


class CandidateFailureField(str, Enum):
    """Allowed normalized fields used in repair-frontier identity."""

    CANDIDATE = "candidate"
    CONTENT = "content"
    PATCH_INTENT = "patch_intent"
    PATCH_OPERATIONS = "patch_intent.operations"
    PATCH_OPERATION = "patch_intent.operations[]"
    PATCH_OPERATION_KIND = "patch_intent.operations[].op"
    PATCH_HEADING = "patch_intent.operations[].heading"
    PATCH_CONTENT = "patch_intent.operations[].content"
    FILES = "files"
    FILE_PATH = "files[].path"
    FILE_OPERATION = "files[].operation"
    FILE_CONTENT = "files[].content"
    FILE_EXECUTABLE = "files[].executable"
    IMPROVEMENT_SIGNAL_IDS = "addressed_improvement_signal_ids"


class CandidateRepresentation(str, Enum):
    """Allowed candidate representations used in repair-frontier identity."""

    CANDIDATE_PACKAGE = "candidate_package"
    FULL_CONTENT = "full_content"
    PATCH_INTENT = "patch_intent"
    FILES_ONLY = "files_only"


class CandidateMaterializationError(ValueError):
    """Typed source error whose prose is audit data, never failure identity."""

    def __init__(
        self,
        code: CandidateMaterializationCode,
        message: str,
        *,
        field_path: CandidateFailureField,
    ) -> None:
        self.code = CandidateMaterializationCode(code)
        self.field_path = CandidateFailureField(field_path)
        super().__init__(str(message)[:512])


_ARRAY_INDEX_RE = re.compile(r"\[(?:\d+|\*)\]")
_CONTRACT_FINGERPRINT_RE = re.compile(r"sha256:[0-9a-f]{64}")


def normalize_candidate_failure_field(value: Any) -> CandidateFailureField:
    """Map only recognized field paths into the bounded identity vocabulary."""

    normalized = _ARRAY_INDEX_RE.sub("[]", str(value or "").strip())
    try:
        return CandidateFailureField(normalized)
    except ValueError:
        return CandidateFailureField.CANDIDATE


def normalize_candidate_representation(value: Any) -> CandidateRepresentation:
    """Map only recognized representations into the bounded identity vocabulary."""

    try:
        return CandidateRepresentation(str(value or "").strip())
    except ValueError:
        return CandidateRepresentation.CANDIDATE_PACKAGE


def normalize_candidate_materialization_code(
    value: Any,
) -> CandidateMaterializationCode:
    """Map only framework-defined failure codes into frontier identity."""

    try:
        return CandidateMaterializationCode(str(value or "").strip())
    except ValueError:
        return CandidateMaterializationCode.INVALID


def normalize_candidate_contract_fingerprint(value: Any) -> str | None:
    """Accept only canonical contract digests as semantic identity."""

    normalized = str(value or "").strip()
    return (
        normalized
        if _CONTRACT_FINGERPRINT_RE.fullmatch(normalized)
        else None
    )


def candidate_materialization_requirement_id(
    *,
    representation: Any,
    field_path: Any,
) -> str:
    """Return a stable identity from controlled enums, excluding error prose."""

    normalized_representation = normalize_candidate_representation(representation)
    normalized_field = normalize_candidate_failure_field(field_path)
    return (
        "candidate-materialization/"
        f"{normalized_representation.value}/{normalized_field.value}"
    )
