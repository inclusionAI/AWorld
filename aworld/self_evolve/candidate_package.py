from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Any, Iterable

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
            raise ValueError(f"duplicate candidate file path: {path}")
        seen.add(path)
        operation = str(item.operation or "upsert").strip().lower()
        if operation not in _OPERATIONS:
            raise ValueError(f"unsupported candidate file operation: {operation}")
        if operation == "upsert":
            if not isinstance(item.content, str):
                raise ValueError(f"candidate file upsert requires text content: {path}")
            size = len(item.content.encode("utf-8"))
            if size > MAX_CANDIDATE_FILE_BYTES:
                raise ValueError(f"candidate file exceeds byte limit: {path}")
            total_bytes += size
        else:
            if item.content is not None:
                raise ValueError(f"candidate file delete cannot include content: {path}")
            if item.executable:
                raise ValueError(f"candidate file delete cannot be executable: {path}")
        normalized.append(
            CandidateFileDelta(
                path=path,
                operation=operation,
                content=item.content,
                executable=bool(item.executable),
            )
        )
    if len(normalized) > MAX_CANDIDATE_FILE_COUNT:
        raise ValueError("candidate file count exceeds limit")
    if total_bytes > MAX_CANDIDATE_PACKAGE_BYTES:
        raise ValueError("candidate package exceeds byte limit")
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


def candidate_files_total_bytes(files: Iterable[CandidateFileDelta]) -> int:
    return sum(
        len(item.content.encode("utf-8"))
        for item in validate_candidate_files(files)
        if item.operation == "upsert" and item.content is not None
    )


def _normalized_replay_path(raw_path: str) -> str:
    value = str(raw_path or "").strip()
    if not value or "\\" in value:
        raise ValueError("candidate file path must be inside replay/")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("candidate file path must be inside replay/")
    if not path.parts or path.parts[0] != "replay" or len(path.parts) < 2:
        raise ValueError("candidate file path must be inside replay/")
    return path.as_posix()
