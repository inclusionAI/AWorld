from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import yaml

from aworld.self_evolve.candidate_errors import (
    CandidateFailureField,
    CandidateMaterializationCode,
    CandidateMaterializationError,
)
from aworld.self_evolve.types import (
    CandidateFileDelta,
    CandidateVariant,
    to_json_dict,
)
from aworld.skills.structure_types import SkillStructuralEditIntent


MAX_CANDIDATE_FILE_COUNT = 32
MAX_CANDIDATE_FILE_BYTES = 256 * 1024
MAX_CANDIDATE_PACKAGE_BYTES = 1024 * 1024
_OPERATIONS = frozenset({"upsert", "delete"})
_REPLAY_FILE_REFERENCE = re.compile(
    r"(?<![A-Za-z0-9_./-])(?:\./)?"
    r"(replay/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+)"
    r"(?![A-Za-z0-9_./-])"
)


class CandidateMutationKind(str, Enum):
    """Which independently governed surface a candidate package changes."""

    NO_CHANGE = "no_change"
    TARGET_BEHAVIOR = "target_behavior"
    EVALUATION_SUPPORT = "evaluation_support"
    TARGET_BEHAVIOR_WITH_SUPPORT = "target_behavior_with_support"


@dataclass(frozen=True)
class CandidateMutationClassification:
    """Separates releasable target behavior from evaluation-only support.

    Candidate-owned files currently live under the framework-reserved
    ``replay/`` package.  They may be required to make a target replayable and
    are released atomically with an accepted target, but they are not by
    themselves evidence that target behavior improved.
    """

    kind: CandidateMutationKind
    target_behavior_changed: bool
    evaluation_support_changed: bool
    current_target_behavior_fingerprint: str
    candidate_target_behavior_fingerprint: str
    support_file_paths: tuple[str, ...] = ()

    @property
    def quality_evaluation_allowed(self) -> bool:
        return self.target_behavior_changed

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "target_behavior_changed": self.target_behavior_changed,
            "evaluation_support_changed": self.evaluation_support_changed,
            "quality_evaluation_allowed": self.quality_evaluation_allowed,
            "current_target_behavior_fingerprint": (
                self.current_target_behavior_fingerprint
            ),
            "candidate_target_behavior_fingerprint": (
                self.candidate_target_behavior_fingerprint
            ),
            "support_file_paths": list(self.support_file_paths),
        }


def classify_candidate_mutation(
    candidate: CandidateVariant,
    *,
    current_content: str,
) -> CandidateMutationClassification:
    """Classify a candidate without using task- or target-id-specific rules."""

    current_fingerprint = candidate_target_behavior_fingerprint(
        current_content,
        target_type=candidate.target.target_type,
    )
    candidate_fingerprint = candidate_target_behavior_fingerprint(
        candidate.content,
        target_type=candidate.target.target_type,
    )
    target_changed = candidate_fingerprint != current_fingerprint
    support_paths = tuple(
        item.path for item in validate_candidate_files(candidate.files)
    )
    support_changed = bool(support_paths)
    if target_changed and support_changed:
        kind = CandidateMutationKind.TARGET_BEHAVIOR_WITH_SUPPORT
    elif target_changed:
        kind = CandidateMutationKind.TARGET_BEHAVIOR
    elif support_changed:
        kind = CandidateMutationKind.EVALUATION_SUPPORT
    else:
        kind = CandidateMutationKind.NO_CHANGE
    return CandidateMutationClassification(
        kind=kind,
        target_behavior_changed=target_changed,
        evaluation_support_changed=support_changed,
        current_target_behavior_fingerprint=current_fingerprint,
        candidate_target_behavior_fingerprint=candidate_fingerprint,
        support_file_paths=support_paths,
    )


