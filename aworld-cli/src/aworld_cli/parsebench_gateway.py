"""Typed mcpgateway client and reduction boundary for ParseBench runs."""

from __future__ import annotations

import fcntl
import gzip
import hashlib
import json
import math
import os
import re
import secrets
import stat
import tarfile
import tempfile
import time
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from zipfile import ZIP_STORED, BadZipFile, ZipFile, ZipInfo

import httpx
import yaml
from typing_extensions import Self

from aworld.benchmarks.parsebench.contracts import (
    CONVERTER_VERSION,
    DATASET_PACKAGE_SCHEMA_VERSION,
    DATASET_REVISION,
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
    SCORER_REVISION,
    SELECTION_MANIFEST_SCHEMA_VERSION,
    TASK_ARCHIVE_SCHEMA_VERSION,
    ParseBenchDimension,
)
from aworld.benchmarks.parsebench.package_contract import (
    agent_dockerfile,
    artifact_specs,
    instruction,
    public_scope_contract,
    public_task_contract,
    task_toml,
    verifier_dockerfile,
    verifier_script,
)
from aworld.benchmarks.parsebench.scoring import (
    ParseBenchCaseResult,
    reduce_parsebench_case_results,
)

DEFAULT_MODEL_PROFILE = "default__gemini-3.1-pro-preview"
RUN_MANIFEST_SCHEMA = "aworld.parsebench.gateway-run/v4"
REPORT_SCHEMA = "aworld.parsebench.gateway-report/v3"
SUBMISSION_INTENT_SCHEMA = "aworld.parsebench.gateway-submission-intent/v3"
IMAGE_BUILD_RECEIPT_SCHEMA = "aworld.parsebench.dataset-images/v2"
_PACKAGE_MANIFEST_SCHEMA = MATERIAL_MANIFEST_SCHEMA_VERSION
_IMPORT_PATH = "api/v1/dataset-meta/package/import"
_DATASET_IMAGE_PATH = "api/v1/dataset-meta/{dataset_id}/images"
_DATASET_IMAGE_BUILD_PATH = "api/v1/dataset-meta/{dataset_id}/images/build"
_TASK_IMAGE_DETAIL_PATH = "api/v1/dataset-meta/{dataset_id}/tasks/{task_id}"
_TASK_IMAGE_BUILD_PATH = "api/v1/dataset-meta/{dataset_id}/tasks/{task_id}/image/build"
_SUBMIT_PATH = "api/batch/submit"
_RESULTS_PATH = "api/batch/results"
_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
_PROFILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_CONTROL_BYTES = 16 * 1024 * 1024
_MAX_GROUND_TRUTH_BYTES = 8 * 1024 * 1024
_MAX_PACKAGE_BYTES = 4 * 1024 * 1024 * 1024
_MAX_TASK_ARCHIVE_BYTES = 1024 * 1024 * 1024
_MAX_TASK_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024
_MAX_TASK_MEMBERS = 64
_MAX_ZIP_ENTRIES = PINNED_PARSEBENCH_CONTRACT.unique_execution_count + 4
_MAX_BATCH_RESULTS = 10_000
_MAX_BATCH_RESULTS_BYTES = 64 * 1024 * 1024
_ACTIVE_IMAGE_STATUSES = frozenset({"QUEUED", "SUBMITTING", "BUILDING", "UNKNOWN"})
_FAILED_IMAGE_STATUSES = frozenset({"FAILED", "PARTIAL_FAILED", "SOURCE_MISSING"})
_IMAGE_RUNTIME_TYPES = frozenset({"offline", "online"})
_IMMUTABLE_IMAGE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._/:-]{0,430}@sha256:[0-9a-f]{64}$"
)
_RUNTIME_IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@:-]{0,510}$")
_SOURCE_RUNTIME_PATH = re.compile(
    r"^/workspace/input/document(?:\.pdf|\.png|\.jpg|\.jpeg|\.jfif|\.docx)$"
)
_SELECTION_MATERIAL_PATHS = frozenset(
    {
        "task.toml",
        "instruction.md",
        f"environment/{PARSEBENCH_TASK_FILENAME}",
        "tests/test.sh",
        "tests/ground_truth.json",
    }
)


