"""Offline ParseBench checkout validation and executable Dataset authoring."""

from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import re
import subprocess
import tarfile
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from hashlib import sha1, sha256
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any, cast
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

from .contracts import (
    CONVERTER_VERSION,
    DATASET_PACKAGE_SCHEMA_VERSION,
    EXPECTED_SOURCE_FIELDS,
    GROUND_TRUTH_SCHEMA_VERSION,
    MATERIAL_MANIFEST_SCHEMA_VERSION,
    PARSEBENCH_SCOPE_FILENAME,
    PARSEBENCH_SCOPE_SCHEMA_VERSION,
    PARSEBENCH_TASK_FILENAME,
    PARSEBENCH_TASK_RUNTIME_PATH,
    PARSEBENCH_TASK_SCHEMA_VERSION,
    PINNED_FULL_SELECTION_MANIFEST_SHA256,
    PINNED_MATERIAL_MANIFEST_SHA256,
    PINNED_PARSEBENCH_CONTRACT,
    PINNED_PARSEBENCH_RUNTIME_IMAGE,
    PROVENANCE_SCHEMA_VERSION,
    SELECTION_MANIFEST_SCHEMA_VERSION,
    TASK_ARCHIVE_SCHEMA_VERSION,
    FileEvidence,
    JsonValue,
    PackageBuildResult,
    ParseBenchContract,
    ParseBenchDatasetError,
    ParseBenchDimension,
    ParseBenchExecution,
    ParseBenchRule,
    ParseBenchSourceDataset,
    SmokeSelection,
)
from .package_contract import (
    agent_dockerfile as _render_agent_dockerfile,
)
from .package_contract import (
    artifact_specs as _render_artifact_specs,
)
from .package_contract import (
    canonical_json as _canonical_json,
)
from .package_contract import (
    instruction as _render_instruction,
)
from .package_contract import (
    public_scope_contract as _render_public_scope_contract,
)
from .package_contract import (
    public_task_contract as _render_public_task_contract,
)
from .package_contract import (
    task_toml as _render_task_toml,
)
from .package_contract import (
    verifier_dockerfile as _render_verifier_dockerfile,
)
from .package_contract import (
    verifier_script as _render_verifier_script,
)

DEFAULT_DATASET_ID = "parsebench-2805a1d9"
DEFAULT_SERVICE_NAME = "benchmark-catalog"
DEFAULT_RUNTIME_IMAGE = PINNED_PARSEBENCH_RUNTIME_IMAGE

_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
_RUNTIME_IMAGE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@:-]{0,510}$")
_IMMUTABLE_RUNTIME_IMAGE_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._/:-]{0,430}@sha256:[0-9a-f]{64}$"
)
_SUPPORTED_SOURCE_SUFFIXES = frozenset(
    {".pdf", ".png", ".jpg", ".jpeg", ".jfif", ".docx"}
)
_MAX_JSONL_LINE_BYTES = 16 * 1024 * 1024
_LFS_POINTER_PREFIX = b"version https://git-lfs.github.com/spec/v1"
_DIMENSION_ORDER = {
    dimension: index for index, dimension in enumerate(ParseBenchDimension)
}


@dataclass(frozen=True, slots=True)
class _RevisionEvidence:
    revision: str
    kind: str
    blobs_root: Path | None = None


@dataclass(frozen=True, slots=True)
class _PackageScope:
    kind: str
    selection_manifest_sha256: str
    publishable: bool
    non_publishable_reasons: tuple[str, ...]
    selected_execution_count: int
    runtime_image: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": PARSEBENCH_SCOPE_SCHEMA_VERSION,
            "kind": self.kind,
            "selection_manifest_sha256": self.selection_manifest_sha256,
            "publishable": self.publishable,
            "non_publishable_reasons": list(self.non_publishable_reasons),
            "selected_execution_count": self.selected_execution_count,
            "runtime_image": self.runtime_image,
            "dataset_revision": PINNED_PARSEBENCH_CONTRACT.dataset_revision,
            "scorer_revision": PINNED_PARSEBENCH_CONTRACT.scorer_revision,
        }


class _DuplicateJsonKey(ValueError):
    pass


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _reject_nonfinite_json(_: str) -> None:
    raise ValueError("non-finite number")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite number")
    return parsed


def _parse_json_object(
    value: str | bytes,
    *,
    code: str,
    message: str,
) -> dict[str, Any]:
    try:
        parsed = json.loads(
            value,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonfinite_json,
            parse_float=_finite_float,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        OverflowError,
        RecursionError,
    ):
        raise ParseBenchDatasetError(code, message) from None
    if not isinstance(parsed, dict):
        raise ParseBenchDatasetError(code, message)
    return parsed


def _sha256_file(path: Path) -> str:
    digest = sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    except (OSError, RuntimeError):
        raise ParseBenchDatasetError(
            "source_read_failed",
            "ParseBench source material could not be read",
        ) from None
    return "sha256:" + digest.hexdigest()


def _looks_like_lfs_pointer(path: Path) -> bool:
    try:
        with path.open("rb") as source:
            return source.read(len(_LFS_POINTER_PREFIX)) == _LFS_POINTER_PREFIX
    except OSError:
        raise ParseBenchDatasetError(
            "source_read_failed",
            "ParseBench source material could not be read",
        ) from None


