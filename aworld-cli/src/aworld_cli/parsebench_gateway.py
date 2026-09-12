"""Typed mcpgateway client and reduction boundary for ParseBench runs."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tarfile
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from zipfile import BadZipFile, ZipFile

import httpx
import yaml

from aworld.benchmarks.parsebench.contracts import (
    DATASET_PACKAGE_SCHEMA_VERSION,
    DATASET_REVISION,
    SCORER_REVISION,
    PINNED_PARSEBENCH_CONTRACT,
    ParseBenchDimension,
)
from aworld.benchmarks.parsebench.scoring import (
    ParseBenchCaseResult,
    reduce_parsebench_case_results,
)


DEFAULT_MODEL_PROFILE = "default__gemini-3.1-pro-preview"
RUN_MANIFEST_SCHEMA = "aworld.parsebench.gateway-run/v1"
REPORT_SCHEMA = "aworld.parsebench.gateway-report/v1"
_PACKAGE_MANIFEST_SCHEMA = "yolo-dataset-material-manifest/v1"
_IMPORT_PATH = "api/v1/dataset-meta/package/import"
_SUBMIT_PATH = "api/batch/submit"
_RESULTS_PATH = "api/batch/results"
_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
_PROFILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_CONTROL_BYTES = 16 * 1024 * 1024
_MAX_GROUND_TRUTH_BYTES = 8 * 1024 * 1024


class ParseBenchGatewayError(RuntimeError):
    """Stable error that does not retain credentials or remote response bodies."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def atomic_write_json(path: Path | str, value: object) -> None:
    """Atomically publish one deterministic control-plane JSON document."""

    destination = Path(path).expanduser().resolve()
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
    finally:
        temporary.unlink(missing_ok=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _safe_archive_name(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and all(
        part not in {"", ".", ".."} for part in path.parts
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


def _json_object(payload: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ParseBenchGatewayError(
            "package_control_invalid", f"ParseBench package {label} is invalid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ParseBenchGatewayError(
            "package_control_invalid", f"ParseBench package {label} must be an object"
        )
    return value


def _yaml_object(payload: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = yaml.safe_load(payload)
    except (UnicodeError, yaml.YAMLError) as exc:
        raise ParseBenchGatewayError(
            "package_control_invalid", f"ParseBench package {label} is invalid YAML"
        ) from exc
    if not isinstance(value, dict):
        raise ParseBenchGatewayError(
            "package_control_invalid", f"ParseBench package {label} must be an object"
        )
    return value


def _catalog_task_ids(payload: bytes, *, dataset_id: str) -> tuple[str, ...]:
    task_ids: list[str] = []
    seen: set[str] = set()
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
    if not task_ids:
        raise ParseBenchGatewayError(
            "package_catalog_invalid", "ParseBench package catalog is empty"
        )
    return tuple(task_ids)


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


def _ground_truth_dimensions(
    archive: ZipFile,
    *,
    task_id: str,
    task_archive_path: str,
) -> tuple[ParseBenchDimension, ...]:
    if not _safe_archive_name(task_archive_path):
        raise ParseBenchGatewayError(
            "package_manifest_invalid", "ParseBench task archive path is unsafe"
        )
    try:
        task_stream = archive.open(task_archive_path)
    except KeyError as exc:
        raise ParseBenchGatewayError(
            "package_manifest_invalid", "ParseBench task archive is missing"
        ) from exc
    expected_name = f"{task_id}/tests/ground_truth.json"
    found: bytes | None = None
    try:
        with task_stream, tarfile.open(fileobj=task_stream, mode="r|gz") as task_archive:
            for member in task_archive:
                if not _safe_archive_name(member.name):
                    raise ParseBenchGatewayError(
                        "package_manifest_invalid",
                        "ParseBench task archive contains an unsafe path",
                    )
                if member.name != expected_name:
                    continue
                if found is not None or not member.isfile() or member.size > _MAX_GROUND_TRUTH_BYTES:
                    raise ParseBenchGatewayError(
                        "package_ground_truth_invalid",
                        "ParseBench task ground truth is invalid",
                    )
                source = task_archive.extractfile(member)
                if source is None:
                    raise ParseBenchGatewayError(
                        "package_ground_truth_invalid",
                        "ParseBench task ground truth cannot be read",
                    )
                found = source.read(_MAX_GROUND_TRUTH_BYTES + 1)
    except (tarfile.TarError, OSError) as exc:
        raise ParseBenchGatewayError(
            "package_manifest_invalid", "ParseBench task archive is invalid"
        ) from exc
    if found is None or len(found) > _MAX_GROUND_TRUTH_BYTES:
        raise ParseBenchGatewayError(
            "package_ground_truth_invalid",
            "ParseBench task ground truth is missing or too large",
        )
    ground_truth = _json_object(found, label="task ground truth")
    if (
        ground_truth.get("task_id") != task_id
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
    except (TypeError, ValueError) as exc:
        raise ParseBenchGatewayError(
            "package_ground_truth_invalid",
            "ParseBench task ground truth contains an invalid dimension",
        ) from exc
    if len(set(dimensions)) != len(dimensions):
        raise ParseBenchGatewayError(
            "package_ground_truth_invalid",
            "ParseBench task ground truth contains duplicate dimensions",
        )
    return dimensions


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

    def __post_init__(self) -> None:
        object.__setattr__(self, "package_path", Path(self.package_path).resolve())
        object.__setattr__(
            self,
            "expected_case_ids",
            MappingProxyType(dict(self.expected_case_ids)),
        )


def inspect_parsebench_package(path: Path | str) -> ParseBenchPackageDescriptor:
    """Validate a local executable package before upload or score reduction."""

    package_path = Path(path).expanduser().resolve()
    if not package_path.is_file():
        raise ParseBenchGatewayError(
            "package_missing", "ParseBench package does not exist"
        )
    try:
        with ZipFile(package_path) as archive:
            names = [item.filename for item in archive.infolist()]
            if len(names) != len(set(names)) or any(
                not _safe_archive_name(name.rstrip("/")) for name in names
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
            ):
                raise ParseBenchGatewayError(
                    "package_identity_invalid",
                    "ParseBench package identity or schema is invalid",
                )
            task_ids = _catalog_task_ids(catalog_payload, dataset_id=dataset_id)
            raw_tasks = manifest.get("tasks")
            if not isinstance(raw_tasks, list) or len(raw_tasks) != len(task_ids):
                raise ParseBenchGatewayError(
                    "package_manifest_invalid",
                    "ParseBench material manifest does not match its catalog",
                )
            task_paths: dict[str, str] = {}
            for raw_task in raw_tasks:
                if not isinstance(raw_task, Mapping):
                    raise ParseBenchGatewayError(
                        "package_manifest_invalid",
                        "ParseBench task material entry is invalid",
                    )
                task_id = raw_task.get("task_id")
                task_path = raw_task.get("path")
                if (
                    not isinstance(task_id, str)
                    or task_id in task_paths
                    or not isinstance(task_path, str)
                    or task_path != f"tasks/{task_id}.tar.gz"
                ):
                    raise ParseBenchGatewayError(
                        "package_manifest_invalid",
                        "ParseBench task material identity is invalid",
                    )
                task_paths[task_id] = task_path
            if set(task_paths) != set(task_ids):
                raise ParseBenchGatewayError(
                    "package_manifest_invalid",
                    "ParseBench material manifest task set does not match the catalog",
                )
            provenance = manifest.get("provenance")
            if not isinstance(provenance, Mapping) or (
                provenance.get("dataset_revision") != DATASET_REVISION
                or provenance.get("scorer_revision") != SCORER_REVISION
            ):
                raise ParseBenchGatewayError(
                    "package_revision_invalid",
                    "ParseBench package revisions are not pinned",
                )
            selection = provenance.get("selection")
            selection_kind = (
                str(selection.get("kind"))
                if isinstance(selection, Mapping) and selection.get("kind")
                else "unknown"
            )
            scope = provenance.get("scope")
            expected_payload = None
            if isinstance(scope, Mapping):
                expected_payload = scope.get("expected_case_ids")
            if expected_payload is None:
                expected_payload = provenance.get("expected_case_ids")
            if expected_payload is None:
                expected_payload = manifest.get("expected_case_ids")
            expected = _coerce_expected_case_ids(
                expected_payload,
                task_ids=task_ids,
            )
            if expected is None:
                mutable: dict[ParseBenchDimension, list[str]] = {
                    dimension: [] for dimension in ParseBenchDimension
                }
                for task_id in task_ids:
                    for dimension in _ground_truth_dimensions(
                        archive,
                        task_id=task_id,
                        task_archive_path=task_paths[task_id],
                    ):
                        mutable[dimension].append(task_id)
                expected = {
                    dimension: tuple(case_ids)
                    for dimension, case_ids in mutable.items()
                }
            expected_payload_for_digest = {
                dimension.value: list(expected[dimension])
                for dimension in ParseBenchDimension
            }
            derived_digest = "sha256:" + hashlib.sha256(
                _canonical_json(expected_payload_for_digest)
            ).hexdigest()
            declared_digest = None
            if isinstance(scope, Mapping):
                declared_digest = scope.get("expected_case_manifest_sha256")
            selection_digest = str(declared_digest or derived_digest)
            if _SHA256.fullmatch(selection_digest) is None:
                raise ParseBenchGatewayError(
                    "package_scope_invalid",
                    "ParseBench package selection digest is invalid",
                )
            if declared_digest is not None and selection_digest != derived_digest:
                raise ParseBenchGatewayError(
                    "package_scope_invalid",
                    "ParseBench expected-case manifest digest does not match its content",
                )
            raw_publishable = (
                scope.get("publishable") if isinstance(scope, Mapping) else None
            )
            expected_counts = dict(
                PINNED_PARSEBENCH_CONTRACT.dimension_execution_counts
            )
            complete_release = (
                len(task_ids) == PINNED_PARSEBENCH_CONTRACT.unique_execution_count
                and all(
                    len(expected[dimension]) == expected_counts[dimension]
                    for dimension in ParseBenchDimension
                )
            )
            publishable = selection_kind == "full" and complete_release and (
                raw_publishable is not False
            )
            if publishable and not complete_release:
                raise ParseBenchGatewayError(
                    "package_scope_invalid",
                    "A publishable ParseBench package is not the complete pinned release",
                )
    except ParseBenchGatewayError:
        raise
    except (BadZipFile, OSError) as exc:
        raise ParseBenchGatewayError(
            "package_invalid", "ParseBench package is not a readable ZIP archive"
        ) from exc
    return ParseBenchPackageDescriptor(
        package_path=package_path,
        package_sha256=_sha256_file(package_path),
        dataset_id=dataset_id,
        service_name=service_name,
        task_ids=task_ids,
        expected_case_ids=expected,
        selection_kind=selection_kind,
        selection_digest=selection_digest,
        publishable=publishable,
    )


def _validated_profile(value: str) -> str:
    value = str(value).strip()
    if _PROFILE.fullmatch(value) is None:
        raise ValueError("model_profile is invalid")
    return value


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
    if package.publishable and selected == package.task_ids and not allow_full:
        raise ParseBenchGatewayError(
            "full_run_requires_opt_in",
            "The complete ParseBench release requires an explicit full-run opt-in",
        )
    return selected


def build_submit_payload(
    package: ParseBenchPackageDescriptor,
    *,
    selected_task_ids: Sequence[str],
    model_profile: str = DEFAULT_MODEL_PROFILE,
    timeout_seconds: int = 3_600,
) -> dict[str, Any]:
    """Build the credential-free public mcpgateway Harbor/Arca request."""

    model_profile = _validated_profile(model_profile)
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or not 60 <= timeout_seconds <= 14_400
    ):
        raise ValueError("timeout_seconds must be between 60 and 14400")
    selected = tuple(selected_task_ids)
    if not selected or any(task_id not in package.task_ids for task_id in selected):
        raise ValueError("selected_task_ids must be a non-empty package subset")
    return {
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
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/") + "/", "", ""))


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
        headers = {"accept": "application/json"}
        if token:
            headers["authorization"] = f"Bearer {token}"
        if staff_id:
            headers["x-lingguang-staff-id"] = staff_id
        self._client = httpx.Client(
            base_url=_normalize_base_url(gateway_url),
            headers=headers,
            timeout=timeout_seconds,
            transport=transport,
        )

    def __enter__(self) -> "ParseBenchGatewayClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    @staticmethod
    def _response_object(response: httpx.Response, *, action: str) -> dict[str, Any]:
        if response.status_code >= 400:
            raise ParseBenchGatewayError(
                "gateway_request_failed",
                f"mcpgateway {action} failed with HTTP {response.status_code}",
            )
        if len(response.content) > _MAX_CONTROL_BYTES:
            raise ParseBenchGatewayError(
                "gateway_response_too_large",
                f"mcpgateway {action} response exceeds the control-plane limit",
            )
        try:
            value = response.json()
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ParseBenchGatewayError(
                "gateway_response_invalid",
                f"mcpgateway {action} returned invalid JSON",
            ) from exc
        if not isinstance(value, dict):
            raise ParseBenchGatewayError(
                "gateway_response_invalid",
                f"mcpgateway {action} response must be an object",
            )
        return value

    def import_package(self, package: ParseBenchPackageDescriptor) -> dict[str, Any]:
        headers = {
            "content-type": "application/zip",
            "content-length": str(package.package_path.stat().st_size),
            "x-dataset-content-sha256": package.package_sha256,
        }
        try:
            with package.package_path.open("rb") as stream:
                response = self._client.post(_IMPORT_PATH, headers=headers, content=stream)
        except (httpx.HTTPError, OSError) as exc:
            raise ParseBenchGatewayError(
                "gateway_unavailable", "mcpgateway package import could not complete"
            ) from exc
        result = self._response_object(response, action="package import")
        if (
            result.get("dataset_id") != package.dataset_id
            or result.get("service_name") != package.service_name
        ):
            raise ParseBenchGatewayError(
                "gateway_identity_mismatch",
                "mcpgateway imported a different Dataset identity",
            )
        return result

    def submit(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.post(_SUBMIT_PATH, json=dict(payload))
        except httpx.HTTPError as exc:
            raise ParseBenchGatewayError(
                "gateway_unavailable", "mcpgateway batch submission could not complete"
            ) from exc
        return self._response_object(response, action="batch submission")

    def batch_results(
        self,
        batch_id: str,
        *,
        page_size: int = 1_000,
    ) -> dict[str, Any]:
        if not isinstance(batch_id, str) or _IDENTITY.fullmatch(batch_id) is None:
            raise ValueError("batch_id is invalid")
        if not 1 <= page_size <= 1_000:
            raise ValueError("page_size must be between 1 and 1000")
        offset = 0
        all_results: list[dict[str, Any]] = []
        summary: dict[str, Any] | None = None
        while True:
            try:
                response = self._client.post(
                    _RESULTS_PATH,
                    json={"batch_id": batch_id, "offset": offset, "limit": page_size},
                )
            except httpx.HTTPError as exc:
                raise ParseBenchGatewayError(
                    "gateway_unavailable", "mcpgateway batch result query could not complete"
                ) from exc
            page = self._response_object(response, action="batch result query")
            raw_results = page.get("results")
            if not isinstance(raw_results, list) or any(
                not isinstance(item, dict) for item in raw_results
            ):
                raise ParseBenchGatewayError(
                    "gateway_response_invalid",
                    "mcpgateway batch result page has invalid results",
                )
            identity = {
                key: page.get(key)
                for key in ("batch_id", "status", "total", "completed", "failed")
            }
            if identity["batch_id"] != batch_id or (
                summary is not None and summary.get("total") != identity["total"]
            ):
                raise ParseBenchGatewayError(
                    "gateway_response_invalid",
                    "mcpgateway batch pagination identity changed",
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
    model_profile: str
    task_to_run: Mapping[str, str]
    expected_case_ids: Mapping[ParseBenchDimension, tuple[str, ...]]

    def __post_init__(self) -> None:
        if (
            _IDENTITY.fullmatch(self.batch_id) is None
            or _IDENTITY.fullmatch(self.dataset_id) is None
            or _IDENTITY.fullmatch(self.service_name) is None
            or _SHA256.fullmatch(self.package_sha256) is None
            or _SHA256.fullmatch(self.selection_digest) is None
            or not self.selection_kind
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
        if set(self.expected_case_ids) != set(ParseBenchDimension):
            raise ValueError("ParseBench gateway run manifest dimensions are incomplete")
        observed = {
            case_id
            for case_ids in self.expected_case_ids.values()
            for case_id in case_ids
        }
        if observed != set(self.task_to_run):
            raise ValueError("ParseBench gateway run manifest case set is invalid")
        object.__setattr__(self, "task_to_run", MappingProxyType(dict(self.task_to_run)))
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
        response: Mapping[str, Any],
    ) -> "GatewayRunManifest":
        batch_id = response.get("batch_id")
        run_ids = response.get("run_ids")
        selected = tuple(selected_task_ids)
        if (
            not isinstance(batch_id, str)
            or _IDENTITY.fullmatch(batch_id) is None
            or not isinstance(run_ids, list)
            or len(run_ids) != len(selected)
            or response.get("total") != len(selected)
            or any(not isinstance(run_id, str) for run_id in run_ids)
            or len(set(run_ids)) != len(run_ids)
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
                package.publishable and selected == package.task_ids
            ),
            model_profile=_validated_profile(model_profile),
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
            "model_profile": self.model_profile,
            "task_to_run": dict(self.task_to_run),
            "expected_case_ids": {
                dimension.value: list(self.expected_case_ids[dimension])
                for dimension in ParseBenchDimension
            },
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GatewayRunManifest":
        if not isinstance(value, Mapping) or value.get("schema") != RUN_MANIFEST_SCHEMA:
            raise ValueError("ParseBench gateway run manifest schema is invalid")
        raw_mapping = value.get("task_to_run")
        raw_expected = value.get("expected_case_ids")
        if not isinstance(raw_mapping, Mapping) or not isinstance(raw_expected, Mapping):
            raise ValueError("ParseBench gateway run manifest is invalid")
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
            model_profile=_validated_profile(str(value.get("model_profile") or "")),
            task_to_run=task_to_run,
            expected_case_ids=expected,
        )
        if result.to_dict() != dict(value):
            raise ValueError("ParseBench gateway run manifest derived fields changed")
        return result


def _count(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ParseBenchGatewayError(
            "reward_invalid", f"ParseBench reward {field} is not a count"
        )
    number = float(value)
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
    number = float(value)
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
        name: _count(rewards.get(f"{prefix}_{name}_count"), field=f"{prefix}_{name}_count")
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
        return ParseBenchCaseResult.official_failure(
            case_id=task_id,
            dimension=dimension,
            diagnostics=("official scorer classified a provider failure",),
        )
    if counts["not_scored"]:
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


def reduce_gateway_batch_results(
    manifest: GatewayRunManifest,
    batch_results: Mapping[str, Any],
) -> dict[str, Any]:
    """Reduce versioned reward vectors and keep smoke results non-publishable."""

    raw_results = batch_results.get("results")
    if (
        batch_results.get("batch_id") != manifest.batch_id
        or not isinstance(raw_results, list)
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
    if not set(by_run).issubset(expected_runs):
        raise ParseBenchGatewayError(
            "gateway_response_invalid", "Batch results contain an unexpected run"
        )
    cases: list[ParseBenchCaseResult] = []
    for task_id, run_id in manifest.task_to_run.items():
        expected_dimensions = tuple(
            dimension
            for dimension in ParseBenchDimension
            if task_id in manifest.expected_case_ids[dimension]
        )
        result = by_run.get(run_id)
        data = result.get("data") if isinstance(result, Mapping) else None
        rewards = data.get("rewards") if isinstance(data, Mapping) else None
        if not isinstance(result, Mapping) or not isinstance(rewards, Mapping):
            cases.extend(
                ParseBenchCaseResult.execution_failed(
                    case_id=task_id,
                    dimension=dimension,
                    diagnostics=("remote run did not expose a verifier reward vector",),
                )
                for dimension in expected_dimensions
            )
            continue
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
        "dataset_revision": DATASET_REVISION,
        "scorer_revision": SCORER_REVISION,
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
            + ([] if publishable else ["smoke or partial results are not leaderboard-publishable"])
        ),
    }


__all__ = (
    "DEFAULT_MODEL_PROFILE",
    "GatewayRunManifest",
    "ParseBenchGatewayClient",
    "ParseBenchGatewayError",
    "ParseBenchPackageDescriptor",
    "REPORT_SCHEMA",
    "RUN_MANIFEST_SCHEMA",
    "atomic_write_json",
    "build_submit_payload",
    "inspect_parsebench_package",
    "reduce_gateway_batch_results",
    "select_package_tasks",
)