class ParseBenchGatewayError(RuntimeError):
    """Stable error that does not retain credentials or remote response bodies."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _canonical_json(value: object, *, newline: bool = True) -> bytes:
    content = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return content + (b"\n" if newline else b"")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_json(path: Path | str, value: object) -> None:
    """Atomically publish one deterministic control-plane JSON document."""

    destination = Path(path).expanduser()
    if not destination.is_absolute():
        destination = Path.cwd() / destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_canonical_json(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _output_lock(destination: Path):
    lock_path = destination.parent / f".{destination.name}.lock"
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError:
        raise ParseBenchGatewayError(
            "output_unavailable", "ParseBench output lock cannot be opened"
        ) from None
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def reserve_json_output(path: Path | str, value: object) -> Path:
    """Exclusively reserve an output path with a canonical recovery document."""

    destination = Path(path).expanduser()
    if not destination.is_absolute():
        destination = Path.cwd() / destination
    payload = _canonical_json(value)
    destination.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    created_identity: tuple[int, int] | None = None
    try:
        with _output_lock(destination):
            try:
                descriptor = os.open(destination, flags, 0o600)
            except FileExistsError:
                raise ParseBenchGatewayError(
                    "output_exists",
                    "Refusing to overwrite an existing ParseBench output",
                ) from None
            except OSError:
                raise ParseBenchGatewayError(
                    "output_unavailable", "ParseBench output cannot be reserved"
                ) from None
            created_metadata = os.fstat(descriptor)
            created_identity = (created_metadata.st_dev, created_metadata.st_ino)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            _fsync_directory(destination.parent)
    except BaseException:
        if created_identity is not None:
            try:
                with _output_lock(destination):
                    metadata = destination.lstat()
                    if (
                        stat.S_ISREG(metadata.st_mode)
                        and (metadata.st_dev, metadata.st_ino) == created_identity
                    ):
                        destination.unlink()
                        _fsync_directory(destination.parent)
            except (OSError, ParseBenchGatewayError):
                pass
        raise
    return destination


def release_json_reservation(path: Path | str, value: object) -> None:
    """Remove only the unchanged reservation created by ``reserve_json_output``."""

    destination = Path(path).expanduser()
    if not destination.is_absolute():
        destination = Path.cwd() / destination
    expected = _canonical_json(value)
    try:
        with _output_lock(destination):
            metadata = destination.lstat()
            if stat.S_ISREG(metadata.st_mode) and destination.read_bytes() == expected:
                destination.unlink()
                _fsync_directory(destination.parent)
    except OSError:
        return


def finalize_json_output(
    path: Path | str,
    *,
    reservation: object,
    value: object,
) -> None:
    """Publish only if the caller still owns the exact output reservation."""

    destination = Path(path).expanduser()
    if not destination.is_absolute():
        destination = Path.cwd() / destination
    expected = _canonical_json(reservation)
    with _output_lock(destination):
        try:
            metadata = destination.lstat()
            owned = (
                stat.S_ISREG(metadata.st_mode) and destination.read_bytes() == expected
            )
        except OSError:
            owned = False
        if not owned:
            raise ParseBenchGatewayError(
                "output_ownership_lost",
                "ParseBench output reservation ownership was lost",
            )
        atomic_write_json(destination, value)


def _sha256_stream(stream: Any) -> str:
    digest = hashlib.sha256()
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _file_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _safe_archive_name(value: str) -> bool:
    path = PurePosixPath(value)
    return (
        bool(value)
        and not path.is_absolute()
        and all(part not in {"", ".", ".."} for part in path.parts)
    )


def _read_zip_control(archive: ZipFile, name: str, *, limit: int) -> bytes:
    names = [item for item in archive.infolist() if item.filename == name]
    if len(names) != 1 or names[0].is_dir() or names[0].file_size > limit:
        raise ParseBenchGatewayError(
            "package_control_invalid",
            f"ParseBench package has an invalid {name} control file",
        )
    with archive.open(names[0]) as stream:
        payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise ParseBenchGatewayError(
            "package_control_invalid",
            f"ParseBench package {name} exceeds the control-file limit",
        )
    return payload


class _DuplicateJsonKey(ValueError):
    pass


def _strict_json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKey(key)
        value[key] = item
    return value


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _json_object(payload: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            payload,
            object_pairs_hook=_strict_json_pairs,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, ValueError, RecursionError):
        raise ParseBenchGatewayError(
            "package_control_invalid", f"ParseBench package {label} is invalid JSON"
        ) from None
    if not isinstance(value, dict):
        raise ParseBenchGatewayError(
            "package_control_invalid", f"ParseBench package {label} must be an object"
        )
    return value


def _zip_member_digest(archive: ZipFile, info: ZipInfo) -> str:
    digest = hashlib.sha256()
    total = 0
    try:
        with archive.open(info) as stream:
            while chunk := stream.read(1024 * 1024):
                total += len(chunk)
                if total > _MAX_TASK_ARCHIVE_BYTES:
                    raise ParseBenchGatewayError(
                        "package_manifest_invalid",
                        "ParseBench task archive exceeds the material limit",
                    )
                digest.update(chunk)
    except (BadZipFile, OSError, RuntimeError):
        raise ParseBenchGatewayError(
            "package_manifest_invalid", "ParseBench task archive cannot be read"
        ) from None
    if total != info.file_size:
        raise ParseBenchGatewayError(
            "package_manifest_invalid",
            "ParseBench task archive size changed while reading",
        )
    return "sha256:" + digest.hexdigest()


class _BoundedReader:
    def __init__(self, source: Any, *, limit: int) -> None:
        self._source = source
        self._limit = limit
        self._total = 0

    def read(self, size: int = -1) -> bytes:
        remaining = self._limit - self._total
        bounded_size = remaining + 1 if size < 0 else min(size, remaining + 1)
        payload = self._source.read(bounded_size)
        self._total += len(payload)
        if self._total > self._limit:
            raise ParseBenchGatewayError(
                "package_manifest_invalid",
                "ParseBench task archive exceeds the expanded material limit",
            )
        return payload


def _yaml_object(payload: bytes, *, label: str) -> dict[str, Any]:
    class StrictSafeLoader(yaml.SafeLoader):
        def compose_node(self, parent: Any, index: Any) -> Any:
            if self.check_event(yaml.AliasEvent):
                raise yaml.YAMLError("YAML aliases are forbidden")
            return super().compose_node(parent, index)

        def construct_mapping(self, node: Any, deep: bool = False) -> dict[str, Any]:
            if not isinstance(node, yaml.MappingNode):
                raise yaml.YAMLError("YAML mapping is invalid")
            self.flatten_mapping(node)
            mapping: dict[str, Any] = {}
            for key_node, value_node in node.value:
                key = self.construct_object(key_node, deep=deep)
                if not isinstance(key, str) or key in mapping:
                    raise yaml.YAMLError("YAML mapping key is duplicate or invalid")
                mapping[key] = self.construct_object(value_node, deep=deep)
            return mapping

    try:
        value = yaml.load(payload, Loader=StrictSafeLoader)
    except (UnicodeError, yaml.YAMLError):
        raise ParseBenchGatewayError(
            "package_control_invalid", f"ParseBench package {label} is invalid YAML"
        ) from None
    if not isinstance(value, dict):
        raise ParseBenchGatewayError(
            "package_control_invalid", f"ParseBench package {label} must be an object"
        )
    return value


def _catalog_rows(
    payload: bytes, *, dataset_id: str
) -> tuple[tuple[str, ...], dict[str, dict[str, Any]]]:
    task_ids: list[str] = []
    seen: set[str] = set()
    rows: dict[str, dict[str, Any]] = {}
    for line_number, raw_line in enumerate(payload.splitlines(), start=1):
        if not raw_line.strip():
            continue
        row = _json_object(raw_line, label=f"dataset.jsonl line {line_number}")
        task_id = row.get("sample_id")
        if (
            row.get("dataset_id") != dataset_id
            or not isinstance(task_id, str)
            or _IDENTITY.fullmatch(task_id) is None
            or row.get("task_id") != task_id
            or task_id in seen
        ):
            raise ParseBenchGatewayError(
                "package_catalog_invalid",
                "ParseBench package catalog identity is invalid",
            )
        seen.add(task_id)
        task_ids.append(task_id)
        rows[task_id] = row
    if not task_ids:
        raise ParseBenchGatewayError(
            "package_catalog_invalid", "ParseBench package catalog is empty"
        )
    return tuple(task_ids), rows


def _coerce_expected_case_ids(
    value: object,
    *,
    task_ids: tuple[str, ...],
) -> dict[ParseBenchDimension, tuple[str, ...]] | None:
    if not isinstance(value, Mapping):
        return None
    known = set(task_ids)
    expected: dict[ParseBenchDimension, tuple[str, ...]] = {}
    observed: set[str] = set()
    for dimension in ParseBenchDimension:
        raw_ids = value.get(dimension.value)
        if not isinstance(raw_ids, list):
            return None
        case_ids: list[str] = []
        local_seen: set[str] = set()
        for case_id in raw_ids:
            if (
                not isinstance(case_id, str)
                or case_id not in known
                or case_id in local_seen
            ):
                raise ParseBenchGatewayError(
                    "package_scope_invalid",
                    "ParseBench expected-case manifest is invalid",
                )
            local_seen.add(case_id)
            observed.add(case_id)
            case_ids.append(case_id)
        expected[dimension] = tuple(case_ids)
    if observed != known:
        raise ParseBenchGatewayError(
            "package_scope_invalid",
            "ParseBench expected-case manifest does not cover every task",
        )
    return expected


def _selection_manifest_contract(
    value: object,
    *,
    task_ids: tuple[str, ...],
    catalog_rows: Mapping[str, Mapping[str, Any]],
) -> tuple[
    dict[ParseBenchDimension, tuple[str, ...]],
    dict[str, dict[str, Any]],
    str,
]:
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version",
        "dataset_revision",
        "scorer_revision",
        "cases",
        "dimension_task_ids",
    }:
        raise ParseBenchGatewayError(
            "package_scope_invalid", "ParseBench selection manifest is missing"
        )
    if (
        value.get("schema_version") != SELECTION_MANIFEST_SCHEMA_VERSION
        or value.get("dataset_revision") != DATASET_REVISION
        or value.get("scorer_revision") != SCORER_REVISION
    ):
        raise ParseBenchGatewayError(
            "package_scope_invalid", "ParseBench selection manifest identity is invalid"
        )
    raw_cases = value.get("cases")
    raw_dimensions = value.get("dimension_task_ids")
    if (
        not isinstance(raw_cases, list)
        or not isinstance(raw_dimensions, Mapping)
        or set(raw_dimensions) != {dimension.value for dimension in ParseBenchDimension}
    ):
        raise ParseBenchGatewayError(
            "package_scope_invalid", "ParseBench selection manifest shape is invalid"
        )

    cases: dict[str, dict[str, Any]] = {}
    case_order: list[str] = []
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict) or set(raw_case) != {
            "task_id",
            "source_runtime_path",
            "source_sha256",
            "source_size",
            "page",
            "dimensions",
            "materials",
        }:
            raise ParseBenchGatewayError(
                "package_scope_invalid", "ParseBench selection case is invalid"
            )
        task_id = raw_case.get("task_id")
        source_runtime_path = raw_case.get("source_runtime_path")
        source_sha256 = raw_case.get("source_sha256")
        source_size = raw_case.get("source_size")
        page = raw_case.get("page")
        raw_case_dimensions = raw_case.get("dimensions")
        raw_materials = raw_case.get("materials")
        if (
            not isinstance(task_id, str)
            or task_id in cases
            or task_id not in catalog_rows
            or not isinstance(source_runtime_path, str)
            or _SOURCE_RUNTIME_PATH.fullmatch(source_runtime_path) is None
            or not isinstance(source_sha256, str)
            or _SHA256.fullmatch(source_sha256) is None
            or isinstance(source_size, bool)
            or not isinstance(source_size, int)
            or not 1 <= source_size <= _MAX_TASK_EXPANDED_BYTES
            or (
                page is not None
                and (isinstance(page, bool) or not isinstance(page, int) or page < 1)
            )
            or not isinstance(raw_case_dimensions, list)
            or not raw_case_dimensions
            or not isinstance(raw_materials, Mapping)
            or set(raw_materials) != _SELECTION_MATERIAL_PATHS
        ):
            raise ParseBenchGatewayError(
                "package_scope_invalid", "ParseBench selection case identity is invalid"
            )
        try:
            dimensions = tuple(
                ParseBenchDimension(dimension) for dimension in raw_case_dimensions
            )
        except (TypeError, ValueError):
            raise ParseBenchGatewayError(
                "package_scope_invalid",
                "ParseBench selection case dimension is invalid",
            ) from None
        if len(set(dimensions)) != len(dimensions):
            raise ParseBenchGatewayError(
                "package_scope_invalid", "ParseBench selection case dimensions repeat"
            )
        materials: dict[str, dict[str, object]] = {}
        for material_path in sorted(_SELECTION_MATERIAL_PATHS):
            raw_material = raw_materials.get(material_path)
            material_limit = (
                _MAX_GROUND_TRUTH_BYTES
                if material_path == "tests/ground_truth.json"
                else 64 * 1024
            )
            if (
                not isinstance(raw_material, Mapping)
                or set(raw_material) != {"size", "sha256"}
                or isinstance(raw_material.get("size"), bool)
                or not isinstance(raw_material.get("size"), int)
                or not 1 <= raw_material["size"] <= material_limit
                or not isinstance(raw_material.get("sha256"), str)
                or _SHA256.fullmatch(raw_material["sha256"]) is None
            ):
                raise ParseBenchGatewayError(
                    "package_scope_invalid",
                    "ParseBench selection material attestation is invalid",
                )
            materials[material_path] = dict(raw_material)
        catalog = catalog_rows[task_id]
        if (
            catalog.get("source_sha256") != source_sha256
            or catalog.get("source_size") != source_size
            or catalog.get("page") != page
            or catalog.get("source_revision") != DATASET_REVISION
            or catalog.get("scorer_revision") != SCORER_REVISION
        ):
            raise ParseBenchGatewayError(
                "package_scope_invalid",
                "ParseBench selection case does not match the catalog",
            )
        normalized_case = dict(raw_case)
        normalized_case["dimensions"] = [dimension.value for dimension in dimensions]
        normalized_case["materials"] = materials
        cases[task_id] = normalized_case
        case_order.append(task_id)
    if tuple(case_order) != task_ids:
        raise ParseBenchGatewayError(
            "package_scope_invalid",
            "ParseBench selection cases do not match the ordered catalog",
        )

    expected = _coerce_expected_case_ids(raw_dimensions, task_ids=task_ids)
    if expected is None:
        raise ParseBenchGatewayError(
            "package_scope_invalid", "ParseBench dimension task manifest is invalid"
        )
    derived = {
        dimension: tuple(
            task_id
            for task_id in task_ids
            if dimension.value in cases[task_id]["dimensions"]
        )
        for dimension in ParseBenchDimension
    }
    if expected != derived:
        raise ParseBenchGatewayError(
            "package_scope_invalid",
            "ParseBench dimension task manifest does not match its cases",
        )
    digest = (
        "sha256:"
        + hashlib.sha256(_canonical_json(dict(value), newline=False)).hexdigest()
    )
    return expected, cases, digest


def _scope_contract(
    value: object,
    *,
    task_count: int,
    selection_digest: str,
) -> tuple[str, bool, str]:
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version",
        "kind",
        "selection_manifest_sha256",
        "publishable",
        "non_publishable_reasons",
        "selected_execution_count",
        "runtime_image",
        "dataset_revision",
        "scorer_revision",
    }:
        raise ParseBenchGatewayError(
            "package_scope_invalid", "ParseBench benchmark scope is missing"
        )
    kind = value.get("kind")
    publishable = value.get("publishable")
    reasons = value.get("non_publishable_reasons")
    runtime_image = value.get("runtime_image")
    if (
        value.get("schema_version") != PARSEBENCH_SCOPE_SCHEMA_VERSION
        or kind not in {"smoke", "official-full", "custom"}
        or not isinstance(publishable, bool)
        or not isinstance(reasons, list)
        or any(not isinstance(reason, str) or not reason for reason in reasons)
        or len(reasons) != len(set(reasons))
        or value.get("selected_execution_count") != task_count
        or value.get("selection_manifest_sha256") != selection_digest
        or value.get("dataset_revision") != DATASET_REVISION
        or value.get("scorer_revision") != SCORER_REVISION
        or not isinstance(runtime_image, str)
        or _RUNTIME_IMAGE.fullmatch(runtime_image) is None
    ):
        raise ParseBenchGatewayError(
            "package_scope_invalid", "ParseBench benchmark scope is invalid"
        )
    if publishable and (
        kind != "official-full"
        or reasons
        or _IMMUTABLE_IMAGE.fullmatch(runtime_image) is None
        or runtime_image != PINNED_PARSEBENCH_RUNTIME_IMAGE
    ):
        raise ParseBenchGatewayError(
            "package_scope_invalid",
            "A publishable ParseBench scope is not an approved immutable official release",
        )
    if not publishable and not reasons:
        raise ParseBenchGatewayError(
            "package_scope_invalid",
            "A non-publishable ParseBench scope must explain why",
        )
    return str(kind), publishable, runtime_image


def _provenance_contract(
    value: object,
    *,
    scope: Mapping[str, Any],
    task_count: int,
    expected_case_ids: Mapping[ParseBenchDimension, tuple[str, ...]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate provenance fields that bind Dataset authoring to execution."""

    required_keys = {
        "schema_version",
        "converter",
        "dataset_revision",
        "scorer_revision",
        "revision_evidence",
        "runtime_image",
        "pinned_material_manifest_sha256",
        "pinned_material_count",
        "benchmark_scope",
        "task_contract",
        "source_files",
        "source_rule_count",
        "source_execution_count",
        "selected_rule_count",
        "selected_execution_count",
        "selected_dimension_execution_counts",
        "selection",
    }
    if not isinstance(value, Mapping) or set(value) != required_keys:
        raise ParseBenchGatewayError(
            "package_revision_invalid", "ParseBench package provenance is invalid"
        )
    source_files = value.get("source_files")
    expected_source_paths = tuple(
        relative_path
        for _dimension, relative_path in PINNED_PARSEBENCH_CONTRACT.source_files
    )
    if (
        value.get("schema_version") != PROVENANCE_SCHEMA_VERSION
        or value.get("converter") != CONVERTER_VERSION
        or value.get("dataset_revision") != DATASET_REVISION
        or value.get("scorer_revision") != SCORER_REVISION
        or value.get("revision_evidence")
        not in {"huggingface-content-addressed-snapshot", "clean-git-checkout"}
        or value.get("runtime_image") != scope.get("runtime_image")
        or value.get("pinned_material_manifest_sha256")
        != PINNED_MATERIAL_MANIFEST_SHA256
        or value.get("pinned_material_count")
        != len(PINNED_PARSEBENCH_CONTRACT.material_digests)
        or value.get("benchmark_scope") != scope
        or value.get("task_contract")
        != {
            "schema_version": PARSEBENCH_TASK_SCHEMA_VERSION,
            "filename": PARSEBENCH_TASK_FILENAME,
            "runtime_path": PARSEBENCH_TASK_RUNTIME_PATH,
        }
        or not isinstance(source_files, list)
        or len(source_files) != len(expected_source_paths)
    ):
        raise ParseBenchGatewayError(
            "package_revision_invalid", "ParseBench package revisions are not pinned"
        )
    source_rule_count = 0
    observed_source_paths: list[str] = []
    for source_file in source_files:
        if (
            not isinstance(source_file, Mapping)
            or set(source_file) != {"path", "size", "sha256", "row_count"}
            or not isinstance(source_file.get("path"), str)
            or isinstance(source_file.get("size"), bool)
            or not isinstance(source_file.get("size"), int)
            or source_file["size"] < 1
            or not isinstance(source_file.get("sha256"), str)
            or _SHA256.fullmatch(source_file["sha256"]) is None
            or isinstance(source_file.get("row_count"), bool)
            or not isinstance(source_file.get("row_count"), int)
            or source_file["row_count"] < 1
        ):
            raise ParseBenchGatewayError(
                "package_revision_invalid",
                "ParseBench source provenance is invalid",
            )
        observed_source_paths.append(source_file["path"])
        source_rule_count += source_file["row_count"]
    selected_dimension_counts = value.get("selected_dimension_execution_counts")
    expected_dimension_counts = {
        dimension.value: len(expected_case_ids[dimension])
        for dimension in ParseBenchDimension
    }
    source_execution_count = value.get("source_execution_count")
    selected_rule_count = value.get("selected_rule_count")
    selection = value.get("selection")
    if (
        tuple(observed_source_paths) != expected_source_paths
        or value.get("source_rule_count") != source_rule_count
        or isinstance(source_execution_count, bool)
        or not isinstance(source_execution_count, int)
        or source_execution_count < task_count
        or isinstance(selected_rule_count, bool)
        or not isinstance(selected_rule_count, int)
        or selected_rule_count < task_count
        or value.get("selected_execution_count") != task_count
        or selected_dimension_counts != expected_dimension_counts
        or not isinstance(selection, Mapping)
    ):
        raise ParseBenchGatewayError(
            "package_revision_invalid", "ParseBench provenance counts are invalid"
        )
    kind = scope.get("kind")
    if kind == "smoke":
        if (
            set(selection) != {"kind", "per_dimension", "seed"}
            or selection.get("kind") != "balanced-smoke"
            or isinstance(selection.get("per_dimension"), bool)
            or not isinstance(selection.get("per_dimension"), int)
            or selection["per_dimension"] < 1
            or not isinstance(selection.get("seed"), str)
            or not selection["seed"]
        ):
            raise ParseBenchGatewayError(
                "package_revision_invalid",
                "ParseBench smoke provenance is invalid",
            )
    else:
        expected_selection_kind = "full" if kind == "official-full" else "custom"
        if dict(selection) != {"kind": expected_selection_kind}:
            raise ParseBenchGatewayError(
                "package_revision_invalid",
                "ParseBench selection provenance is invalid",
            )
    return dict(value), dict(selection)


def _dataset_descriptor_contract(
    dataset: Mapping[str, Any],
    *,
    dataset_id: str,
    service_name: str,
    scope: Mapping[str, Any],
    selection: Mapping[str, Any],
) -> None:
    """Reject descriptor fields that could alter mcpgateway package resolution."""

    if (
        set(dataset)
        != {
            "schema_version",
            "dataset_id",
            "service_name",
            "catalog",
            "materials",
            "params",
        }
        or dataset.get("schema_version") != DATASET_PACKAGE_SCHEMA_VERSION
        or dataset.get("dataset_id") != dataset_id
        or dataset.get("service_name") != service_name
        or dataset.get("catalog") != "dataset.jsonl"
        or dataset.get("materials") != "manifest.json"
    ):
        raise ParseBenchGatewayError(
            "package_identity_invalid", "ParseBench Dataset descriptor is invalid"
        )
    params = dataset.get("params")
    expected_params: dict[str, Any] = {
        "dataset_revision": DATASET_REVISION,
        "scorer_revision": SCORER_REVISION,
        "authoring_contract": TASK_ARCHIVE_SCHEMA_VERSION,
        "converter": CONVERTER_VERSION,
        "runtime_image": scope["runtime_image"],
        "selection": (
            "smoke"
            if scope["kind"] == "smoke"
            else ("full" if scope["kind"] == "official-full" else "custom")
        ),
        "scope": scope["kind"],
        "selection_manifest_sha256": scope["selection_manifest_sha256"],
        "publishable": scope["publishable"],
        "non_publishable_reasons": scope["non_publishable_reasons"],
        "pinned_material_manifest_sha256": PINNED_MATERIAL_MANIFEST_SHA256,
        "agent_network_mode": "no-network",
    }
    if scope["kind"] == "smoke":
        expected_params.update(
            {
                "smoke_per_dimension": selection["per_dimension"],
                "smoke_seed": selection["seed"],
            }
        )
    if not isinstance(params, Mapping) or dict(params) != expected_params:
        raise ParseBenchGatewayError(
            "package_scope_invalid",
            "ParseBench Dataset descriptor parameters are inconsistent",
        )