def candidate_target_behavior_fingerprint(
    content: str,
    *,
    target_type: str,
) -> str:
    """Fingerprint releasable behavior while ignoring release bookkeeping.

    ``self_evolve`` frontmatter is publication metadata written by the
    framework.  Treating it as a behavior delta lets a repackaged but otherwise
    unchanged skill pass quality gates.  Invalid frontmatter deliberately falls
    back to the raw content; structural validation remains authoritative.
    """

    normalized = str(content or "").replace("\r\n", "\n").replace("\r", "\n")
    payload: object = normalized
    if target_type == "skill":
        lines = normalized.split("\n")
        if lines and lines[0].strip() == "---":
            try:
                end_index = next(
                    index
                    for index, line in enumerate(lines[1:], start=1)
                    if line.strip() == "---"
                )
                front_matter = yaml.safe_load("\n".join(lines[1:end_index])) or {}
            except (StopIteration, yaml.YAMLError):
                front_matter = None
            if isinstance(front_matter, dict):
                public_front_matter = dict(front_matter)
                public_front_matter.pop("self_evolve", None)
                payload = {
                    "front_matter": public_front_matter,
                    "body": "\n".join(lines[end_index + 1 :]).rstrip(),
                }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


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
    payload = {
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
    if candidate.structural_edit_intent is not None:
        payload["structural_edit_intent"] = to_json_dict(
            candidate.structural_edit_intent
        )
    return payload


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
        "schema_version": "aworld.self_evolve.candidate_semantic_package.v2",
        "target": {
            "target_type": candidate.target.target_type,
            "target_id": candidate.target.target_id,
        },
        "content_semantic_fingerprint": (
            content_semantic_fingerprint
            or candidate_content_semantic_fingerprint(candidate.content)
        ),
        "structural_edit_intent_fingerprint": (
            _structural_edit_intent_fingerprint(
                candidate.structural_edit_intent
            )
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


def _structural_edit_intent_fingerprint(
    intent: SkillStructuralEditIntent | None,
) -> str | None:
    if intent is None:
        return None
    payload = {
        "schema_version": intent.schema_version,
        "authority": intent.authority,
        "reason": intent.reason,
        "authorization": intent.authorization,
        "base_content_fingerprint": intent.base_content_fingerprint,
        "candidate_content_fingerprint": (
            intent.candidate_content_fingerprint
        ),
        "actions": [
            {
                "action": action.action,
                "section_path": list(action.section_path),
                "base_section_fingerprint": (
                    action.base_section_fingerprint
                ),
                "result_section_fingerprint": (
                    action.result_section_fingerprint
                ),
            }
            for action in intent.actions
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


def candidate_package_referenced_paths(content: str) -> tuple[str, ...]:
    """Return concrete replay-package file paths named by skill Markdown.

    Directory-only references such as ``replay/`` are intentionally excluded.
    Every returned path uses the same normalized vocabulary as candidate file
    deltas, so generation, verification, and release can share one closure
    contract.
    """

    referenced: set[str] = set()
    for match in _REPLAY_FILE_REFERENCE.finditer(str(content or "")):
        raw_path = match.group(1).rstrip(".,;:")
        try:
            referenced.add(_normalized_replay_path(raw_path))
        except CandidateMaterializationError:
            continue
    return tuple(sorted(referenced))


def candidate_package_reference_report(
    candidate: CandidateVariant,
    *,
    package_root: str | Path | None = None,
    existing_paths: Iterable[str] = (),
) -> dict[str, object]:
    """Validate dependency closure and, when rooted, materialized file deltas."""

    if candidate.target.target_type != "skill":
        return {
            "closed": True,
            "references_closed": True,
            "referenced_file_count": 0,
            "referenced_paths": [],
            "candidate_owned_referenced_paths": [],
            "existing_referenced_paths": [],
            "missing_referenced_paths": [],
            "materialized_file_deltas_checked": False,
            "materialized_file_delta_count": 0,
            "materialized_file_deltas_closed": True,
            "missing_candidate_file_paths": [],
            "mismatched_candidate_file_paths": [],
            "undeleted_candidate_file_paths": [],
        }
    referenced_paths = candidate_package_referenced_paths(candidate.content)
    files = {
        item.path: item for item in validate_candidate_files(candidate.files)
    }
    materialized_root = Path(package_root) if package_root is not None else None
    missing_candidate_files: list[str] = []
    mismatched_candidate_files: list[str] = []
    undeleted_candidate_files: list[str] = []
    if materialized_root is not None:
        for relative_path, delta in files.items():
            destination = materialized_root.joinpath(
                *PurePosixPath(relative_path).parts
            )
            if delta.operation == "delete":
                if destination.exists() or destination.is_symlink():
                    undeleted_candidate_files.append(relative_path)
                continue
            if not _is_regular_package_file(
                destination,
                root=materialized_root,
            ):
                missing_candidate_files.append(relative_path)
                continue
            try:
                materialized_content = destination.read_text(encoding="utf-8")
                materialized_executable = bool(
                    destination.stat().st_mode & 0o111
                )
            except OSError:
                mismatched_candidate_files.append(relative_path)
                continue
            if (
                materialized_content != (delta.content or "")
                or materialized_executable != delta.executable
            ):
                mismatched_candidate_files.append(relative_path)
    known_existing_paths = {
        normalized
        for raw_path in existing_paths
        for normalized in (_known_replay_path(raw_path),)
        if normalized is not None
    }
    root: Path | None = None
    if materialized_root is not None:
        root = materialized_root
    elif candidate.target.path:
        root = Path(candidate.target.path).expanduser().parent

    missing: list[str] = []
    candidate_owned: list[str] = []
    existing: list[str] = []
    for relative_path in referenced_paths:
        delta = files.get(relative_path)
        if delta is not None:
            destination = (
                materialized_root.joinpath(
                    *PurePosixPath(relative_path).parts
                )
                if materialized_root is not None
                else None
            )
            if delta.operation == "upsert" and (
                destination is None
                or _is_regular_package_file(destination, root=materialized_root)
            ):
                candidate_owned.append(relative_path)
            else:
                missing.append(relative_path)
            continue
        if materialized_root is None and relative_path in known_existing_paths:
            existing.append(relative_path)
            continue
        destination = (
            root.joinpath(*PurePosixPath(relative_path).parts)
            if root is not None
            else None
        )
        if destination is not None and _is_regular_package_file(
            destination,
            root=root,
        ):
            existing.append(relative_path)
        else:
            missing.append(relative_path)
    materialized_file_deltas_closed = not (
        missing_candidate_files
        or mismatched_candidate_files
        or undeleted_candidate_files
    )
    return {
        "closed": not missing and materialized_file_deltas_closed,
        "references_closed": not missing,
        "referenced_file_count": len(referenced_paths),
        "referenced_paths": list(referenced_paths),
        "candidate_owned_referenced_paths": candidate_owned,
        "existing_referenced_paths": existing,
        "missing_referenced_paths": missing,
        "materialized_file_deltas_checked": materialized_root is not None,
        "materialized_file_delta_count": len(files),
        "materialized_file_deltas_closed": materialized_file_deltas_closed,
        "missing_candidate_file_paths": missing_candidate_files,
        "mismatched_candidate_file_paths": mismatched_candidate_files,
        "undeleted_candidate_file_paths": undeleted_candidate_files,
    }


def _known_replay_path(raw_path: str) -> str | None:
    try:
        return _normalized_replay_path(str(raw_path).strip().replace("\\", "/"))
    except CandidateMaterializationError:
        return None


def _is_regular_package_file(path: Path, *, root: Path | None) -> bool:
    if root is None or root.is_symlink():
        return False
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return False
    return path.is_file()


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
