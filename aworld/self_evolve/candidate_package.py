from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath
from typing import Any, Iterable

from aworld.self_evolve.candidate_errors import (
    CandidateFailureField,
    CandidateMaterializationCode,
    CandidateMaterializationError,
)
from aworld.self_evolve.types import CandidateFileDelta, CandidateVariant


MAX_CANDIDATE_FILE_COUNT = 32
MAX_CANDIDATE_FILE_BYTES = 256 * 1024
MAX_CANDIDATE_PACKAGE_BYTES = 1024 * 1024
_OPERATIONS = frozenset({"upsert", "delete"})


def validate_candidate_files(
    files: Iterable[CandidateFileDelta],
) -> tuple[CandidateFileDelta, ...]:
    normalized: list[CandidateFileDelta] = []
    seen: set[str] = set()
    total_bytes = 0
    for item in files:
        path = _normalized_replay_path(item.path)
        if path in seen:
            raise CandidateMaterializationError(
                CandidateMaterializationCode.FILE_PATH_DUPLICATE,
                f"duplicate candidate file path: {path}",
                field_path=CandidateFailureField.FILE_PATH,
            )
        seen.add(path)
        operation = str(item.operation or "upsert").strip().lower()
        if operation not in _OPERATIONS:
            raise CandidateMaterializationError(
                CandidateMaterializationCode.FILE_OPERATION_INVALID,
                f"unsupported candidate file operation: {operation}",
                field_path=CandidateFailureField.FILE_OPERATION,
            )
        if operation == "upsert":
            if not isinstance(item.content, str):
                raise CandidateMaterializationError(
                    CandidateMaterializationCode.FILE_CONTENT_REQUIRED,
                    f"candidate file upsert requires text content: {path}",
                    field_path=CandidateFailureField.FILE_CONTENT,
                )
            size = len(item.content.encode("utf-8"))
            if size > MAX_CANDIDATE_FILE_BYTES:
                raise CandidateMaterializationError(
                    CandidateMaterializationCode.FILE_CONTENT_TOO_LARGE,
                    f"candidate file exceeds byte limit: {path}",
                    field_path=CandidateFailureField.FILE_CONTENT,
                )
            total_bytes += size
        else:
            if item.content is not None:
                raise CandidateMaterializationError(
                    CandidateMaterializationCode.FILE_DELETE_CONTENT_INVALID,
                    f"candidate file delete cannot include content: {path}",
                    field_path=CandidateFailureField.FILE_CONTENT,
                )
            if item.executable:
                raise CandidateMaterializationError(
                    CandidateMaterializationCode.FILE_DELETE_EXECUTABLE_INVALID,
                    f"candidate file delete cannot be executable: {path}",
                    field_path=CandidateFailureField.FILE_EXECUTABLE,
                )
        normalized.append(
            CandidateFileDelta(
                path=path,
                operation=operation,
                content=item.content,
                executable=bool(item.executable),
            )
        )
    if len(normalized) > MAX_CANDIDATE_FILE_COUNT:
        raise CandidateMaterializationError(
            CandidateMaterializationCode.FILE_COUNT_EXCEEDED,
            "candidate file count exceeds limit",
            field_path=CandidateFailureField.FILES,
        )
    if total_bytes > MAX_CANDIDATE_PACKAGE_BYTES:
        raise CandidateMaterializationError(
            CandidateMaterializationCode.PACKAGE_BYTES_EXCEEDED,
            "candidate package exceeds byte limit",
            field_path=CandidateFailureField.FILES,
        )
    return tuple(sorted(normalized, key=lambda item: item.path))


def candidate_package_payload(candidate: CandidateVariant) -> dict[str, Any]:
    files = validate_candidate_files(candidate.files)
    return {
        "target": {
            "target_type": candidate.target.target_type,
            "target_id": candidate.target.target_id,
            "path": candidate.target.path,
        },
        "content": candidate.content,
        "files": [
            {
                "path": item.path,
                "operation": item.operation,
                "content": item.content,
                "executable": item.executable,
            }
            for item in files
        ],
    }


def candidate_package_fingerprint(candidate: CandidateVariant) -> str:
    payload = candidate_package_payload(candidate)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def candidate_content_semantic_fingerprint(content: str) -> str:
    """Return the normalized semantic identity of candidate target content."""

    semantic_lines = [
        re.sub(r"\s+", " ", line.strip().casefold())
        for line in content.splitlines()
        if line.strip() and line.strip() != "---"
    ]
    return "sha256:" + hashlib.sha256(
        "\n".join(semantic_lines).encode("utf-8")
    ).hexdigest()


def candidate_semantic_package_fingerprint(
    candidate: CandidateVariant,
    *,
    content_semantic_fingerprint: str | None = None,
) -> str:
    """Fingerprint target semantics together with every candidate-owned file.

    Target markdown keeps the historical whitespace/case normalization. Candidate
    files preserve internal bytes and casing, but normalize line endings and
    terminal blank lines because those cannot constitute a material repair branch.
    This prevents formatting-only retries from consuming a repair frontier while
    retaining executable and schema changes as distinct packages.
    """

    files = validate_candidate_files(candidate.files)
    payload = {
        "schema_version": "aworld.self_evolve.candidate_semantic_package.v1",
        "target": {
            "target_type": candidate.target.target_type,
            "target_id": candidate.target.target_id,
        },
        "content_semantic_fingerprint": (
            content_semantic_fingerprint
            or candidate_content_semantic_fingerprint(candidate.content)
        ),
        "files": [
            {
                "path": item.path,
                "operation": item.operation,
                "content_fingerprint": (
                    "sha256:"
                    + hashlib.sha256(
                        _semantic_candidate_file_content(item.content).encode(
                            "utf-8"
                        )
                    ).hexdigest()
                    if item.content is not None
                    else None
                ),
                "executable": item.executable,
            }
            for item in files
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _semantic_candidate_file_content(content: str) -> str:
    """Normalize transport-only text differences without rewriting source."""

    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def candidate_files_total_bytes(files: Iterable[CandidateFileDelta]) -> int:
    return sum(
        len(item.content.encode("utf-8"))
        for item in validate_candidate_files(files)
        if item.operation == "upsert" and item.content is not None
    )


def _normalized_replay_path(raw_path: str) -> str:
    value = str(raw_path or "").strip()
    if not value or "\\" in value:
        raise CandidateMaterializationError(
            CandidateMaterializationCode.FILE_PATH_INVALID,
            "candidate file path must be inside replay/",
            field_path=CandidateFailureField.FILE_PATH,
        )
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise CandidateMaterializationError(
            CandidateMaterializationCode.FILE_PATH_INVALID,
            "candidate file path must be inside replay/",
            field_path=CandidateFailureField.FILE_PATH,
        )
    if not path.parts or path.parts[0] != "replay" or len(path.parts) < 2:
        raise CandidateMaterializationError(
            CandidateMaterializationCode.FILE_PATH_INVALID,
            "candidate file path must be inside replay/",
            field_path=CandidateFailureField.FILE_PATH,
        )
    return path.as_posix()