def _content_attestation(content: bytes) -> dict[str, object]:
    return {
        "size": len(content),
        "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
    }


def _read_and_hash(source: Any, *, limit: int) -> tuple[int, str, bytes | None]:
    digest = hashlib.sha256()
    total = 0
    retained = bytearray() if limit <= _MAX_GROUND_TRUTH_BYTES else None
    while chunk := source.read(min(1024 * 1024, limit - total + 1)):
        total += len(chunk)
        if total > limit:
            raise ParseBenchGatewayError(
                "package_task_contract_invalid",
                "ParseBench task archive material exceeds its declared limit",
            )
        digest.update(chunk)
        if retained is not None:
            retained.extend(chunk)
    return (
        total,
        "sha256:" + digest.hexdigest(),
        bytes(retained) if retained is not None else None,
    )


def _validate_tar_metadata(
    member: tarfile.TarInfo,
    *,
    is_directory: bool,
    mode: int,
) -> None:
    expected_type = tarfile.DIRTYPE if is_directory else tarfile.REGTYPE
    if (
        member.type != expected_type
        or member.mode != mode
        or member.uid != 0
        or member.gid != 0
        or member.uname != ""
        or member.gname != ""
        or member.mtime != 0
        or member.linkname != ""
        or member.pax_headers
        or (is_directory and member.size != 0)
    ):
        raise ParseBenchGatewayError(
            "package_task_contract_invalid",
            "ParseBench task archive metadata is not canonical",
        )


def _inspect_task_archive(
    archive: ZipFile,
    *,
    task_id: str,
    task_archive_path: str,
    selection_case: Mapping[str, Any],
    scope: Mapping[str, Any],
) -> tuple[tuple[ParseBenchDimension, ...], int]:
    if not _safe_archive_name(task_archive_path):
        raise ParseBenchGatewayError(
            "package_manifest_invalid", "ParseBench task archive path is unsafe"
        )
    try:
        task_stream = archive.open(task_archive_path)
    except KeyError:
        raise ParseBenchGatewayError(
            "package_manifest_invalid", "ParseBench task archive is missing"
        ) from None
    source_runtime_path = str(selection_case["source_runtime_path"])
    source_name = PurePosixPath(source_runtime_path).name
    ground_truth_name = f"{task_id}/tests/ground_truth.json"
    source_names = {
        f"{task_id}/environment/input/{source_name}",
        f"{task_id}/tests/input/{source_name}",
    }
    expected_directories = {
        task_id,
        f"{task_id}/environment",
        f"{task_id}/environment/input",
        f"{task_id}/tests",
        f"{task_id}/tests/input",
    }
    relative_expected_content = {
        "task.toml": task_toml(),
        "instruction.md": instruction(
            source_runtime_path=source_runtime_path,
            page=selection_case["page"],
        ),
        "environment/Dockerfile": agent_dockerfile(
            runtime_image=str(scope["runtime_image"])
        ),
        f"environment/{PARSEBENCH_TASK_FILENAME}": public_task_contract(
            task_id=task_id,
            source_runtime_path=source_runtime_path,
            source_sha256=str(selection_case["source_sha256"]),
            source_size=int(selection_case["source_size"]),
            page=selection_case["page"],
        ),
        f"environment/{PARSEBENCH_SCOPE_FILENAME}": public_scope_contract(scope),
        "tests/Dockerfile": verifier_dockerfile(
            runtime_image=str(scope["runtime_image"])
        ),
        f"tests/{PARSEBENCH_SCOPE_FILENAME}": public_scope_contract(scope),
        "tests/test.sh": verifier_script(),
    }
    expected_content = {
        f"{task_id}/{relative_path}": content
        for relative_path, content in relative_expected_content.items()
    }
    selection_materials = selection_case["materials"]
    for relative_path in _SELECTION_MATERIAL_PATHS - {"tests/ground_truth.json"}:
        if selection_materials[relative_path] != _content_attestation(
            relative_expected_content[relative_path]
        ):
            raise ParseBenchGatewayError(
                "package_scope_invalid",
                "ParseBench selection material does not match the task contract",
            )
    expected_files = set(expected_content) | source_names | {ground_truth_name}
    expected_names = expected_directories | expected_files
    found_ground_truth: bytes | None = None
    observed_names: set[str] = set()
    member_count = 0
    expanded_bytes = 0
    try:
        with (
            task_stream,
            gzip.GzipFile(fileobj=task_stream) as expanded_stream,
            tarfile.open(
                fileobj=_BoundedReader(
                    expanded_stream,
                    limit=_MAX_TASK_EXPANDED_BYTES,
                ),
                mode="r|",
            ) as task_archive,
        ):
            for member in task_archive:
                member_count += 1
                expanded_bytes += max(0, member.size)
                if (
                    member_count > _MAX_TASK_MEMBERS
                    or member.size < 0
                    or expanded_bytes > _MAX_TASK_EXPANDED_BYTES
                ):
                    raise ParseBenchGatewayError(
                        "package_manifest_invalid",
                        "ParseBench task archive exceeds its expanded material limits",
                    )
                if not _safe_archive_name(member.name):
                    raise ParseBenchGatewayError(
                        "package_manifest_invalid",
                        "ParseBench task archive contains an unsafe path",
                    )
                if not (member.isfile() or member.isdir()):
                    raise ParseBenchGatewayError(
                        "package_manifest_invalid",
                        "ParseBench task archive contains a special filesystem entry",
                    )
                if member.name in observed_names or member.name not in expected_names:
                    raise ParseBenchGatewayError(
                        "package_task_contract_invalid",
                        "ParseBench task archive contains duplicate or unexpected material",
                    )
                observed_names.add(member.name)
                if member.name in expected_directories:
                    _validate_tar_metadata(member, is_directory=True, mode=0o755)
                    continue
                expected_mode = (
                    0o755 if member.name.endswith("/tests/test.sh") else 0o644
                )
                _validate_tar_metadata(member, is_directory=False, mode=expected_mode)
                source = task_archive.extractfile(member)
                if source is None:
                    raise ParseBenchGatewayError(
                        "package_task_contract_invalid",
                        "ParseBench task archive material cannot be read",
                    )
                if member.name in source_names:
                    expected_size = int(selection_case["source_size"])
                    expected_sha256 = str(selection_case["source_sha256"])
                    size, digest, _content = _read_and_hash(source, limit=expected_size)
                    if (
                        member.size != expected_size
                        or size != expected_size
                        or digest != expected_sha256
                    ):
                        raise ParseBenchGatewayError(
                            "package_task_contract_invalid",
                            "ParseBench task source does not match its selection attestation",
                        )
                    continue
                if member.name == ground_truth_name:
                    ground_truth_attestation = selection_materials[
                        "tests/ground_truth.json"
                    ]
                    expected_size = int(ground_truth_attestation["size"])
                    size, digest, content = _read_and_hash(
                        source, limit=_MAX_GROUND_TRUTH_BYTES
                    )
                    if (
                        member.size != expected_size
                        or size != expected_size
                        or digest != ground_truth_attestation["sha256"]
                        or content is None
                    ):
                        raise ParseBenchGatewayError(
                            "package_ground_truth_invalid",
                            "ParseBench task ground truth does not match its selection attestation",
                        )
                    found_ground_truth = content
                    continue
                expected = expected_content[member.name]
                size, digest, _content = _read_and_hash(source, limit=len(expected))
                if (
                    member.size != len(expected)
                    or size != len(expected)
                    or digest != _content_attestation(expected)["sha256"]
                ):
                    raise ParseBenchGatewayError(
                        "package_task_contract_invalid",
                        "ParseBench task archive material does not match its contract",
                    )
    except (EOFError, tarfile.TarError, OSError):
        raise ParseBenchGatewayError(
            "package_manifest_invalid", "ParseBench task archive is invalid"
        ) from None
    if observed_names != expected_names or found_ground_truth is None:
        raise ParseBenchGatewayError(
            "package_task_contract_invalid",
            "ParseBench task archive is missing required material",
        )
    ground_truth = _json_object(found_ground_truth, label="task ground truth")
    if (
        _canonical_json(ground_truth) != found_ground_truth
        or set(ground_truth)
        != {
            "schema_version",
            "dataset_revision",
            "scorer_revision",
            "task_id",
            "source",
            "dimensions",
            "rules",
        }
        or ground_truth.get("schema_version") != GROUND_TRUTH_SCHEMA_VERSION
        or ground_truth.get("task_id") != task_id
        or ground_truth.get("dataset_revision") != DATASET_REVISION
        or ground_truth.get("scorer_revision") != SCORER_REVISION
    ):
        raise ParseBenchGatewayError(
            "package_ground_truth_invalid",
            "ParseBench task ground-truth identity is invalid",
        )
    raw_dimensions = ground_truth.get("dimensions")
    if not isinstance(raw_dimensions, list) or not raw_dimensions:
        raise ParseBenchGatewayError(
            "package_ground_truth_invalid",
            "ParseBench task ground truth has no dimensions",
        )
    try:
        dimensions = tuple(ParseBenchDimension(value) for value in raw_dimensions)
    except (TypeError, ValueError):
        raise ParseBenchGatewayError(
            "package_ground_truth_invalid",
            "ParseBench task ground truth contains an invalid dimension",
        ) from None
    if len(set(dimensions)) != len(dimensions):
        raise ParseBenchGatewayError(
            "package_ground_truth_invalid",
            "ParseBench task ground truth contains duplicate dimensions",
        )
    source = ground_truth.get("source")
    if (
        list(raw_dimensions) != selection_case.get("dimensions")
        or not isinstance(source, Mapping)
        or set(source) != {"path", "runtime_path", "size", "sha256", "page"}
        or source.get("runtime_path") != source_runtime_path
        or source.get("sha256") != selection_case.get("source_sha256")
        or source.get("size") != selection_case.get("source_size")
        or source.get("page") != selection_case.get("page")
    ):
        raise ParseBenchGatewayError(
            "package_ground_truth_invalid",
            "ParseBench task ground truth does not match the selection manifest",
        )
    rules = ground_truth.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ParseBenchGatewayError(
            "package_ground_truth_invalid",
            "ParseBench task ground truth has no rules",
        )
    for rule in rules:
        if (
            not isinstance(rule, Mapping)
            or set(rule)
            != {
                "id",
                "dimension",
                "type",
                "rule",
                "page",
                "expected_markdown",
                "tags",
                "provenance",
            }
            or rule.get("dimension") not in raw_dimensions
        ):
            raise ParseBenchGatewayError(
                "package_ground_truth_invalid",
                "ParseBench task ground truth contains an invalid rule",
            )
    return dimensions, len(rules)


@dataclass(frozen=True, slots=True)
class DatasetPublicationReceipt:
    generation: int
    root_modify_time: int
    content_sha256: str
    task_set_sha256: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.generation, bool)
            or not isinstance(self.generation, int)
            or not 1 <= self.generation <= 2**63 - 1
            or isinstance(self.root_modify_time, bool)
            or not isinstance(self.root_modify_time, int)
            or not 1 <= self.root_modify_time <= 2**63 - 1
            or not isinstance(self.content_sha256, str)
            or _SHA256.fullmatch(self.content_sha256) is None
            or not isinstance(self.task_set_sha256, str)
            or _SHA256.fullmatch(self.task_set_sha256) is None
        ):
            raise ValueError("Dataset publication receipt is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "generation": self.generation,
            "root_modify_time": self.root_modify_time,
            "content_sha256": self.content_sha256,
            "task_set_sha256": self.task_set_sha256,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> DatasetPublicationReceipt:
        if set(value) != {
            "generation",
            "root_modify_time",
            "content_sha256",
            "task_set_sha256",
        }:
            raise ValueError("Dataset publication receipt fields are invalid")
        return cls(
            generation=value.get("generation"),
            root_modify_time=value.get("root_modify_time"),
            content_sha256=value.get("content_sha256"),
            task_set_sha256=value.get("task_set_sha256"),
        )


@dataclass(frozen=True, slots=True)
class DatasetImageBuildReceipt:
    """Secret-free proof that the selected publication images became READY."""

    dataset_id: str
    service_name: str
    dataset_generation: int
    task_set_sha256: str
    runtime_type: str
    selected_task_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.dataset_id, str)
            or _IDENTITY.fullmatch(self.dataset_id) is None
            or not isinstance(self.service_name, str)
            or _IDENTITY.fullmatch(self.service_name) is None
            or isinstance(self.dataset_generation, bool)
            or not isinstance(self.dataset_generation, int)
            or not 1 <= self.dataset_generation <= 2**63 - 1
            or not isinstance(self.task_set_sha256, str)
            or _SHA256.fullmatch(self.task_set_sha256) is None
            or self.runtime_type not in _IMAGE_RUNTIME_TYPES
            or not isinstance(self.selected_task_ids, tuple)
            or not self.selected_task_ids
            or any(
                not isinstance(task_id, str) or _IDENTITY.fullmatch(task_id) is None
                for task_id in self.selected_task_ids
            )
            or len(set(self.selected_task_ids)) != len(self.selected_task_ids)
        ):
            raise ValueError("Dataset image build receipt is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": IMAGE_BUILD_RECEIPT_SCHEMA,
            "dataset_id": self.dataset_id,
            "service_name": self.service_name,
            "dataset_generation": self.dataset_generation,
            "task_set_sha256": self.task_set_sha256,
            "runtime_type": self.runtime_type,
            "selected_task_ids": list(self.selected_task_ids),
            "status": "READY",
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> DatasetImageBuildReceipt:
        if (
            set(value)
            != {
                "schema",
                "dataset_id",
                "service_name",
                "dataset_generation",
                "task_set_sha256",
                "runtime_type",
                "selected_task_ids",
                "status",
            }
            or value.get("schema") != IMAGE_BUILD_RECEIPT_SCHEMA
            or value.get("status") != "READY"
            or not isinstance(value.get("selected_task_ids"), list)
        ):
            raise ValueError("Dataset image build receipt fields are invalid")
        return cls(
            dataset_id=value.get("dataset_id"),
            service_name=value.get("service_name"),
            dataset_generation=value.get("dataset_generation"),
            task_set_sha256=value.get("task_set_sha256"),
            runtime_type=value.get("runtime_type"),
            selected_task_ids=tuple(value["selected_task_ids"]),
        )


@dataclass(frozen=True, slots=True)
class ParseBenchPackageDescriptor:
    package_path: Path
    package_sha256: str
    dataset_id: str
    service_name: str
    task_ids: tuple[str, ...]
    expected_case_ids: Mapping[ParseBenchDimension, tuple[str, ...]]
    selection_kind: str
    selection_digest: str
    publishable: bool
    runtime_image: str
    package_device: int
    package_inode: int
    package_size: int
    package_mtime_ns: int
    package_ctime_ns: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.runtime_image, str)
            or _RUNTIME_IMAGE.fullmatch(self.runtime_image) is None
            or any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in (
                    self.package_device,
                    self.package_inode,
                    self.package_size,
                    self.package_mtime_ns,
                    self.package_ctime_ns,
                )
            )
            or self.package_size < 1
        ):
            raise ValueError("ParseBench package descriptor is invalid")
        package_path = Path(self.package_path).expanduser()
        if not package_path.is_absolute():
            package_path = Path.cwd() / package_path
        object.__setattr__(self, "package_path", package_path)
        object.__setattr__(
            self,
            "expected_case_ids",
            MappingProxyType(dict(self.expected_case_ids)),
        )