def _git_checkout_revision(source_root: Path) -> str | None:
    if not (source_root / ".git").exists():
        return None
    try:
        completed = subprocess.run(
            ["git", "-C", str(source_root), "rev-parse", "--verify", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    revision = completed.stdout.strip()
    if completed.returncode != 0 or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        return None
    return revision


def _hf_snapshot_blob_root(source_root: Path) -> Path | None:
    cache_root = source_root.parent.parent
    if (
        source_root.parent.name != "snapshots"
        or not cache_root.name.startswith("datasets--")
        or re.fullmatch(r"[0-9a-f]{40}", source_root.name) is None
    ):
        return None
    try:
        blobs_root = (cache_root / "blobs").resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not blobs_root.is_dir() or not blobs_root.is_relative_to(cache_root):
        return None
    return blobs_root


def _source_revision_evidence(source_root: Path) -> _RevisionEvidence:
    if (source_root / ".git").exists():
        git_revision = _git_checkout_revision(source_root)
        if git_revision is None:
            raise ParseBenchDatasetError(
                "source_revision_unverifiable",
                "ParseBench Git checkout revision could not be verified",
            )
        return _RevisionEvidence(git_revision, "clean-git-checkout")
    if (source_root / ".parsebench-revision").exists():
        raise ParseBenchDatasetError(
            "source_revision_marker_unsupported",
            "ParseBench exported revision markers are not trusted evidence",
        )
    blobs_root = _hf_snapshot_blob_root(source_root)
    if blobs_root is not None:
        return _RevisionEvidence(
            source_root.name,
            "huggingface-content-addressed-snapshot",
            blobs_root,
        )
    if (
        source_root.parent.name == "snapshots"
        and re.fullmatch(r"[0-9a-f]{40}", source_root.name) is not None
    ):
        raise ParseBenchDatasetError(
            "source_revision_unverifiable",
            "ParseBench Hugging Face snapshot has no bounded blob store",
        )
    raise ParseBenchDatasetError(
        "source_revision_missing",
        "ParseBench checkout has no verifiable revision evidence",
    )


def _validate_printable_token(
    value: object, *, field: str, max_length: int = 512
) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > max_length
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ParseBenchDatasetError(
            "source_field_invalid",
            f"ParseBench source contains an invalid {field}",
        )
    return value


def _is_allowed_material_file(source_root: Path, resolved: Path) -> bool:
    if resolved.is_relative_to(source_root):
        return True
    blobs_root = _hf_snapshot_blob_root(source_root)
    return blobs_root is not None and resolved.is_relative_to(blobs_root)


def _git_material_command(
    source_root: Path,
    arguments: Sequence[str],
    relative_paths: Sequence[str],
) -> bytes:
    try:
        completed = subprocess.run(
            ["git", "-C", str(source_root), *arguments, "--", *relative_paths],
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        raise ParseBenchDatasetError(
            "source_revision_unverifiable",
            "ParseBench Git material state could not be verified",
        ) from None
    if completed.returncode != 0:
        raise ParseBenchDatasetError(
            "source_revision_unverifiable",
            "ParseBench Git material state could not be verified",
        )
    return completed.stdout


def _validate_clean_git_material(
    source_root: Path,
    relative_paths: Sequence[str],
) -> None:
    for start in range(0, len(relative_paths), 128):
        paths = relative_paths[start : start + 128]
        tracked_output = _git_material_command(
            source_root,
            ("ls-files", "--cached", "-v", "-z"),
            paths,
        )
        tracked: set[str] = set()
        unsafe_index_flag = False
        for raw_item in tracked_output.split(b"\0"):
            if not raw_item:
                continue
            item = os.fsdecode(raw_item)
            if len(item) < 3 or item[:2] != "H ":
                unsafe_index_flag = True
                continue
            tracked.add(item[2:])
        if unsafe_index_flag or tracked != set(paths):
            raise ParseBenchDatasetError(
                "source_revision_dirty",
                "ParseBench Git material is untracked or differs from HEAD",
            )
        status = _git_material_command(
            source_root,
            ("status", "--porcelain=v1", "-z", "--untracked-files=all"),
            paths,
        )
        if status:
            raise ParseBenchDatasetError(
                "source_revision_dirty",
                "ParseBench Git material is untracked or differs from HEAD",
            )


def _reject_git_material_symlinks(
    source_root: Path,
    relative_paths: Sequence[str],
) -> None:
    for relative_path in relative_paths:
        candidate = source_root
        for part in PurePosixPath(relative_path).parts:
            candidate /= part
            try:
                is_symlink = candidate.is_symlink()
            except OSError:
                raise ParseBenchDatasetError(
                    "source_revision_unverifiable",
                    "ParseBench Git material state could not be verified",
                ) from None
            if is_symlink:
                raise ParseBenchDatasetError(
                    "source_revision_dirty",
                    "ParseBench Git material must not contain symlinks",
                )


def _git_blob_sha1_file(path: Path) -> str:
    try:
        size = path.stat().st_size
        digest = sha1(f"blob {size}\0".encode("ascii"), usedforsecurity=False)
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    except (OSError, RuntimeError):
        raise ParseBenchDatasetError(
            "source_read_failed",
            "ParseBench source material could not be read",
        ) from None
    return digest.hexdigest()


def _validate_hf_blob_material(
    blobs_root: Path,
    material_paths: Iterable[Path],
) -> None:
    for material_path in sorted(set(material_paths)):
        try:
            resolved = material_path.resolve(strict=True)
        except (OSError, RuntimeError):
            raise ParseBenchDatasetError(
                "source_revision_unverifiable",
                "ParseBench snapshot material could not be resolved",
            ) from None
        if resolved.parent != blobs_root:
            raise ParseBenchDatasetError(
                "source_revision_unverifiable",
                "ParseBench snapshot material is not backed by its blob store",
            )
        blob_id = resolved.name
        if re.fullmatch(r"[0-9a-f]{64}", blob_id) is not None:
            matches = _sha256_file(resolved) == f"sha256:{blob_id}"
        elif re.fullmatch(r"[0-9a-f]{40}", blob_id) is not None:
            matches = _git_blob_sha1_file(resolved) == blob_id
        else:
            raise ParseBenchDatasetError(
                "source_revision_unverifiable",
                "ParseBench snapshot uses a non-content-addressed blob",
            )
        if not matches:
            raise ParseBenchDatasetError(
                "source_content_mismatch",
                "ParseBench snapshot material does not match its blob identity",
            )


def _validate_revision_materials(
    source_root: Path,
    revision_evidence: _RevisionEvidence,
    material_paths: dict[str, Path],
    expected_materials: tuple[tuple[str, int, str], ...],
) -> None:
    logical_paths = tuple(sorted(material_paths))
    expected = {
        relative_path: (size, digest)
        for relative_path, size, digest in expected_materials
    }
    if set(logical_paths) != set(expected):
        raise ParseBenchDatasetError(
            "source_material_manifest_mismatch",
            "ParseBench source paths do not match the pinned material manifest",
        )
    if revision_evidence.kind == "clean-git-checkout":
        _reject_git_material_symlinks(source_root, logical_paths)
        _validate_clean_git_material(source_root, logical_paths)
    else:
        if revision_evidence.blobs_root is None:
            raise AssertionError("snapshot revision evidence has no blob root")
        _validate_hf_blob_material(
            revision_evidence.blobs_root,
            material_paths.values(),
        )
    for relative_path in logical_paths:
        expected_size, expected_sha256 = expected[relative_path]
        material_path = source_root.joinpath(*PurePosixPath(relative_path).parts)
        try:
            actual_size = material_path.stat().st_size
        except OSError:
            raise ParseBenchDatasetError(
                "source_revision_unverifiable",
                "ParseBench source material could not be verified",
            ) from None
        if (
            actual_size != expected_size
            or _sha256_file(material_path) != expected_sha256
        ):
            raise ParseBenchDatasetError(
                "source_content_mismatch",
                "ParseBench source material does not match the pinned revision",
            )


def _validate_resource_path(source_root: Path, value: object) -> tuple[str, Path]:
    source_path = _validate_printable_token(value, field="pdf path", max_length=4096)
    posix_path = PurePosixPath(source_path)
    if (
        posix_path.is_absolute()
        or "\\" in source_path
        or posix_path.as_posix() != source_path
        or len(posix_path.parts) < 2
        or posix_path.parts[0] != "docs"
        or any(part in {"", ".", ".."} for part in posix_path.parts)
        or posix_path.suffix.lower() not in _SUPPORTED_SOURCE_SUFFIXES
    ):
        raise ParseBenchDatasetError(
            "resource_path_unsafe",
            "ParseBench source contains an unsafe resource path",
        )
    candidate = source_root.joinpath(*posix_path.parts)
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ParseBenchDatasetError(
            "resource_missing",
            "ParseBench source references a missing resource",
        ) from None
    if not resolved.is_file() or not _is_allowed_material_file(source_root, resolved):
        raise ParseBenchDatasetError(
            "resource_path_unsafe",
            "ParseBench source contains an unsafe resource path",
        )
    if _looks_like_lfs_pointer(resolved):
        raise ParseBenchDatasetError(
            "resource_unmaterialized",
            "ParseBench source resource is an unmaterialized LFS pointer",
        )
    # Keep the logical checkout path so the source suffix survives Hugging Face's
    # extensionless blob symlink. Revision validation separately binds its target.
    return source_path, candidate


def _validate_page(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ParseBenchDatasetError(
            "source_field_invalid",
            "ParseBench source contains an invalid page number",
        )
    return value


def _validate_tags(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ParseBenchDatasetError(
            "source_field_invalid",
            "ParseBench source contains invalid tags",
        )
    tags: list[str] = []
    for item in value:
        tags.append(_validate_printable_token(item, field="tag", max_length=128))
    return tuple(tags)


def _validate_expected_markdown(
    value: object,
    *,
    dimension: ParseBenchDimension,
    rule_type: str,
) -> str | None:
    if value is not None and not isinstance(value, str):
        raise ParseBenchDatasetError(
            "source_field_invalid",
            "ParseBench source contains invalid expected markdown",
        )
    if dimension is ParseBenchDimension.TABLE:
        if rule_type != "expected_markdown" or not value:
            raise ParseBenchDatasetError(
                "source_field_invalid",
                "ParseBench table source is missing expected markdown",
            )
        return value
    if value is not None:
        raise ParseBenchDatasetError(
            "source_field_invalid",
            "ParseBench non-table source contains unexpected markdown ground truth",
        )
    return None


def _load_rule(
    row: dict[str, Any],
    *,
    source_root: Path,
    dimension: ParseBenchDimension,
    source_jsonl: str,
    source_line: int,
) -> tuple[ParseBenchRule, Path]:
    if set(row) != EXPECTED_SOURCE_FIELDS:
        raise ParseBenchDatasetError(
            "source_schema_mismatch",
            "ParseBench source row does not match the pinned eight-field schema",
        )
    category = _validate_printable_token(
        row["category"], field="category", max_length=64
    )
    if category != dimension.value:
        raise ParseBenchDatasetError(
            "source_field_invalid",
            "ParseBench source category does not match its JSONL split",
        )
    source_path, source_file = _validate_resource_path(source_root, row["pdf"])
    rule_id = _validate_printable_token(row["id"], field="rule id", max_length=512)
    rule_type = _validate_printable_token(
        row["type"], field="rule type", max_length=128
    )
    raw_rule = row["rule"]
    if not isinstance(raw_rule, str):
        raise ParseBenchDatasetError(
            "rule_json_invalid",
            "ParseBench rule must be a JSON-encoded object",
        )
    rule_payload = _parse_json_object(
        raw_rule,
        code="rule_json_invalid",
        message="ParseBench rule is not a valid JSON-encoded object",
    )
    page = _validate_page(row["page"])
    if dimension is ParseBenchDimension.LAYOUT and page is None:
        raise ParseBenchDatasetError(
            "source_field_invalid",
            "ParseBench layout rule is missing its one-indexed page",
        )
    expected_markdown = _validate_expected_markdown(
        row["expected_markdown"],
        dimension=dimension,
        rule_type=rule_type,
    )
    tags = _validate_tags(row["tags"])
    return (
        ParseBenchRule(
            source_path=source_path,
            dimension=dimension,
            rule_id=rule_id,
            rule_type=rule_type,
            rule_payload=cast(dict[str, JsonValue], rule_payload),
            page=page,
            expected_markdown=expected_markdown,
            tags=tags,
            source_jsonl=source_jsonl,
            source_line=source_line,
        ),
        source_file,
    )


def _read_jsonl_rules(
    path: Path,
    *,
    source_root: Path,
    dimension: ParseBenchDimension,
) -> tuple[list[ParseBenchRule], dict[str, Path], FileEvidence]:
    logical_name = path.name
    try:
        resolved_path = path.resolve(strict=True)
    except (OSError, RuntimeError):
        resolved_path = path
    if not resolved_path.is_file():
        raise ParseBenchDatasetError(
            "source_file_missing",
            "ParseBench checkout is missing a required dimension JSONL",
        )
    if not _is_allowed_material_file(source_root, resolved_path):
        raise ParseBenchDatasetError(
            "source_file_unsafe",
            "ParseBench dimension JSONL resolves outside its source checkout",
        )
    path = resolved_path
    if _looks_like_lfs_pointer(path):
        raise ParseBenchDatasetError(
            "source_file_unmaterialized",
            "ParseBench dimension JSONL is an unmaterialized LFS pointer",
        )
    rules: list[ParseBenchRule] = []
    resource_files: dict[str, Path] = {}
    try:
        with path.open("rb") as source:
            for line_number, raw_line in enumerate(source, start=1):
                if len(raw_line) > _MAX_JSONL_LINE_BYTES:
                    raise ParseBenchDatasetError(
                        "source_line_too_large",
                        "ParseBench source row exceeds the authoring limit",
                    )
                if not raw_line.strip():
                    continue
                row = _parse_json_object(
                    raw_line,
                    code="source_jsonl_invalid",
                    message="ParseBench dimension JSONL contains an invalid row",
                )
                rule, resource_file = _load_rule(
                    row,
                    source_root=source_root,
                    dimension=dimension,
                    source_jsonl=logical_name,
                    source_line=line_number,
                )
                rules.append(rule)
                resource_files[rule.source_path] = resource_file
    except ParseBenchDatasetError:
        raise
    except OSError:
        raise ParseBenchDatasetError(
            "source_read_failed",
            "ParseBench dimension JSONL could not be read",
        ) from None
    if not rules:
        raise ParseBenchDatasetError(
            "source_file_empty",
            "ParseBench dimension JSONL has no rules",
        )
    evidence = FileEvidence(
        relative_path=logical_name,
        size=path.stat().st_size,
        sha256=_sha256_file(path),
        row_count=len(rules),
    )
    return rules, resource_files, evidence


def task_id_for_source(source_path: str, *, page: int | None = None) -> str:
    """Return a stable gateway-safe Task identity for one ParseBench execution."""

    page_key = "all" if page is None else str(page)
    digest = sha256(f"{source_path}\0page:{page_key}".encode()).hexdigest()[:32]
    return f"pb-{digest}"


def _ordered_dimensions(
    rules: Iterable[ParseBenchRule],
) -> tuple[ParseBenchDimension, ...]:
    return tuple(
        sorted(
            {rule.dimension for rule in rules},
            key=_DIMENSION_ORDER.__getitem__,
        )
    )


def _build_executions(
    rules: Sequence[ParseBenchRule],
    resource_files: dict[str, Path],
) -> tuple[ParseBenchExecution, ...]:
    grouped: dict[tuple[str, int | None], list[ParseBenchRule]] = defaultdict(list)
    for rule in rules:
        grouped[(rule.source_path, rule.page)].append(rule)
    task_sources: dict[str, tuple[str, int | None]] = {}
    executions: list[ParseBenchExecution] = []
    execution_keys = sorted(
        grouped,
        key=lambda item: (item[0], -1 if item[1] is None else item[1]),
    )
    for source_path, page in execution_keys:
        execution_key = (source_path, page)
        task_id = task_id_for_source(source_path, page=page)
        previous = task_sources.setdefault(task_id, execution_key)
        if previous != execution_key:
            raise ParseBenchDatasetError(
                "task_id_collision",
                "ParseBench executions collide on their deterministic Task identity",
            )
        source_file = resource_files[source_path]
        source_rules = tuple(
            sorted(
                grouped[execution_key],
                key=lambda rule: (
                    _DIMENSION_ORDER[rule.dimension],
                    rule.source_line,
                    rule.rule_id,
                ),
            )
        )
        scoped_rule_ids: set[tuple[ParseBenchDimension, str]] = set()
        for rule in source_rules:
            scoped_rule_id = (rule.dimension, rule.rule_id)
            if scoped_rule_id in scoped_rule_ids:
                raise ParseBenchDatasetError(
                    "duplicate_rule_id",
                    "ParseBench execution contains a duplicate dimension rule identity",
                )
            scoped_rule_ids.add(scoped_rule_id)
        source_sha256 = _sha256_file(source_file)
        if _SHA256_PATTERN.fullmatch(source_sha256) is None:
            raise AssertionError("internal checksum is not canonical")
        executions.append(
            ParseBenchExecution(
                task_id=task_id,
                source_path=source_path,
                source_file=source_file,
                source_size=source_file.stat().st_size,
                source_sha256=source_sha256,
                page=page,
                dimensions=_ordered_dimensions(source_rules),
                rules=source_rules,
            )
        )
    return tuple(sorted(executions, key=lambda item: item.task_id))


def _validate_cardinality(
    dataset: ParseBenchSourceDataset,
    *,
    contract: ParseBenchContract,
) -> None:
    rule_counts = Counter(rule.dimension for rule in dataset.rules)
    execution_counts = Counter(
        dimension
        for execution in dataset.executions
        for dimension in execution.dimensions
    )
    valid = len(dataset.rules) == contract.total_rule_count
    valid = valid and len(dataset.executions) == contract.unique_execution_count
    for dimension in ParseBenchDimension:
        valid = valid and rule_counts[dimension] == contract.expected_rules_for(
            dimension
        )
        valid = valid and execution_counts[
            dimension
        ] == contract.expected_executions_for(dimension)
    if not valid:
        raise ParseBenchDatasetError(
            "cardinality_mismatch",
            "ParseBench checkout cardinality does not match the pinned release",
        )


def load_parsebench_checkout(
    source_root: Path,
    *,
    contract: ParseBenchContract = PINNED_PARSEBENCH_CONTRACT,
) -> ParseBenchSourceDataset:
    """Load and strictly validate a local pinned Hugging Face-style checkout."""

    source_root = Path(source_root).expanduser().resolve()
    if not source_root.is_dir():
        raise ParseBenchDatasetError(
            "source_root_invalid",
            "ParseBench source root is not a directory",
        )
    revision_evidence = _source_revision_evidence(source_root)
    if revision_evidence.revision != contract.dataset_revision:
        raise ParseBenchDatasetError(
            "source_revision_mismatch",
            "ParseBench checkout revision does not match the pinned contract",
        )
    all_rules: list[ParseBenchRule] = []
    all_resources: dict[str, Path] = {}
    source_evidence: list[FileEvidence] = []
    for dimension, relative_path in contract.source_files:
        rules, resources, evidence = _read_jsonl_rules(
            source_root / relative_path,
            source_root=source_root,
            dimension=dimension,
        )
        all_rules.extend(rules)
        all_resources.update(resources)
        source_evidence.append(evidence)
    executions = _build_executions(all_rules, all_resources)
    material_paths = dict(all_resources)
    material_paths.update(
        {
            item.relative_path: (source_root / item.relative_path)
            for item in source_evidence
        }
    )
    _validate_revision_materials(
        source_root,
        revision_evidence,
        material_paths,
        contract.material_digests,
    )
    dataset = ParseBenchSourceDataset(
        source_root=source_root,
        dataset_revision=revision_evidence.revision,
        scorer_revision=contract.scorer_revision,
        revision_evidence=revision_evidence.kind,
        source_files=tuple(source_evidence),
        rules=tuple(all_rules),
        executions=executions,
    )
    _validate_cardinality(dataset, contract=contract)
    return dataset


def select_smoke_executions(
    executions: Sequence[ParseBenchExecution],
    selection: SmokeSelection,
) -> tuple[ParseBenchExecution, ...]:
    """Select stable hash-ranked examples independently for all five dimensions."""

    by_task_id: dict[str, ParseBenchExecution] = {}
    for execution in executions:
        if execution.task_id in by_task_id:
            raise ParseBenchDatasetError(
                "duplicate_task_id",
                "ParseBench execution set contains a duplicate Task identity",
            )
        by_task_id[execution.task_id] = execution
    selected: set[str] = set()
    for dimension in ParseBenchDimension:
        candidates = [
            execution
            for execution in by_task_id.values()
            if dimension in execution.dimensions
        ]
        candidates.sort(
            key=lambda execution: (
                sha256(
                    (
                        f"{selection.seed}\0{dimension.value}\0"
                        f"{execution.source_path}\0{execution.page}"
                    ).encode()
                ).hexdigest(),
                execution.task_id,
            )
        )
        if len(candidates) < selection.per_dimension:
            raise ParseBenchDatasetError(
                "smoke_dimension_incomplete",
                "ParseBench smoke selection cannot cover every requested dimension",
            )
        selected.update(item.task_id for item in candidates[: selection.per_dimension])
    return tuple(by_task_id[task_id] for task_id in sorted(selected))


def _content_attestation(content: bytes) -> dict[str, object]:
    return {
        "size": len(content),
        "sha256": "sha256:" + sha256(content).hexdigest(),
    }


def _verifier_runtime_modules() -> dict[str, bytes]:
    """Return the self-contained verifier code embedded in every task archive.

    The compact runtime contract intentionally excludes the 350 KiB authoring
    material manifest.  A verifier needs only pinned identities, dimensions,
    source-file routing, and the full-campaign execution count.
    """

    package_root = Path(__file__).resolve().parent
    source_file_rows = "\n".join(
        f"            ParseBenchDimension.{dimension.name}: {filename!r},"
        for dimension, filename in PINNED_PARSEBENCH_CONTRACT.source_files
    )
    compact_contracts = f'''"""Minimal pinned ParseBench verifier contract."""
from dataclasses import dataclass
from enum import Enum

DATASET_REVISION = {PINNED_PARSEBENCH_CONTRACT.dataset_revision!r}
SCORER_REVISION = {PINNED_PARSEBENCH_CONTRACT.scorer_revision!r}
GROUND_TRUTH_SCHEMA_VERSION = {GROUND_TRUTH_SCHEMA_VERSION!r}

class ParseBenchDimension(str, Enum):
    TABLE = "table"
    CHART = "chart"
    TEXT_CONTENT = "text_content"
    TEXT_FORMATTING = "text_formatting"
    LAYOUT = "layout"

@dataclass(frozen=True)
class _VerifierContract:
    unique_execution_count: int = {PINNED_PARSEBENCH_CONTRACT.unique_execution_count}

    def source_file_for(self, dimension: ParseBenchDimension) -> str:
        return {{
{source_file_rows}
        }}[dimension]

PINNED_PARSEBENCH_CONTRACT = _VerifierContract()
'''.encode()
    return {
        "__init__.py": b'"""Dataset-owned ParseBench verifier runtime."""\n',
        "contracts.py": compact_contracts,
        "scoring.py": (package_root / "scoring.py").read_bytes(),
        "verifier.py": (package_root / "verifier.py").read_bytes(),
    }


def _scorer_build_files() -> dict[str, bytes]:
    root = Path(__file__).resolve().parents[2] / "resources/scorer"
    names = (
        "install_scorer.py",
        "requirements.txt",
        "build-requirements.txt",
        "AWORLD_SCORER_BUNDLE_MANIFEST.json",
        "VENDORED.md",
    )
    return {name: (root / name).read_bytes() for name in names}


def _selection_case(execution: ParseBenchExecution) -> dict[str, object]:
    source_runtime_path = (
        f"/workspace/input/document{execution.source_file.suffix.lower()}"
    )
    return {
        "task_id": execution.task_id,
        "source_runtime_path": source_runtime_path,
        "source_sha256": execution.source_sha256,
        "source_size": execution.source_size,
        "page": execution.page,
        "dimensions": [dimension.value for dimension in execution.dimensions],
        "materials": {
            "task.toml": _content_attestation(_task_toml()),
            "environment/Dockerfile": _content_attestation(
                _dockerfile(runtime_image=DEFAULT_RUNTIME_IMAGE)
            ),
            "tests/Dockerfile": _content_attestation(
                _verifier_dockerfile(runtime_image=DEFAULT_RUNTIME_IMAGE)
            ),
            **{
                f"tests/scorer/{name}": _content_attestation(content)
                for name, content in _scorer_build_files().items()
            },
            "instruction.md": _content_attestation(_instruction(execution)),
            f"environment/{PARSEBENCH_TASK_FILENAME}": _content_attestation(
                _public_task_contract(execution)
            ),
            "tests/test.sh": _content_attestation(_verifier_script()),
            "tests/ground_truth.json": _content_attestation(
                _private_ground_truth(execution)
            ),
            **{
                f"tests/parsebench_runtime/{name}": _content_attestation(content)
                for name, content in _verifier_runtime_modules().items()
            },
        },
    }


def _selection_manifest(
    dataset: ParseBenchSourceDataset,
    selected: Sequence[ParseBenchExecution],
) -> dict[str, object]:
    ordered = sorted(selected, key=lambda item: item.task_id)
    return {
        "schema_version": SELECTION_MANIFEST_SCHEMA_VERSION,
        "dataset_revision": dataset.dataset_revision,
        "scorer_revision": dataset.scorer_revision,
        "cases": [_selection_case(execution) for execution in ordered],
        "dimension_task_ids": {
            dimension.value: [
                execution.task_id
                for execution in ordered
                if dimension in execution.dimensions
            ]
            for dimension in ParseBenchDimension
        },
    }


def _selection_manifest_sha256(
    dataset: ParseBenchSourceDataset,
    selected: Sequence[ParseBenchExecution],
) -> str:
    return (
        "sha256:"
        + sha256(_canonical_json(_selection_manifest(dataset, selected))).hexdigest()
    )


def _is_complete_pinned_release(
    dataset: ParseBenchSourceDataset,
    selected: Sequence[ParseBenchExecution],
    *,
    contract: ParseBenchContract,
    selection: SmokeSelection | None,
) -> bool:
    if (
        selection is not None
        or contract != PINNED_PARSEBENCH_CONTRACT
        or tuple(selected) != dataset.executions
        or len(selected) != PINNED_PARSEBENCH_CONTRACT.unique_execution_count
    ):
        return False
    execution_counts = Counter(
        dimension for execution in selected for dimension in execution.dimensions
    )
    return all(
        execution_counts[dimension]
        == PINNED_PARSEBENCH_CONTRACT.expected_executions_for(dimension)
        for dimension in ParseBenchDimension
    )


def _package_scope(
    dataset: ParseBenchSourceDataset,
    selected: Sequence[ParseBenchExecution],
    *,
    contract: ParseBenchContract,
    selection: SmokeSelection | None,
    runtime_image: str,
    immutable_runtime_image: bool,
) -> _PackageScope:
    complete_release = _is_complete_pinned_release(
        dataset,
        selected,
        contract=contract,
        selection=selection,
    )
    selection_manifest_sha256 = _selection_manifest_sha256(dataset, selected)
    pinned_selection = (
        selection_manifest_sha256 == PINNED_FULL_SELECTION_MANIFEST_SHA256
    )
    reasons: list[str] = []
    if selection is not None:
        reasons.append("smoke_selection")
    if contract != PINNED_PARSEBENCH_CONTRACT:
        reasons.append("noncanonical_contract")
    elif selection is None and not complete_release:
        reasons.append("incomplete_official_selection")
    elif selection is None and not pinned_selection:
        reasons.append("unpinned_selection_manifest")
    if (
        selection is None
        and contract == PINNED_PARSEBENCH_CONTRACT
        and complete_release
        and runtime_image != PINNED_PARSEBENCH_RUNTIME_IMAGE
    ):
        reasons.append("unapproved_verifier_runtime")
    if not immutable_runtime_image:
        reasons.append("mutable_runtime_image")
    kind = (
        "smoke"
        if selection is not None
        else ("official-full" if contract == PINNED_PARSEBENCH_CONTRACT else "custom")
    )
    return _PackageScope(
        kind=kind,
        selection_manifest_sha256=selection_manifest_sha256,
        publishable=not reasons and complete_release and pinned_selection,
        non_publishable_reasons=tuple(reasons),
        selected_execution_count=len(selected),
        runtime_image=runtime_image,
    )


def _private_ground_truth(execution: ParseBenchExecution) -> bytes:
    document = {
        "schema_version": GROUND_TRUTH_SCHEMA_VERSION,
        "dataset_revision": PINNED_PARSEBENCH_CONTRACT.dataset_revision,
        "scorer_revision": PINNED_PARSEBENCH_CONTRACT.scorer_revision,
        "task_id": execution.task_id,
        "source": {
            "path": execution.source_path,
            "runtime_path": f"/workspace/input/document{execution.source_file.suffix.lower()}",
            "size": execution.source_size,
            "sha256": execution.source_sha256,
            "page": execution.page,
        },
        "dimensions": [dimension.value for dimension in execution.dimensions],
        "rules": [
            {
                "id": rule.rule_id,
                "dimension": rule.dimension.value,
                "type": rule.rule_type,
                "rule": rule.rule_payload,
                "page": rule.page,
                "expected_markdown": rule.expected_markdown,
                "tags": list(rule.tags),
                "provenance": {
                    "source_jsonl": rule.source_jsonl,
                    "source_line": rule.source_line,
                },
            }
            for rule in execution.rules
        ],
    }
    return _canonical_json(document, newline=True)


def _public_task_contract(execution: ParseBenchExecution) -> bytes:
    return _render_public_task_contract(
        task_id=execution.task_id,
        source_runtime_path=(
            f"/workspace/input/document{execution.source_file.suffix.lower()}"
        ),
        source_sha256=execution.source_sha256,
        source_size=execution.source_size,
        page=execution.page,
    )


def _public_scope_contract(scope: _PackageScope) -> bytes:
    return _render_public_scope_contract(scope.to_dict())


def _instruction(execution: ParseBenchExecution) -> bytes:
    return _render_instruction(
        source_runtime_path=(
            f"/workspace/input/document{execution.source_file.suffix.lower()}"
        ),
        page=execution.page,
    )


def _task_toml() -> bytes:
    return _render_task_toml()


def _dockerfile(*, runtime_image: str) -> bytes:
    return _render_agent_dockerfile(runtime_image=runtime_image)


def _verifier_dockerfile(*, runtime_image: str) -> bytes:
    return _render_verifier_dockerfile(runtime_image=runtime_image)


def _verifier_script() -> bytes:
    return _render_verifier_script()


def _tar_add_directory(archive: tarfile.TarFile, name: str) -> None:
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    info.mode = 0o755
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    archive.addfile(info)


def _tar_add_bytes(
    archive: tarfile.TarFile,
    name: str,
    content: bytes,
    *,
    mode: int = 0o644,
) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(content)
    info.mode = mode
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    archive.addfile(info, BytesIO(content))


def _tar_add_file(
    archive: tarfile.TarFile,
    name: str,
    source_path: Path,
    *,
    expected_size: int,
    expected_sha256: str,
) -> None:
    if (
        source_path.stat().st_size != expected_size
        or _sha256_file(source_path) != expected_sha256
    ):
        raise ParseBenchDatasetError(
            "source_changed",
            "ParseBench source material changed during package authoring",
        )
    info = tarfile.TarInfo(name)
    info.size = expected_size
    info.mode = 0o644
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    with source_path.open("rb") as source:
        archive.addfile(info, source)


def _write_task_archive(
    execution: ParseBenchExecution,
    destination: Path,
    *,
    runtime_image: str,
    scope: _PackageScope,
) -> None:
    prefix = execution.task_id
    suffix = execution.source_file.suffix.lower()
    # Keep dependent compression streams nested so each one is finalized before
    # its backing stream exits.
    with destination.open("wb") as raw:  # noqa: SIM117
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(
                fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT
            ) as archive:
                for directory in (
                    prefix,
                    f"{prefix}/environment",
                    f"{prefix}/environment/input",
                    f"{prefix}/tests",
                    f"{prefix}/tests/input",
                    f"{prefix}/tests/parsebench_runtime",
                    f"{prefix}/tests/scorer",
                ):
                    _tar_add_directory(archive, directory)
                _tar_add_bytes(
                    archive,
                    f"{prefix}/task.toml",
                    _task_toml(),
                )
                _tar_add_bytes(
                    archive, f"{prefix}/instruction.md", _instruction(execution)
                )
                _tar_add_bytes(
                    archive,
                    f"{prefix}/environment/Dockerfile",
                    _dockerfile(runtime_image=runtime_image),
                )
                _tar_add_bytes(
                    archive,
                    f"{prefix}/environment/{PARSEBENCH_TASK_FILENAME}",
                    _public_task_contract(execution),
                )
                _tar_add_bytes(
                    archive,
                    f"{prefix}/environment/{PARSEBENCH_SCOPE_FILENAME}",
                    _public_scope_contract(scope),
                )
                _tar_add_file(
                    archive,
                    f"{prefix}/environment/input/document{suffix}",
                    execution.source_file,
                    expected_size=execution.source_size,
                    expected_sha256=execution.source_sha256,
                )
                _tar_add_bytes(
                    archive,
                    f"{prefix}/tests/Dockerfile",
                    _verifier_dockerfile(runtime_image=runtime_image),
                )
                _tar_add_bytes(
                    archive,
                    f"{prefix}/tests/{PARSEBENCH_SCOPE_FILENAME}",
                    _public_scope_contract(scope),
                )
                _tar_add_file(
                    archive,
                    f"{prefix}/tests/input/document{suffix}",
                    execution.source_file,
                    expected_size=execution.source_size,
                    expected_sha256=execution.source_sha256,
                )
                _tar_add_bytes(
                    archive,
                    f"{prefix}/tests/test.sh",
                    _verifier_script(),
                    mode=0o755,
                )
                _tar_add_bytes(
                    archive,
                    f"{prefix}/tests/ground_truth.json",
                    _private_ground_truth(execution),
                )
                for name, content in _scorer_build_files().items():
                    _tar_add_bytes(archive, f"{prefix}/tests/scorer/{name}", content)
                for name, content in _verifier_runtime_modules().items():
                    _tar_add_bytes(
                        archive,
                        f"{prefix}/tests/parsebench_runtime/{name}",
                        content,
                    )


def _artifact_specs() -> list[dict[str, str]]:
    return _render_artifact_specs()


def _catalog_row(
    execution: ParseBenchExecution,
    *,
    dataset_id: str,
    instruction: str,
    scope: _PackageScope,
) -> dict[str, object]:
    return {
        "dataset_id": dataset_id,
        "sample_id": execution.task_id,
        "task_id": execution.task_id,
        "source": (
            f"parsebench@{PINNED_PARSEBENCH_CONTRACT.dataset_revision}/"
            f"{execution.task_id}"
        ),
        "source_size": execution.source_size,
        "source_sha256": execution.source_sha256,
        "source_revision": PINNED_PARSEBENCH_CONTRACT.dataset_revision,
        "scorer_revision": PINNED_PARSEBENCH_CONTRACT.scorer_revision,
        "page": execution.page,
        "instruction": instruction,
        "instruction_source": f"tasks/{execution.task_id}/instruction.md",
        "task_dir": f"tasks/{execution.task_id}.tar.gz",
        "task_material_kind": TASK_ARCHIVE_SCHEMA_VERSION,
        "benchmark_scope": scope.to_dict(),
        "task_contract": {
            "schema_version": PARSEBENCH_TASK_SCHEMA_VERSION,
            "path": f"environment/{PARSEBENCH_TASK_FILENAME}",
            "runtime_path": PARSEBENCH_TASK_RUNTIME_PATH,
        },
        "artifact_specs": _artifact_specs(),
    }


def _zip_info(name: str, *, compress_type: int) -> ZipInfo:
    info = ZipInfo(name, date_time=_ZIP_TIMESTAMP)
    info.create_system = 3
    info.compress_type = compress_type
    info.external_attr = 0o100644 << 16
    return info


def _zip_bytes(package: ZipFile, name: str, content: bytes) -> None:
    package.writestr(_zip_info(name, compress_type=ZIP_DEFLATED), content)


def _zip_file(package: ZipFile, name: str, source_path: Path) -> None:
    info = _zip_info(name, compress_type=ZIP_STORED)
    info.file_size = source_path.stat().st_size
    with source_path.open("rb") as source, package.open(info, "w") as destination:
        while chunk := source.read(1024 * 1024):
            destination.write(chunk)


def _validate_identity(value: str, *, field: str) -> str:
    if _IDENTITY_PATTERN.fullmatch(value) is None:
        raise ParseBenchDatasetError(
            "package_identity_invalid",
            f"ParseBench package {field} is invalid",
        )
    return value


def _validate_runtime_image(
    value: str,
    *,
    allow_mutable_local_image: bool,
) -> tuple[str, bool]:
    if _RUNTIME_IMAGE_PATTERN.fullmatch(value) is None:
        raise ParseBenchDatasetError(
            "runtime_image_invalid",
            "ParseBench runtime image reference is invalid",
        )
    immutable = _IMMUTABLE_RUNTIME_IMAGE_PATTERN.fullmatch(value) is not None
    if not immutable and not allow_mutable_local_image:
        raise ParseBenchDatasetError(
            "runtime_image_mutable",
            "ParseBench runtime image must be pinned by sha256 digest",
        )
    return value, immutable


def _dataset_yaml(
    *,
    dataset_id: str,
    service_name: str,
    runtime_image: str,
    selection: SmokeSelection | None,
    scope: _PackageScope,
) -> bytes:
    selection_kind = (
        "smoke"
        if selection is not None
        else ("full" if scope.kind == "official-full" else "custom")
    )
    lines = [
        f"schema_version: {json.dumps(DATASET_PACKAGE_SCHEMA_VERSION)}",
        f"dataset_id: {json.dumps(dataset_id)}",
        f"service_name: {json.dumps(service_name)}",
        "catalog: dataset.jsonl",
        "materials: manifest.json",
        "params:",
        '  artifact_contract: "parsebench.parse-output/v1"',
        "  dataset_revision: "
        + json.dumps(PINNED_PARSEBENCH_CONTRACT.dataset_revision),
        "  scorer_revision: " + json.dumps(PINNED_PARSEBENCH_CONTRACT.scorer_revision),
        f"  authoring_contract: {json.dumps(TASK_ARCHIVE_SCHEMA_VERSION)}",
        f"  converter: {json.dumps(CONVERTER_VERSION)}",
        f"  runtime_image: {json.dumps(runtime_image)}",
        f"  selection: {json.dumps(selection_kind)}",
        f"  scope: {json.dumps(scope.kind)}",
        "  selection_manifest_sha256: " + json.dumps(scope.selection_manifest_sha256),
        f"  publishable: {str(scope.publishable).lower()}",
        "  non_publishable_reasons: " + json.dumps(list(scope.non_publishable_reasons)),
        "  pinned_material_manifest_sha256: "
        + json.dumps(PINNED_MATERIAL_MANIFEST_SHA256),
        '  agent_network_mode: "no-network"',
    ]
    if selection is not None:
        lines.extend(
            (
                f"  smoke_per_dimension: {selection.per_dimension}",
                f"  smoke_seed: {json.dumps(selection.seed, ensure_ascii=False)}",
            )
        )
    return ("\n".join(lines) + "\n").encode("utf-8")


def _readme(
    *,
    selection: SmokeSelection | None,
    scope: _PackageScope,
) -> bytes:
    if selection is not None:
        selection_text = (
            "a deterministic five-dimension smoke subset "
            f"({selection.per_dimension} hash-ranked execution(s) per dimension, "
            f"seed `{selection.seed}`)"
        )
    elif scope.kind == "official-full":
        selection_text = "the complete 2,078-execution release"
    else:
        selection_text = (
            f"a noncanonical custom selection ({scope.selected_execution_count} "
            "execution(s))"
        )
    publication = (
        "This is a publishable official-full package."
        if scope.publishable
        else (
            "This package is not publishable (`publishable=false`): "
            + ", ".join(scope.non_publishable_reasons)
            + "."
        )
    )
    return (
        "# ParseBench executable Dataset\n\n"
        f"This package contains {selection_text} from ParseBench dataset revision "
        f"`{PINNED_PARSEBENCH_CONTRACT.dataset_revision}` and binds scorer revision "
        f"`{PINNED_PARSEBENCH_CONTRACT.scorer_revision}`. {publication}\n\n"
        f"Selection manifest: `{scope.selection_manifest_sha256}`.\n\n"
        "Source documents are copied into each Task's public `environment/input/` "
        "and isolated verifier `tests/input/` trees so both containers see the same "
        "pinned bytes. "
        "Rules, tags, and expected Markdown are placed only in verifier-owned "
        "`tests/ground_truth.json`; they are intentionally absent from the catalog, "
        "instruction, and public task contract. Harbor collects agent outputs from "
        "`/logs/artifacts`, stops the agent environment, and then starts the isolated "
        "verifier. Both authored environments default to `no-network`; production "
        "may add only an explicit model-proxy host at deployment time. Local smoke "
        "runs that need connectivity must use an explicit non-publishable mode and "
        "an external/model proxy. No network fetch is performed while authoring "
        "this package. Agent and model choices belong to the evaluation run. "
        "Both task images use a public Python base; the verifier installs its "
        "own hash-pinned official scorer from public sources at image build time.\n"
    ).encode()


def _provenance(
    dataset: ParseBenchSourceDataset,
    selected: Sequence[ParseBenchExecution],
    *,
    runtime_image: str,
    selection: SmokeSelection | None,
    scope: _PackageScope,
) -> dict[str, object]:
    selected_execution_counts = Counter(
        dimension for execution in selected for dimension in execution.dimensions
    )
    selection_payload: dict[str, object] = {
        "kind": "full" if scope.kind == "official-full" else "custom"
    }
    if selection is not None:
        selection_payload = {
            "kind": "balanced-smoke",
            "per_dimension": selection.per_dimension,
            "seed": selection.seed,
        }
    return {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "converter": CONVERTER_VERSION,
        "dataset_revision": dataset.dataset_revision,
        "scorer_revision": dataset.scorer_revision,
        "revision_evidence": dataset.revision_evidence,
        "runtime_image": runtime_image,
        "pinned_material_manifest_sha256": PINNED_MATERIAL_MANIFEST_SHA256,
        "pinned_material_count": len(PINNED_PARSEBENCH_CONTRACT.material_digests),
        "benchmark_scope": scope.to_dict(),
        "task_contract": {
            "schema_version": PARSEBENCH_TASK_SCHEMA_VERSION,
            "filename": PARSEBENCH_TASK_FILENAME,
            "runtime_path": PARSEBENCH_TASK_RUNTIME_PATH,
        },
        "source_files": [
            {
                "path": item.relative_path,
                "size": item.size,
                "sha256": item.sha256,
                "row_count": item.row_count,
            }
            for item in dataset.source_files
        ],
        "source_rule_count": len(dataset.rules),
        "source_execution_count": len(dataset.executions),
        "selected_rule_count": sum(len(item.rules) for item in selected),
        "selected_execution_count": len(selected),
        "selected_dimension_execution_counts": {
            dimension.value: selected_execution_counts[dimension]
            for dimension in ParseBenchDimension
        },
        "selection": selection_payload,
    }


def build_parsebench_executable_dataset(
    source_root: Path,
    output: Path,
    *,
    contract: ParseBenchContract = PINNED_PARSEBENCH_CONTRACT,
    selection: SmokeSelection | None = None,
    dataset_id: str | None = None,
    service_name: str = DEFAULT_SERVICE_NAME,
    runtime_image: str = DEFAULT_RUNTIME_IMAGE,
    allow_mutable_local_image: bool = False,
) -> PackageBuildResult:
    """Build one deterministic ``yolo-dataset-package/v2`` ZIP entirely offline."""

    if (
        contract.dataset_revision != PINNED_PARSEBENCH_CONTRACT.dataset_revision
        or contract.scorer_revision != PINNED_PARSEBENCH_CONTRACT.scorer_revision
    ):
        raise ParseBenchDatasetError(
            "authoring_contract_unpinned",
            "Executable ParseBench authoring only supports the pinned release",
        )
    service_name = _validate_identity(service_name, field="service_name")
    if not isinstance(allow_mutable_local_image, bool):
        raise TypeError("allow_mutable_local_image must be a bool")
    runtime_image, immutable_runtime_image = _validate_runtime_image(
        runtime_image,
        allow_mutable_local_image=allow_mutable_local_image,
    )
    dataset = load_parsebench_checkout(source_root, contract=contract)
    selected = (
        dataset.executions
        if selection is None
        else select_smoke_executions(dataset.executions, selection)
    )
    if not selected:
        raise ParseBenchDatasetError(
            "selection_empty",
            "ParseBench package selection contains no executions",
        )
    scope = _package_scope(
        dataset,
        selected,
        contract=contract,
        selection=selection,
        runtime_image=runtime_image,
        immutable_runtime_image=immutable_runtime_image,
    )
    if dataset_id is None:
        if scope.publishable:
            dataset_id = DEFAULT_DATASET_ID
        else:
            scope_suffix = scope.selection_manifest_sha256.removeprefix("sha256:")[:12]
            qualifier = "smoke" if scope.kind == "smoke" else "local"
            dataset_id = f"{DEFAULT_DATASET_ID}-{qualifier}-{scope_suffix}"
    elif not scope.publishable and dataset_id == DEFAULT_DATASET_ID:
        raise ParseBenchDatasetError(
            "dataset_id_scope_mismatch",
            "A non-publishable ParseBench package cannot use the official dataset_id",
        )
    dataset_id = _validate_identity(dataset_id, field="dataset_id")
    output = Path(output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    source_paths = {item.source_file.resolve() for item in dataset.executions} | {
        (dataset.source_root / item.relative_path).resolve()
        for item in dataset.source_files
    }
    if output in source_paths:
        raise ParseBenchDatasetError(
            "output_conflict",
            "ParseBench package output overlaps pinned source material",
        )
    with tempfile.TemporaryDirectory(
        prefix=".parsebench-package-",
        dir=output.parent,
    ) as temporary_directory:
        work_root = Path(temporary_directory)
        built_tasks: list[
            tuple[ParseBenchExecution, Path, str, int, dict[str, object]]
        ] = []
        for execution in selected:
            archive_file = work_root / f"{execution.task_id}.tar.gz"
            _write_task_archive(
                execution,
                archive_file,
                runtime_image=runtime_image,
                scope=scope,
            )
            digest = _sha256_file(archive_file)
            instruction = _instruction(execution).decode("utf-8")
            built_tasks.append(
                (
                    execution,
                    archive_file,
                    digest,
                    archive_file.stat().st_size,
                    _catalog_row(
                        execution,
                        dataset_id=dataset_id,
                        instruction=instruction,
                        scope=scope,
                    ),
                )
            )
        catalog = b"".join(
            _canonical_json(item[4], newline=True) for item in built_tasks
        )
        manifest = {
            "schema_version": MATERIAL_MANIFEST_SCHEMA_VERSION,
            "benchmark_scope": scope.to_dict(),
            "selection_manifest": _selection_manifest(dataset, selected),
            "catalog": {
                "path": "dataset.jsonl",
                "size": len(catalog),
                "sha256": "sha256:" + sha256(catalog).hexdigest(),
            },
            "tasks": [
                {
                    "task_id": execution.task_id,
                    "path": f"tasks/{execution.task_id}.tar.gz",
                    "size": size,
                    "sha256": digest,
                }
                for execution, _file, digest, size, _row in built_tasks
            ],
            "provenance": _provenance(
                dataset,
                selected,
                runtime_image=runtime_image,
                selection=selection,
                scope=scope,
            ),
        }
        temporary_output = work_root / "package.zip"
        with ZipFile(temporary_output, "w", allowZip64=True) as package:
            _zip_bytes(
                package,
                "dataset.yaml",
                _dataset_yaml(
                    dataset_id=dataset_id,
                    service_name=service_name,
                    runtime_image=runtime_image,
                    selection=selection,
                    scope=scope,
                ),
            )
            _zip_bytes(package, "dataset.jsonl", catalog)
            _zip_bytes(package, "manifest.json", _canonical_json(manifest))
            _zip_bytes(
                package,
                "README.md",
                _readme(selection=selection, scope=scope),
            )
            for execution, archive_file, _digest, _size, _row in built_tasks:
                _zip_file(
                    package,
                    f"tasks/{execution.task_id}.tar.gz",
                    archive_file,
                )
        package_sha256 = _sha256_file(temporary_output)
        os.replace(temporary_output, output)
    dimensions = tuple(
        dimension
        for dimension in ParseBenchDimension
        if any(dimension in execution.dimensions for execution in selected)
    )
    return PackageBuildResult(
        output=output,
        package_sha256=package_sha256,
        task_count=len(selected),
        rule_count=sum(len(item.rules) for item in selected),
        dimensions=dimensions,
        selection=selection,
        publishable=scope.publishable,
        selection_manifest_sha256=scope.selection_manifest_sha256,
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Convert a local pinned ParseBench checkout into a deterministic "
            "yolo-dataset-package/v2 archive without network access."
        )
    )
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--dataset-id")
    parser.add_argument("--service-name", default=DEFAULT_SERVICE_NAME)
    parser.add_argument(
        "--base-image",
        "--runtime-image",
        dest="runtime_image",
        default=DEFAULT_RUNTIME_IMAGE,
    )
    parser.add_argument(
        "--allow-mutable-local-image",
        action="store_true",
        help="allow a mutable tagged image and mark the package non-publishable",
    )
    parser.add_argument("--smoke-per-dimension", type=int)
    parser.add_argument("--smoke-seed", default="parsebench-smoke-v1")
    arguments = parser.parse_args(argv)
    selection = None
    if arguments.smoke_per_dimension is not None:
        selection = SmokeSelection(
            per_dimension=arguments.smoke_per_dimension,
            seed=arguments.smoke_seed,
        )
    result = build_parsebench_executable_dataset(
        arguments.source,
        arguments.output,
        selection=selection,
        dataset_id=arguments.dataset_id,
        service_name=arguments.service_name,
        runtime_image=arguments.runtime_image,
        allow_mutable_local_image=arguments.allow_mutable_local_image,
    )
    print(
        json.dumps(
            {
                "output": str(result.output),
                "sha256": result.package_sha256,
                "task_count": result.task_count,
                "rule_count": result.rule_count,
                "dimensions": [dimension.value for dimension in result.dimensions],
                "publishable": result.publishable,
                "selection_manifest_sha256": result.selection_manifest_sha256,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()


__all__ = (
    "DEFAULT_DATASET_ID",
    "DEFAULT_RUNTIME_IMAGE",
    "DEFAULT_SERVICE_NAME",
    "build_parsebench_executable_dataset",
    "load_parsebench_checkout",
    "main",
    "select_smoke_executions",
    "task_id_for_source",
)