def inspect_parsebench_package(path: Path | str) -> ParseBenchPackageDescriptor:
    """Validate a local executable package before upload or score reduction."""

    package_path = Path(path).expanduser()
    if not package_path.is_absolute():
        package_path = Path.cwd() / package_path
    try:
        lexical_metadata = package_path.lstat()
        if not stat.S_ISREG(lexical_metadata.st_mode):
            raise OSError
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(package_path, flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or _file_identity(
            metadata
        ) != _file_identity(lexical_metadata):
            os.close(descriptor)
            raise OSError
        package_stream = os.fdopen(descriptor, "rb")
    except OSError:
        raise ParseBenchGatewayError(
            "package_missing", "ParseBench package cannot be inspected"
        ) from None
    package_size = metadata.st_size
    if not 1 <= package_size <= _MAX_PACKAGE_BYTES:
        package_stream.close()
        raise ParseBenchGatewayError(
            "package_invalid", "ParseBench package exceeds the package-size limit"
        )
    try:
        package_sha256 = _sha256_stream(package_stream)
        package_stream.seek(0)
        with ZipFile(package_stream) as archive:
            entries = archive.infolist()
            names = [item.filename for item in entries]
            if (
                len(entries) > _MAX_ZIP_ENTRIES
                or len(names) != len(set(names))
                or any(not _safe_archive_name(name.rstrip("/")) for name in names)
            ):
                raise ParseBenchGatewayError(
                    "package_manifest_invalid",
                    "ParseBench package contains duplicate or unsafe paths",
                )
            dataset = _yaml_object(
                _read_zip_control(archive, "dataset.yaml", limit=64 * 1024),
                label="dataset.yaml",
            )
            manifest = _json_object(
                _read_zip_control(archive, "manifest.json", limit=_MAX_CONTROL_BYTES),
                label="manifest.json",
            )
            catalog_payload = _read_zip_control(
                archive, "dataset.jsonl", limit=_MAX_CONTROL_BYTES
            )
            dataset_id = dataset.get("dataset_id")
            service_name = dataset.get("service_name")
            if (
                dataset.get("schema_version") != DATASET_PACKAGE_SCHEMA_VERSION
                or not isinstance(dataset_id, str)
                or _IDENTITY.fullmatch(dataset_id) is None
                or not isinstance(service_name, str)
                or _IDENTITY.fullmatch(service_name) is None
                or manifest.get("schema_version") != _PACKAGE_MANIFEST_SCHEMA
                or set(manifest)
                != {
                    "schema_version",
                    "benchmark_scope",
                    "selection_manifest",
                    "catalog",
                    "tasks",
                    "provenance",
                }
            ):
                raise ParseBenchGatewayError(
                    "package_identity_invalid",
                    "ParseBench package identity or schema is invalid",
                )
            task_ids, catalog_rows = _catalog_rows(
                catalog_payload, dataset_id=dataset_id
            )
            catalog_material = manifest.get("catalog")
            catalog_digest = "sha256:" + hashlib.sha256(catalog_payload).hexdigest()
            if (
                not isinstance(catalog_material, Mapping)
                or set(catalog_material) != {"path", "size", "sha256"}
                or catalog_material.get("path") != "dataset.jsonl"
                or catalog_material.get("size") != len(catalog_payload)
                or catalog_material.get("sha256") != catalog_digest
            ):
                raise ParseBenchGatewayError(
                    "package_manifest_invalid",
                    "ParseBench catalog material digest is invalid",
                )
            raw_tasks = manifest.get("tasks")
            if not isinstance(raw_tasks, list) or len(raw_tasks) != len(task_ids):
                raise ParseBenchGatewayError(
                    "package_manifest_invalid",
                    "ParseBench material manifest does not match its catalog",
                )
            entry_by_name = {entry.filename: entry for entry in entries}
            task_materials: dict[str, tuple[str, int, str]] = {}
            for raw_task in raw_tasks:
                if not isinstance(raw_task, Mapping) or set(raw_task) != {
                    "task_id",
                    "path",
                    "size",
                    "sha256",
                }:
                    raise ParseBenchGatewayError(
                        "package_manifest_invalid",
                        "ParseBench task material entry is invalid",
                    )
                task_id = raw_task.get("task_id")
                task_path = raw_task.get("path")
                task_size = raw_task.get("size")
                task_sha256 = raw_task.get("sha256")
                if (
                    not isinstance(task_id, str)
                    or task_id in task_materials
                    or not isinstance(task_path, str)
                    or task_path != f"tasks/{task_id}.tar.gz"
                    or isinstance(task_size, bool)
                    or not isinstance(task_size, int)
                    or not 1 <= task_size <= _MAX_TASK_ARCHIVE_BYTES
                    or not isinstance(task_sha256, str)
                    or _SHA256.fullmatch(task_sha256) is None
                ):
                    raise ParseBenchGatewayError(
                        "package_manifest_invalid",
                        "ParseBench task material identity is invalid",
                    )
                task_materials[task_id] = (task_path, task_size, task_sha256)
            if tuple(task_materials) != task_ids:
                raise ParseBenchGatewayError(
                    "package_manifest_invalid",
                    "ParseBench material manifest task set does not match the catalog",
                )
            expected_names = {
                "dataset.yaml",
                "dataset.jsonl",
                "manifest.json",
                "README.md",
                *(material[0] for material in task_materials.values()),
            }
            if set(names) != expected_names:
                raise ParseBenchGatewayError(
                    "package_manifest_invalid",
                    "ParseBench package contains undeclared material",
                )

            provenance = manifest.get("provenance")
            scope = manifest.get("benchmark_scope")
            if (
                not isinstance(provenance, Mapping)
                or scope != provenance.get("benchmark_scope")
                or any(
                    row.get("benchmark_scope") != scope for row in catalog_rows.values()
                )
            ):
                raise ParseBenchGatewayError(
                    "package_scope_invalid",
                    "ParseBench benchmark scope mirrors are inconsistent",
                )
            expected, selection_cases, selection_digest = _selection_manifest_contract(
                manifest.get("selection_manifest"),
                task_ids=task_ids,
                catalog_rows=catalog_rows,
            )
            selection_kind, declared_publishable, runtime_image = _scope_contract(
                scope,
                task_count=len(task_ids),
                selection_digest=selection_digest,
            )
            normalized_provenance, selection = _provenance_contract(
                provenance,
                scope=scope,
                task_count=len(task_ids),
                expected_case_ids=expected,
            )
            _dataset_descriptor_contract(
                dataset,
                dataset_id=dataset_id,
                service_name=service_name,
                scope=scope,
                selection=selection,
            )

            expected_counts = dict(
                PINNED_PARSEBENCH_CONTRACT.dimension_execution_counts
            )
            complete_release = len(
                task_ids
            ) == PINNED_PARSEBENCH_CONTRACT.unique_execution_count and all(
                len(expected[dimension]) == expected_counts[dimension]
                for dimension in ParseBenchDimension
            )
            if selection_kind == "official-full" and (
                not complete_release
                or selection_digest != PINNED_FULL_SELECTION_MANIFEST_SHA256
            ):
                raise ParseBenchGatewayError(
                    "package_scope_invalid",
                    "An official-full ParseBench package is not the complete pinned release",
                )

            observed_rule_count = 0
            for task_id, (task_path, task_size, task_sha256) in task_materials.items():
                task_entry = entry_by_name.get(task_path)
                catalog_task = catalog_rows[task_id]
                task_contract = catalog_task.get("task_contract")
                selection_case = selection_cases[task_id]
                if (
                    set(catalog_task)
                    != {
                        "dataset_id",
                        "sample_id",
                        "task_id",
                        "source",
                        "source_size",
                        "source_sha256",
                        "source_revision",
                        "scorer_revision",
                        "page",
                        "instruction",
                        "instruction_source",
                        "task_dir",
                        "task_material_kind",
                        "benchmark_scope",
                        "task_contract",
                        "artifact_specs",
                    }
                    or catalog_task.get("source")
                    != f"parsebench@{DATASET_REVISION}/{task_id}"
                    or catalog_task.get("instruction")
                    != instruction(
                        source_runtime_path=str(selection_case["source_runtime_path"]),
                        page=selection_case["page"],
                    ).decode("utf-8")
                    or catalog_task.get("instruction_source")
                    != f"tasks/{task_id}/instruction.md"
                    or catalog_task.get("artifact_specs") != artifact_specs()
                    or task_entry is None
                    or task_entry.is_dir()
                    or task_entry.compress_type != ZIP_STORED
                    or task_entry.file_size != task_size
                    or catalog_task.get("task_dir") != task_path
                    or catalog_task.get("task_material_kind")
                    != TASK_ARCHIVE_SCHEMA_VERSION
                    or not isinstance(task_contract, Mapping)
                    or task_contract
                    != {
                        "path": "environment/parsebench-task.json",
                        "runtime_path": PARSEBENCH_TASK_RUNTIME_PATH,
                        "schema_version": PARSEBENCH_TASK_SCHEMA_VERSION,
                    }
                    or _zip_member_digest(archive, task_entry) != task_sha256
                ):
                    raise ParseBenchGatewayError(
                        "package_manifest_invalid",
                        "ParseBench task material digest is invalid",
                    )
                dimensions, rule_count = _inspect_task_archive(
                    archive,
                    task_id=task_id,
                    task_archive_path=task_path,
                    selection_case=selection_case,
                    scope=scope,
                )
                observed_rule_count += rule_count
                if (
                    tuple(
                        dimension
                        for dimension in ParseBenchDimension
                        if task_id in expected[dimension]
                    )
                    != dimensions
                ):
                    raise ParseBenchGatewayError(
                        "package_scope_invalid",
                        "ParseBench ground-truth dimensions do not match the selection manifest",
                    )

            if normalized_provenance["selected_rule_count"] != observed_rule_count:
                raise ParseBenchGatewayError(
                    "package_revision_invalid",
                    "ParseBench selected rule provenance is invalid",
                )

            publishable = declared_publishable and complete_release
    except ParseBenchGatewayError:
        raise
    except (BadZipFile, OSError, RuntimeError):
        raise ParseBenchGatewayError(
            "package_invalid", "ParseBench package is not a readable ZIP archive"
        ) from None
    finally:
        package_stream.close()
    return ParseBenchPackageDescriptor(
        package_path=package_path,
        package_sha256=package_sha256,
        dataset_id=dataset_id,
        service_name=service_name,
        task_ids=task_ids,
        expected_case_ids=expected,
        selection_kind=selection_kind,
        selection_digest=selection_digest,
        publishable=publishable,
        runtime_image=runtime_image,
        package_device=metadata.st_dev,
        package_inode=metadata.st_ino,
        package_size=metadata.st_size,
        package_mtime_ns=metadata.st_mtime_ns,
        package_ctime_ns=metadata.st_ctime_ns,
    )


def _validated_profile(value: str) -> str:
    value = str(value).strip()
    if _PROFILE.fullmatch(value) is None:
        raise ValueError("model_profile is invalid")
    return value


def _validated_timeout(value: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 60 <= value <= 14_400
    ):
        raise ValueError("timeout_seconds must be between 60 and 14400")
    return value


def batch_acceptance_checksum(
    *,
    batch_id: str,
    run_ids: Sequence[str],
) -> str:
    receipt = {
        "schema_version": "batch-acceptance/v1",
        "batch_id": batch_id,
        "total": len(run_ids),
        "run_ids": list(run_ids),
    }
    return (
        "sha256:" + hashlib.sha256(_canonical_json(receipt, newline=False)).hexdigest()
    )


def select_package_tasks(
    package: ParseBenchPackageDescriptor,
    *,
    task_ids: Sequence[str] = (),
    limit: int | None = None,
    allow_full: bool = False,
) -> tuple[str, ...]:
    """Select an ordered task subset while requiring opt-in for a full release."""

    if limit is not None and (
        isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
    ):
        raise ValueError("limit must be a positive integer")
    requested = tuple(task_ids)
    if len(set(requested)) != len(requested):
        raise ValueError("task_ids contains a duplicate")
    unknown = [task_id for task_id in requested if task_id not in package.task_ids]
    if unknown:
        raise ParseBenchGatewayError(
            "task_not_in_package", "A requested task is not present in the package"
        )
    selected = requested or package.task_ids
    if limit is not None:
        selected = selected[:limit]
    if not selected:
        raise ParseBenchGatewayError("selection_empty", "No ParseBench tasks selected")
    selects_entire_package = len(selected) == len(package.task_ids) and set(
        selected
    ) == set(package.task_ids)
    is_full_release = selects_entire_package and (
        package.selection_kind == "official-full"
        or len(package.task_ids) == PINNED_PARSEBENCH_CONTRACT.unique_execution_count
    )
    if is_full_release and not allow_full:
        raise ParseBenchGatewayError(
            "full_run_requires_opt_in",
            "The complete ParseBench release requires an explicit full-run opt-in",
        )
    return selected


def build_submit_payload(
    package: ParseBenchPackageDescriptor,
    *,
    selected_task_ids: Sequence[str],
    publication: DatasetPublicationReceipt,
    client_request_id: str,
    model_profile: str = DEFAULT_MODEL_PROFILE,
    timeout_seconds: int = 3_600,
) -> dict[str, Any]:
    """Build the credential-free public mcpgateway Harbor/Arca request."""

    model_profile = _validated_profile(model_profile)
    client_request_id = _validated_profile(client_request_id)
    timeout_seconds = _validated_timeout(timeout_seconds)
    selected = tuple(selected_task_ids)
    if (
        not selected
        or len(set(selected)) != len(selected)
        or any(task_id not in package.task_ids for task_id in selected)
    ):
        raise ValueError("selected_task_ids must be a non-empty package subset")
    return {
        "client_request_id": client_request_id,
        "scheduler_type": "harbor",
        "scheduler_config": {
            "execution_environment": "agent_only",
            "harness_profile": "aworld",
            "model_profile": model_profile,
        },
        "payload": {
            "protocol": "agent-service.execute.v1",
            "samples": [
                {
                    "service_name": package.service_name,
                    "dataset_id": package.dataset_id,
                    "sample_id": task_id,
                    "dataset_publication": publication.to_dict(),
                }
                for task_id in selected
            ],
            "execution_variants": [
                {
                    "harness_profile": "aworld",
                    "model_id": model_profile,
                }
            ],
            "params": {"n_attempts": 1},
            "task_info": {
                "job_type": "parsebench",
                "dataset_revision": DATASET_REVISION,
                "scorer_revision": SCORER_REVISION,
                "selection_digest": package.selection_digest,
            },
            "timeout_s": timeout_seconds,
        },
    }


def build_submission_intent(
    package: ParseBenchPackageDescriptor,
    *,
    selected_task_ids: Sequence[str],
    model_profile: str,
    timeout_seconds: int,
    gateway_url: str,
) -> dict[str, Any]:
    """Create a secret-free, durable record before either mutating gateway call."""

    selected = tuple(selected_task_ids)
    model_profile = _validated_profile(model_profile)
    timeout_seconds = _validated_timeout(timeout_seconds)
    if (
        not selected
        or len(set(selected)) != len(selected)
        or any(task_id not in package.task_ids for task_id in selected)
    ):
        raise ValueError("selected_task_ids must be a non-empty package subset")
    client_request_id = f"parsebench-{secrets.token_hex(16)}"
    request = {
        "dataset_id": package.dataset_id,
        "service_name": package.service_name,
        "package_sha256": package.package_sha256,
        "selection_digest": package.selection_digest,
        "selected_task_ids": list(selected),
        "model_profile": model_profile,
        "timeout_seconds": timeout_seconds,
        "gateway_url": _normalize_base_url(gateway_url),
        "client_request_id": client_request_id,
    }
    return {
        "schema": SUBMISSION_INTENT_SCHEMA,
        "status": "submitting",
        "reservation_id": secrets.token_hex(16),
        "request_sha256": "sha256:"
        + hashlib.sha256(_canonical_json(request, newline=False)).hexdigest(),
        **request,
    }


def bind_submission_intent(
    intent: Mapping[str, Any],
    publication: DatasetPublicationReceipt,
) -> dict[str, Any]:
    """Advance an owned pre-import intent to its immutable publication receipt."""

    if (
        intent.get("schema") != SUBMISSION_INTENT_SCHEMA
        or intent.get("status") != "submitting"
    ):
        raise ValueError("ParseBench submission intent cannot be publication-bound")
    return {
        **dict(intent),
        "status": "imported",
        "dataset_publication": publication.to_dict(),
    }


def bind_image_build_intent(
    intent: Mapping[str, Any],
    receipt: DatasetImageBuildReceipt,
) -> dict[str, Any]:
    """Advance an imported intent after its exact selected images are READY."""

    raw_publication = intent.get("dataset_publication")
    try:
        publication = DatasetPublicationReceipt.from_dict(raw_publication)
    except (TypeError, ValueError):
        raise ValueError(
            "ParseBench image build intent has no valid publication"
        ) from None
    if (
        intent.get("schema") != SUBMISSION_INTENT_SCHEMA
        or intent.get("status") != "imported"
        or receipt.dataset_id != intent.get("dataset_id")
        or receipt.service_name != intent.get("service_name")
        or list(receipt.selected_task_ids) != intent.get("selected_task_ids")
        or receipt.dataset_generation != publication.generation
        or receipt.task_set_sha256 != publication.task_set_sha256
    ):
        raise ValueError("ParseBench submission intent cannot be image-bound")
    return {
        **dict(intent),
        "status": "images_ready",
        "image_build_receipt": receipt.to_dict(),
    }


def load_submission_intent(
    path: Path | str,
    *,
    package: ParseBenchPackageDescriptor,
    selected_task_ids: Sequence[str],
    model_profile: str,
    timeout_seconds: int,
    gateway_url: str,
) -> tuple[dict[str, Any], DatasetPublicationReceipt | None]:
    """Load and identity-check an interrupted, canonical submission intent."""

    intent_path = Path(path).expanduser()
    if not intent_path.is_absolute():
        intent_path = Path.cwd() / intent_path
    try:
        metadata = intent_path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or not 1 <= metadata.st_size <= _MAX_CONTROL_BYTES
        ):
            raise OSError
        payload = intent_path.read_bytes()
        value = json.loads(
            payload,
            object_pairs_hook=_strict_json_pairs,
            parse_constant=_reject_json_constant,
        )
    except (OSError, TypeError, UnicodeError, ValueError, RecursionError):
        raise ParseBenchGatewayError(
            "submission_intent_invalid",
            "ParseBench submission intent is missing or invalid",
        ) from None
    base_keys = {
        "schema",
        "status",
        "reservation_id",
        "client_request_id",
        "request_sha256",
        "dataset_id",
        "service_name",
        "package_sha256",
        "selection_digest",
        "selected_task_ids",
        "model_profile",
        "timeout_seconds",
        "gateway_url",
    }
    if (
        not isinstance(value, Mapping)
        or _canonical_json(value) != payload
        or value.get("schema") != SUBMISSION_INTENT_SCHEMA
        or value.get("status") not in {"submitting", "imported", "images_ready"}
        or set(value)
        != base_keys
        | (
            {"dataset_publication"}
            if value.get("status") in {"imported", "images_ready"}
            else set()
        )
        | ({"image_build_receipt"} if value.get("status") == "images_ready" else set())
        or not isinstance(value.get("reservation_id"), str)
        or re.fullmatch(r"[0-9a-f]{32}", value["reservation_id"]) is None
    ):
        raise ParseBenchGatewayError(
            "submission_intent_invalid", "ParseBench submission intent is invalid"
        )
    selected = tuple(selected_task_ids)
    client_request_id = str(value.get("client_request_id") or "")
    request = {
        "dataset_id": package.dataset_id,
        "service_name": package.service_name,
        "package_sha256": package.package_sha256,
        "selection_digest": package.selection_digest,
        "selected_task_ids": list(selected),
        "model_profile": _validated_profile(model_profile),
        "timeout_seconds": _validated_timeout(timeout_seconds),
        "gateway_url": _normalize_base_url(gateway_url),
        "client_request_id": client_request_id,
    }
    expected_request_sha256 = (
        "sha256:" + hashlib.sha256(_canonical_json(request, newline=False)).hexdigest()
    )
    try:
        _validated_profile(client_request_id)
    except ValueError:
        raise ParseBenchGatewayError(
            "submission_intent_invalid", "ParseBench submission request ID is invalid"
        ) from None
    if (
        any(value.get(key) != item for key, item in request.items())
        or value.get("request_sha256") != expected_request_sha256
    ):
        raise ParseBenchGatewayError(
            "submission_intent_conflict",
            "ParseBench resume arguments do not match the saved submission intent",
        )
    publication = None
    if value["status"] in {"imported", "images_ready"}:
        raw_publication = value.get("dataset_publication")
        try:
            if not isinstance(raw_publication, Mapping):
                raise TypeError
            publication = DatasetPublicationReceipt.from_dict(raw_publication)
        except (TypeError, ValueError):
            raise ParseBenchGatewayError(
                "submission_intent_invalid",
                "ParseBench saved publication receipt is invalid",
            ) from None
    if value["status"] == "images_ready":
        raw_receipt = value.get("image_build_receipt")
        try:
            if not isinstance(raw_receipt, Mapping):
                raise TypeError
            image_receipt = DatasetImageBuildReceipt.from_dict(raw_receipt)
        except (TypeError, ValueError):
            raise ParseBenchGatewayError(
                "submission_intent_invalid",
                "ParseBench saved image build receipt is invalid",
            ) from None
        assert publication is not None
        if (
            image_receipt.dataset_id != package.dataset_id
            or image_receipt.service_name != package.service_name
            or image_receipt.dataset_generation != publication.generation
            or image_receipt.task_set_sha256 != publication.task_set_sha256
            or image_receipt.selected_task_ids != selected
        ):
            raise ParseBenchGatewayError(
                "submission_intent_invalid",
                "ParseBench saved image build receipt does not match the intent",
            )
    return dict(value), publication


def _normalize_base_url(value: str) -> str:
    parsed = urlsplit(str(value).strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("gateway_url must be an absolute HTTP(S) URL without userinfo")
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path.rstrip("/") + "/", "", "")
    )


class ParseBenchGatewayClient:
    """Small synchronous client; mutating POST requests are never auto-retried."""

    def __init__(
        self,
        gateway_url: str,
        *,
        token: str | None = None,
        staff_id: str | None = None,
        timeout_seconds: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not math.isfinite(float(timeout_seconds)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        base_url = _normalize_base_url(gateway_url)
        parsed_url = urlsplit(base_url)
        if (
            (token or staff_id)
            and parsed_url.scheme != "https"
            and parsed_url.hostname
            not in {
                "127.0.0.1",
                "localhost",
                "::1",
            }
        ):
            raise ValueError(
                "gateway_url must use HTTPS when gateway credentials are configured"
            )
        headers = {"accept": "application/json"}
        if token:
            headers["authorization"] = f"Bearer {token}"
        if staff_id:
            headers["x-lingguang-staff-id"] = staff_id
        self._client = httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=timeout_seconds,
            transport=transport,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    @staticmethod
    def _unwrap_response(value: object, *, action: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ParseBenchGatewayError(
                "gateway_response_invalid",
                f"mcpgateway {action} response must be an object",
            )
        data = value.get("data")
        envelope = isinstance(data, Mapping) and (
            len(value) == 1 or "success" in value or "code" in value
        )
        if not envelope:
            return value
        code = value.get("code")
        if value.get("success") is False or code not in {
            None,
            0,
            200,
            "0",
            "200",
            "OK",
            "SUCCESS",
        }:
            raise ParseBenchGatewayError(
                "gateway_request_failed",
                f"mcpgateway {action} envelope reports failure",
            )
        return dict(data)

    def _request_object(
        self,
        method: str,
        path: str,
        *,
        action: str,
        headers: Mapping[str, str] | None = None,
        content: Any = None,
        json_body: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            with self._client.stream(
                method,
                path,
                headers=headers,
                content=content,
                json=dict(json_body) if json_body is not None else None,
                params=dict(params) if params is not None else None,
            ) as response:
                if not 200 <= response.status_code < 300:
                    raise ParseBenchGatewayError(
                        "gateway_request_failed",
                        f"mcpgateway {action} failed with HTTP {response.status_code}",
                    )
                raw_length = response.headers.get("content-length")
                if raw_length is not None:
                    try:
                        declared_length = int(raw_length)
                    except ValueError:
                        raise ParseBenchGatewayError(
                            "gateway_response_invalid",
                            f"mcpgateway {action} returned an invalid content length",
                        ) from None
                    if declared_length < 0 or declared_length > _MAX_CONTROL_BYTES:
                        raise ParseBenchGatewayError(
                            "gateway_response_too_large",
                            f"mcpgateway {action} response exceeds the control-plane limit",
                        )
                payload = bytearray()
                for chunk in response.iter_bytes(chunk_size=64 * 1024):
                    payload.extend(chunk)
                    if len(payload) > _MAX_CONTROL_BYTES:
                        raise ParseBenchGatewayError(
                            "gateway_response_too_large",
                            f"mcpgateway {action} response exceeds the control-plane limit",
                        )
        except ParseBenchGatewayError:
            raise
        except httpx.HTTPError:
            raise ParseBenchGatewayError(
                "gateway_unavailable", f"mcpgateway {action} could not complete"
            ) from None
        try:
            value = json.loads(
                payload,
                object_pairs_hook=_strict_json_pairs,
                parse_constant=_reject_json_constant,
            )
        except (UnicodeError, ValueError, RecursionError):
            raise ParseBenchGatewayError(
                "gateway_response_invalid",
                f"mcpgateway {action} returned invalid JSON",
            ) from None
        return self._unwrap_response(value, action=action)

    def _post_object(
        self,
        path: str,
        *,
        action: str,
        headers: Mapping[str, str] | None = None,
        content: Any = None,
        json_body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._request_object(
            "POST",
            path,
            action=action,
            headers=headers,
            content=content,
            json_body=json_body,
        )

    def _get_object(
        self,
        path: str,
        *,
        action: str,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._request_object(
            "GET",
            path,
            action=action,
            params=params,
        )

    def import_package(
        self,
        package: ParseBenchPackageDescriptor,
        *,
        client_request_id: str,
    ) -> DatasetPublicationReceipt:
        client_request_id = _validated_profile(client_request_id)
        headers = {
            "content-type": "application/zip",
            "content-length": str(package.package_size),
            "x-dataset-content-sha256": package.package_sha256,
            "x-client-request-id": client_request_id,
        }
        try:
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(package.package_path, flags)
            metadata = os.fstat(descriptor)
            expected_identity = (
                package.package_device,
                package.package_inode,
                package.package_size,
                package.package_mtime_ns,
                package.package_ctime_ns,
            )
            if (
                not stat.S_ISREG(metadata.st_mode)
                or _file_identity(metadata) != expected_identity
            ):
                os.close(descriptor)
                raise ParseBenchGatewayError(
                    "package_changed",
                    "ParseBench package changed after local inspection",
                )
            with os.fdopen(descriptor, "rb") as stream:
                result = self._post_object(
                    _IMPORT_PATH,
                    action="package import",
                    headers=headers,
                    content=stream,
                )
        except ParseBenchGatewayError:
            raise
        except OSError:
            raise ParseBenchGatewayError(
                "gateway_unavailable", "mcpgateway package import could not complete"
            ) from None
        publication = result.get("publication")
        sample_count = result.get("sample_count")
        material_task_count = result.get("material_task_count")
        publication_sample_count = (
            publication.get("sample_count")
            if isinstance(publication, Mapping)
            else None
        )
        if (
            result.get("dataset_id") != package.dataset_id
            or result.get("service_name") != package.service_name
            or result.get("package_kind") != "executable"
            or isinstance(sample_count, bool)
            or not isinstance(sample_count, int)
            or sample_count != len(package.task_ids)
            or isinstance(material_task_count, bool)
            or not isinstance(material_task_count, int)
            or material_task_count != len(package.task_ids)
            or result.get("catalog_readiness") != "ready"
            or not isinstance(publication, Mapping)
            or isinstance(publication_sample_count, bool)
            or not isinstance(publication_sample_count, int)
            or publication_sample_count != len(package.task_ids)
            or publication.get("status") != "active"
            or isinstance(publication.get("generation"), bool)
            or not isinstance(publication.get("generation"), int)
            or publication["generation"] < 1
            or isinstance(publication.get("root_modify_time"), bool)
            or not isinstance(publication.get("root_modify_time"), int)
            or publication["root_modify_time"] < 1
            or not isinstance(publication.get("content_sha256"), str)
            or _SHA256.fullmatch(publication["content_sha256"]) is None
            or not isinstance(publication.get("task_set_sha256"), str)
            or _SHA256.fullmatch(publication["task_set_sha256"]) is None
            or not isinstance(publication.get("published_at"), str)
            or not publication["published_at"]
        ):
            raise ParseBenchGatewayError(
                "gateway_identity_mismatch",
                "mcpgateway imported a different Dataset identity",
            )
        try:
            return DatasetPublicationReceipt.from_dict(
                {
                    key: publication[key]
                    for key in (
                        "generation",
                        "root_modify_time",
                        "content_sha256",
                        "task_set_sha256",
                    )
                }
            )
        except (KeyError, TypeError, ValueError):
            raise ParseBenchGatewayError(
                "gateway_identity_mismatch",
                "mcpgateway returned an invalid Dataset publication receipt",
            ) from None

    @staticmethod
    def _validate_image_summary(
        value: Mapping[str, Any],
        *,
        package: ParseBenchPackageDescriptor,
        publication: DatasetPublicationReceipt,
        expected_runtime_type: str | None = None,
    ) -> dict[str, Any]:
        count_fields = (
            "total_count",
            "queued_count",
            "building_count",
            "ready_count",
            "failed_count",
            "missing_count",
            "unknown_count",
        )
        valid_statuses = {
            "NOT_STARTED",
            "QUEUED",
            "BUILDING",
            "READY",
            "PARTIAL_FAILED",
            "FAILED",
        }
        if (
            not isinstance(value, Mapping)
            or not isinstance(value.get("enabled"), bool)
            or value.get("repository_configured") is not True
            or value.get("service_name") != package.service_name
            or value.get("dataset_id") != package.dataset_id
            or value.get("dataset_generation") != publication.generation
            or value.get("provider_type") != "YOLO"
            or value.get("runtime_type") not in _IMAGE_RUNTIME_TYPES
            or (
                expected_runtime_type is not None
                and value.get("runtime_type") != expected_runtime_type
            )
            or value.get("status") not in valid_statuses
            or any(
                isinstance(value.get(field), bool)
                or not isinstance(value.get(field), int)
                or value[field] < 0
                or value[field] > len(package.task_ids)
                for field in count_fields
            )
        ):
            raise ParseBenchGatewayError(
                "gateway_identity_mismatch",
                "mcpgateway returned a different Dataset image build identity",
            )
        total = value["total_count"]
        queued = value["queued_count"]
        building = value["building_count"]
        ready = value["ready_count"]
        failed = value["failed_count"]
        missing = value["missing_count"]
        unknown = value["unknown_count"]
        if total != queued + building + ready + failed + missing + unknown:
            raise ParseBenchGatewayError(
                "gateway_identity_mismatch",
                "mcpgateway returned inconsistent Dataset image build counts",
            )
        if total == 0:
            derived_status = "NOT_STARTED"
        elif ready == total:
            derived_status = "READY"
        elif queued and not (building or ready or failed or missing or unknown):
            derived_status = "QUEUED"
        elif queued or building or unknown:
            derived_status = "BUILDING"
        elif ready:
            derived_status = "PARTIAL_FAILED"
        else:
            derived_status = "FAILED"
        if value["status"] != derived_status:
            raise ParseBenchGatewayError(
                "gateway_identity_mismatch",
                "mcpgateway returned an inconsistent Dataset image build status",
            )
        return dict(value)

    def _image_summary(
        self,
        package: ParseBenchPackageDescriptor,
        publication: DatasetPublicationReceipt,
        *,
        expected_runtime_type: str | None = None,
    ) -> dict[str, Any]:
        result = self._get_object(
            _DATASET_IMAGE_PATH.format(dataset_id=package.dataset_id),
            action="Dataset image status",
            params={"service_name": package.service_name, "refresh": "true"},
        )
        return self._validate_image_summary(
            result,
            package=package,
            publication=publication,
            expected_runtime_type=expected_runtime_type,
        )

    def _task_image_status(
        self,
        package: ParseBenchPackageDescriptor,
        *,
        task_id: str,
    ) -> str | None:
        result = self._get_object(
            _TASK_IMAGE_DETAIL_PATH.format(
                dataset_id=package.dataset_id,
                task_id=task_id,
            ),
            action="Dataset Task image status",
            params={"service_name": package.service_name},
        )
        image_status = result.get("image_status")
        arca_ready = result.get("arca_ready")
        image_url = result.get("image_url")
        image_digest = result.get("image_digest")
        if (
            result.get("service_name") != package.service_name
            or result.get("dataset_id") != package.dataset_id
            or result.get("task_id") != task_id
            or image_status
            not in {
                None,
                "QUEUED",
                "SUBMITTING",
                "BUILDING",
                "READY",
                "FAILED",
                "UNKNOWN",
                "SOURCE_MISSING",
            }
            or not isinstance(arca_ready, bool)
            or (image_status == "READY") is not arca_ready
        ):
            raise ParseBenchGatewayError(
                "gateway_identity_mismatch",
                "mcpgateway returned a different Dataset Task image identity",
            )
        if image_status == "READY" and (
            arca_ready is not True
            or not isinstance(image_url, str)
            or not image_url
            or len(image_url) > 2_048
            or any(character.isspace() for character in image_url)
            or not isinstance(image_digest, str)
            or _SHA256.fullmatch(image_digest) is None
        ):
            raise ParseBenchGatewayError(
                "gateway_response_invalid",
                "mcpgateway READY Task image is not immutable",
            )
        return image_status

    def prepare_task_images(
        self,
        package: ParseBenchPackageDescriptor,
        *,
        selected_task_ids: Sequence[str],
        publication: DatasetPublicationReceipt,
        timeout_seconds: float = 3_600.0,
        poll_interval_seconds: float = 2.0,
    ) -> DatasetImageBuildReceipt:
        """Build and wait for only the selected current-publication Task images."""

        selected = tuple(selected_task_ids)
        if (
            not selected
            or len(set(selected)) != len(selected)
            or any(task_id not in package.task_ids for task_id in selected)
        ):
            raise ValueError("selected_task_ids must be a non-empty package subset")
        if (
            not math.isfinite(float(timeout_seconds))
            or timeout_seconds <= 0
            or not math.isfinite(float(poll_interval_seconds))
            or poll_interval_seconds <= 0
        ):
            raise ValueError("Dataset image wait durations must be positive")

        deadline = time.monotonic() + float(timeout_seconds)
        request = {
            "service_name": package.service_name,
            "expected_generation": publication.generation,
            "force_rebuild": False,
        }
        selects_package = len(selected) == len(package.task_ids) and set(
            selected
        ) == set(package.task_ids)
        summary = self._image_summary(package, publication)
        runtime_type = summary["runtime_type"]
        bulk_triggered = False
        if selects_package:
            complete = (
                summary["status"] == "READY"
                and summary["total_count"] == len(package.task_ids)
                and summary["ready_count"] == len(package.task_ids)
            )
            active = summary["status"] in _ACTIVE_IMAGE_STATUSES
            bulk_triggered = complete or (
                active and summary["total_count"] == len(package.task_ids)
            )
            if not complete and not active:
                summary = self._validate_image_summary(
                    self._post_object(
                        _DATASET_IMAGE_BUILD_PATH.format(dataset_id=package.dataset_id),
                        action="Dataset image build",
                        json_body=request,
                    ),
                    package=package,
                    publication=publication,
                    expected_runtime_type=runtime_type,
                )
                bulk_triggered = True
        else:
            for task_id in selected:
                status = self._task_image_status(package, task_id=task_id)
                if status == "READY" or status in _ACTIVE_IMAGE_STATUSES:
                    continue
                self._validate_image_summary(
                    self._post_object(
                        _TASK_IMAGE_BUILD_PATH.format(
                            dataset_id=package.dataset_id,
                            task_id=task_id,
                        ),
                        action="Dataset Task image build",
                        json_body=request,
                    ),
                    package=package,
                    publication=publication,
                    expected_runtime_type=runtime_type,
                )

        while True:
            summary = self._image_summary(
                package,
                publication,
                expected_runtime_type=runtime_type,
            )
            if selects_package:
                ready = (
                    summary["status"] == "READY"
                    and summary["total_count"] == len(package.task_ids)
                    and summary["ready_count"] == len(package.task_ids)
                )
                failed = summary["status"] in _FAILED_IMAGE_STATUSES
                active = summary["status"] in _ACTIVE_IMAGE_STATUSES
                if not ready and not active and not bulk_triggered:
                    self._validate_image_summary(
                        self._post_object(
                            _DATASET_IMAGE_BUILD_PATH.format(
                                dataset_id=package.dataset_id
                            ),
                            action="Dataset image build",
                            json_body=request,
                        ),
                        package=package,
                        publication=publication,
                        expected_runtime_type=runtime_type,
                    )
                    bulk_triggered = True
                    continue
            else:
                statuses = tuple(
                    self._task_image_status(package, task_id=task_id)
                    for task_id in selected
                )
                ready = all(status == "READY" for status in statuses)
                failed = any(status in _FAILED_IMAGE_STATUSES for status in statuses)
            if ready:
                # Close the read window so a concurrent re-publication cannot be
                # recorded as readiness for the imported generation, and a
                # same-generation rebuild cannot be reported as still READY.
                closing_summary = self._image_summary(
                    package,
                    publication,
                    expected_runtime_type=runtime_type,
                )
                if selects_package:
                    closing_ready = (
                        closing_summary["status"] == "READY"
                        and closing_summary["total_count"] == len(package.task_ids)
                        and closing_summary["ready_count"] == len(package.task_ids)
                    )
                else:
                    closing_ready = all(
                        self._task_image_status(package, task_id=task_id) == "READY"
                        for task_id in selected
                    )
                if not closing_ready:
                    continue
                return DatasetImageBuildReceipt(
                    dataset_id=package.dataset_id,
                    service_name=package.service_name,
                    dataset_generation=publication.generation,
                    task_set_sha256=publication.task_set_sha256,
                    runtime_type=runtime_type,
                    selected_task_ids=selected,
                )
            if failed:
                raise ParseBenchGatewayError(
                    "dataset_image_build_failed",
                    "One or more selected ParseBench Task images failed",
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ParseBenchGatewayError(
                    "dataset_image_build_timeout",
                    "Timed out waiting for selected ParseBench Task images",
                )
            time.sleep(min(float(poll_interval_seconds), remaining))

    def submit(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._post_object(
            _SUBMIT_PATH,
            action="batch submission",
            json_body=payload,
        )

    def batch_status(
        self,
        batch_id: str,
        *,
        expected_total: int | None = None,
    ) -> dict[str, Any]:
        """Fetch the mcpgateway ``limit=0`` summary without run payloads."""

        if not isinstance(batch_id, str) or _IDENTITY.fullmatch(batch_id) is None:
            raise ValueError("batch_id is invalid")
        if expected_total is not None and (
            isinstance(expected_total, bool)
            or not isinstance(expected_total, int)
            or not 1 <= expected_total <= _MAX_BATCH_RESULTS
        ):
            raise ValueError("expected_total must be between 1 and 10000")
        summary = self._post_object(
            _RESULTS_PATH,
            action="batch status query",
            json_body={"batch_id": batch_id, "offset": 0, "limit": 0},
        )
        total = summary.get("total")
        completed = summary.get("completed")
        failed = summary.get("failed")
        status = summary.get("status")
        if (
            summary.get("batch_id") != batch_id
            or status not in {"pending", "running", "done"}
            or isinstance(total, bool)
            or not isinstance(total, int)
            or not 1 <= total <= _MAX_BATCH_RESULTS
            or expected_total is not None
            and total != expected_total
            or any(
                isinstance(count, bool)
                or not isinstance(count, int)
                or not 0 <= count <= total
                for count in (completed, failed)
            )
            or completed + failed > total
            or summary.get("results") != []
            or isinstance(summary.get("offset"), bool)
            or not isinstance(summary.get("offset"), int)
            or summary.get("offset") != 0
            or isinstance(summary.get("limit"), bool)
            or not isinstance(summary.get("limit"), int)
            or summary.get("limit") != 0
            or not isinstance(summary.get("has_more"), bool)
            or summary.get("has_more") is not (completed + failed < total)
            or (status == "done") is not (completed + failed == total)
        ):
            raise ParseBenchGatewayError(
                "gateway_response_invalid",
                "mcpgateway batch status metadata is invalid",
            )
        return {
            "batch_id": batch_id,
            "status": status,
            "total": total,
            "completed": completed,
            "failed": failed,
        }

    def batch_results(
        self,
        batch_id: str,
        *,
        page_size: int = 100,
        expected_total: int | None = None,
    ) -> dict[str, Any]:
        if not isinstance(batch_id, str) or _IDENTITY.fullmatch(batch_id) is None:
            raise ValueError("batch_id is invalid")
        if (
            isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or not 1 <= page_size <= 1_000
        ):
            raise ValueError("page_size must be between 1 and 1000")
        if expected_total is not None and (
            isinstance(expected_total, bool)
            or not isinstance(expected_total, int)
            or not 1 <= expected_total <= _MAX_BATCH_RESULTS
        ):
            raise ValueError("expected_total must be between 1 and 10000")
        offset = 0
        all_results: list[dict[str, Any]] = []
        summary: dict[str, Any] | None = None
        seen_run_ids: set[str] = set()
        accumulated_bytes = 0
        while True:
            page = self._post_object(
                _RESULTS_PATH,
                action="batch result query",
                json_body={
                    "batch_id": batch_id,
                    "offset": offset,
                    "limit": page_size,
                    "projection": "rewards",
                },
            )
            raw_results = page.get("results")
            if not isinstance(raw_results, list) or any(
                not isinstance(item, dict) for item in raw_results
            ):
                raise ParseBenchGatewayError(
                    "gateway_response_invalid",
                    "mcpgateway batch result page has invalid results",
                )
            total = page.get("total")
            completed = page.get("completed")
            failed = page.get("failed")
            if (
                isinstance(total, bool)
                or not isinstance(total, int)
                or not 1 <= total <= _MAX_BATCH_RESULTS
                or expected_total is not None
                and total != expected_total
                or any(
                    isinstance(count, bool)
                    or not isinstance(count, int)
                    or not 0 <= count <= total
                    for count in (completed, failed)
                )
                or completed + failed > total
                or page.get("status") not in {"pending", "running", "done"}
                or isinstance(page.get("offset"), bool)
                or not isinstance(page.get("offset"), int)
                or page.get("offset") != offset
                or isinstance(page.get("limit"), bool)
                or not isinstance(page.get("limit"), int)
                or page.get("limit") != page_size
                or len(raw_results) > page_size
                or offset + len(raw_results) > total
                or not isinstance(page.get("has_more"), bool)
                or page.get("has_more") is not (offset + len(raw_results) < total)
            ):
                raise ParseBenchGatewayError(
                    "gateway_response_invalid",
                    "mcpgateway batch result pagination metadata is invalid",
                )
            identity = {
                key: page.get(key)
                for key in ("batch_id", "status", "total", "completed", "failed")
            }
            if identity["batch_id"] != batch_id or (
                summary is not None and summary != identity
            ):
                raise ParseBenchGatewayError(
                    "gateway_response_invalid",
                    "mcpgateway batch pagination snapshot changed",
                )
            page_run_ids = [item.get("run_id") for item in raw_results]
            if any(
                not isinstance(run_id, str)
                or _IDENTITY.fullmatch(run_id) is None
                or run_id in seen_run_ids
                for run_id in page_run_ids
            ) or len(set(page_run_ids)) != len(page_run_ids):
                raise ParseBenchGatewayError(
                    "gateway_response_invalid",
                    "mcpgateway batch result pages contain duplicate or invalid runs",
                )
            seen_run_ids.update(page_run_ids)
            accumulated_bytes += len(_canonical_json(raw_results, newline=False))
            if accumulated_bytes > _MAX_BATCH_RESULTS_BYTES:
                raise ParseBenchGatewayError(
                    "gateway_response_too_large",
                    "mcpgateway batch results exceed the aggregate result limit",
                )
            summary = identity
            all_results.extend(raw_results)
            if page.get("has_more") is not True:
                break
            if not raw_results:
                raise ParseBenchGatewayError(
                    "gateway_response_invalid",
                    "mcpgateway batch pagination made no progress",
                )
            offset += len(raw_results)
        assert summary is not None
        if len(all_results) != summary["total"]:
            raise ParseBenchGatewayError(
                "gateway_response_invalid",
                "mcpgateway batch result pagination is incomplete",
            )
        return {**summary, "results": all_results}


@dataclass(frozen=True, slots=True)
class GatewayRunManifest:
    batch_id: str
    dataset_id: str
    service_name: str
    package_sha256: str
    selection_kind: str
    selection_digest: str
    package_publishable: bool
    dataset_revision: str
    scorer_revision: str
    runtime_image: str
    dataset_publication: DatasetPublicationReceipt
    dataset_image_build: DatasetImageBuildReceipt
    gateway_url: str
    client_request_id: str
    acceptance_checksum: str
    model_profile: str
    task_order: tuple[str, ...]
    task_to_run: Mapping[str, str]
    expected_case_ids: Mapping[ParseBenchDimension, tuple[str, ...]]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.task_to_run, Mapping)
            or not isinstance(self.expected_case_ids, Mapping)
            or not isinstance(self.package_publishable, bool)
            or self.dataset_revision != DATASET_REVISION
            or self.scorer_revision != SCORER_REVISION
            or not isinstance(self.dataset_publication, DatasetPublicationReceipt)
            or not isinstance(self.dataset_image_build, DatasetImageBuildReceipt)
            or not isinstance(self.runtime_image, str)
            or _RUNTIME_IMAGE.fullmatch(self.runtime_image) is None
            or self.gateway_url != _normalize_base_url(self.gateway_url)
            or not isinstance(self.acceptance_checksum, str)
            or _SHA256.fullmatch(self.acceptance_checksum) is None
            or _IDENTITY.fullmatch(self.batch_id) is None
            or _IDENTITY.fullmatch(self.dataset_id) is None
            or _IDENTITY.fullmatch(self.service_name) is None
            or _SHA256.fullmatch(self.package_sha256) is None
            or _SHA256.fullmatch(self.selection_digest) is None
            or self.selection_kind not in {"smoke", "official-full", "custom"}
            or not isinstance(self.task_order, tuple)
            or not self.task_order
            or len(set(self.task_order)) != len(self.task_order)
            or any(
                not isinstance(task_id, str) or _IDENTITY.fullmatch(task_id) is None
                for task_id in self.task_order
            )
            or not self.task_to_run
            or len(set(self.task_to_run.values())) != len(self.task_to_run)
            or any(
                _IDENTITY.fullmatch(task_id) is None
                or _IDENTITY.fullmatch(run_id) is None
                for task_id, run_id in self.task_to_run.items()
            )
        ):
            raise ValueError("ParseBench gateway run manifest identity is invalid")
        _validated_profile(self.model_profile)
        _validated_profile(self.client_request_id)
        if (
            self.dataset_image_build.dataset_id != self.dataset_id
            or self.dataset_image_build.service_name != self.service_name
            or self.dataset_image_build.dataset_generation
            != self.dataset_publication.generation
            or self.dataset_image_build.task_set_sha256
            != self.dataset_publication.task_set_sha256
            or self.dataset_image_build.selected_task_ids != self.task_order
        ):
            raise ValueError("ParseBench gateway run image build identity is invalid")
        if set(self.task_order) != set(self.task_to_run):
            raise ValueError("ParseBench gateway run manifest task order is invalid")
        if self.acceptance_checksum != batch_acceptance_checksum(
            batch_id=self.batch_id,
            run_ids=[self.task_to_run[task_id] for task_id in self.task_order],
        ):
            raise ValueError(
                "ParseBench gateway run manifest acceptance checksum is invalid"
            )
        if set(self.expected_case_ids) != set(ParseBenchDimension):
            raise ValueError(
                "ParseBench gateway run manifest dimensions are incomplete"
            )
        if any(
            not isinstance(case_ids, tuple)
            or len(case_ids) != len(set(case_ids))
            or any(
                not isinstance(case_id, str) or _IDENTITY.fullmatch(case_id) is None
                for case_id in case_ids
            )
            for case_ids in self.expected_case_ids.values()
        ):
            raise ValueError("ParseBench gateway run manifest case IDs are invalid")
        observed = {
            case_id
            for case_ids in self.expected_case_ids.values()
            for case_id in case_ids
        }
        if observed != set(self.task_to_run):
            raise ValueError("ParseBench gateway run manifest case set is invalid")
        if self.package_publishable:
            expected_counts = dict(
                PINNED_PARSEBENCH_CONTRACT.dimension_execution_counts
            )
            if (
                self.selection_kind != "official-full"
                or self.selection_digest != PINNED_FULL_SELECTION_MANIFEST_SHA256
                or PINNED_PARSEBENCH_RUNTIME_IMAGE is None
                or self.runtime_image != PINNED_PARSEBENCH_RUNTIME_IMAGE
                or len(self.task_to_run)
                != PINNED_PARSEBENCH_CONTRACT.unique_execution_count
                or any(
                    len(self.expected_case_ids[dimension]) != expected_counts[dimension]
                    for dimension in ParseBenchDimension
                )
            ):
                raise ValueError(
                    "A publishable ParseBench run manifest must bind the complete pinned release"
                )
        object.__setattr__(
            self, "task_to_run", MappingProxyType(dict(self.task_to_run))
        )
        object.__setattr__(
            self,
            "expected_case_ids",
            MappingProxyType(dict(self.expected_case_ids)),
        )

    @classmethod
    def from_submission(
        cls,
        package: ParseBenchPackageDescriptor,
        *,
        selected_task_ids: Sequence[str],
        model_profile: str,
        publication: DatasetPublicationReceipt,
        image_build: DatasetImageBuildReceipt,
        gateway_url: str,
        client_request_id: str,
        response: Mapping[str, Any],
    ) -> GatewayRunManifest:
        batch_id = response.get("batch_id")
        run_ids = response.get("run_ids")
        response_total = response.get("total")
        selected = tuple(selected_task_ids)
        if (
            not isinstance(batch_id, str)
            or _IDENTITY.fullmatch(batch_id) is None
            or not selected
            or len(set(selected)) != len(selected)
            or any(task_id not in package.task_ids for task_id in selected)
            or not isinstance(run_ids, list)
            or len(run_ids) != len(selected)
            or isinstance(response_total, bool)
            or not isinstance(response_total, int)
            or response_total != len(selected)
            or any(
                not isinstance(run_id, str) or _IDENTITY.fullmatch(run_id) is None
                for run_id in run_ids
            )
            or len(set(run_ids)) != len(run_ids)
            or response.get("acceptance_checksum")
            != batch_acceptance_checksum(batch_id=batch_id, run_ids=run_ids)
        ):
            raise ParseBenchGatewayError(
                "gateway_response_invalid",
                "mcpgateway submission response does not match selected tasks",
            )
        selected_set = set(selected)
        expected = {
            dimension: tuple(
                case_id
                for case_id in package.expected_case_ids[dimension]
                if case_id in selected_set
            )
            for dimension in ParseBenchDimension
        }
        return cls(
            batch_id=batch_id,
            dataset_id=package.dataset_id,
            service_name=package.service_name,
            package_sha256=package.package_sha256,
            selection_kind=package.selection_kind,
            selection_digest=package.selection_digest,
            package_publishable=(
                package.publishable
                and len(selected) == len(package.task_ids)
                and set(selected) == set(package.task_ids)
            ),
            dataset_revision=DATASET_REVISION,
            scorer_revision=SCORER_REVISION,
            runtime_image=package.runtime_image,
            dataset_publication=publication,
            dataset_image_build=image_build,
            gateway_url=_normalize_base_url(gateway_url),
            client_request_id=_validated_profile(client_request_id),
            acceptance_checksum=response["acceptance_checksum"],
            model_profile=_validated_profile(model_profile),
            task_order=selected,
            task_to_run=dict(zip(selected, run_ids, strict=True)),
            expected_case_ids=expected,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": RUN_MANIFEST_SCHEMA,
            "batch_id": self.batch_id,
            "dataset_id": self.dataset_id,
            "service_name": self.service_name,
            "package_sha256": self.package_sha256,
            "selection_kind": self.selection_kind,
            "selection_digest": self.selection_digest,
            "package_publishable": self.package_publishable,
            "dataset_revision": self.dataset_revision,
            "scorer_revision": self.scorer_revision,
            "runtime_image": self.runtime_image,
            "dataset_publication": self.dataset_publication.to_dict(),
            "dataset_image_build": self.dataset_image_build.to_dict(),
            "gateway_url": self.gateway_url,
            "client_request_id": self.client_request_id,
            "acceptance_checksum": self.acceptance_checksum,
            "model_profile": self.model_profile,
            "task_order": list(self.task_order),
            "task_to_run": dict(self.task_to_run),
            "expected_case_ids": {
                dimension.value: list(self.expected_case_ids[dimension])
                for dimension in ParseBenchDimension
            },
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> GatewayRunManifest:
        if not isinstance(value, Mapping) or value.get("schema") != RUN_MANIFEST_SCHEMA:
            raise ValueError("ParseBench gateway run manifest schema is invalid")
        raw_mapping = value.get("task_to_run")
        raw_task_order = value.get("task_order")
        raw_expected = value.get("expected_case_ids")
        raw_publication = value.get("dataset_publication")
        raw_image_build = value.get("dataset_image_build")
        if (
            not isinstance(raw_mapping, Mapping)
            or not isinstance(raw_task_order, list)
            or not isinstance(raw_expected, Mapping)
            or not isinstance(raw_publication, Mapping)
            or not isinstance(raw_image_build, Mapping)
        ):
            raise TypeError("ParseBench gateway run manifest is invalid")
        task_to_run = {str(task): str(run) for task, run in raw_mapping.items()}
        expected = _coerce_expected_case_ids(
            raw_expected,
            task_ids=tuple(task_to_run),
        )
        if expected is None:
            raise ValueError("ParseBench expected-case manifest is invalid")
        result = cls(
            batch_id=str(value.get("batch_id") or ""),
            dataset_id=str(value.get("dataset_id") or ""),
            service_name=str(value.get("service_name") or ""),
            package_sha256=str(value.get("package_sha256") or ""),
            selection_kind=str(value.get("selection_kind") or ""),
            selection_digest=str(value.get("selection_digest") or ""),
            package_publishable=value.get("package_publishable") is True,
            dataset_revision=str(value.get("dataset_revision") or ""),
            scorer_revision=str(value.get("scorer_revision") or ""),
            runtime_image=str(value.get("runtime_image") or ""),
            dataset_publication=DatasetPublicationReceipt.from_dict(raw_publication),
            dataset_image_build=DatasetImageBuildReceipt.from_dict(raw_image_build),
            gateway_url=str(value.get("gateway_url") or ""),
            client_request_id=str(value.get("client_request_id") or ""),
            acceptance_checksum=str(value.get("acceptance_checksum") or ""),
            model_profile=_validated_profile(str(value.get("model_profile") or "")),
            task_order=tuple(raw_task_order),
            task_to_run=task_to_run,
            expected_case_ids=expected,
        )
        if result.to_dict() != dict(value):
            raise ValueError("ParseBench gateway run manifest derived fields changed")
        return result


def validate_gateway_run_destination(
    manifest: GatewayRunManifest,
    gateway_url: str,
) -> None:
    if _normalize_base_url(gateway_url) != manifest.gateway_url:
        raise ParseBenchGatewayError(
            "gateway_identity_mismatch",
            "Gateway URL does not match the submitted ParseBench run",
        )


def load_gateway_run_manifest(path: Path | str) -> GatewayRunManifest:
    """Load one bounded, strict, versioned gateway run manifest."""

    manifest_path = Path(path).expanduser()
    if not manifest_path.is_absolute():
        manifest_path = Path.cwd() / manifest_path
    try:
        metadata = manifest_path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or not 1 <= metadata.st_size <= _MAX_CONTROL_BYTES
        ):
            raise OSError
        payload = manifest_path.read_bytes()
    except OSError:
        raise ParseBenchGatewayError(
            "run_manifest_invalid",
            "ParseBench gateway run manifest is missing or invalid",
        ) from None
    try:
        value = json.loads(
            payload,
            object_pairs_hook=_strict_json_pairs,
            parse_constant=_reject_json_constant,
        )
        if not isinstance(value, Mapping) or _canonical_json(value) != payload:
            raise ValueError
        return GatewayRunManifest.from_dict(value)
    except (TypeError, UnicodeError, ValueError, RecursionError):
        raise ParseBenchGatewayError(
            "run_manifest_invalid",
            "ParseBench gateway run manifest is not a canonical supported document",
        ) from None


def _count(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ParseBenchGatewayError(
            "reward_invalid", f"ParseBench reward {field} is not a count"
        )
    try:
        number = float(value)
    except OverflowError:
        raise ParseBenchGatewayError(
            "reward_invalid", f"ParseBench reward {field} is not a count"
        ) from None
    if not number.is_integer() or number < 0:
        raise ParseBenchGatewayError(
            "reward_invalid", f"ParseBench reward {field} is not a count"
        )
    return int(number)


def _score(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ParseBenchGatewayError(
            "reward_invalid", f"ParseBench reward {field} is not numeric"
        )
    try:
        number = float(value)
    except OverflowError:
        raise ParseBenchGatewayError(
            "reward_invalid", f"ParseBench reward {field} is outside [0, 1]"
        ) from None
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise ParseBenchGatewayError(
            "reward_invalid", f"ParseBench reward {field} is outside [0, 1]"
        )
    return number


def _case_from_rewards(
    *,
    task_id: str,
    dimension: ParseBenchDimension,
    rewards: Mapping[str, Any],
) -> ParseBenchCaseResult:
    prefix = f"parsebench_{dimension.value}"
    counts = {
        name: _count(
            rewards.get(f"{prefix}_{name}_count"), field=f"{prefix}_{name}_count"
        )
        for name in (
            "numeric",
            "not_scored",
            "official_failure",
            "execution_failure",
            "missing",
        )
    }
    if sum(counts.values()) != 1:
        raise ParseBenchGatewayError(
            "reward_invalid",
            "A ParseBench task dimension must represent exactly one official case",
        )
    if counts["execution_failure"]:
        return ParseBenchCaseResult.execution_failed(
            case_id=task_id,
            dimension=dimension,
            diagnostics=("remote verifier reported an execution failure",),
        )
    if counts["missing"]:
        return ParseBenchCaseResult.missing(
            case_id=task_id,
            dimension=dimension,
            diagnostics=("remote verifier reported a missing result",),
        )
    if counts["official_failure"]:
        score_key = f"{prefix}_score"
        if _score(rewards.get(score_key), field=score_key) != 0.0:
            raise ParseBenchGatewayError(
                "reward_invalid",
                f"ParseBench reward {score_key} must be zero for an official failure",
            )
        return ParseBenchCaseResult.official_failure(
            case_id=task_id,
            dimension=dimension,
            diagnostics=("official scorer classified a provider failure",),
        )
    if counts["not_scored"]:
        if f"{prefix}_score" in rewards:
            raise ParseBenchGatewayError(
                "reward_invalid",
                f"ParseBench reward {prefix}_score is invalid for a not-scored case",
            )
        return ParseBenchCaseResult.not_scored(
            case_id=task_id,
            dimension=dimension,
        )
    score_key = f"{prefix}_score"
    return ParseBenchCaseResult.scored(
        case_id=task_id,
        dimension=dimension,
        score=_score(rewards.get(score_key), field=score_key),
    )


def _validated_reward_vector(
    rewards: Mapping[str, Any],
    *,
    dimensions: Sequence[ParseBenchDimension],
) -> None:
    allowed = {"reward"}
    required = {"reward"}
    for dimension in dimensions:
        prefix = f"parsebench_{dimension.value}"
        allowed.add(f"{prefix}_score")
        count_keys = {
            f"{prefix}_{name}_count"
            for name in (
                "numeric",
                "not_scored",
                "official_failure",
                "execution_failure",
                "missing",
            )
        }
        required.update(count_keys)
        allowed.update(count_keys)
    if set(rewards) - allowed or not required.issubset(rewards):
        raise ParseBenchGatewayError(
            "reward_invalid",
            "ParseBench verifier reward keys do not match the expected dimensions",
        )
    primary = _score(rewards["reward"], field="reward")
    diagnostic_scores = [
        _score(rewards[key], field=key)
        for key in sorted(allowed - required)
        if key in rewards
    ]
    expected_primary = (
        math.fsum(diagnostic_scores) / len(diagnostic_scores)
        if diagnostic_scores
        else 0.0
    )
    if not math.isclose(primary, expected_primary, rel_tol=0.0, abs_tol=1e-12):
        raise ParseBenchGatewayError(
            "reward_invalid",
            "ParseBench task reward does not match its dimension diagnostics",
        )


def reduce_gateway_batch_results(
    manifest: GatewayRunManifest,
    batch_results: Mapping[str, Any],
) -> dict[str, Any]:
    """Reduce versioned reward vectors and keep smoke results non-publishable."""

    raw_results = batch_results.get("results")
    expected_total = len(manifest.task_to_run)
    completed = batch_results.get("completed")
    failed = batch_results.get("failed")
    total = batch_results.get("total")
    if (
        batch_results.get("batch_id") != manifest.batch_id
        or batch_results.get("status") != "done"
        or isinstance(total, bool)
        or not isinstance(total, int)
        or total != expected_total
        or isinstance(completed, bool)
        or not isinstance(completed, int)
        or isinstance(failed, bool)
        or not isinstance(failed, int)
        or completed < 0
        or failed < 0
        or completed + failed != expected_total
        or not isinstance(raw_results, list)
        or len(raw_results) != expected_total
        or any(not isinstance(item, Mapping) for item in raw_results)
    ):
        raise ParseBenchGatewayError(
            "gateway_response_invalid", "Batch results do not match the run manifest"
        )
    by_run: dict[str, Mapping[str, Any]] = {}
    for result in raw_results:
        run_id = result.get("run_id")
        if not isinstance(run_id, str) or run_id in by_run:
            raise ParseBenchGatewayError(
                "gateway_response_invalid", "Batch result run identities are invalid"
            )
        by_run[run_id] = result
    expected_runs = set(manifest.task_to_run.values())
    if set(by_run) != expected_runs:
        raise ParseBenchGatewayError(
            "gateway_response_invalid", "Batch results contain an unexpected run"
        )
    observed_completed = sum(
        result.get("status") == "COMPLETED" for result in by_run.values()
    )
    if completed != observed_completed or failed != expected_total - observed_completed:
        raise ParseBenchGatewayError(
            "gateway_response_invalid",
            "Batch terminal counters do not match its run statuses",
        )
    cases: list[ParseBenchCaseResult] = []
    for task_id in manifest.task_order:
        run_id = manifest.task_to_run[task_id]
        expected_dimensions = tuple(
            dimension
            for dimension in ParseBenchDimension
            if task_id in manifest.expected_case_ids[dimension]
        )
        result = by_run.get(run_id)
        data = result.get("data") if isinstance(result, Mapping) else None
        sample_id = result.get("sample_id") if isinstance(result, Mapping) else None
        if sample_id != task_id:
            raise ParseBenchGatewayError(
                "gateway_response_invalid",
                "Batch result sample identity does not match the run manifest",
            )
        rewards = data.get("rewards") if isinstance(data, Mapping) else None
        if (
            not isinstance(result, Mapping)
            or result.get("status") != "COMPLETED"
            or not isinstance(data, Mapping)
            or data.get("status") != "completed"
            or not isinstance(rewards, Mapping)
        ):
            cases.extend(
                ParseBenchCaseResult.execution_failed(
                    case_id=task_id,
                    dimension=dimension,
                    diagnostics=("remote run did not expose a verifier reward vector",),
                )
                for dimension in expected_dimensions
            )
            continue
        _validated_reward_vector(rewards, dimensions=expected_dimensions)
        for dimension in expected_dimensions:
            cases.append(
                _case_from_rewards(
                    task_id=task_id,
                    dimension=dimension,
                    rewards=rewards,
                )
            )
    official = reduce_parsebench_case_results(
        cases,
        expected_case_ids=manifest.expected_case_ids,
    )
    publishable = manifest.package_publishable and official.publishable
    return {
        "schema": REPORT_SCHEMA,
        "batch_id": manifest.batch_id,
        "dataset_id": manifest.dataset_id,
        "service_name": manifest.service_name,
        "package_sha256": manifest.package_sha256,
        "dataset_revision": manifest.dataset_revision,
        "scorer_revision": manifest.scorer_revision,
        "runtime_image": manifest.runtime_image,
        "dataset_publication": manifest.dataset_publication.to_dict(),
        "dataset_image_build": manifest.dataset_image_build.to_dict(),
        "gateway_url": manifest.gateway_url,
        "client_request_id": manifest.client_request_id,
        "acceptance_checksum": manifest.acceptance_checksum,
        "model_profile": manifest.model_profile,
        "scope": {
            "kind": manifest.selection_kind,
            "selection_digest": manifest.selection_digest,
            "task_count": len(manifest.task_to_run),
            "package_publishable": manifest.package_publishable,
        },
        "status": official.status.value,
        "publishable": publishable,
        "overall_score": official.overall_score,
        "overall_percent": official.overall_percent,
        "official_reduction": official.to_dict(),
        "diagnostics": (
            list(official.diagnostics)
            + (
                []
                if publishable
                else ["smoke or partial results are not leaderboard-publishable"]
            )
        ),
    }


__all__ = (
    "DEFAULT_MODEL_PROFILE",
    "IMAGE_BUILD_RECEIPT_SCHEMA",
    "REPORT_SCHEMA",
    "RUN_MANIFEST_SCHEMA",
    "SUBMISSION_INTENT_SCHEMA",
    "DatasetImageBuildReceipt",
    "DatasetPublicationReceipt",
    "GatewayRunManifest",
    "ParseBenchGatewayClient",
    "ParseBenchGatewayError",
    "ParseBenchPackageDescriptor",
    "atomic_write_json",
    "batch_acceptance_checksum",
    "bind_image_build_intent",
    "bind_submission_intent",
    "build_submission_intent",
    "build_submit_payload",
    "finalize_json_output",
    "inspect_parsebench_package",
    "load_gateway_run_manifest",
    "load_submission_intent",
    "reduce_gateway_batch_results",
    "release_json_reservation",
    "reserve_json_output",
    "select_package_tasks",
    "validate_gateway_run_destination",
)
