from __future__ import annotations

import ast
import ctypes
import functools
import hashlib
import json
import re
import math
import os
import signal
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, NoReturn, Protocol, Sequence

from aworld.self_evolve.replay_adaptation import (
    ReplayAdapterBinding,
    ReplayAdapterContext,
    ReplayCapabilityRequirement,
    ReplayDependency,
    validate_replay_binding_concurrency,
)
from aworld.self_evolve.schema_diagnostics import (
    SchemaFieldRepairConstraint,
    SchemaFieldViolation,
    aggregate_schema_field_violations,
    schema_field_diagnostic_details,
)
from aworld.self_evolve.sanitization import sanitize_text


REPLAY_CAPABILITY_SCHEMA_VERSION = "aworld.skill.replay_capability.v1"
REPLAY_CAPABILITY_PROTOCOL_VERSION = "aworld.replay.subprocess.v1"
REPLAY_CAPABILITY_REQUEST_SCHEMA_VERSION = "aworld.replay.capability_request.v1"
REPLAY_CAPABILITY_RESULT_SCHEMA_VERSION = "aworld.replay.capability_result.v1"
REPLAY_CAPABILITY_MANIFEST_PATH = "replay/capability.json"

_MANIFEST_PATH = PurePosixPath(REPLAY_CAPABILITY_MANIFEST_PATH)
_SUPPORTED_REQUIREMENT_KINDS = frozenset(
    {
        "conversation_context",
        "http_resource",
        "local_endpoint",
        "local_file",
        "stateful_tool",
    }
)
REPLAY_CAPABILITY_SUPPORTED_REQUIREMENT_KINDS = tuple(
    sorted(_SUPPORTED_REQUIREMENT_KINDS)
)
_SUPPORTED_READINESS_KINDS = frozenset({"http", "tcp"})
_SUPPORTED_PROTOCOL_PROBE_KINDS = frozenset({"http", "tcp", "websocket"})
_SUPPORTED_SERVICE_TRANSPORTS = frozenset(
    {"http_fixture", "skill_runtime", "tcp_fixture"}
)
_MAX_JSON_BYTES = 1024 * 1024
_MAX_FIXTURE_COUNT = 64
_MAX_FIXTURE_FILE_BYTES = 16 * 1024 * 1024
_MAX_FIXTURE_TOTAL_BYTES = 64 * 1024 * 1024
_MAX_READINESS_TIMEOUT_SECONDS = 30.0
REPLAY_CAPABILITY_MAX_PROTOCOL_PROBES = 16
REPLAY_CAPABILITY_MAX_RESPONSE_CONTAINS_CHARS = 4_096
REPLAY_RESPONSE_INDEX_ENV = "AWORLD_REPLAY_RESPONSE_INDEX"
REPLAY_RESPONSE_RECORD_ID_ENV = "AWORLD_REPLAY_RESPONSE_RECORD_ID"
REPLAY_RESPONSE_REQUIREMENT_ID_ENV = "AWORLD_REPLAY_REQUIREMENT_ID"
REPLAY_RESPONSE_SERVICE_ID_ENV = "AWORLD_REPLAY_SERVICE_ID"
REPLAY_RESPONSE_INDEX_CONSUMER = "json_sidecar_record_value_projector"
REPLAY_RESPONSE_SELECTOR_POLICY = "framework_recorded_response_v1"

REPLAY_CAPABILITY_SUPPORTED_READINESS_KINDS = tuple(
    sorted(_SUPPORTED_READINESS_KINDS)
)
REPLAY_CAPABILITY_SUPPORTED_PROTOCOL_PROBE_KINDS = tuple(
    sorted(_SUPPORTED_PROTOCOL_PROBE_KINDS)
)
REPLAY_CAPABILITY_SUPPORTED_SERVICE_TRANSPORTS = tuple(
    sorted(_SUPPORTED_SERVICE_TRANSPORTS)
)


class _DarwinProcTaskInfo(ctypes.Structure):
    """Subset layout returned by ``proc_pidinfo(PROC_PIDTASKINFO)``."""

    _fields_ = [
        ("pti_virtual_size", ctypes.c_uint64),
        ("pti_resident_size", ctypes.c_uint64),
        ("pti_total_user", ctypes.c_uint64),
        ("pti_total_system", ctypes.c_uint64),
        ("pti_threads_user", ctypes.c_uint64),
        ("pti_threads_system", ctypes.c_uint64),
        ("pti_policy", ctypes.c_int32),
        ("pti_faults", ctypes.c_int32),
        ("pti_pageins", ctypes.c_int32),
        ("pti_cow_faults", ctypes.c_int32),
        ("pti_messages_sent", ctypes.c_int32),
        ("pti_messages_received", ctypes.c_int32),
        ("pti_syscalls_mach", ctypes.c_int32),
        ("pti_syscalls_unix", ctypes.c_int32),
        ("pti_csw", ctypes.c_int32),
        ("pti_threadnum", ctypes.c_int32),
        ("pti_numrunning", ctypes.c_int32),
        ("pti_priority", ctypes.c_int32),
    ]


@functools.lru_cache(maxsize=1)
def _darwin_libproc() -> Any | None:
    try:
        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    except OSError:
        return None
    library.proc_pidinfo.argtypes = (
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint64,
        ctypes.c_void_p,
        ctypes.c_int,
    )
    library.proc_pidinfo.restype = ctypes.c_int
    return library


def _darwin_process_memory_bytes(process_id: int) -> int | None:
    """Read RSS without spawning ``ps`` on every watchdog sample."""

    library = _darwin_libproc()
    if library is None:
        return None
    info = _DarwinProcTaskInfo()
    info_size = ctypes.sizeof(info)
    result_size = library.proc_pidinfo(
        process_id,
        4,  # PROC_PIDTASKINFO
        0,
        ctypes.byref(info),
        info_size,
    )
    if result_size == info_size:
        return int(info.pti_resident_size)
    if result_size == 0:
        # A process can exit between poll() and the watchdog sample.
        return 0
    return None


class ReplayCapabilityError(RuntimeError):
    """Raised when a skill-owned replay capability violates the protocol."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


def python_source_syntax_counterexample(
    *,
    source_path: str,
    error: SyntaxError,
    code: str = "runtime_python_syntax_invalid",
) -> dict[str, object]:
    """Build a stable, payload-free repair contract for invalid Python source."""

    message = str(error.msg or "invalid Python syntax")
    syntax_kind = (
        "global_declaration_after_use"
        if any(
            marker in message
            for marker in (
                "used prior to global declaration",
                "assigned to before global declaration",
            )
        )
        else "python_compile_invalid"
    )
    source_role = "compiler" if code.startswith("compiler_") else "runtime"
    required_action = (
        "declare the global once before its first use in the function, or "
        "remove the duplicate global declaration"
        if syntax_kind == "global_declaration_after_use"
        else f"make the candidate-owned Python {source_role} compile successfully"
    )
    identity_payload = {
        "code": code,
        "source_path": source_path,
        "syntax_kind": syntax_kind,
    }
    identity = hashlib.sha256(
        json.dumps(
            identity_payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": "aworld.self_evolve.python_source_counterexample.v1",
        "counterexample_id": f"python-source-counterexample-{identity}",
        "owner": "candidate",
        **identity_payload,
        "line": max(1, int(error.lineno or 1)),
        "offset": max(0, int(error.offset or 0)),
        "message": message,
        "required_action": required_action,
    }


def _validate_discovered_python_entrypoint(
    capability: DiscoveredReplayCapability,
) -> None:
    source_path = capability.manifest.entrypoint
    try:
        compile(capability.entrypoint.read_bytes(), source_path, "exec")
    except SyntaxError as exc:
        counterexample = python_source_syntax_counterexample(
            source_path=source_path,
            error=exc,
            code="compiler_python_syntax_invalid",
        )
        raise ReplayCapabilityError(
            (
                "candidate replay Python compiler does not compile: "
                f"{source_path}:{counterexample['line']}: "
                f"{counterexample['message']}"
            ),
            code="compiler_python_syntax_invalid",
            details={
                "source_path": source_path,
                "syntax_kind": counterexample["syntax_kind"],
                "source_line": counterexample["line"],
                "source_offset": counterexample["offset"],
                "counterexample_contracts": [counterexample],
            },
        ) from exc
    except (OSError, UnicodeError) as exc:
        raise ReplayCapabilityError(
            f"candidate replay Python compiler is unreadable: {source_path}",
            code="compiler_python_source_unreadable",
            details={"source_path": source_path},
        ) from exc


def _validate_frozen_python_runtime(
    runtime_root: Path,
    runtime_files: Sequence[FrozenReplayFile],
) -> None:
    for item in runtime_files:
        if Path(item.path).suffix.casefold() != ".py":
            continue
        path = _resolve_output_file(runtime_root, item.path)
        try:
            compile(path.read_bytes(), item.path, "exec")
        except SyntaxError as exc:
            counterexample = python_source_syntax_counterexample(
                source_path=item.path,
                error=exc,
            )
            raise ReplayCapabilityError(
                (
                    "candidate replay Python runtime does not compile: "
                    f"{item.path}:{counterexample['line']}: "
                    f"{counterexample['message']}"
                ),
                code="runtime_python_syntax_invalid",
                details={
                    "source_path": item.path,
                    "syntax_kind": counterexample["syntax_kind"],
                    "source_line": counterexample["line"],
                    "source_offset": counterexample["offset"],
                    "counterexample_contracts": [counterexample],
                },
            ) from exc
        except (OSError, UnicodeError) as exc:
            raise ReplayCapabilityError(
                f"candidate replay Python runtime is unreadable: {item.path}",
                code="runtime_python_source_unreadable",
                details={"source_path": item.path},
            ) from exc


def _raise_schema_field_error(
    message: str,
    violations: Sequence[SchemaFieldViolation],
    *,
    extra_details: Mapping[str, object] | None = None,
) -> NoReturn:
    if not violations:
        raise ValueError("schema field error requires at least one violation")
    violations = aggregate_schema_field_violations(violations)
    details = schema_field_diagnostic_details(violations)
    details["counterexample_contracts"] = [
        _schema_field_counterexample_contract(violation)
        for violation in violations[:100]
    ]
    if extra_details:
        details.update(dict(extra_details))
    raise ReplayCapabilityError(
        message,
        code="schema_field_validation_failed",
        details=details,
    )


def _schema_field_counterexample_contract(
    violation: SchemaFieldViolation,
) -> dict[str, object]:
    """Project a parse/schema violation into a payload-free executable check."""

    identity_payload = {
        "constraint_identity_digest": violation.constraint.identity_digest,
    }
    encoded = json.dumps(
        identity_payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return {
        "schema_version": "aworld.self_evolve.schema_counterexample.v1",
        "counterexample_id": (
            "schema-counterexample-" + hashlib.sha256(encoded).hexdigest()
        ),
        **identity_payload,
        # Observed values explain this occurrence but do not define the
        # counterexample.  Keeping them outside the ID makes repeated
        # violations of the same schema contract converge across repair runs.
        "actual_fingerprint": violation.actual_fingerprint,
        "actual_type": violation.actual_type,
        "constraint": violation.constraint.to_dict(),
        "required_checks": [
            "field_selector_resolves_subject",
            "schema_constraint_accepts_subject",
        ],
    }


def _schema_field_violation(
    *,
    schema_layer: str,
    field_path: str,
    rule: str,
    expected: Sequence[str],
    value: Any,
    occurrence_count: int = 1,
    value_domain: str = "schema_value",
    required_operations: Sequence[str] = (),
    forbidden_operations: Sequence[str] = (),
) -> SchemaFieldViolation:
    return SchemaFieldViolation.create(
        SchemaFieldRepairConstraint(
            schema_layer=schema_layer,
            field_path=field_path,
            rule=rule,
            expected=tuple(expected),
            value_domain=value_domain,
            required_operations=tuple(required_operations),
            forbidden_operations=tuple(forbidden_operations),
        ),
        value,
        occurrence_count=occurrence_count,
    )


def _absolute_path_violation(
    *,
    field_path: str,
    value: Any,
    occurrence_count: int = 1,
) -> SchemaFieldViolation:
    return _schema_field_violation(
        schema_layer="compile_result",
        field_path=field_path,
        rule="starts_with",
        expected=("/",),
        value=value,
        occurrence_count=occurrence_count,
    )


def _readiness_duplicate_violation(
    value: Any,
    *,
    occurrence_count: int = 1,
) -> SchemaFieldViolation:
    return _schema_field_violation(
        schema_layer="compile_result",
        field_path="services[*].protocol_probes[*].path",
        rule="enum",
        expected=("fixture_derived_data_plane_probe",),
        value=value,
        occurrence_count=occurrence_count,
        value_domain="source_behavior",
        required_operations=(
            "keep_readiness_in_readiness_field",
            "declare_distinct_fixture_derived_data_plane_probe",
        ),
        forbidden_operations=(
            "copy_readiness_endpoint_into_protocol_probes",
        ),
    )


def _advertised_websocket_probe_violation() -> SchemaFieldViolation:
    return _schema_field_violation(
        schema_layer="compile_result",
        field_path="services[*].protocol_probes",
        rule="enum",
        expected=("advertised_websocket_with_data_plane_probe",),
        value="advertised_websocket_without_data_plane_probe",
        value_domain="source_behavior",
        required_operations=(
            "declare_websocket_data_plane_probe",
            "provide_websocket_request_text",
            "provide_fixture_derived_websocket_response",
        ),
        forbidden_operations=(
            "advertise_websocket_without_websocket_probe",
        ),
    )


def fingerprint_skill_package(skill_root: str | Path) -> str:
    root = Path(skill_root).expanduser().resolve()
    if not root.is_dir():
        raise ReplayCapabilityError(f"skill root is not a directory: {root}")
    package_entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ReplayCapabilityError("skill package cannot contain symlinks")
        if path.is_file():
            package_entries.append(
                _file_manifest_entry(path, path.relative_to(root).as_posix())
            )
    return _json_fingerprint({"files": package_entries})


def fingerprint_replay_capability_package(
    skill_root: str | Path,
    *,
    manifest_path: str | Path,
    entrypoint: str | Path,
    runtime_files: Sequence[str | Path] = (),
) -> str:
    """Fingerprint only the declared replay-capability implementation surface.

    Skill guidance and other release files affect the candidate package identity,
    but they do not change the executable replay environment.  Keeping those two
    identities separate allows sibling behavior candidates to reuse one compiled
    replay adaptation and baseline while still preserving full-package release
    fidelity checks.
    """

    root = Path(skill_root).expanduser().resolve()
    if not root.is_dir():
        raise ReplayCapabilityError(f"skill root is not a directory: {root}")
    resolved_manifest = Path(manifest_path).expanduser().resolve()
    capability_root = resolved_manifest.parent
    try:
        capability_root.relative_to(root)
    except ValueError as exc:
        raise ReplayCapabilityError(
            "replay capability manifest must be contained by the skill root"
        ) from exc

    # Only declared executable inputs may invalidate a compiled adaptation.
    # Including every sibling under ``replay/`` made documentation, diagnostics,
    # and undeclared proposal files look like baseline-affecting changes.  That
    # prevented safe baseline reuse across behavior candidates and batches.
    surface_paths: set[Path] = {resolved_manifest}
    for declared_path in (entrypoint, *runtime_files):
        resolved = Path(declared_path).expanduser().resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ReplayCapabilityError(
                "declared replay capability file must be contained by the skill root"
            ) from exc
        if not resolved.is_file() or resolved.is_symlink():
            raise ReplayCapabilityError(
                "declared replay capability file must be a regular file"
            )
        surface_paths.add(resolved)

    package_entries = [
        _file_manifest_entry(path, path.relative_to(root).as_posix())
        for path in sorted(surface_paths)
    ]
    return _json_fingerprint(
        {
            "surface": "replay_capability",
            "files": package_entries,
        }
    )


@dataclass(frozen=True)
class ReplayCapabilityManifest:
    schema_version: str
    capability_id: str
    protocol: str
    entrypoint: str
    handles: tuple[str, ...]
    runtime_files: tuple[str, ...] = ()
    concurrency_mode: str = "exclusive"
    resource_key: str | None = None
    binding_fingerprint: str | None = None


@dataclass(frozen=True)
class DiscoveredReplayCapability:
    skill_root: Path
    manifest_path: Path
    manifest: ReplayCapabilityManifest
    entrypoint: Path
    runtime_files: tuple[Path, ...]
    package_fingerprint: str


def _replay_capability_compile_request_identity(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Project a compile request onto stable, content-addressed identity.

    Snapshot and derivation paths are locations of already fingerprinted
    inputs.  Including those locations made identical campaign cycles produce
    different capability identities solely because their artifact directory
    names changed.
    """

    raw_derivations = payload.get("evidence_derivations")
    stable_derivations: dict[str, list[dict[str, Any]]] = {}
    if isinstance(raw_derivations, Mapping):
        for evidence_ref, raw_entries in sorted(raw_derivations.items()):
            entries: list[dict[str, Any]] = []
            if isinstance(raw_entries, (list, tuple)):
                for raw_entry in raw_entries:
                    if not isinstance(raw_entry, Mapping):
                        continue
                    entries.append(
                        {
                            str(key): value
                            for key, value in sorted(
                                raw_entry.items(),
                                key=lambda item: str(item[0]),
                            )
                            if str(key) not in {"path", "response_index_path"}
                        }
                    )
            stable_derivations[str(evidence_ref)] = entries
    raw_snapshots = payload.get("context_snapshots")
    snapshot_ids = (
        sorted(str(case_id) for case_id in raw_snapshots)
        if isinstance(raw_snapshots, Mapping)
        else []
    )
    return {
        "schema_version": payload.get("schema_version"),
        "capability_id": payload.get("capability_id"),
        "requirements": payload.get("requirements"),
        "context_snapshot_ids": snapshot_ids,
        "task_inputs": payload.get("task_inputs"),
        "evidence_derivations": stable_derivations,
        "capability_package_fingerprint": payload.get(
            "capability_package_fingerprint"
        ),
        "context_fingerprint": payload.get("context_fingerprint"),
    }


@dataclass(frozen=True)
class ReplayCapabilityCompileRequest:
    schema_version: str
    capability_id: str
    requirements: tuple[ReplayCapabilityRequirement, ...]
    context_snapshots: Mapping[str, str]
    task_inputs: Mapping[str, Any]
    evidence_derivations: Mapping[str, tuple[Mapping[str, Any], ...]]
    capability_root: str
    capability_package_fingerprint: str
    context_fingerprint: str
    request_fingerprint: str

    @classmethod
    def create(
        cls,
        *,
        requirements: Sequence[ReplayCapabilityRequirement],
        context_snapshots: Mapping[str, str],
        task_inputs: Mapping[str, Any],
        capability_root: str | Path,
        context_fingerprint: str,
        capability_package_fingerprint: str | None = None,
        evidence_derivations: Mapping[
            str, Sequence[Mapping[str, Any]]
        ] | None = None,
    ) -> ReplayCapabilityCompileRequest:
        root = Path(capability_root).expanduser().resolve()
        discovered = discover_replay_capability(root)
        if discovered is None:
            raise ReplayCapabilityError(
                f"replay capability manifest not found under: {root}"
            )
        package_fingerprint = (
            capability_package_fingerprint
            if capability_package_fingerprint is not None
            else discovered.package_fingerprint
        )
        authoritative_capability_id = discovered.manifest.capability_id
        authoritative_capability_id = _parse_identifier(
            authoritative_capability_id,
            label="compile request capability_id",
        )
        normalized_derivations = {
            str(evidence_ref): [dict(item) for item in entries]
            for evidence_ref, entries in sorted(
                (evidence_derivations or {}).items()
            )
        }
        payload = {
            "schema_version": REPLAY_CAPABILITY_REQUEST_SCHEMA_VERSION,
            "capability_id": authoritative_capability_id,
            "requirements": [asdict(item) for item in requirements],
            "context_snapshots": dict(sorted(context_snapshots.items())),
            "task_inputs": dict(sorted(task_inputs.items())),
            "evidence_derivations": normalized_derivations,
            "capability_root": str(root),
            "capability_package_fingerprint": package_fingerprint,
            "context_fingerprint": context_fingerprint,
        }
        return cls(
            schema_version=REPLAY_CAPABILITY_REQUEST_SCHEMA_VERSION,
            capability_id=authoritative_capability_id,
            requirements=tuple(requirements),
            context_snapshots=payload["context_snapshots"],
            task_inputs=payload["task_inputs"],
            evidence_derivations={
                evidence_ref: tuple(entries)
                for evidence_ref, entries in normalized_derivations.items()
            },
            capability_root=str(root),
            capability_package_fingerprint=package_fingerprint,
            context_fingerprint=context_fingerprint,
            request_fingerprint=_json_fingerprint(
                _replay_capability_compile_request_identity(payload)
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReplayReadinessProbe:
    kind: str
    timeout_seconds: float
    path: str = "/"


@dataclass(frozen=True)
class ReplayProtocolProbe:
    kind: str
    timeout_seconds: float
    path: str = "/"
    validate_advertised_websockets: bool = False
    request_text: str | None = None
    response_contains: str | None = None
    response_record_id: str | None = None


@dataclass(frozen=True)
class ReplayServiceSpec:
    service_id: str
    requirement_id: str
    transport: str
    response_fixture: str
    runtime_entrypoint: str | None = None
    readiness: ReplayReadinessProbe = field(
        default_factory=lambda: ReplayReadinessProbe(
            kind="tcp",
            timeout_seconds=10.0,
        )
    )
    protocol_probes: tuple[ReplayProtocolProbe, ...] = ()


@dataclass(frozen=True)
class ReplayCapabilityCompileResult:
    schema_version: str
    capability_id: str
    deterministic: bool
    handled_requirements: tuple[str, ...]
    unhandled_requirements: tuple[str, ...]
    evidence_refs: Mapping[str, tuple[str, ...]]
    fixture_evidence_refs: Mapping[str, tuple[str, ...]]
    fixtures: tuple[str, ...]
    endpoint_replacements: Mapping[str, str]
    services: tuple[ReplayServiceSpec, ...]
    concurrency_mode: str = "exclusive"
    resource_key: str | None = None
    binding_fingerprint: str | None = None


@dataclass(frozen=True)
class FrozenReplayFile:
    path: str
    sha256: str
    size: int


@dataclass(frozen=True)
class FrozenReplayCapability:
    capability_id: str
    capability_package_fingerprint: str
    request_fingerprint: str
    frozen_root: str
    handled_requirements: tuple[str, ...]
    unhandled_requirements: tuple[str, ...]
    evidence_refs: Mapping[str, tuple[str, ...]]
    fixture_evidence_refs: Mapping[str, tuple[str, ...]]
    fixtures: tuple[FrozenReplayFile, ...]
    runtime_files: tuple[FrozenReplayFile, ...]
    endpoint_replacements: Mapping[str, str]
    services: tuple[ReplayServiceSpec, ...]
    deterministic: bool
    fingerprint: str
    ready: bool
    concurrency_mode: str = "exclusive"
    resource_key: str | None = None
    binding_fingerprint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def replay_capability_semantic_fingerprint(
    capability: FrozenReplayCapability,
) -> str:
    """Identify frozen replay behavior without run-local storage locations.

    ``request_fingerprint`` in legacy artifacts included absolute compile and
    evidence paths.  It remains part of the signed frozen-manifest fingerprint
    for artifact verification, but it is not executable support identity.
    """

    return _json_fingerprint(
        {
            "schema_version": "aworld.self_evolve.replay_capability_identity.v1",
            "capability_id": capability.capability_id,
            "capability_package_fingerprint": (
                capability.capability_package_fingerprint
            ),
            "handled_requirements": list(capability.handled_requirements),
            "unhandled_requirements": list(capability.unhandled_requirements),
            "evidence_refs": capability.evidence_refs,
            "fixture_evidence_refs": capability.fixture_evidence_refs,
            "fixtures": [asdict(item) for item in capability.fixtures],
            "runtime_files": [asdict(item) for item in capability.runtime_files],
            "endpoint_replacements": capability.endpoint_replacements,
            "services": [asdict(item) for item in capability.services],
            "deterministic": capability.deterministic,
            "concurrency_mode": capability.concurrency_mode,
            "resource_key": capability.resource_key,
            "binding_fingerprint": capability.binding_fingerprint,
        }
    )


def frozen_replay_fixture_shape_fingerprints(
    capability: FrozenReplayCapability,
) -> dict[str, str]:
    """Fingerprint frozen fixture structure without retaining payload values.

    Object keys contribute only through hashes and scalar values contribute
    only their JSON type.  This lets conformance distinguish recorded-response
    schemas while ensuring the resulting report cannot reproduce fixture
    content.
    """

    root = Path(capability.frozen_root).expanduser().resolve() / "fixtures"
    fingerprints: dict[str, str] = {}
    fixture_paths = tuple(
        dict.fromkeys(service.response_fixture for service in capability.services)
    )
    for relative_path in fixture_paths:
        try:
            unresolved_path = root / relative_path
            if unresolved_path.is_symlink():
                raise ReplayCapabilityError(
                    "frozen replay fixture cannot be a symlink"
                )
            path = unresolved_path.resolve(strict=True)
            if (
                not path.is_relative_to(root)
                or not path.is_file()
                or path.is_symlink()
            ):
                raise ReplayCapabilityError(
                    "frozen replay fixture must be a local regular file"
                )
            raw = path.read_bytes()
        except OSError as exc:
            raise ReplayCapabilityError(
                "frozen replay fixture is unavailable for structural fingerprinting"
            ) from exc
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            descriptor: object = {
                "kind": "non_json",
                "size_bucket": max(1, len(raw)).bit_length(),
                "content_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
            }
        except (MemoryError, RecursionError) as exc:
            raise ReplayCapabilityError(
                "fixture structure exceeds safe fingerprinting resources"
            ) from exc
        else:
            descriptor = {
                "bounded_descriptor": (
                    _fixture_structure_descriptor(value)
                    if len(raw) <= 2 * 1024 * 1024
                    else {
                        "kind": "oversized_json",
                        "size_bucket": len(raw).bit_length(),
                    }
                ),
                # The report descriptor is bounded, but grouping accounts for
                # every structural node regardless of file size or nesting.
                "full_structure_sha256": _fixture_full_structure_digest(value),
            }
        fingerprints[relative_path] = _json_fingerprint(descriptor)
    return fingerprints


def _fixture_full_structure_digest(value: Any) -> str:
    """Hash the complete JSON shape with an iterative bounded-resource walk."""

    digest = hashlib.sha256()
    stack: list[tuple[str, Any, int]] = [("value", value, 0)]
    visited_nodes = 0
    max_nodes = 4_000_000
    while stack:
        action, item, decoded_depth = stack.pop()
        visited_nodes += 1
        if visited_nodes > max_nodes:
            raise ReplayCapabilityError(
                "fixture structure exceeds safe fingerprinting resources"
            )
        if action == "token":
            digest.update(item)
            continue
        if isinstance(item, Mapping):
            digest.update(b"object{")
            stack.append(("token", b"}", decoded_depth))
            items = sorted(item.items(), key=lambda pair: str(pair[0]))
            for key, nested in reversed(items):
                key_bytes = str(key).encode("utf-8")
                stack.append(("value", nested, decoded_depth))
                stack.append(("token", hashlib.sha256(key_bytes).digest(), decoded_depth))
            continue
        if isinstance(item, list):
            digest.update(b"array[")
            stack.append(("token", b"]", decoded_depth))
            for nested in reversed(item):
                stack.append(("value", nested, decoded_depth))
            continue
        if isinstance(item, str):
            stripped = item.strip()
            if (
                decoded_depth < 3
                and len(stripped) <= 256 * 1024
                and stripped[:1] in {"{", "["}
            ):
                try:
                    decoded = json.loads(stripped)
                except json.JSONDecodeError:
                    pass
                else:
                    if isinstance(decoded, (Mapping, list)):
                        digest.update(b"encoded-json:")
                        stack.append(
                            ("value", decoded, decoded_depth + 1)
                        )
                        continue
            digest.update(b"string;")
            continue
        if isinstance(item, bool):
            digest.update(b"boolean;")
        elif item is None:
            digest.update(b"null;")
        elif isinstance(item, (int, float)):
            digest.update(b"number;")
        else:
            digest.update(b"unknown;")
    return "sha256:" + digest.hexdigest()


def _fixture_structure_descriptor(
    value: Any,
    *,
    depth: int = 0,
    decoded_depth: int = 0,
) -> object:
    if depth >= 10:
        return {"kind": "truncated"}
    if isinstance(value, Mapping):
        fields = []
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))[:128]:
            fields.append(
                {
                    "key_sha256": hashlib.sha256(
                        str(key).encode("utf-8")
                    ).hexdigest(),
                    "value": _fixture_structure_descriptor(
                        item,
                        depth=depth + 1,
                        decoded_depth=decoded_depth,
                    ),
                }
            )
        return {
            "kind": "object",
            "field_count": min(len(value), 129),
            "fields": fields,
        }
    if isinstance(value, list):
        element_shapes: dict[str, object] = {}
        for item in value[:128]:
            shape = _fixture_structure_descriptor(
                item,
                depth=depth + 1,
                decoded_depth=decoded_depth,
            )
            fingerprint = _json_fingerprint(shape)
            element_shapes.setdefault(fingerprint, shape)
        return {
            "kind": "array",
            "length_bucket": min(len(value), 129),
            "element_shapes": [
                element_shapes[key] for key in sorted(element_shapes)
            ],
        }
    if isinstance(value, str):
        stripped = value.strip()
        if (
            decoded_depth < 3
            and len(stripped) <= 256 * 1024
            and stripped[:1] in {"{", "["}
        ):
            try:
                decoded = json.loads(stripped)
            except json.JSONDecodeError:
                pass
            else:
                if isinstance(decoded, (Mapping, list)):
                    return {
                        "kind": "encoded_json",
                        "value": _fixture_structure_descriptor(
                            decoded,
                            depth=depth + 1,
                            decoded_depth=decoded_depth + 1,
                        ),
                    }
        return {"kind": "string"}
    if isinstance(value, bool):
        return {"kind": "boolean"}
    if value is None:
        return {"kind": "null"}
    if isinstance(value, (int, float)):
        return {"kind": "number"}
    return {"kind": "unknown"}


@dataclass(frozen=True)
class FrozenReplayCapabilityAdapter:
    capability: FrozenReplayCapability
    requirements: tuple[ReplayCapabilityRequirement, ...]

    @property
    def adapter_id(self) -> str:
        return f"skill-replay:{self.capability.capability_id}"

    def bind(
        self,
        dependency: ReplayDependency,
        *,
        context: ReplayAdapterContext,
    ) -> ReplayAdapterBinding | None:
        del context
        handled = set(self.capability.handled_requirements)
        if not any(
            item.requirement_id in handled
            and item.kind == dependency.kind
            and item.identifier == dependency.identifier
            for item in self.requirements
        ):
            return None
        return ReplayAdapterBinding(
            adapter_id=self.adapter_id,
            dependency_id=dependency.identifier,
            deterministic=self.capability.deterministic,
            concurrency_mode=self.capability.concurrency_mode,
            resource_key=self.capability.resource_key,
            binding_fingerprint=self.capability.binding_fingerprint,
        )


class ReplayCapabilityExecutor(Protocol):
    def execute(
        self,
        capability: DiscoveredReplayCapability,
        request: ReplayCapabilityCompileRequest,
        run_root: Path,
    ) -> ReplayCapabilityCompileResult:
        """Compile one isolated replay capability result."""


class SubprocessReplayCapabilityExecutor:
    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        max_output_chars: int = 64_000,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_output_chars <= 0:
            raise ValueError("max_output_chars must be positive")
        self.timeout_seconds = timeout_seconds
        self.max_output_chars = max_output_chars

    def execute(
        self,
        capability: DiscoveredReplayCapability,
        request: ReplayCapabilityCompileRequest,
        run_root: Path,
    ) -> ReplayCapabilityCompileResult:
        run_root.mkdir(parents=True, exist_ok=False)
        output_root = run_root / "output"
        output_root.mkdir()
        request_path = run_root / "request.json"
        _write_json(request_path, request.to_dict())
        request_path.chmod(0o400)
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        stdout_path = run_root / "compiler.stdout.txt"
        stderr_path = run_root / "compiler.stderr.txt"
        try:
            with stdout_path.open("wb") as stdout_handle, stderr_path.open(
                "wb"
            ) as stderr_handle:
                command = build_replay_sandboxed_command(
                    [
                        sys.executable,
                        "-I",
                        str(capability.entrypoint),
                        "--request",
                        str(request_path),
                        "--output",
                        str(output_root),
                    ],
                    read_roots=(
                        capability.skill_root,
                        run_root,
                        *(
                            Path(path).expanduser().resolve().parent
                            for path in request.context_snapshots.values()
                            if Path(path).expanduser().exists()
                        ),
                        *(
                            Path(str(item.get("path"))).expanduser().resolve().parent
                            for entries in request.evidence_derivations.values()
                            for item in entries
                            if isinstance(item.get("path"), str)
                            and Path(str(item.get("path"))).expanduser().exists()
                        ),
                    ),
                    writable_roots=(run_root,),
                    allow_loopback=False,
                )
                command = build_replay_resource_limited_command(
                    command,
                    max_file_bytes=max(
                        self.max_output_chars,
                        _MAX_FIXTURE_FILE_BYTES,
                    ),
                    max_memory_bytes=512 * 1024 * 1024,
                    cpu_seconds=max(1, math.ceil(self.timeout_seconds)),
                )
                process = subprocess.Popen(
                    command,
                    cwd=run_root,
                    env=environment,
                    shell=False,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    start_new_session=True,
                )
                deadline = time.monotonic() + self.timeout_seconds
                total_output_limit = _MAX_FIXTURE_TOTAL_BYTES + 4 * _MAX_JSON_BYTES
                while process.poll() is None:
                    if _directory_size_bytes(run_root) > total_output_limit:
                        _terminate_process_group(process)
                        raise ReplayCapabilityError(
                            "replay capability compile exceeded total disk limit"
                        )
                    if replay_process_memory_bytes(process.pid) > 512 * 1024 * 1024:
                        _terminate_process_group(process)
                        raise ReplayCapabilityError(
                            "replay capability compile exceeded memory limit"
                        )
                    if time.monotonic() >= deadline:
                        _terminate_process_group(process)
                        raise subprocess.TimeoutExpired(
                            command,
                            self.timeout_seconds,
                        )
                    time.sleep(0.02)
        except subprocess.TimeoutExpired as exc:
            raise ReplayCapabilityError(
                f"replay capability compile timed out after {self.timeout_seconds}s"
            ) from exc
        if process.returncode != 0:
            stdout = _bounded_output(stdout_path, self.max_output_chars)
            stderr = _bounded_output(stderr_path, self.max_output_chars)
            raise ReplayCapabilityError(
                "replay capability compile failed "
                f"(exit={process.returncode}, stdout={stdout!r}, stderr={stderr!r})"
            )
        result_path = output_root / "result.json"
        if not result_path.is_file() or result_path.is_symlink():
            raise ReplayCapabilityError(
                "replay capability did not produce output/result.json"
            )
        return _parse_compile_result(result_path, capability, request, output_root)


def discover_replay_capability(
    skill_root: str | Path,
) -> DiscoveredReplayCapability | None:
    root = Path(skill_root).expanduser().resolve()
    manifest_path = root / _MANIFEST_PATH.as_posix()
    if not manifest_path.exists():
        return None
    if not root.is_dir():
        raise ReplayCapabilityError(f"skill root is not a directory: {root}")
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ReplayCapabilityError("replay capability manifest must be a regular file")
    raw = _read_json_object(manifest_path, label="replay capability manifest")
    manifest = _parse_manifest(raw)
    entrypoint = _resolve_skill_file(root, manifest.entrypoint, label="entrypoint")
    if entrypoint.suffix.lower() != ".py":
        raise ReplayCapabilityError("replay capability entrypoint must be a Python file")
    runtime_files = tuple(
        _resolve_skill_file(root, item, label="runtime file")
        for item in manifest.runtime_files
    )
    return DiscoveredReplayCapability(
        skill_root=root,
        manifest_path=manifest_path,
        manifest=manifest,
        entrypoint=entrypoint,
        runtime_files=runtime_files,
        package_fingerprint=fingerprint_replay_capability_package(
            root,
            manifest_path=manifest_path,
            entrypoint=entrypoint,
            runtime_files=runtime_files,
        ),
    )


def compile_and_freeze_capability(
    capability: DiscoveredReplayCapability,
    request: ReplayCapabilityCompileRequest,
    artifact_root: str | Path,
    *,
    executor: ReplayCapabilityExecutor | None = None,
) -> FrozenReplayCapability:
    if request.capability_package_fingerprint != capability.package_fingerprint:
        raise ReplayCapabilityError(
            "replay capability package changed after compile request creation"
        )
    artifact = Path(artifact_root).expanduser().resolve()
    artifact.mkdir(parents=True, exist_ok=True)
    compile_a = artifact / "compile-a"
    compile_b = artifact / "compile-b"
    frozen_root = artifact / "frozen"
    for path in (compile_a, compile_b, frozen_root):
        _remove_path(path)
    compiler = executor or SubprocessReplayCapabilityExecutor()
    try:
        _verify_discovered_capability_unchanged(capability)
        _validate_discovered_python_entrypoint(capability)
        first = compiler.execute(capability, request, compile_a)
        _verify_discovered_capability_unchanged(capability)
        second = compiler.execute(capability, request, compile_b)
        _verify_discovered_capability_unchanged(capability)
        first_snapshot = _compile_snapshot(first, compile_a / "output", capability)
        second_snapshot = _compile_snapshot(second, compile_b / "output", capability)
        if first_snapshot != second_snapshot:
            raise ReplayCapabilityError(
                "replay capability produced non-deterministic compile results"
            )
        if not first.deterministic:
            raise ReplayCapabilityError(
                "replay capability declared a non-deterministic compile result"
            )
        frozen = _freeze_compile_result(
            capability=capability,
            request=request,
            result=first,
            output_root=compile_a / "output",
            frozen_root=frozen_root,
        )
        verify_frozen_replay_capability(frozen)
    except ReplayCapabilityError as exc:
        supplemental = _compile_failure_runtime_binding_diagnostics(
            capability,
            request=request,
            output_roots=(compile_a / "output", compile_b / "output"),
        )
        _remove_path(frozen_root)
        if supplemental is not None:
            merged_details = dict(exc.details)
            for key in (
                "schema_field_constraints",
                "schema_field_violations",
                "source_behavior_proofs",
            ):
                existing = merged_details.get(key)
                added = supplemental.get(key)
                if isinstance(added, list):
                    merged_details[key] = [
                        *(existing if isinstance(existing, list) else []),
                        *added,
                    ]
            merged_details["constraint_collection"] = "compile_and_runtime"
            raise ReplayCapabilityError(
                str(exc),
                code=exc.code,
                details=merged_details,
            ) from exc
        raise
    except Exception:
        _remove_path(frozen_root)
        raise
    return frozen


def _compile_failure_runtime_binding_diagnostics(
    capability: DiscoveredReplayCapability,
    *,
    request: ReplayCapabilityCompileRequest,
    output_roots: Sequence[Path],
) -> dict[str, object] | None:
    """Collect an independent runtime invariant after compiler validation fails.

    Compiler-result schema errors must not hide an already-provable runtime
    failure. The check remains capability-generic: it activates only for a
    runtime-required request whose compiler emitted a fixture with a recorded
    non-empty response.
    """

    if not any(
        requirement.status == "runtime_required"
        for requirement in request.requirements
    ):
        return None
    has_recorded_response = False
    for root in output_roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*"))[:_MAX_FIXTURE_COUNT]:
            if not path.is_file() or path.is_symlink():
                continue
            try:
                if path.stat().st_size > _MAX_FIXTURE_FILE_BYTES:
                    continue
                response_index = _build_recorded_response_index(
                    path.read_bytes(),
                    observed_operations=(),
                )
            except OSError:
                continue
            records = response_index.get("records")
            if isinstance(records, list) and any(
                isinstance(record, Mapping)
                and record.get("non_empty") is True
                and "value" in record
                for record in records
            ):
                has_recorded_response = True
                break
        if has_recorded_response:
            break
    if not has_recorded_response:
        return None

    proofs: list[dict[str, object]] = []
    for runtime_path in capability.runtime_files:
        if runtime_path.suffix.casefold() != ".py":
            continue
        try:
            proof = recorded_response_index_source_behavior_proof(
                runtime_path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError):
            continue
        proofs.append(proof)
        if proof.get("proven") is True:
            return None
    if not proofs:
        return None

    violation = _schema_field_violation(
        schema_layer="runtime",
        field_path=(
            "environment.AWORLD_REPLAY_RESPONSE_INDEX.consumer"
        ),
        rule="enum",
        expected=(REPLAY_RESPONSE_INDEX_CONSUMER,),
        value="source_behavior_not_detected",
        value_domain="source_behavior",
        required_operations=(
            "read_environment_binding_as_path",
            "bind_environment_path_to_json_file_reader",
            "access_records_array",
            "project_record_value_field_directly",
        ),
        forbidden_operations=(
            "coerce_environment_binding_to_numeric_index",
            "hide_environment_read_behind_zero_arg_return_helper",
            "substitute_raw_fixture_recursive_scan",
        ),
    )
    details = schema_field_diagnostic_details((violation,))
    details["source_behavior_proofs"] = proofs
    return details


def _verify_discovered_capability_unchanged(
    capability: DiscoveredReplayCapability,
) -> None:
    current = discover_replay_capability(capability.skill_root)
    if (
        current is None
        or current.manifest.capability_id != capability.manifest.capability_id
        or current.package_fingerprint != capability.package_fingerprint
    ):
        raise ReplayCapabilityError(
            "replay capability package changed during deterministic compilation"
        )


def verify_frozen_replay_capability(capability: FrozenReplayCapability) -> None:
    root = Path(capability.frozen_root).expanduser().resolve()
    for category, files in (
        ("fixtures", capability.fixtures),
        ("runtime", capability.runtime_files),
    ):
        category_root = root / category
        for item in files:
            path = _resolve_output_file(category_root, item.path)
            actual = _frozen_file(path, item.path)
            if actual != item:
                raise ReplayCapabilityError(
                    f"frozen replay capability file changed: {category}/{item.path}"
                )
    _verify_recorded_response_indexes_and_runtime_bindings(capability, root=root)
    manifest = _read_json_object(
        root / "frozen_manifest.json",
        label="frozen replay capability manifest",
    )
    payload = {
        "schema_version": REPLAY_CAPABILITY_RESULT_SCHEMA_VERSION,
        "capability_id": capability.capability_id,
        "capability_package_fingerprint": (
            capability.capability_package_fingerprint
        ),
        "request_fingerprint": capability.request_fingerprint,
        "handled_requirements": list(capability.handled_requirements),
        "unhandled_requirements": list(capability.unhandled_requirements),
        "evidence_refs": capability.evidence_refs,
        "fixture_evidence_refs": capability.fixture_evidence_refs,
        "fixtures": [asdict(item) for item in capability.fixtures],
        "runtime_files": [asdict(item) for item in capability.runtime_files],
        "endpoint_replacements": capability.endpoint_replacements,
        "services": [asdict(item) for item in capability.services],
        "deterministic": capability.deterministic,
    }
    if any(
        key in manifest
        for key in ("concurrency_mode", "resource_key", "binding_fingerprint")
    ):
        payload.update(
            {
                "concurrency_mode": capability.concurrency_mode,
                "resource_key": capability.resource_key,
                "binding_fingerprint": capability.binding_fingerprint,
            }
        )
    if _json_fingerprint(payload) != capability.fingerprint:
        raise ReplayCapabilityError("frozen replay capability fingerprint mismatch")
    if manifest.get("fingerprint") != capability.fingerprint:
        raise ReplayCapabilityError("frozen replay capability manifest mismatch")


def _verify_recorded_response_indexes_and_runtime_bindings(
    capability: FrozenReplayCapability,
    *,
    root: Path,
) -> None:
    """Verify framework-derived sidecars and the generic runtime binding.

    Operation-indexed response sidecars are derived from immutable fixtures,
    rather than declared by a skill compiler. Recomputing them here both
    protects their integrity and establishes a transport-independent contract:
    a skill runtime cannot claim readiness while ignoring recorded responses
    that the framework has made available to it.
    """

    fixture_root = root / "fixtures"
    runtime_root = root / "runtime"
    observed_operations = _declared_probe_operations(capability.services)
    verified_fixtures: dict[str, Mapping[str, Any]] = {}
    for service in capability.services:
        response_index = verified_fixtures.get(service.response_fixture)
        if response_index is None:
            fixture_path = _resolve_output_file(
                fixture_root,
                service.response_fixture,
            )
            try:
                response_index = _build_recorded_response_index(
                    fixture_path.read_bytes(),
                    observed_operations=observed_operations,
                )
            except OSError as exc:
                raise ReplayCapabilityError(
                    "cannot verify frozen replay response index"
                ) from exc
            verified_fixtures[service.response_fixture] = response_index
            sidecar_path = fixture_path.with_suffix(".responses.json")
            if response_index.get("records"):
                actual_index = _read_json_object(
                    sidecar_path,
                    label="frozen replay response index",
                )
                if actual_index != response_index:
                    raise ReplayCapabilityError(
                        "frozen replay response index does not match its fixture"
                    )

        records = response_index.get("records")
        has_nonempty_record = isinstance(records, list) and any(
            isinstance(record, Mapping)
            and record.get("non_empty") is True
            and "value" in record
            for record in records
        )
        if service.transport != "skill_runtime" or not has_nonempty_record:
            continue
        if service.runtime_entrypoint is None:
            raise ReplayCapabilityError(
                "skill runtime with recorded responses requires an entrypoint"
            )
        runtime_entrypoint = _resolve_output_file(
            runtime_root,
            service.runtime_entrypoint,
        )
        try:
            source = runtime_entrypoint.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ReplayCapabilityError(
                "skill runtime entrypoint must be readable UTF-8 source"
            ) from exc
        source_behavior_proof = recorded_response_index_source_behavior_proof(
            source
        )
        if source_behavior_proof.get("proven") is not True:
            _raise_schema_field_error(
                "skill runtime with recorded responses must consume "
                f"{REPLAY_RESPONSE_INDEX_ENV} as a JSON sidecar file path, "
                "not a numeric index: in one provable lexical data-flow scope, "
                "bind the environment value directly to a JSON file reader (or "
                "pass it as that reader's path parameter), iterate "
                "index_object['records'], and directly project record['value']; "
                "do not hide the environment read behind a zero-argument return "
                "helper or substitute a recursive scan of the raw fixture",
                (
                    _schema_field_violation(
                        schema_layer="runtime",
                        field_path=(
                            "environment.AWORLD_REPLAY_RESPONSE_INDEX.consumer"
                        ),
                        rule="enum",
                        expected=(REPLAY_RESPONSE_INDEX_CONSUMER,),
                        value="source_behavior_not_detected",
                        value_domain="source_behavior",
                        required_operations=(
                            "read_environment_binding_as_path",
                            "bind_environment_path_to_json_file_reader",
                            "access_records_array",
                            "project_record_value_field_directly",
                        ),
                        forbidden_operations=(
                            "coerce_environment_binding_to_numeric_index",
                            "hide_environment_read_behind_zero_arg_return_helper",
                            "substitute_raw_fixture_recursive_scan",
                        ),
                    ),
                ),
                extra_details={
                    "source_behavior_proofs": [source_behavior_proof],
                },
            )


def _require_recorded_response_fixture_for_sidecar_runtime(
    *,
    capability: DiscoveredReplayCapability,
    request: ReplayCapabilityCompileRequest,
    result: ReplayCapabilityCompileResult,
    fixture: str,
    response_index: Mapping[str, Any],
) -> None:
    """Reject a compiler selector that strands a proven sidecar consumer.

    The framework may offer several immutable derivations for one evidence
    reference.  A compact request/tool-call fragment can be the smallest
    source while containing no recorded response at all.  If a declared skill
    runtime is statically proven to consume ``AWORLD_REPLAY_RESPONSE_INDEX``,
    launching it without a generated sidecar can only fail.  Detect that
    candidate-owned source-selection error before operational conformance.
    """

    records = response_index.get("records")
    if isinstance(records, list) and records:
        return
    requirements = {
        item.requirement_id: item for item in request.requirements
    }
    affected_services: list[str] = []
    available_recorded_source_count = 0
    for service in result.services:
        if (
            service.response_fixture != fixture
            or service.transport != "skill_runtime"
            or service.runtime_entrypoint is None
            or not service.protocol_probes
        ):
            continue
        requirement = requirements.get(service.requirement_id)
        if requirement is None:
            continue
        eligible_sources = [
            source
            for evidence_ref in requirement.evidence_refs
            for source in request.evidence_derivations.get(evidence_ref, ())
            if isinstance(source.get("response_index_path"), str)
            and bool(str(source["response_index_path"]).strip())
            and isinstance(source.get("response_record_count"), int)
            and not isinstance(source.get("response_record_count"), bool)
            and int(source["response_record_count"]) > 0
        ]
        if not eligible_sources:
            continue
        runtime_path = _resolve_skill_file(
            capability.skill_root,
            service.runtime_entrypoint,
            label="runtime entrypoint",
        )
        try:
            runtime_source = runtime_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if recorded_response_index_source_behavior_proof(runtime_source).get(
            "proven"
        ) is not True:
            continue
        affected_services.append(service.service_id)
        available_recorded_source_count += len(eligible_sources)
    if not affected_services:
        return

    violation = _schema_field_violation(
        schema_layer="compiler",
        field_path="evidence_derivations[*].response_index_path",
        rule="enum",
        expected=("recorded_response_source",),
        value="selected_fixture_without_recorded_response",
        value_domain="source_behavior",
        required_operations=(
            "restrict_to_positive_recorded_response_sources",
            "prefer_maximum_response_record_count",
        ),
        forbidden_operations=(
            "rank_all_sources_by_minimum_byte_length_first",
        ),
    )
    details = schema_field_diagnostic_details((violation,))
    details.update(
        {
            "counterexample_contracts": [
                _schema_field_counterexample_contract(violation)
            ],
            "fixture": fixture,
            "affected_service_ids": sorted(affected_services),
            "available_recorded_source_count": (
                available_recorded_source_count
            ),
        }
    )
    raise ReplayCapabilityError(
        "skill runtime consumes AWORLD_REPLAY_RESPONSE_INDEX but the compiler "
        "selected a fixture without recorded responses",
        code="recorded_response_fixture_unselected",
        details=details,
    )


def _runtime_consumes_recorded_response_index(source: str) -> bool:
    """Recognize the minimal language-level sidecar consumption contract.

    The analyzer intentionally proves a small data-flow property rather than
    accepting disconnected token mentions: within one lexical scope, a value
    read from the response-index environment binding must flow into a file
    reader.  The runtime must also project the framework-owned ``records`` and
    ``value`` keys.  Precise payload correctness remains the responsibility of
    the protocol probe.
    """

    return recorded_response_index_source_behavior_proof(source).get(
        "proven"
    ) is True


def recorded_response_index_source_behavior_proof(
    source: str,
) -> dict[str, object]:
    """Return a bounded proof graph for the response-index source contract.

    The result deliberately describes language-level operations and broken
    propagation edges, never source excerpts or fixture payloads. It is safe to
    pass through candidate repair feedback and gives a generator executable
    evidence about *why* a data-flow claim was not proved.
    """

    required_operations = (
        "read_environment_binding_as_path",
        "bind_environment_path_to_json_file_reader",
        "access_records_array",
        "project_record_value_field_directly",
    )
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        operation_status = {
            operation: False for operation in required_operations
        }
        topology = {
            "operation_status": operation_status,
            "unsupported_boundary_kinds": ["syntax_error"],
        }
        return {
            "schema_version": "aworld.self_evolve.source_behavior_proof.v1",
            "analyzer": "python_ast_bounded_dataflow",
            "predicate": (
                "environment.AWORLD_REPLAY_RESPONSE_INDEX.consumer"
            ),
            "expected_behavior": REPLAY_RESPONSE_INDEX_CONSUMER,
            "proven": False,
            "proof_fingerprint": _source_behavior_proof_fingerprint(topology),
            "operation_status": operation_status,
            "missing_operations": list(required_operations),
            "unsupported_boundaries": [
                {
                    "kind": "syntax_error",
                    "line": max(1, int(exc.lineno or 1)),
                }
            ],
            "repair_guidance": [
                "make the required source branch parseable before proving data flow"
            ],
        }

    _inline_response_index_environment_key_constants(tree)

    accessed_keys = _runtime_accessed_key_locations(tree)
    function_scopes = _runtime_function_scopes(tree)
    reader_parameters = _runtime_file_reader_parameter_summaries(function_scopes)
    named_scopes = _runtime_named_lexical_scopes(tree)
    environment_sources: list[dict[str, object]] = []
    file_readers: list[dict[str, object]] = []
    unsupported_boundaries: list[dict[str, object]] = []
    reader_binding_proven = False
    for scope_name, scope_nodes in named_scopes:
        reader_binding_proven = reader_binding_proven or (
            _scope_reads_response_index_file(
                scope_nodes,
                reader_parameters=reader_parameters,
                function_scopes=function_scopes,
            )
        )
        environment_sources.extend(
            _runtime_environment_source_locations(scope_name, scope_nodes)
        )
        file_readers.extend(
            _runtime_file_reader_locations(scope_name, scope_nodes)
        )
        unsupported_boundaries.extend(
            _runtime_attribute_storage_boundaries(scope_name, scope_nodes)
        )

    operation_status = {
        "read_environment_binding_as_path": bool(environment_sources),
        "bind_environment_path_to_json_file_reader": reader_binding_proven,
        "access_records_array": bool(accessed_keys.get("records")),
        "project_record_value_field_directly": bool(accessed_keys.get("value")),
    }
    missing_operations = [
        operation
        for operation in required_operations
        if not operation_status[operation]
    ]
    proven = not missing_operations
    boundary_kinds = sorted(
        {
            str(item.get("kind") or "unknown")
            for item in unsupported_boundaries
        }
    )
    environment_scope_names = {
        str(item["scope"]) for item in environment_sources
    }
    reader_scope_names = {str(item["scope"]) for item in file_readers}
    topology = {
        "operation_status": operation_status,
        "unsupported_boundary_kinds": boundary_kinds,
        "environment_source_scope_count": len(environment_scope_names),
        "file_reader_scope_count": len(reader_scope_names),
        "source_reader_scope_overlap": bool(
            environment_scope_names & reader_scope_names
        ),
    }
    guidance: list[str] = []
    if not operation_status["read_environment_binding_as_path"]:
        guidance.append(
            "read the framework environment binding in the runtime source branch"
        )
    if not reader_binding_proven:
        guidance.append(
            "keep the environment-derived path and JSON file reader in one lexical scope or pass it only through explicit function parameters"
        )
    if unsupported_boundaries:
        guidance.append(
            "replace attribute or container state propagation with local assignments or explicit function arguments"
        )
    if not operation_status["access_records_array"]:
        guidance.append("access the sidecar records field explicitly")
    if not operation_status["project_record_value_field_directly"]:
        guidance.append("project each record value field explicitly")
    return {
        "schema_version": "aworld.self_evolve.source_behavior_proof.v1",
        "analyzer": "python_ast_bounded_dataflow",
        "predicate": "environment.AWORLD_REPLAY_RESPONSE_INDEX.consumer",
        "expected_behavior": REPLAY_RESPONSE_INDEX_CONSUMER,
        "proven": proven,
        "proof_fingerprint": _source_behavior_proof_fingerprint(topology),
        "operation_status": operation_status,
        "missing_operations": missing_operations,
        "environment_sources": environment_sources[:16],
        "file_readers": file_readers[:16],
        "key_accesses": {
            key: values[:16]
            for key, values in sorted(accessed_keys.items())
            if key in {"records", "value"}
        },
        "unsupported_boundaries": unsupported_boundaries[:16],
        "repair_guidance": guidance[:8],
    }


def _source_behavior_proof_fingerprint(topology: Mapping[str, object]) -> str:
    encoded = json.dumps(
        topology,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _inline_response_index_environment_key_constants(tree: ast.Module) -> None:
    """Resolve safe module constants used only as environment lookup keys.

    Generated runtimes commonly name the framework environment key before
    reading it, for example ``INDEX_ENV = "AWORLD_REPLAY_RESPONSE_INDEX"``.
    The data-flow analyzer previously required the literal to appear directly
    in ``os.environ.get``/``os.getenv`` and therefore rejected that equivalent
    local path flow.  Resolve only a direct, unique top-level string binding;
    dynamic aliases, reassignments, destructuring, imports, and names declared
    global inside a function remain outside the proof boundary.
    """

    candidates: set[str] = set()
    for statement in tree.body:
        target: ast.AST | None = None
        value: ast.AST | None = None
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target = statement.targets[0]
            value = statement.value
        elif isinstance(statement, ast.AnnAssign):
            target = statement.target
            value = statement.value
        if not isinstance(target, ast.Name):
            continue
        if (
            isinstance(value, ast.Constant)
            and value.value == REPLAY_RESPONSE_INDEX_ENV
        ):
            candidates.add(target.id)

    module_binding_counts: dict[str, int] = {}

    class ModuleBindingCounter(ast.NodeVisitor):
        def bind(self, name: str) -> None:
            module_binding_counts[name] = module_binding_counts.get(name, 0) + 1

        def visit_Name(self, node: ast.Name) -> None:
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                self.bind(node.id)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.bind(node.name)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self.bind(node.name)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self.bind(node.name)

        def visit_Lambda(self, node: ast.Lambda) -> None:
            return None

        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                self.bind(alias.asname or alias.name.partition(".")[0])

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            for alias in node.names:
                self.bind(alias.asname or alias.name)

    counter = ModuleBindingCounter()
    for statement in tree.body:
        counter.visit(statement)

    global_mutations = {
        name
        for node in ast.walk(tree)
        if isinstance(node, ast.Global)
        for name in node.names
    }
    safe_names = {
        name
        for name in candidates
        if module_binding_counts.get(name) == 1 and name not in global_mutations
    }
    if not safe_names:
        return

    class EnvironmentKeyInliner(ast.NodeTransformer):
        @staticmethod
        def replacement(value: ast.AST) -> ast.AST:
            if isinstance(value, ast.Name) and value.id in safe_names:
                return ast.copy_location(
                    ast.Constant(value=REPLAY_RESPONSE_INDEX_ENV),
                    value,
                )
            return value

        def visit_Call(self, node: ast.Call) -> ast.AST:
            self.generic_visit(node)
            if not node.args:
                return node
            if isinstance(node.func, ast.Attribute):
                is_environment_get = (
                    node.func.attr == "get"
                    and _is_os_environ_expression(node.func.value)
                ) or (
                    node.func.attr == "getenv"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "os"
                )
            else:
                is_environment_get = (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "getenv"
                )
            if is_environment_get:
                node.args[0] = self.replacement(node.args[0])
            return node

        def visit_Subscript(self, node: ast.Subscript) -> ast.AST:
            self.generic_visit(node)
            if _is_os_environ_expression(node.value):
                node.slice = self.replacement(node.slice)
            return node

    EnvironmentKeyInliner().visit(tree)


def _runtime_accessed_key_locations(
    tree: ast.Module,
) -> dict[str, list[dict[str, object]]]:
    locations: dict[str, list[dict[str, object]]] = {}
    for node in ast.walk(tree):
        key: object | None = None
        access_kind: str | None = None
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            key = node.slice.value
            access_kind = "subscript"
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            key = node.args[0].value
            access_kind = "mapping_get"
        if not isinstance(key, str) or access_kind is None:
            continue
        locations.setdefault(key, []).append(
            {
                "line": max(1, int(getattr(node, "lineno", 1))),
                "access_kind": access_kind,
            }
        )
    return locations


def _runtime_named_lexical_scopes(
    tree: ast.Module,
) -> tuple[tuple[str, tuple[ast.AST, ...]], ...]:
    scopes: list[tuple[str, tuple[ast.AST, ...]]] = []
    module_nodes = _collect_runtime_scope_nodes(tree.body)
    if module_nodes:
        scopes.append(("<module>", module_nodes))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function_nodes = _collect_runtime_scope_nodes(node.body)
            if function_nodes:
                scopes.append((node.name, function_nodes))
    return tuple(scopes)


def _runtime_environment_source_locations(
    scope_name: str,
    nodes: Sequence[ast.AST],
) -> list[dict[str, object]]:
    locations: list[dict[str, object]] = []
    seen_lines: set[int] = set()
    for node in nodes:
        if not isinstance(node, (ast.Call, ast.Subscript)):
            continue
        if not _expression_depends_on_response_index(
            node,
            set(),
            include_environment_binding=True,
        ):
            continue
        line = max(1, int(getattr(node, "lineno", 1)))
        if line in seen_lines:
            continue
        seen_lines.add(line)
        locations.append({"scope": scope_name, "line": line})
    return locations


def _runtime_file_reader_locations(
    scope_name: str,
    nodes: Sequence[ast.AST],
) -> list[dict[str, object]]:
    locations: list[dict[str, object]] = []
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        reader_kind: str | None = None
        argument: ast.AST | None = None
        if isinstance(node.func, ast.Name) and node.func.id == "open":
            reader_kind = "open"
            argument = node.args[0] if node.args else None
        elif isinstance(node.func, ast.Attribute) and node.func.attr in {
            "open",
            "read_bytes",
            "read_text",
        }:
            reader_kind = node.func.attr
            argument = node.func.value
        if reader_kind is None:
            continue
        locations.append(
            {
                "scope": scope_name,
                "line": max(1, int(getattr(node, "lineno", 1))),
                "reader_kind": reader_kind,
                "argument_kind": (
                    type(argument).__name__ if argument is not None else "missing"
                ),
            }
        )
    return locations


def _runtime_attribute_storage_boundaries(
    scope_name: str,
    nodes: Sequence[ast.AST],
) -> list[dict[str, object]]:
    bound_names = _runtime_scope_tainted_names(
        nodes,
        initial_bound_names=set(),
        include_environment_binding=True,
    )
    boundaries: list[dict[str, object]] = []
    for node in nodes:
        if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            continue
        value = node.value
        if not _expression_depends_on_response_index(
            value,
            bound_names,
            include_environment_binding=True,
        ):
            continue
        raw_targets: Sequence[ast.AST] = (
            node.targets if isinstance(node, ast.Assign) else (node.target,)
        )
        for target in raw_targets:
            for nested in ast.walk(target):
                if not isinstance(nested, (ast.Attribute, ast.Subscript)):
                    continue
                boundaries.append(
                    {
                        "kind": (
                            "attribute_storage"
                            if isinstance(nested, ast.Attribute)
                            else "container_storage"
                        ),
                        "scope": scope_name,
                        "line": max(1, int(getattr(nested, "lineno", 1))),
                    }
                )
                break
    return boundaries


def _runtime_lexical_scopes(tree: ast.Module) -> tuple[tuple[ast.AST, ...], ...]:
    """Return bounded lexical scopes without merging unrelated functions."""

    scopes: list[tuple[ast.AST, ...]] = []
    module_nodes = _collect_runtime_scope_nodes(tree.body)
    if module_nodes:
        scopes.append(module_nodes)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function_nodes = _collect_runtime_scope_nodes(node.body)
            if function_nodes:
                scopes.append(function_nodes)
    return tuple(scopes)


def _collect_runtime_scope_nodes(
    statements: Sequence[ast.stmt],
) -> tuple[ast.AST, ...]:
    class ScopeCollector(ast.NodeVisitor):
        def __init__(self) -> None:
            self.nodes: list[ast.AST] = []

        def generic_visit(self, node: ast.AST) -> None:
            self.nodes.append(node)
            super().generic_visit(node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            return None

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            return None

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            return None

        def visit_Lambda(self, node: ast.Lambda) -> None:
            return None

    collector = ScopeCollector()
    for statement in statements:
        collector.visit(statement)
    return tuple(collector.nodes)


def _runtime_function_scopes(
    tree: ast.Module,
) -> dict[str, tuple[tuple[str, ...], tuple[ast.AST, ...]]]:
    functions: dict[str, tuple[tuple[str, ...], tuple[ast.AST, ...]]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        parameters = tuple(
            argument.arg
            for argument in (
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            )
        )
        functions[node.name] = (
            parameters,
            _collect_runtime_scope_nodes(node.body),
        )
    return functions


def _runtime_file_reader_parameter_summaries(
    function_scopes: Mapping[
        str,
        tuple[tuple[str, ...], tuple[ast.AST, ...]],
    ],
) -> dict[str, frozenset[int]]:
    """Summarize parameters that reach readers through bounded call chains."""

    summaries: dict[str, set[int]] = {
        function_name: set() for function_name in function_scopes
    }
    # Each fixed-point pass adds at least one parameter or terminates. The
    # aggregate parameter count is therefore a strict upper bound.
    pass_limit = 1 + sum(len(value[0]) for value in function_scopes.values())
    for _ in range(pass_limit):
        changed = False
        frozen = {
            name: frozenset(parameter_indexes)
            for name, parameter_indexes in summaries.items()
        }
        for function_name, (parameters, nodes) in function_scopes.items():
            for parameter_index, parameter_name in enumerate(parameters):
                if parameter_index in summaries[function_name]:
                    continue
                if _scope_taint_reaches_file_reader(
                    nodes,
                    initial_bound_names={parameter_name},
                    include_environment_binding=False,
                    reader_parameters=frozen,
                    function_scopes=function_scopes,
                ):
                    summaries[function_name].add(parameter_index)
                    changed = True
        if not changed:
            break
    return {
        name: frozenset(parameter_indexes)
        for name, parameter_indexes in summaries.items()
    }


def _scope_reads_response_index_file(
    nodes: Sequence[ast.AST],
    *,
    reader_parameters: Mapping[str, frozenset[int]],
    function_scopes: Mapping[
        str,
        tuple[tuple[str, ...], tuple[ast.AST, ...]],
    ],
) -> bool:
    """Prove environment-binding flow into a file-reader in one scope."""

    return _scope_taint_reaches_file_reader(
        nodes,
        initial_bound_names=set(),
        include_environment_binding=True,
        reader_parameters=reader_parameters,
        function_scopes=function_scopes,
    )


def _scope_taint_reaches_file_reader(
    nodes: Sequence[ast.AST],
    *,
    initial_bound_names: set[str],
    include_environment_binding: bool,
    reader_parameters: Mapping[str, frozenset[int]],
    function_scopes: Mapping[
        str,
        tuple[tuple[str, ...], tuple[ast.AST, ...]],
    ],
) -> bool:
    """Propagate one bounded source through assignments and local calls."""

    bound_names = _runtime_scope_tainted_names(
        nodes,
        initial_bound_names=initial_bound_names,
        include_environment_binding=include_environment_binding,
    )

    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "open":
            if node.args and _expression_depends_on_response_index(
                node.args[0],
                bound_names,
                include_environment_binding=include_environment_binding,
            ):
                return True
        if isinstance(node.func, ast.Attribute) and node.func.attr in {
            "open",
            "read_bytes",
            "read_text",
        }:
            if _expression_depends_on_response_index(
                node.func.value,
                bound_names,
                include_environment_binding=include_environment_binding,
            ):
                return True
        called_name = _runtime_called_function_name(node.func)
        if called_name is None or called_name not in reader_parameters:
            continue
        parameters = function_scopes[called_name][0]
        for parameter_index in reader_parameters[called_name]:
            argument = _runtime_call_argument(node, parameter_index, parameters)
            if argument is not None and _expression_depends_on_response_index(
                argument,
                bound_names,
                include_environment_binding=include_environment_binding,
            ):
                return True
    return False


def _runtime_scope_tainted_names(
    nodes: Sequence[ast.AST],
    *,
    initial_bound_names: set[str],
    include_environment_binding: bool,
) -> set[str]:
    """Return local names transitively derived from one bounded source."""

    bound_names = set(initial_bound_names)
    assignments: list[tuple[tuple[str, ...], ast.AST]] = []
    for node in nodes:
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            value = node.value
            raw_targets: Sequence[ast.AST]
            if isinstance(node, ast.Assign):
                raw_targets = node.targets
            else:
                raw_targets = (node.target,)
            target_names = tuple(
                name
                for target in raw_targets
                for name in _assigned_runtime_names(target)
            )
            if target_names:
                assignments.append((target_names, value))

    changed = True
    while changed:
        changed = False
        for target_names, value in assignments:
            if not _expression_depends_on_response_index(
                value,
                bound_names,
                include_environment_binding=include_environment_binding,
            ):
                continue
            for name in target_names:
                if name not in bound_names:
                    bound_names.add(name)
                    changed = True
    return bound_names


def _runtime_called_function_name(function: ast.AST) -> str | None:
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        return function.attr
    return None


def _runtime_call_argument(
    call: ast.Call,
    parameter_index: int,
    parameters: Sequence[str],
) -> ast.AST | None:
    if parameter_index < len(call.args):
        return call.args[parameter_index]
    if parameter_index >= len(parameters):
        return None
    parameter_name = parameters[parameter_index]
    for keyword in call.keywords:
        if keyword.arg == parameter_name:
            return keyword.value
    return None


def _assigned_runtime_names(target: ast.AST) -> tuple[str, ...]:
    if isinstance(target, ast.Name):
        return (target.id,)
    if isinstance(target, (ast.Tuple, ast.List)):
        return tuple(
            name
            for item in target.elts
            for name in _assigned_runtime_names(item)
        )
    return ()


def _expression_depends_on_response_index(
    expression: ast.AST,
    bound_names: set[str],
    *,
    include_environment_binding: bool = True,
) -> bool:
    for node in ast.walk(expression):
        if isinstance(node, ast.Name) and node.id in bound_names:
            return True
        if not include_environment_binding:
            continue
        if isinstance(node, ast.Subscript):
            key = node.slice
            if (
                isinstance(key, ast.Constant)
                and key.value == REPLAY_RESPONSE_INDEX_ENV
                and _is_os_environ_expression(node.value)
            ):
                return True
        if not isinstance(node, ast.Call):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        if node.args[0].value != REPLAY_RESPONSE_INDEX_ENV:
            continue
        if isinstance(node.func, ast.Attribute):
            if node.func.attr == "get" and _is_os_environ_expression(
                node.func.value
            ):
                return True
            if (
                node.func.attr == "getenv"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "os"
            ):
                return True
        if isinstance(node.func, ast.Name) and node.func.id == "getenv":
            return True
    return False


def _is_os_environ_expression(expression: ast.AST) -> bool:
    return (
        isinstance(expression, ast.Attribute)
        and isinstance(expression.value, ast.Name)
        and expression.value.id == "os"
        and expression.attr == "environ"
    ) or (isinstance(expression, ast.Name) and expression.id == "environ")


def _parse_manifest(raw: Mapping[str, Any]) -> ReplayCapabilityManifest:
    schema_version = _required_string(raw, "schema_version", "manifest")
    if schema_version != REPLAY_CAPABILITY_SCHEMA_VERSION:
        _raise_schema_field_error(
            f"unsupported replay capability schema: {schema_version}",
            (
                _schema_field_violation(
                    schema_layer="manifest",
                    field_path="schema_version",
                    rule="enum",
                    expected=(REPLAY_CAPABILITY_SCHEMA_VERSION,),
                    value=schema_version,
                ),
            ),
        )
    protocol = _required_string(raw, "protocol", "manifest")
    if protocol != REPLAY_CAPABILITY_PROTOCOL_VERSION:
        _raise_schema_field_error(
            f"unsupported replay capability protocol: {protocol}",
            (
                _schema_field_violation(
                    schema_layer="manifest",
                    field_path="protocol",
                    rule="enum",
                    expected=(REPLAY_CAPABILITY_PROTOCOL_VERSION,),
                    value=protocol,
                ),
            ),
        )
    capability_id = _required_identifier(raw, "capability_id", "manifest")
    entrypoint = _normalized_relative_path(
        _required_string(raw, "entrypoint", "manifest"),
        label="entrypoint",
    )
    handles = _string_tuple(raw.get("handles"), label="manifest handles")
    if not handles:
        raise ReplayCapabilityError("replay capability handles must not be empty")
    unsupported = sorted(set(handles) - _SUPPORTED_REQUIREMENT_KINDS)
    if unsupported:
        _raise_schema_field_error(
            f"unsupported replay capability requirement kinds: {unsupported}",
            tuple(
                _schema_field_violation(
                    schema_layer="manifest",
                    field_path="handles[*]",
                    rule="enum",
                    expected=REPLAY_CAPABILITY_SUPPORTED_REQUIREMENT_KINDS,
                    value=value,
                )
                for value in unsupported
            ),
        )
    runtime_files = tuple(
        _normalized_relative_path(item, label="runtime file")
        for item in _string_tuple(raw.get("runtime_files", ()), label="runtime_files")
    )
    if len(set(runtime_files)) != len(runtime_files):
        raise ReplayCapabilityError("replay capability runtime_files contain duplicates")
    concurrency_mode = str(raw.get("concurrency_mode") or "exclusive")
    resource_key = _optional_bounded_string(raw.get("resource_key"), "resource_key")
    binding_fingerprint = _optional_bounded_string(
        raw.get("binding_fingerprint"),
        "binding_fingerprint",
    )
    try:
        validated_binding = validate_replay_binding_concurrency(
            ReplayAdapterBinding(
                adapter_id=f"skill-replay:{capability_id}",
                dependency_id="manifest",
                deterministic=True,
                concurrency_mode=concurrency_mode,
                resource_key=(
                    resource_key
                    if resource_key is not None
                    else (
                        None
                        if concurrency_mode == "isolated"
                        else f"replay-capability:{capability_id}"
                    )
                ),
                binding_fingerprint=binding_fingerprint,
            )
        )
    except ValueError as exc:
        raise ReplayCapabilityError(str(exc)) from exc
    return ReplayCapabilityManifest(
        schema_version=schema_version,
        capability_id=capability_id,
        protocol=protocol,
        entrypoint=entrypoint,
        handles=tuple(sorted(set(handles))),
        runtime_files=runtime_files,
        concurrency_mode=validated_binding.concurrency_mode,
        resource_key=validated_binding.resource_key,
        binding_fingerprint=binding_fingerprint,
    )


def _parse_compile_result(
    result_path: Path,
    capability: DiscoveredReplayCapability,
    request: ReplayCapabilityCompileRequest,
    output_root: Path,
) -> ReplayCapabilityCompileResult:
    raw = _read_json_object(result_path, label="replay capability result")
    schema_version = _required_string(raw, "schema_version", "result")
    if schema_version != REPLAY_CAPABILITY_RESULT_SCHEMA_VERSION:
        _raise_schema_field_error(
            f"unsupported replay capability result schema: {schema_version}",
            (
                _schema_field_violation(
                    schema_layer="compile_result",
                    field_path="schema_version",
                    rule="enum",
                    expected=(REPLAY_CAPABILITY_RESULT_SCHEMA_VERSION,),
                    value=schema_version,
                ),
            ),
        )
    raw_capability_id = raw.get("capability_id")
    try:
        capability_id = _parse_identifier(
            raw_capability_id,
            label="result capability_id",
        )
    except ReplayCapabilityError:
        _raise_schema_field_error(
            (
                "replay capability result capability_id must match the "
                "manifest capability_id"
            ),
            (
                _schema_field_violation(
                    schema_layer="compile_result",
                    field_path="capability_id",
                    rule="enum",
                    expected=(capability.manifest.capability_id,),
                    value=raw_capability_id,
                ),
            ),
        )
    if capability_id != capability.manifest.capability_id:
        _raise_schema_field_error(
            (
                "replay capability result capability_id must match the "
                "manifest capability_id"
            ),
            (
                _schema_field_violation(
                    schema_layer="compile_result",
                    field_path="capability_id",
                    rule="enum",
                    expected=(capability.manifest.capability_id,),
                    value=capability_id,
                ),
            ),
        )
    deterministic = raw.get("deterministic")
    if not isinstance(deterministic, bool):
        _raise_schema_field_error(
            "result deterministic must be a boolean",
            (
                _schema_field_violation(
                    schema_layer="compile_result",
                    field_path="deterministic",
                    rule="type",
                    expected=("boolean",),
                    value=deterministic,
                ),
            ),
        )
    handled = _string_tuple(
        raw.get("handled_requirements", ()), label="handled_requirements"
    )
    unhandled = _string_tuple(
        raw.get("unhandled_requirements", ()), label="unhandled_requirements"
    )
    requirement_list_violations: list[SchemaFieldViolation] = []
    for field_path, values in (
        ("handled_requirements", handled),
        ("unhandled_requirements", unhandled),
    ):
        duplicate_count = len(values) - len(set(values))
        if duplicate_count <= 0:
            continue
        requirement_list_violations.append(
            _schema_field_violation(
                schema_layer="compile_result",
                field_path=field_path,
                rule="unique",
                expected=(),
                value=values,
                occurrence_count=duplicate_count,
            )
        )
    if requirement_list_violations:
        _raise_schema_field_error(
            "result requirement lists contain duplicates",
            requirement_list_violations,
            extra_details={
                "required_collection_strategy": (
                    "classify each request requirement exactly once and append "
                    "its id to only one result collection"
                ),
            },
        )
    if set(handled) & set(unhandled):
        raise ReplayCapabilityError("handled and unhandled requirements overlap")
    requirements = {item.requirement_id: item for item in request.requirements}
    reported = set(handled) | set(unhandled)
    if reported != set(requirements):
        raise ReplayCapabilityError(
            "result must classify every requested replay requirement exactly once"
        )
    for requirement_id in handled:
        if requirements[requirement_id].kind not in capability.manifest.handles:
            raise ReplayCapabilityError(
                f"capability cannot handle requirement kind: {requirements[requirement_id].kind}"
            )
    raw_evidence = raw.get("evidence_refs", {})
    if not isinstance(raw_evidence, dict):
        raise ReplayCapabilityError("result evidence_refs must be an object")
    evidence_refs: dict[str, tuple[str, ...]] = {}
    for requirement_id in handled:
        values = _string_tuple(
            raw_evidence.get(requirement_id),
            label=f"evidence_refs[{requirement_id}]",
        )
        if not values:
            raise ReplayCapabilityError(
                f"handled requirement lacks an evidence reference: {requirement_id}"
            )
        allowed = set(requirements[requirement_id].evidence_refs)
        if not set(values).issubset(allowed):
            raise ReplayCapabilityError(
                f"result contains an unrecorded evidence reference: {requirement_id}"
            )
        evidence_refs[requirement_id] = values
    unexpected_evidence = set(raw_evidence) - set(handled)
    if unexpected_evidence:
        raise ReplayCapabilityError(
            f"result evidence references unknown handled requirements: {unexpected_evidence}"
        )
    fixtures = tuple(
        _normalized_relative_path(item, label="fixture")
        for item in _string_tuple(raw.get("fixtures", ()), label="fixtures")
    )
    if len(set(fixtures)) != len(fixtures):
        _raise_schema_field_error(
            "result fixtures contain duplicates",
            (
                _schema_field_violation(
                    schema_layer="compile_result",
                    field_path="fixtures",
                    rule="unique",
                    expected=(),
                    value=fixtures,
                    occurrence_count=len(fixtures) - len(set(fixtures)),
                ),
            ),
            extra_details={
                "required_collection_strategy": (
                    "declare each relative fixture path once; when requirements "
                    "share evidence either allocate requirement-qualified paths or "
                    "reuse one fixture and merge its provenance bindings"
                ),
                "allowed_fixture_topologies": [
                    "unique_path_per_requirement",
                    "shared_path_with_merged_provenance",
                ],
            },
        )
    if len(fixtures) > _MAX_FIXTURE_COUNT:
        raise ReplayCapabilityError("result fixture count exceeds limit")
    fixture_total_bytes = 0
    for fixture in fixtures:
        fixture_path = _resolve_output_file(output_root, fixture)
        fixture_size = fixture_path.stat().st_size
        if fixture_size > _MAX_FIXTURE_FILE_BYTES:
            raise ReplayCapabilityError("result fixture exceeds byte limit")
        fixture_total_bytes += fixture_size
    if fixture_total_bytes > _MAX_FIXTURE_TOTAL_BYTES:
        raise ReplayCapabilityError("result fixture total exceeds byte limit")
    fixture_evidence_refs = _validate_fixture_provenance(
        raw.get("fixture_evidence_refs"),
        fixtures=fixtures,
        requirement_evidence_refs=evidence_refs,
        request=request,
        output_root=output_root,
    )
    _validate_declared_output_files(output_root, fixtures)
    services = _parse_services(
        raw.get("services", ()),
        output_root=output_root,
        fixtures=fixtures,
        runtime_files=capability.manifest.runtime_files,
        handled_requirements=set(handled),
        fixture_evidence_refs=fixture_evidence_refs,
        requirement_evidence_refs=evidence_refs,
    )
    for requirement_id in handled:
        requirement = requirements[requirement_id]
        if requirement.status != "runtime_required":
            continue
        requirement_services = [
            service
            for service in services
            if service.requirement_id == requirement_id
        ]
        if not any(
            service.transport == "skill_runtime"
            for service in requirement_services
        ):
            observed_transport = next(
                (
                    service.transport
                    for service in requirement_services
                    if service.transport
                ),
                None,
            )
            _raise_schema_field_error(
                "runtime_required requirement must use skill_runtime: "
                f"{requirement_id}",
                (
                    _schema_field_violation(
                        schema_layer="compile_result",
                        field_path=(
                            "services[*@request.requirement.status:"
                            "runtime_required].transport"
                        ),
                        rule="enum",
                        expected=("skill_runtime",),
                        value=observed_transport,
                        occurrence_count=max(1, len(requirement_services)),
                    ),
                ),
            )
    service_ids = {item.service_id for item in services}
    replacements_raw = raw.get("endpoint_replacements", {})
    if not isinstance(replacements_raw, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in replacements_raw.items()
    ):
        raise ReplayCapabilityError("result endpoint_replacements must map strings")
    handled_identifiers = {requirements[item].identifier for item in handled}
    replacements: dict[str, str] = {}
    for key, service_id in replacements_raw.items():
        identifier = requirements[key].identifier if key in handled else key
        if identifier not in handled_identifiers:
            raise ReplayCapabilityError(
                "endpoint replacement is not backed by a handled requirement"
            )
        previous = replacements.get(identifier)
        if previous is not None and previous != service_id:
            raise ReplayCapabilityError(
                "endpoint replacements conflict after requirement normalization"
            )
        replacements[identifier] = service_id
    network_requirements = {
        requirement_id: requirements[requirement_id]
        for requirement_id in handled
        if requirements[requirement_id].kind in {"http_resource", "local_endpoint"}
    }
    for requirement_id, requirement in network_requirements.items():
        if requirement.identifier in replacements:
            continue
        matching_services = [
            service.service_id
            for service in services
            if service.requirement_id == requirement_id
        ]
        if len(matching_services) != 1:
            raise ReplayCapabilityError(
                "handled network requirement lacks an unambiguous endpoint replacement"
            )
        replacements[requirement.identifier] = matching_services[0]
    if not set(replacements.values()).issubset(service_ids):
        raise ReplayCapabilityError(
            "endpoint replacement references an unknown replay service"
        )
    services_by_id = {item.service_id: item for item in services}
    for identifier, service_id in replacements.items():
        service = services_by_id[service_id]
        if requirements[service.requirement_id].identifier != identifier:
            raise ReplayCapabilityError(
                "endpoint replacement service is bound to a different requirement"
            )
    result_mode = str(
        raw.get("concurrency_mode") or capability.manifest.concurrency_mode
    )
    result_resource_key = _optional_bounded_string(
        raw.get("resource_key"),
        "result resource_key",
    )
    if "resource_key" not in raw:
        result_resource_key = capability.manifest.resource_key
    result_binding_fingerprint = _optional_bounded_string(
        raw.get("binding_fingerprint"),
        "result binding_fingerprint",
    )
    if result_binding_fingerprint is None:
        result_binding_fingerprint = capability.manifest.binding_fingerprint
    if result_binding_fingerprint is None:
        result_binding_fingerprint = _json_fingerprint(
            {
                "capability_package_fingerprint": capability.package_fingerprint,
                "request_fingerprint": request.request_fingerprint,
                "handled_requirements": list(handled),
            }
        )
    try:
        validated_binding = validate_replay_binding_concurrency(
            ReplayAdapterBinding(
                adapter_id=f"skill-replay:{capability_id}",
                dependency_id="compile-result",
                deterministic=deterministic,
                concurrency_mode=result_mode,
                resource_key=result_resource_key,
                binding_fingerprint=result_binding_fingerprint,
            )
        )
    except ValueError as exc:
        raise ReplayCapabilityError(str(exc)) from exc
    return ReplayCapabilityCompileResult(
        schema_version=schema_version,
        capability_id=capability_id,
        deterministic=deterministic,
        handled_requirements=tuple(handled),
        unhandled_requirements=tuple(unhandled),
        evidence_refs=evidence_refs,
        fixture_evidence_refs=fixture_evidence_refs,
        fixtures=fixtures,
        endpoint_replacements=dict(sorted(replacements.items())),
        services=services,
        concurrency_mode=validated_binding.concurrency_mode,
        resource_key=validated_binding.resource_key,
        binding_fingerprint=validated_binding.binding_fingerprint,
    )


def _validate_fixture_provenance(
    raw: Any,
    *,
    fixtures: Sequence[str],
    requirement_evidence_refs: Mapping[str, tuple[str, ...]],
    request: ReplayCapabilityCompileRequest,
    output_root: Path,
) -> dict[str, tuple[str, ...]]:
    if not isinstance(raw, dict):
        _raise_schema_field_error(
            "result fixture_evidence_refs must be an object",
            (
                _schema_field_violation(
                    schema_layer="compile_result",
                    field_path="fixture_evidence_refs",
                    rule="type",
                    expected=("object",),
                    value=raw,
                ),
            ),
        )
    if set(raw) != set(fixtures):
        _raise_schema_field_error(
            "result must provide evidence provenance for every fixture exactly once",
            (
                _schema_field_violation(
                    schema_layer="compile_result",
                    field_path="fixtures[*].evidence_refs",
                    rule="required",
                    expected=(),
                    value=raw,
                    occurrence_count=max(1, len(set(fixtures) ^ set(raw))),
                ),
            ),
        )
    allowed_refs = {
        ref
        for refs in requirement_evidence_refs.values()
        for ref in refs
    }
    validated: dict[str, tuple[str, ...]] = {}
    for fixture in fixtures:
        refs = _string_tuple(
            raw.get(fixture),
            label=f"fixture_evidence_refs[{fixture}]",
        )
        if not refs or not set(refs).issubset(allowed_refs):
            raise ReplayCapabilityError(
                f"fixture provenance is not backed by handled evidence: {fixture}"
            )
        fixture_bytes = _resolve_output_file(output_root, fixture).read_bytes()
        source_values: set[bytes] = set()
        for evidence_ref in refs:
            source_values.update(
                _evidence_source_values(evidence_ref, request=request)
            )
        if fixture_bytes not in source_values:
            raise ReplayCapabilityError(
                f"fixture bytes are not directly derived from recorded evidence: {fixture}"
            )
        validated[fixture] = refs
    return dict(sorted(validated.items()))


def _evidence_source_values(
    evidence_ref: str,
    *,
    request: ReplayCapabilityCompileRequest,
) -> set[bytes]:
    if evidence_ref.startswith("context:"):
        case_id = next(
            (
                item
                for item in sorted(
                    request.context_snapshots,
                    key=len,
                    reverse=True,
                )
                if evidence_ref.startswith(f"context:{item}:")
            ),
            None,
        )
        if case_id is None:
            raise ReplayCapabilityError("invalid context evidence reference")
        expected_fingerprint = evidence_ref[len(f"context:{case_id}:") :]
        snapshot_value = request.context_snapshots.get(case_id)
        if snapshot_value is None:
            raise ReplayCapabilityError(
                f"fixture evidence context is unavailable: {case_id}"
            )
        snapshot_path = Path(snapshot_value).expanduser().resolve()
        if not snapshot_path.is_file() or snapshot_path.is_symlink():
            raise ReplayCapabilityError(
                f"fixture evidence context is unavailable: {case_id}"
            )
        if snapshot_path.stat().st_size > 16 * 1024 * 1024:
            raise ReplayCapabilityError("fixture evidence context exceeds byte limit")
        try:
            payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReplayCapabilityError("fixture evidence context is invalid") from exc
        if not isinstance(payload, dict) or payload.get("fingerprint") != expected_fingerprint:
            raise ReplayCapabilityError("fixture evidence context fingerprint mismatch")
        fingerprint_payload = dict(payload)
        fingerprint_payload.pop("fingerprint", None)
        if _json_fingerprint(fingerprint_payload) != expected_fingerprint:
            raise ReplayCapabilityError(
                "fixture evidence context fingerprint verification failed"
            )
        source_values: set[bytes] = set()
        for key in ("task_input", "steps", "prior_turns"):
            if key in payload:
                source_values.update(_payload_derivations(payload[key]))
        return source_values
    if evidence_ref.startswith("case:") and evidence_ref.endswith(":input"):
        case_id = evidence_ref[len("case:") : -len(":input")]
        if case_id not in request.task_inputs:
            raise ReplayCapabilityError(f"fixture evidence input is unavailable: {case_id}")
        return _payload_derivations(request.task_inputs[case_id])
    raise ReplayCapabilityError(f"unsupported fixture evidence reference: {evidence_ref}")


def _payload_derivations(value: Any) -> set[bytes]:
    values = {
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    }
    if isinstance(value, str):
        values.add(value.encode("utf-8"))
    elif isinstance(value, Mapping):
        for item in value.values():
            values.update(_payload_derivations(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            values.update(_payload_derivations(item))
    return values


def materialize_replay_evidence_derivations(
    request: ReplayCapabilityCompileRequest,
    output_root: str | Path,
    *,
    max_entries_per_ref: int = 16,
    max_entry_bytes: int = 1024 * 1024,
) -> dict[str, tuple[Mapping[str, Any], ...]]:
    """Materialize bounded, provenance-safe source bytes for skill compilers.

    Every emitted file is one value already accepted by the framework's fixture
    provenance validator. Candidate-owned code may select and copy these files, but
    does not need to understand the trajectory snapshot's internal JSON shape.
    """

    if max_entries_per_ref <= 0 or max_entry_bytes <= 0:
        raise ValueError("evidence derivation limits must be positive")
    root = Path(output_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    identifiers_by_ref: dict[str, set[str]] = {}
    for requirement in request.requirements:
        for evidence_ref in requirement.evidence_refs:
            identifiers_by_ref.setdefault(evidence_ref, set()).add(
                requirement.identifier
            )

    catalog: dict[str, tuple[Mapping[str, Any], ...]] = {}
    for evidence_ref in sorted(identifiers_by_ref):
        identifiers = sorted(identifiers_by_ref[evidence_ref])
        values = [
            value
            for value in _evidence_source_values(evidence_ref, request=request)
            if value and len(value) <= max_entry_bytes
        ]
        ranked = sorted(
            set(values),
            key=lambda value: (
                any(identifier.encode("utf-8") in value for identifier in identifiers),
                value.lstrip().startswith((b"{", b"[", b"<")),
                len(value),
                hashlib.sha256(value).hexdigest(),
            ),
            reverse=True,
        )[:max_entries_per_ref]
        entries: list[Mapping[str, Any]] = []
        for value in ranked:
            digest = hashlib.sha256(value).hexdigest()
            path = root / f"{digest}.bin"
            if not path.exists():
                path.write_bytes(value)
                path.chmod(0o444)
            response_index = _build_recorded_response_index(value)
            response_index_path: Path | None = None
            if response_index["records"]:
                response_index_path = root / f"{digest}.responses.json"
                if not response_index_path.exists():
                    response_index_path.write_text(
                        json.dumps(response_index, ensure_ascii=False, indent=2)
                        + "\n",
                        encoding="utf-8",
                    )
            preview = value[:160].decode("utf-8", errors="replace")
            entry: dict[str, Any] = {
                "path": str(path),
                "sha256": f"sha256:{digest}",
                "byte_length": len(value),
                "preview": preview,
                "matching_identifiers": [
                    identifier
                    for identifier in identifiers
                    if identifier.encode("utf-8") in value
                ],
            }
            if response_index_path is not None:
                entry["response_index_path"] = str(response_index_path)
                entry["response_record_count"] = len(response_index["records"])
                entry["response_operations"] = response_index["operations"]
            entries.append(entry)
        if entries:
            catalog[evidence_ref] = tuple(entries)
    return catalog


_FIXTURE_GATEWAY_KEYS = frozenset({"action_result", "tool_outputs"})
_FIXTURE_PAYLOAD_KEYS = frozenset(
    {"body", "content", "data", "output", "outputs", "response", "responses", "result", "results"}
)
_FIXTURE_OPERATION_KEYS = frozenset(
    {"action_name", "command", "method", "name", "operation", "path", "route", "tool_name"}
)


def _build_recorded_response_index(
    raw: bytes,
    *,
    max_records: int = 128,
    max_nodes: int = 100_000,
    observed_operations: Sequence[str] = (),
) -> dict[str, Any]:
    """Build a bounded, generic operation-to-response index for skill compilers.

    The index is intentionally protocol-neutral: it records where an arbitrary
    nested trajectory gateway contains a response payload, its operation hint,
    and the decoded response shape.  Payload bytes remain in the provenance
    fixture; a skill-owned compiler can correlate the index with that fixture and
    choose the protocol-specific projection.  This prevents compilers from
    treating the first scalar in an outer envelope as the task response.
    """

    roots: list[Any] = []
    try:
        roots.append(json.loads(raw.decode("utf-8", errors="replace")))
    except (UnicodeDecodeError, json.JSONDecodeError):
        for line in raw.decode("utf-8", errors="replace").splitlines()[:4096]:
            if not line.strip():
                continue
            try:
                roots.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    records: list[dict[str, Any]] = []
    operations: list[str] = []
    visited = 0
    pending: list[tuple[Any, tuple[str, ...], str | None, int]] = [
        (root, (), None, 0) for root in reversed(roots[:4096])
    ]
    while pending and visited < max_nodes and len(records) < max_records:
        node, path, inherited_operation, decoded_depth = pending.pop()
        visited += 1
        if isinstance(node, str):
            stripped = node.strip()
            if (
                decoded_depth < 4
                and stripped[:1] in {"{", "["}
                and len(stripped) <= 2 * 1024 * 1024
            ):
                try:
                    decoded = json.loads(stripped)
                except json.JSONDecodeError:
                    decoded = None
                if isinstance(decoded, (Mapping, list)):
                    pending.append((decoded, path, inherited_operation, decoded_depth + 1))
            continue
        if isinstance(node, Mapping):
            operation = _fixture_operation_hint(node) or inherited_operation
            for key, value in reversed(list(node.items())[:4096]):
                key_name = str(key).strip().casefold()
                child_path = (*path, str(key))
                if key_name in _FIXTURE_GATEWAY_KEYS:
                    payloads = _fixture_payload_nodes(value)
                    if not payloads:
                        payloads = [(value, child_path)]
                    for payload, payload_path in payloads:
                        shape = _fixture_json_shape(payload)
                        record = {
                            "ordinal": len(records),
                            "gateway_key": key_name,
                            "operation": _fixture_operation_hint(value) or operation,
                            "payload_path": ".".join(payload_path),
                            "shape": shape,
                            "non_empty": _fixture_non_empty(payload),
                        }
                        # Preserve a bounded decoded payload so a skill-owned
                        # adapter can return the recorded value, rather than
                        # only a path/shape descriptor.
                        if isinstance(payload, (Mapping, list, str)):
                            encoded_payload = json.dumps(
                                payload,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ).encode("utf-8")
                            if len(encoded_payload) <= 64 * 1024:
                                record["value"] = payload
                        # Tool output often embeds a JSON file behind line
                        # prefixes (for example ``12→{...}``) or markdown.
                        # Keep bounded decoded composites as first-class
                        # records so a skill runtime can return the actual
                        # response shape instead of the surrounding log text.
                        if isinstance(payload, str):
                            for embedded_index, embedded in enumerate(
                                _embedded_json_values(payload)
                            ):
                                encoded = json.dumps(
                                    embedded,
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                ).encode("utf-8")
                                if len(encoded) > 64 * 1024:
                                    continue
                                records.append(
                                    {
                                        "ordinal": len(records),
                                        "gateway_key": key_name,
                                        "operation": record.get("operation"),
                                        "payload_path": (
                                            f"{'.'.join(payload_path)}"
                                            f"#embedded{embedded_index}"
                                        ),
                                        "shape": _fixture_json_shape(embedded),
                                        "non_empty": _fixture_non_empty(embedded),
                                        "value": embedded,
                                    }
                                )
                        records.append(record)
                        hint = record.get("operation")
                        if isinstance(hint, str) and hint and hint not in operations:
                            operations.append(hint)
                    pending.append((value, child_path, operation, decoded_depth))
                    continue
                pending.append((value, child_path, operation, decoded_depth))
            continue
        if isinstance(node, list):
            pending.extend(
                (item, (*path, str(index)), inherited_operation, decoded_depth)
                for index, item in reversed(list(enumerate(node[:4096])))
            )
    # This sidecar is a runtime selection index, not a raw event log. Preserve
    # the source ordinal, but put bounded, probeable values first so the public
    # "first non_empty record" rule agrees with the framework's strict probe.
    # Otherwise a truncated JSON-looking wrapper can precede a later decoded
    # composite: the runtime selects the wrapper while preflight silently
    # derives expectations from the composite.
    for record in records:
        assertion_count, transport_ready = _runtime_response_record_evidence(
            record.get("value")
        )
        record["protocol_eligible"] = (
            record.get("non_empty") is True and assertion_count > 0
        )
        record["transport_ready"] = (
            record["protocol_eligible"] and transport_ready
        )
        record["semantic_payload_score"] = _runtime_response_semantic_score(
            record.get("value")
        )
    records.sort(
        key=lambda record: (
            not bool(record.get("protocol_eligible")),
            not bool(record.get("transport_ready")),
            -int(record.get("semantic_payload_score") or 0),
            int(record.get("ordinal") or 0),
        )
    )
    for record in records:
        record["record_id"] = _recorded_response_record_id(record)
        canonical_assertion = _canonical_recorded_response_assertion(
            record.get("value")
        )
        if canonical_assertion is not None:
            record["canonical_probe_assertion"] = canonical_assertion
            record["selector_policy"] = REPLAY_RESPONSE_SELECTOR_POLICY
    # A trajectory may label the producer operation (for example
    # ``read_file``), while a skill-owned adapter receives a protocol method
    # (for example ``Runtime.evaluate``).  Alias each declared probe operation
    # to an actual non-empty fixture value so the adapter can correlate the
    # request without inventing a placeholder response.
    for operation in observed_operations:
        normalized_operation = str(operation).strip()
        if not normalized_operation or normalized_operation in operations:
            continue
        source = next(
            (
                record
                for record in records
                if record.get("non_empty") is True
                and "value" in record
                and _fixture_non_empty(record.get("value"))
            ),
            None,
        )
        if source is None:
            continue
        alias = dict(source)
        alias.pop("record_id", None)
        alias["ordinal"] = len(records)
        alias["operation"] = normalized_operation
        alias["derived_operation"] = True
        alias["source_ordinal"] = source.get("ordinal")
        alias["source_record_id"] = source.get("record_id")
        alias["payload_path"] = (
            f"{source.get('payload_path', '')}#derived:{normalized_operation}"
        )
        alias["record_id"] = _recorded_response_record_id(alias)
        records.append(alias)
        operations.append(normalized_operation)
    return {
        "schema_version": "aworld.self_evolve.recorded_response_index.v1",
        "visited_nodes": visited,
        "operations": operations[:64],
        "records": records,
    }


def _recorded_response_record_id(record: Mapping[str, Any]) -> str:
    """Return a stable identity for one immutable response-index record."""

    value = record.get("value")
    try:
        value_fingerprint = _json_fingerprint(value)
    except (TypeError, ValueError):
        value_fingerprint = _json_fingerprint(str(value))
    payload = {
        "gateway_key": record.get("gateway_key"),
        "operation": record.get("operation"),
        "payload_path": record.get("payload_path"),
        "source_ordinal": record.get("source_ordinal", record.get("ordinal")),
        "value_fingerprint": value_fingerprint,
    }
    return "response-record-" + _json_fingerprint(payload).removeprefix("sha256:")


def _canonical_recorded_response_assertion(
    value: Any,
    *,
    max_chars: int = REPLAY_CAPABILITY_MAX_RESPONSE_CONTAINS_CHARS,
) -> str | None:
    """Select one exact bounded scalar from a recorded response value.

    The selector never truncates.  Compiler declarations, frozen response
    indexes, conformance, and runtime probes can therefore share one identity
    and one exact assertion instead of independently guessing a fixture leaf.
    """

    candidates: set[str] = set()
    pending: list[tuple[Any, int]] = [(value, 0)]
    visited = 0
    while pending and visited < 4096:
        current, decoded_depth = pending.pop()
        visited += 1
        if isinstance(current, Mapping):
            pending.extend(
                (nested, decoded_depth)
                for nested in reversed(list(current.values())[:512])
            )
            continue
        if isinstance(current, (list, tuple)):
            pending.extend(
                (nested, decoded_depth)
                for nested in reversed(list(current)[:512])
            )
            continue
        if isinstance(current, bool) or current is None:
            continue
        if isinstance(current, str):
            normalized = current.strip()
            if (
                decoded_depth < 4
                and normalized[:1] in {"{", "["}
                and len(normalized) <= 64 * 1024
            ):
                try:
                    decoded = json.loads(normalized)
                except json.JSONDecodeError:
                    decoded = None
                if isinstance(decoded, (Mapping, list)):
                    pending.append((decoded, decoded_depth + 1))
            if normalized and len(normalized) <= max_chars:
                candidates.add(normalized)
            continue
        if isinstance(current, (int, float)):
            encoded = json.dumps(current, ensure_ascii=False)
            if len(encoded) <= max_chars:
                candidates.add(encoded)
    if not candidates:
        return None
    # Prefer a discriminative recorded value while retaining deterministic
    # ordering for equal-sized values.  The digest avoids value-dependent
    # lexical preferences and is never exposed in public diagnostics.
    return max(
        candidates,
        key=lambda item: (
            len(item),
            hashlib.sha256(item.encode("utf-8")).hexdigest(),
        ),
    )


def _probe_operation(probe: ReplayProtocolProbe) -> str | None:
    request_text = probe.request_text
    if not isinstance(request_text, str) or not request_text.strip():
        return None
    try:
        request = json.loads(request_text)
    except (TypeError, json.JSONDecodeError):
        return None
    return _fixture_operation_hint(request)


def _framework_bind_protocol_probes(
    probes: Sequence[ReplayProtocolProbe],
    *,
    fixture_bytes: bytes,
) -> tuple[ReplayProtocolProbe, ...]:
    """Bind data-plane probes to immutable framework response records."""

    observed_operations = tuple(
        dict.fromkeys(
            operation
            for probe in probes
            if (operation := _probe_operation(probe)) is not None
        )
    )
    response_index = _build_recorded_response_index(
        fixture_bytes,
        observed_operations=observed_operations,
    )
    records = tuple(
        record
        for record in response_index.get("records", ())
        if isinstance(record, Mapping)
        and record.get("non_empty") is True
        and isinstance(record.get("record_id"), str)
        and isinstance(record.get("canonical_probe_assertion"), str)
    )
    if not records:
        return tuple(probes)
    bound: list[ReplayProtocolProbe] = []
    for probe in probes:
        if (
            probe.response_contains is None
            or (probe.kind == "http" and probe.validate_advertised_websockets)
        ):
            bound.append(probe)
            continue
        operation = _probe_operation(probe)
        record = next(
            (
                item
                for item in records
                if operation is not None and item.get("operation") == operation
            ),
            records[0],
        )
        bound.append(
            replace(
                probe,
                response_contains=str(record["canonical_probe_assertion"]),
                response_record_id=str(record["record_id"]),
            )
        )
    return tuple(bound)


def _runtime_response_record_evidence(value: Any) -> tuple[int, bool]:
    """Return bounded assertion count and direct-transport eligibility.

    This mirrors the generic replay probe's value rules without persisting
    assertion text. JSON-looking strings are recursively decoded, so an
    oversized or truncated wrapper is not preferred over a complete record.
    """

    selected: set[str] = set()
    pending: list[tuple[Any, int]] = [(value, 0)]
    visited = 0
    while pending and visited < 4096 and len(selected) < 2:
        current, decoded_depth = pending.pop()
        visited += 1
        if isinstance(current, Mapping):
            encoded = json.dumps(
                current,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).strip()
            if encoded and len(encoded) <= 4096:
                selected.add(encoded)
            pending.extend(
                (nested, decoded_depth)
                for nested in reversed(list(current.values())[:512])
            )
            continue
        if isinstance(current, (list, tuple)):
            encoded = json.dumps(
                current,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).strip()
            if encoded and len(encoded) <= 4096:
                selected.add(encoded)
            pending.extend(
                (nested, decoded_depth)
                for nested in reversed(list(current)[:512])
            )
            continue
        if isinstance(current, str):
            stripped = current.strip()
            if (
                decoded_depth < 4
                and stripped[:1] in {"{", "["}
                and len(stripped) <= 64 * 1024
            ):
                try:
                    decoded = json.loads(stripped)
                except json.JSONDecodeError:
                    decoded = None
                if isinstance(decoded, (Mapping, list)):
                    pending.append((decoded, decoded_depth + 1))
                    continue
            if stripped and len(stripped) <= 4096:
                selected.add(stripped)
        elif isinstance(current, (int, float)) and not isinstance(current, bool):
            selected.add(json.dumps(current, ensure_ascii=False))

    try:
        encoded_bytes = len(
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    except (TypeError, ValueError):
        encoded_bytes = 48 * 1024 + 1
    return len(selected), encoded_bytes <= 48 * 1024


def _runtime_response_semantic_score(value: Any) -> int:
    """Rank bounded records by task-bearing payload richness.

    Transport readiness alone cannot distinguish an execution-status envelope
    from the actual bounded content captured later in the same operation.  Use
    only generic structural signals so response indexes prefer richer payloads
    without depending on a task, endpoint, field name, or fixture value.
    """

    pending: list[Any] = [value]
    visited = 0
    scalar_count = 0
    container_count = 0
    text_chars = 0
    while pending and visited < 4096:
        current = pending.pop()
        visited += 1
        if isinstance(current, Mapping):
            container_count += 1
            pending.extend(reversed(list(current.values())[:512]))
        elif isinstance(current, (list, tuple)):
            container_count += 1
            pending.extend(reversed(list(current)[:512]))
        elif isinstance(current, str):
            stripped = current.strip()
            if stripped:
                scalar_count += 1
                text_chars += min(len(stripped), 32 * 1024)
        elif isinstance(current, (int, float)) and not isinstance(current, bool):
            scalar_count += 1
            text_chars += len(str(current))

    return (
        min(text_chars, 64 * 1024)
        + min(scalar_count, 256) * 32
        + min(container_count, 256) * 8
    )


def _declared_probe_operations(
    services: Sequence[ReplayServiceSpec],
) -> tuple[str, ...]:
    """Extract protocol operation names from declared probe request payloads."""

    operations: list[str] = []
    for service in services:
        for probe in service.protocol_probes:
            request_text = probe.request_text
            if not isinstance(request_text, str) or not request_text.strip():
                continue
            try:
                request = json.loads(request_text)
            except (TypeError, json.JSONDecodeError):
                continue
            operation = _fixture_operation_hint(request)
            if isinstance(operation, str) and operation and operation not in operations:
                operations.append(operation)
    return tuple(operations)


def _fixture_operation_hint(value: Any) -> str | None:
    pending: list[tuple[Any, int]] = [(value, 0)]
    visited = 0
    while pending and visited < 256:
        current, depth = pending.pop()
        visited += 1
        if depth > 8:
            continue
        if isinstance(current, Mapping):
            for key, nested in current.items():
                if str(key).strip().casefold() in _FIXTURE_OPERATION_KEYS:
                    if isinstance(nested, str) and nested.strip():
                        return sanitize_text(nested.strip(), max_chars=160)
                elif isinstance(nested, (Mapping, list)):
                    pending.append((nested, depth + 1))
        elif isinstance(current, list):
            pending.extend((nested, depth + 1) for nested in reversed(current[:256]))
    return None


def _fixture_payload_nodes(value: Any) -> list[tuple[Any, tuple[str, ...]]]:
    found: list[tuple[Any, tuple[str, ...]]] = []
    pending: list[tuple[Any, tuple[str, ...], int]] = [(value, (), 0)]
    while pending and len(found) < 128:
        node, path, depth = pending.pop()
        if depth > 32:
            continue
        if isinstance(node, Mapping):
            for key, nested in reversed(list(node.items())[:4096]):
                child_path = (*path, str(key))
                if str(key).strip().casefold() in _FIXTURE_PAYLOAD_KEYS:
                    found.append((nested, child_path))
                else:
                    pending.append((nested, child_path, depth + 1))
        elif isinstance(node, list):
            pending.extend(
                (nested, (*path, str(index)), depth + 1)
                for index, nested in reversed(list(enumerate(node[:4096])))
            )
        elif isinstance(node, str):
            stripped = node.strip()
            if depth < 4 and stripped[:1] in {"{", "["}:
                try:
                    decoded = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(decoded, (Mapping, list)):
                    pending.append((decoded, path, depth + 1))
    return found


def _embedded_json_values(
    value: str,
    *,
    max_values: int = 8,
    _depth: int = 0,
) -> list[Any]:
    """Decode bounded JSON composites embedded in tool/file output text."""

    text = value.strip()
    if not text:
        return []
    candidates = [text]
    # ``read_file`` output prefixes each line with ``N→``.  Remove only that
    # presentation layer; the underlying JSON remains fixture-derived.
    normalized_lines = [
        re.sub(r"^\s*\d+→\s?", "", line)
        for line in text.splitlines()
    ]
    normalized = "\n".join(normalized_lines).strip()
    if normalized != text:
        candidates.append(normalized)
    decoder = json.JSONDecoder()
    values: list[Any] = []
    seen: set[str] = set()
    for candidate in candidates:
        for index, char in enumerate(candidate):
            if char not in "[{":
                continue
            try:
                parsed, _ = decoder.raw_decode(candidate[index:])
            except json.JSONDecodeError:
                continue
            if not isinstance(parsed, (Mapping, list)):
                continue
            fingerprint = json.dumps(
                parsed,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            values.append(parsed)
            if _depth < 2:
                nested_strings: list[str] = []
                pending: list[Any] = [parsed]
                while pending:
                    nested = pending.pop()
                    if isinstance(nested, str):
                        nested_strings.append(nested)
                    elif isinstance(nested, Mapping):
                        pending.extend(nested.values())
                    elif isinstance(nested, list):
                        pending.extend(nested)
                for nested_text in nested_strings:
                    if len(values) >= max_values:
                        break
                    for nested_value in _embedded_json_values(
                        nested_text,
                        max_values=max_values - len(values),
                        _depth=_depth + 1,
                    ):
                        nested_fingerprint = json.dumps(
                            nested_value,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        if nested_fingerprint not in seen:
                            seen.add(nested_fingerprint)
                            values.append(nested_value)
            if len(values) >= max_values:
                return values
    return values


def _fixture_json_shape(value: Any) -> str:
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, bool):
        return "boolean"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return "number"
    return "unknown"


def _fixture_non_empty(value: Any) -> bool:
    if isinstance(value, (Mapping, list, tuple, str)):
        return bool(value)
    return value is not None


def _validate_compile_result_service_schema(raw: Any) -> None:
    """Validate shape-complete service fields before semantic compilation."""

    if not isinstance(raw, list):
        _raise_schema_field_error(
            "result services must be an array",
            (
                _schema_field_violation(
                    schema_layer="compile_result",
                    field_path="services",
                    rule="type",
                    expected=("array",),
                    value=raw,
                ),
            ),
        )
    violations: list[SchemaFieldViolation] = []
    first_invalid_transport: object | None = None
    semantic_failure_codes: set[str] = set()
    for service in raw:
        if not isinstance(service, Mapping):
            violations.append(
                _schema_field_violation(
                    schema_layer="compile_result",
                    field_path="services[*]",
                    rule="type",
                    expected=("object",),
                    value=service,
                )
            )
            continue
        if "transport" not in service:
            violations.append(
                _schema_field_violation(
                    schema_layer="compile_result",
                    field_path="services[*].transport",
                    rule="required",
                    expected=REPLAY_CAPABILITY_SUPPORTED_SERVICE_TRANSPORTS,
                    value=None,
                )
            )
        elif service.get("transport") not in _SUPPORTED_SERVICE_TRANSPORTS:
            transport = service.get("transport")
            if first_invalid_transport is None:
                first_invalid_transport = transport
            violations.append(
                _schema_field_violation(
                    schema_layer="compile_result",
                    field_path="services[*].transport",
                    rule="enum",
                    expected=REPLAY_CAPABILITY_SUPPORTED_SERVICE_TRANSPORTS,
                    value=transport,
                )
            )
        readiness = service.get("readiness", {})
        if not isinstance(readiness, Mapping):
            violations.append(
                _schema_field_violation(
                    schema_layer="compile_result",
                    field_path="services[*].readiness",
                    rule="type",
                    expected=("object",),
                    value=readiness,
                )
            )
        elif readiness.get("kind") not in _SUPPORTED_READINESS_KINDS:
            violations.append(
                _schema_field_violation(
                    schema_layer="compile_result",
                    field_path="services[*].readiness.kind",
                    rule=(
                        "required" if "kind" not in readiness else "enum"
                    ),
                    expected=REPLAY_CAPABILITY_SUPPORTED_READINESS_KINDS,
                    value=readiness.get("kind"),
                )
            )
        elif (
            "path" in readiness
            and (
                not isinstance(readiness.get("path"), str)
                or not str(readiness.get("path")).startswith("/")
            )
        ):
            violations.append(
                _absolute_path_violation(
                    field_path="services[*].readiness.path",
                    value=readiness.get("path"),
                )
            )
            semantic_failure_codes.add("readiness_path_invalid")
        raw_probes = service.get("protocol_probes")
        if raw_probes is None:
            continue
        if not isinstance(raw_probes, list):
            violations.append(
                _schema_field_violation(
                    schema_layer="compile_result",
                    field_path="services[*].protocol_probes",
                    rule="type",
                    expected=("array",),
                    value=raw_probes,
                )
            )
            continue
        for probe in raw_probes:
            if not isinstance(probe, Mapping):
                violations.append(
                    _schema_field_violation(
                        schema_layer="compile_result",
                        field_path="services[*].protocol_probes[*]",
                        rule="type",
                        expected=("object",),
                        value=probe,
                    )
                )
                continue
            if probe.get("kind") not in _SUPPORTED_PROTOCOL_PROBE_KINDS:
                violations.append(
                    _schema_field_violation(
                        schema_layer="compile_result",
                        field_path="services[*].protocol_probes[*].kind",
                        rule=("required" if "kind" not in probe else "enum"),
                        expected=(
                            ()
                            if "kind" not in probe
                            else REPLAY_CAPABILITY_SUPPORTED_PROTOCOL_PROBE_KINDS
                        ),
                        value=probe.get("kind"),
                    )
                )
            probe_path = probe.get("path", "/")
            if not isinstance(probe_path, str) or not probe_path.startswith("/"):
                violations.append(
                    _absolute_path_violation(
                        field_path="services[*].protocol_probes[*].path",
                        value=probe_path,
                    )
                )
                semantic_failure_codes.add("protocol_probe_path_invalid")
            validate_advertised = probe.get(
                "validate_advertised_websockets",
                False,
            )
            if not isinstance(validate_advertised, bool):
                violations.append(
                    _schema_field_violation(
                        schema_layer="compile_result",
                        field_path=(
                            "services[*].protocol_probes[*]."
                            "validate_advertised_websockets"
                        ),
                        rule="type",
                        expected=("boolean",),
                        value=validate_advertised,
                    )
                )
            for field_name, max_chars in (
                ("request_text", 16_384),
                (
                    "response_contains",
                    REPLAY_CAPABILITY_MAX_RESPONSE_CONTAINS_CHARS,
                ),
            ):
                if field_name not in probe or probe.get(field_name) is None:
                    continue
                field_value = probe.get(field_name)
                field_path = (
                    "services[*].protocol_probes[*]." + field_name
                )
                if not isinstance(field_value, str):
                    violations.append(
                        _schema_field_violation(
                            schema_layer="compile_result",
                            field_path=field_path,
                            rule="type",
                            expected=("string",),
                            value=field_value,
                        )
                    )
                elif not field_value:
                    violations.append(
                        _schema_field_violation(
                            schema_layer="compile_result",
                            field_path=field_path,
                            rule="non_empty",
                            expected=(),
                            value=field_value,
                        )
                    )
                elif len(field_value) > max_chars:
                    violations.append(
                        _schema_field_violation(
                            schema_layer="compile_result",
                            field_path=field_path,
                            rule="max_chars",
                            expected=(str(max_chars),),
                            value=field_value,
                        )
                    )
            if probe.get("kind") in {"tcp", "websocket"}:
                for required_field in ("request_text", "response_contains"):
                    if probe.get(required_field) is None:
                        violations.append(
                            _schema_field_violation(
                                schema_layer="compile_result",
                                field_path=(
                                    "services[*].protocol_probes[*]."
                                    + required_field
                                ),
                                rule="required",
                                expected=(),
                                value=None,
                            )
                        )
        if service.get("transport") != "skill_runtime":
            continue
        probe_mappings = tuple(
            probe for probe in raw_probes if isinstance(probe, Mapping)
        )
        if isinstance(readiness, Mapping):
            readiness_kind = readiness.get("kind")
            readiness_path = readiness.get("path", "/")
            duplicates = tuple(
                probe
                for probe in probe_mappings
                if probe.get("kind") == readiness_kind
                and probe.get("path", "/") == readiness_path
            )
            if duplicates:
                violations.append(
                    _readiness_duplicate_violation(
                        readiness_path,
                        occurrence_count=len(duplicates),
                    )
                )
                semantic_failure_codes.add(
                    "protocol_probe_duplicates_readiness"
                )
        advertises_websocket = any(
            probe.get("kind") == "http"
            and probe.get("validate_advertised_websockets") is True
            for probe in probe_mappings
        )
        has_websocket_data_plane = any(
            probe.get("kind") == "websocket"
            and bool(probe.get("request_text"))
            and bool(probe.get("response_contains"))
            for probe in probe_mappings
        )
        if advertises_websocket and not has_websocket_data_plane:
            violations.append(_advertised_websocket_probe_violation())
            semantic_failure_codes.add("advertised_websocket_probe_missing")
    if not violations:
        return
    message = "replay capability result violates schema field constraints"
    if first_invalid_transport is not None:
        message = (
            "unsupported replay service transport: "
            + sanitize_text(first_invalid_transport, max_chars=120)
        )
    elif any(
        violation.constraint.field_path.endswith(".response_contains")
        and violation.constraint.rule in {"max_chars", "non_empty", "type"}
        for violation in violations
    ):
        message = (
            "protocol probe response_contains must be non-empty and at most "
            f"{REPLAY_CAPABILITY_MAX_RESPONSE_CONTAINS_CHARS} characters"
        )
    if len(semantic_failure_codes) == 1:
        semantic_code = next(iter(semantic_failure_codes))
        if semantic_code == "advertised_websocket_probe_missing":
            message = (
                "advertised WebSocket requires a websocket data-plane "
                "protocol probe"
            )
        elif semantic_code == "protocol_probe_duplicates_readiness":
            message = "protocol_probes must not duplicate readiness probes"
        elif semantic_code == "protocol_probe_path_invalid":
            message = "protocol probe path must start with /"
        elif semantic_code == "readiness_path_invalid":
            message = "HTTP readiness path must start with /"
    else:
        semantic_code = "compile_result_constraints_failed"
    _raise_schema_field_error(
        message,
        tuple(violations),
        extra_details=(
            {
                "code": semantic_code,
                "constraint_failure_codes": sorted(semantic_failure_codes),
            }
            if semantic_failure_codes
            else None
        ),
    )


def _parse_services(
    raw: Any,
    *,
    output_root: Path,
    fixtures: Sequence[str],
    runtime_files: Sequence[str],
    handled_requirements: set[str],
    fixture_evidence_refs: Mapping[str, tuple[str, ...]],
    requirement_evidence_refs: Mapping[str, tuple[str, ...]],
) -> tuple[ReplayServiceSpec, ...]:
    _validate_compile_result_service_schema(raw)
    if not isinstance(raw, list):
        raise AssertionError("schema validation did not reject invalid services")
    _validate_conditional_service_fields(raw)
    services: list[ReplayServiceSpec] = []
    seen: set[str] = set()
    fixture_probe_violations: list[dict[str, object]] = []
    for value in raw:
        if not isinstance(value, dict):
            raise ReplayCapabilityError("replay service must be an object")
        service_id = _required_identifier(value, "service_id", "service")
        if service_id in seen:
            raise ReplayCapabilityError(f"duplicate replay service id: {service_id}")
        seen.add(service_id)
        requirement_id = _required_identifier(
            value,
            "requirement_id",
            "service",
        )
        if requirement_id not in handled_requirements:
            raise ReplayCapabilityError(
                f"replay service requirement is not handled: {requirement_id}"
            )
        transport = _required_string(value, "transport", "service")
        if transport not in _SUPPORTED_SERVICE_TRANSPORTS:
            raise ReplayCapabilityError(
                f"unsupported replay service transport: {transport}"
            )
        response_fixture = _normalized_relative_path(
            _required_string(value, "response_fixture", "service"),
            label="service response fixture",
        )
        if response_fixture not in fixtures:
            raise ReplayCapabilityError(
                f"replay service response fixture is not declared: {response_fixture}"
            )
        if not set(fixture_evidence_refs[response_fixture]).issubset(
            requirement_evidence_refs[requirement_id]
        ):
            raise ReplayCapabilityError(
                "replay service fixture evidence belongs to a different requirement"
            )
        runtime_entrypoint_raw = value.get("runtime_entrypoint")
        runtime_entrypoint: str | None = None
        protocol_probes: tuple[ReplayProtocolProbe, ...] = ()
        readiness_raw = value.get("readiness", {})
        if not isinstance(readiness_raw, dict):
            raise ReplayCapabilityError(
                f"replay service readiness must be an object: {service_id}"
            )
        kind = _required_string(readiness_raw, "kind", "readiness")
        if kind not in _SUPPORTED_READINESS_KINDS:
            raise ReplayCapabilityError(f"unsupported readiness kind: {kind}")
        timeout = readiness_raw.get("timeout_seconds", 10.0)
        if (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or timeout <= 0
            or timeout > _MAX_READINESS_TIMEOUT_SECONDS
        ):
            raise ReplayCapabilityError(
                "readiness timeout_seconds must be between 0 and "
                f"{_MAX_READINESS_TIMEOUT_SECONDS}"
            )
        path = readiness_raw.get("path", "/")
        if not isinstance(path, str) or not path.startswith("/"):
            _raise_schema_field_error(
                "HTTP readiness path must start with /",
                (
                    _absolute_path_violation(
                        field_path="services[*].readiness.path",
                        value=path,
                    ),
                ),
                extra_details={
                    "code": "readiness_path_invalid",
                    "service_id": service_id,
                },
            )
        readiness = ReplayReadinessProbe(
            kind=kind,
            timeout_seconds=float(timeout),
            path=path,
        )
        if transport == "skill_runtime":
            runtime_entrypoint = _normalize_runtime_entrypoint(
                _required_string(value, "runtime_entrypoint", "service"),
                runtime_files=runtime_files,
            )
            if not runtime_entrypoint.endswith(".py"):
                raise ReplayCapabilityError(
                    "skill runtime entrypoint must be a Python file"
                )
            protocol_probes = _parse_protocol_probes(
                value.get("protocol_probes"),
                service_id=service_id,
            )
            if not any(_is_data_plane_probe(item) for item in protocol_probes):
                raise ReplayCapabilityError(
                    "skill runtime service requires a data-plane protocol probe: "
                    f"{service_id}"
                )
            duplicate_readiness_probes = tuple(
                probe
                for probe in protocol_probes
                if probe.kind == readiness.kind and probe.path == readiness.path
            )
            if duplicate_readiness_probes:
                _raise_schema_field_error(
                    "protocol_probes must not duplicate readiness probes: "
                    f"{service_id} kind={readiness.kind} path={readiness.path}",
                    (
                        _readiness_duplicate_violation(
                            value=readiness.path,
                            occurrence_count=len(duplicate_readiness_probes),
                        ),
                    ),
                    extra_details={
                        "code": "protocol_probe_duplicates_readiness",
                        "service_id": service_id,
                        "probe_kind": readiness.kind,
                        "probe_path": readiness.path,
                    },
                )
            if any(
                item.kind == "http" and item.validate_advertised_websockets
                for item in protocol_probes
            ) and not any(
                item.kind == "websocket" and _is_data_plane_probe(item)
                for item in protocol_probes
            ):
                _raise_schema_field_error(
                    "advertised WebSocket requires a websocket data-plane protocol "
                    f"probe: {service_id}",
                    (
                        _advertised_websocket_probe_violation(),
                    ),
                    extra_details={
                        "code": "advertised_websocket_probe_missing",
                        "service_id": service_id,
                    },
                )
            fixture_bytes = _resolve_output_file(
                output_root,
                response_fixture,
            ).read_bytes()
            protocol_probes = _framework_bind_protocol_probes(
                protocol_probes,
                fixture_bytes=fixture_bytes,
            )
            for probe in protocol_probes:
                if (
                    probe.response_contains is not None
                    and not (
                        probe.kind == "http"
                        and probe.validate_advertised_websockets
                    )
                    and not replay_payload_contains_expected_value(
                        probe.response_contains,
                        fixture_bytes,
                    )
                ):
                    expected = probe.response_contains
                    expected_bytes = expected.encode("utf-8")
                    fixture_probe_violations.append(
                        {
                            "service_id": service_id,
                            "requirement_id": requirement_id,
                            "kind": probe.kind,
                            "path": probe.path,
                            "declared_response_fingerprint": (
                                "sha256:"
                                + hashlib.sha256(expected_bytes).hexdigest()
                            ),
                            "declared_response_bytes": len(expected_bytes),
                            "declared_response_shape": "utf8_text",
                        }
                    )
        elif runtime_entrypoint_raw is not None:  # pragma: no cover - prevalidated
            raise AssertionError(
                "conditional service validation accepted fixture runtime entrypoint"
            )
        services.append(
            ReplayServiceSpec(
                service_id=service_id,
                requirement_id=requirement_id,
                transport=transport,
                response_fixture=response_fixture,
                runtime_entrypoint=runtime_entrypoint,
                readiness=readiness,
                protocol_probes=protocol_probes,
            )
        )
    if fixture_probe_violations:
        first = fixture_probe_violations[0]
        constraints = [
            {
                "requirement_id": str(item["requirement_id"]),
                "kind": str(item["kind"]),
                "path": str(item["path"]),
                "max_response_chars": (
                    REPLAY_CAPABILITY_MAX_RESPONSE_CONTAINS_CHARS
                ),
            }
            for item in fixture_probe_violations
        ]
        unique_constraints = list(
            {
                (
                    item["requirement_id"],
                    item["kind"],
                    item["path"],
                    item["max_response_chars"],
                ): item
                for item in constraints
            }.values()
        )
        raise ReplayCapabilityError(
            "protocol probe response_contains must be derived from the "
            f"declared fixture: {first['service_id']} "
            f"kind={first['kind']} path={first['path']} "
            "expected_sha256="
            f"{str(first['declared_response_fingerprint']).removeprefix('sha256:')} "
            f"expected_bytes={first['declared_response_bytes']} "
            f"expected_shape={first['declared_response_shape']}",
            code="protocol_probe_not_fixture_derived",
            details={
                "fixture_probe_constraints": unique_constraints,
                "fixture_probe_violation_count": len(fixture_probe_violations),
                "fixture_probe_violations": fixture_probe_violations,
            },
        )
    return tuple(sorted(services, key=lambda item: item.service_id))


_SERVICE_CONDITIONAL_PRESENCE_RULES = (
    {
        "selector_field": "transport",
        "selector_values": ("skill_runtime",),
        "subject_field": "runtime_entrypoint",
        "required": True,
    },
    {
        "selector_field": "transport",
        "selector_values": ("http_fixture", "tcp_fixture"),
        "subject_field": "runtime_entrypoint",
        "required": False,
    },
)


def _validate_conditional_service_fields(
    services: Sequence[Mapping[str, Any]],
) -> None:
    """Validate declarative cross-field presence rules before fail-fast parsing."""

    violations: list[SchemaFieldViolation] = []
    relationships: list[dict[str, object]] = []
    for rule in _SERVICE_CONDITIONAL_PRESENCE_RULES:
        selector_field = str(rule["selector_field"])
        selector_values = tuple(str(item) for item in rule["selector_values"])
        subject_field = str(rule["subject_field"])
        required = rule["required"] is True
        relationships.append(
            {
                "selector_field": selector_field,
                "selector_values": list(selector_values),
                "subject_field": subject_field,
                "presence": "required" if required else "forbidden",
            }
        )
        for service in services:
            selector = service.get(selector_field)
            if selector not in selector_values:
                continue
            subject_declared = (
                subject_field in service
                and service.get(subject_field) is not None
            )
            subject_present = bool(
                subject_declared and service.get(subject_field) != ""
            )
            condition_satisfied = (
                subject_present if required else not subject_declared
            )
            if condition_satisfied:
                continue
            predicate = f"{selector_field}:{selector}"
            field_path = f"services[*@{predicate}].{subject_field}"
            violations.append(
                _schema_field_violation(
                    schema_layer="compile_result",
                    field_path=field_path,
                    rule="required" if required else "type",
                    expected=() if required else ("null",),
                    value=service.get(subject_field),
                )
            )
    if violations:
        _raise_schema_field_error(
            "compile-result service fields violate conditional presence rules",
            violations,
            extra_details={
                "code": "service_conditional_field_validation_failed",
                "conditional_field_rules": relationships,
            },
        )


def _parse_protocol_probes(
    raw: Any,
    *,
    service_id: str,
) -> tuple[ReplayProtocolProbe, ...]:
    if not isinstance(raw, list) or not raw:
        raise ReplayCapabilityError(
            f"skill runtime service requires protocol_probes: {service_id}"
        )
    if len(raw) > REPLAY_CAPABILITY_MAX_PROTOCOL_PROBES:
        raise ReplayCapabilityError(
            "protocol_probes cannot exceed "
            f"{REPLAY_CAPABILITY_MAX_PROTOCOL_PROBES} items"
        )
    probes: list[ReplayProtocolProbe] = []
    for value in raw:
        if not isinstance(value, dict):
            raise ReplayCapabilityError("protocol probe must be an object")
        kind = _required_string(value, "kind", "protocol probe")
        if kind not in _SUPPORTED_PROTOCOL_PROBE_KINDS:
            raise ReplayCapabilityError(f"unsupported protocol probe kind: {kind}")
        timeout = value.get("timeout_seconds", 5.0)
        if (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or timeout <= 0
            or timeout > _MAX_READINESS_TIMEOUT_SECONDS
        ):
            raise ReplayCapabilityError(
                "protocol probe timeout_seconds must be between 0 and "
                f"{_MAX_READINESS_TIMEOUT_SECONDS}"
            )
        path = value.get("path", "/")
        if not isinstance(path, str) or not path.startswith("/"):
            _raise_schema_field_error(
                "HTTP protocol probe path must start with /",
                (
                    _absolute_path_violation(
                        field_path="services[*].protocol_probes[*].path",
                        value=path,
                    ),
                ),
                extra_details={
                    "code": "protocol_probe_path_invalid",
                    "service_id": service_id,
                    "probe_kind": kind,
                },
            )
        validate_links = value.get("validate_advertised_websockets", False)
        if not isinstance(validate_links, bool):
            raise ReplayCapabilityError(
                "validate_advertised_websockets must be boolean"
            )
        if validate_links and kind != "http":
            raise ReplayCapabilityError(
                "only HTTP protocol probes can validate advertised WebSockets"
            )
        request_text = _optional_bounded_probe_text(
            value.get("request_text"),
            field="request_text",
            max_chars=16_384,
        )
        response_contains = _optional_bounded_probe_text(
            value.get("response_contains"),
            field="response_contains",
            max_chars=REPLAY_CAPABILITY_MAX_RESPONSE_CONTAINS_CHARS,
        )
        if kind in {"tcp", "websocket"} and (
            request_text is None or response_contains is None
        ):
            raise ReplayCapabilityError(
                f"{kind} data-plane probe requires request_text and response_contains"
            )
        if kind == "http" and request_text is not None:
            raise ReplayCapabilityError(
                "HTTP protocol probe does not accept request_text"
            )
        probes.append(
            ReplayProtocolProbe(
                kind=kind,
                timeout_seconds=float(timeout),
                path=path,
                validate_advertised_websockets=validate_links,
                request_text=request_text,
                response_contains=response_contains,
            )
        )
    return tuple(probes)


def _optional_bounded_probe_text(
    value: Any,
    *,
    field: str,
    max_chars: int,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > max_chars:
        raise ReplayCapabilityError(
            f"protocol probe {field} must be non-empty and at most {max_chars} characters"
        )
    return value


def replay_payload_contains_expected_value(
    expected: str,
    payload_bytes: bytes,
) -> bool:
    expected_bytes = expected.encode("utf-8")
    if expected_bytes in payload_bytes:
        return True
    if len(payload_bytes) > _MAX_JSON_BYTES:
        return False
    try:
        text = payload_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return False

    roots: list[Any] = []
    try:
        roots.append(json.loads(text))
    except (TypeError, ValueError, json.JSONDecodeError):
        for line in text.splitlines()[:4096]:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                roots.append(json.loads(stripped))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
    if not roots:
        return expected in text

    expected_value: Any = _NO_DECODED_EXPECTATION
    stripped_expected = expected.strip()
    if (
        len(stripped_expected) <= _MAX_JSON_BYTES
        and stripped_expected[:1] in {"{", "["}
        and stripped_expected[-1:] in {"}", "]"}
    ):
        try:
            expected_value = json.loads(stripped_expected)
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

    pending: list[Any] = list(reversed(roots))
    visited = 0
    while pending and visited < 50_000:
        current = pending.pop()
        visited += 1
        if (
            expected_value is not _NO_DECODED_EXPECTATION
            and current == expected_value
        ):
            return True
        if isinstance(current, Mapping):
            pending.extend(reversed(tuple(current.values())))
            continue
        if isinstance(current, (list, tuple)):
            pending.extend(reversed(current))
            continue
        if isinstance(current, str):
            if expected in current:
                return True
            stripped = current.strip()
            if (
                len(stripped) <= _MAX_JSON_BYTES
                and stripped[:1] in {"{", "["}
                and stripped[-1:] in {"}", "]"}
            ):
                try:
                    pending.append(json.loads(stripped))
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
            continue
        if current is not None and expected == str(current):
            return True
    return False


_NO_DECODED_EXPECTATION = object()


def _is_data_plane_probe(probe: ReplayProtocolProbe) -> bool:
    if probe.kind == "http":
        return bool(probe.response_contains)
    return bool(probe.request_text and probe.response_contains)


def _normalize_runtime_entrypoint(
    value: str,
    *,
    runtime_files: Sequence[str],
) -> str:
    candidates: list[str] = []
    try:
        candidates.append(
            _normalized_relative_path(value, label="service runtime entrypoint")
        )
    except ReplayCapabilityError:
        pass
    module_name, separator, callable_name = value.partition(":")
    module_parts = module_name.split(".")
    if (
        module_parts
        and all(part.isidentifier() for part in module_parts)
        and (not separator or callable_name.isidentifier())
    ):
        candidates.append("/".join(module_parts) + ".py")
    for candidate in candidates:
        if candidate in runtime_files:
            return candidate
    raise ReplayCapabilityError(
        "skill runtime entrypoint must be declared in manifest runtime_files"
    )


def _compile_snapshot(
    result: ReplayCapabilityCompileResult,
    output_root: Path,
    capability: DiscoveredReplayCapability,
) -> dict[str, Any]:
    return {
        "result": asdict(result),
        "fixtures": [
            _file_manifest_entry(
                _resolve_output_file(output_root, relative), relative
            )
            for relative in result.fixtures
        ],
        "runtime_files": [
            _file_manifest_entry(
                path, path.relative_to(capability.skill_root).as_posix()
            )
            for path in capability.runtime_files
        ],
    }


def _freeze_compile_result(
    *,
    capability: DiscoveredReplayCapability,
    request: ReplayCapabilityCompileRequest,
    result: ReplayCapabilityCompileResult,
    output_root: Path,
    frozen_root: Path,
) -> FrozenReplayCapability:
    fixtures_root = frozen_root / "fixtures"
    runtime_root = frozen_root / "runtime"
    fixtures_root.mkdir(parents=True)
    runtime_root.mkdir()
    frozen_fixtures: list[FrozenReplayFile] = []
    for relative in result.fixtures:
        source = _resolve_output_file(output_root, relative)
        destination = _destination_inside(fixtures_root, relative, label="fixture")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination, follow_symlinks=False)
        # A skill-owned runtime receives only the frozen fixture path.  Keep a
        # generic operation index beside that fixture so the runtime can
        # correlate a request with a nested recorded response without having
        # to rediscover the trajectory envelope on every protocol call.  The
        # sidecar is derived solely from the immutable fixture and is copied
        # with the frozen capability; it does not impose a protocol-specific
        # adapter on the framework.
        try:
            response_index = _build_recorded_response_index(
                destination.read_bytes(),
                observed_operations=_declared_probe_operations(result.services),
            )
        except OSError:
            response_index = {"records": []}
        _require_recorded_response_fixture_for_sidecar_runtime(
            capability=capability,
            request=request,
            result=result,
            fixture=relative,
            response_index=response_index,
        )
        if response_index.get("records"):
            response_index_path = destination.with_suffix(".responses.json")
            _write_json(response_index_path, response_index)
        frozen_fixtures.append(_frozen_file(destination, relative))
    frozen_runtime: list[FrozenReplayFile] = []
    for source in capability.runtime_files:
        relative = source.relative_to(capability.skill_root).as_posix()
        destination = _destination_inside(runtime_root, relative, label="runtime file")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination, follow_symlinks=False)
        destination.chmod(source.stat().st_mode & 0o777)
        frozen_runtime.append(_frozen_file(destination, relative))
    _validate_frozen_python_runtime(runtime_root, frozen_runtime)
    payload = {
        "schema_version": REPLAY_CAPABILITY_RESULT_SCHEMA_VERSION,
        "capability_id": result.capability_id,
        "capability_package_fingerprint": capability.package_fingerprint,
        "request_fingerprint": request.request_fingerprint,
        "handled_requirements": list(result.handled_requirements),
        "unhandled_requirements": list(result.unhandled_requirements),
        "evidence_refs": result.evidence_refs,
        "fixture_evidence_refs": result.fixture_evidence_refs,
        "fixtures": [asdict(item) for item in frozen_fixtures],
        "runtime_files": [asdict(item) for item in frozen_runtime],
        "endpoint_replacements": result.endpoint_replacements,
        "services": [asdict(item) for item in result.services],
        "deterministic": result.deterministic,
        "concurrency_mode": result.concurrency_mode,
        "resource_key": result.resource_key,
        "binding_fingerprint": result.binding_fingerprint,
    }
    fingerprint = _json_fingerprint(payload)
    manifest = {**payload, "fingerprint": fingerprint}
    _write_json(frozen_root / "frozen_manifest.json", manifest)
    return FrozenReplayCapability(
        capability_id=result.capability_id,
        capability_package_fingerprint=capability.package_fingerprint,
        request_fingerprint=request.request_fingerprint,
        frozen_root=str(frozen_root),
        handled_requirements=result.handled_requirements,
        unhandled_requirements=result.unhandled_requirements,
        evidence_refs=result.evidence_refs,
        fixture_evidence_refs=result.fixture_evidence_refs,
        fixtures=tuple(frozen_fixtures),
        runtime_files=tuple(frozen_runtime),
        endpoint_replacements=result.endpoint_replacements,
        services=result.services,
        deterministic=result.deterministic,
        fingerprint=fingerprint,
        ready=result.deterministic and not result.unhandled_requirements,
        concurrency_mode=result.concurrency_mode,
        resource_key=result.resource_key,
        binding_fingerprint=result.binding_fingerprint,
    )


def _resolve_skill_file(root: Path, relative: str, *, label: str) -> Path:
    normalized = _normalized_relative_path(relative, label=label)
    candidate = root.joinpath(*PurePosixPath(normalized).parts)
    _reject_symlink_components(root, candidate, label=label)
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ReplayCapabilityError(f"replay capability {label} does not exist: {relative}") from exc
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ReplayCapabilityError(
            f"replay capability {label} must be a regular file inside skill root"
        )
    return resolved


def _resolve_output_file(output_root: Path, relative: str) -> Path:
    normalized = _normalized_relative_path(relative, label="fixture")
    candidate = output_root.joinpath(*PurePosixPath(normalized).parts)
    _reject_symlink_components(output_root, candidate, label="fixture")
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ReplayCapabilityError(f"replay fixture does not exist: {relative}") from exc
    if not resolved.is_relative_to(output_root) or not resolved.is_file():
        raise ReplayCapabilityError(
            "replay fixture must be a regular file inside capability output"
        )
    return resolved


def _reject_symlink_components(root: Path, candidate: Path, *, label: str) -> None:
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ReplayCapabilityError(
            f"replay capability {label} must be inside skill root"
        ) from exc
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ReplayCapabilityError(f"replay capability {label} cannot use symlinks")


def _normalized_relative_path(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\\" in value:
        raise ReplayCapabilityError(f"replay capability {label} must be inside skill root")
    path = PurePosixPath(value.strip())
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ReplayCapabilityError(f"replay capability {label} must be inside skill root")
    return path.as_posix()


def _destination_inside(root: Path, relative: str, *, label: str) -> Path:
    normalized = _normalized_relative_path(relative, label=label)
    destination = root.joinpath(*PurePosixPath(normalized).parts)
    if not destination.resolve().is_relative_to(root.resolve()):
        raise ReplayCapabilityError(f"{label} destination escapes frozen root")
    return destination


def _parse_identifier(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReplayCapabilityError(f"{label} must be a non-empty string")
    normalized = value.strip()
    if not all(character.isalnum() or character in "._-" for character in normalized):
        raise ReplayCapabilityError(f"{label} contains unsupported characters")
    return normalized


def _required_identifier(raw: Mapping[str, Any], key: str, label: str) -> str:
    return _parse_identifier(raw.get(key), label=f"{label} {key}")


def _required_string(raw: Mapping[str, Any], key: str, label: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ReplayCapabilityError(f"{label} {key} must be a non-empty string")
    return value.strip()


def _string_tuple(value: Any, *, label: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ReplayCapabilityError(f"{label} must be an array of non-empty strings")
    return tuple(value)


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        if path.stat().st_size > _MAX_JSON_BYTES:
            raise ReplayCapabilityError(f"{label} exceeds byte limit: {path}")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReplayCapabilityError(f"invalid {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ReplayCapabilityError(f"{label} must contain a JSON object")
    return value


def _optional_bounded_string(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ReplayCapabilityError(f"{label} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > 512 or any(ord(character) < 32 for character in normalized):
        raise ReplayCapabilityError(f"{label} exceeds the safe string contract")
    return normalized


def _validate_declared_output_files(
    output_root: Path,
    fixtures: Sequence[str],
) -> None:
    allowed = {"result.json", *fixtures}
    observed: set[str] = set()
    for path in output_root.rglob("*"):
        if path.is_symlink():
            raise ReplayCapabilityError(
                "replay capability output cannot contain symlinks"
            )
        if path.is_file():
            observed.add(path.relative_to(output_root).as_posix())
    undeclared = observed - allowed
    if undeclared:
        _raise_schema_field_error(
            (
                "replay capability produced undeclared output files: "
                f"{sorted(undeclared)}. Remove them: the compiler may write only "
                "result.json and its declared evidence fixtures. The framework "
                "derives the recorded-response sidecar after compile and supplies "
                "its path through AWORLD_REPLAY_RESPONSE_INDEX automatically; do "
                "not write, declare, relocate, or pass a compiler-owned response index"
            ),
            (
                _schema_field_violation(
                    schema_layer="compiler_output",
                    field_path="files[*]",
                    rule="enum",
                    expected=("result.json", "declared_fixture"),
                    value=sorted(undeclared),
                    occurrence_count=len(undeclared),
                ),
            ),
        )


def _bounded_output(path: Path, max_chars: int) -> str:
    try:
        raw = path.read_bytes()
    except OSError:
        return ""
    return raw[-max_chars:].decode("utf-8", errors="replace")


def _terminate_process_group(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=2.0)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired as exc:
        raise ReplayCapabilityError(
            "replay capability process could not be terminated"
        ) from exc


def replay_process_resource_limiter(
    *,
    max_file_bytes: int,
    max_memory_bytes: int,
    cpu_seconds: int,
) -> Any | None:
    if os.name != "posix":
        return None
    try:
        import resource
    except ImportError:
        return None

    def limit() -> None:
        resource.setrlimit(resource.RLIMIT_FSIZE, (max_file_bytes, max_file_bytes))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        memory_resource = (
            resource.RLIMIT_AS
            if sys.platform != "darwin" and hasattr(resource, "RLIMIT_AS")
            else None
        )
        if memory_resource is not None:
            current_soft, current_hard = resource.getrlimit(memory_resource)
            memory_limit = min(
                value
                for value in (max_memory_bytes, current_soft, current_hard)
                if value > 0
            )
            resource.setrlimit(memory_resource, (memory_limit, memory_limit))
        if hasattr(resource, "RLIMIT_NPROC"):
            current_soft, current_hard = resource.getrlimit(resource.RLIMIT_NPROC)
            process_limit = min(
                value for value in (32, current_soft, current_hard) if value > 0
            )
            resource.setrlimit(
                resource.RLIMIT_NPROC,
                (process_limit, process_limit),
            )

    return limit


_RESOURCE_LIMIT_EXEC_CODE = r"""
import json
import os
import resource
import sys

limits = json.loads(sys.argv[1])
command = json.loads(sys.argv[2])


def finite(value):
    return value not in (-1, resource.RLIM_INFINITY) and value > 0


def apply_limit(kind, soft, hard=None):
    current_soft, current_hard = resource.getrlimit(kind)
    requested_hard = soft if hard is None else hard
    effective_hard = requested_hard
    if finite(current_hard):
        effective_hard = min(effective_hard, current_hard)
    effective_soft = min(soft, effective_hard)
    resource.setrlimit(kind, (effective_soft, effective_hard))


apply_limit(resource.RLIMIT_FSIZE, limits["file_bytes"])
apply_limit(resource.RLIMIT_CPU, limits["cpu_seconds"], limits["cpu_seconds"] + 1)
if sys.platform != "darwin" and hasattr(resource, "RLIMIT_AS"):
    apply_limit(resource.RLIMIT_AS, limits["memory_bytes"])
if hasattr(resource, "RLIMIT_NPROC"):
    apply_limit(resource.RLIMIT_NPROC, 32)
os.execv(command[0], command)
"""


def build_replay_resource_limited_command(
    command: Sequence[str],
    *,
    max_file_bytes: int,
    max_memory_bytes: int,
    cpu_seconds: int,
) -> list[str]:
    """Apply child limits after exec instead of using ``preexec_fn``.

    Python documents ``preexec_fn`` as unsafe in threaded processes. Replay
    repetitions intentionally run concurrently, so a child can otherwise hang
    between fork and exec while appearing alive to the readiness watchdog. A
    tiny isolated Python launcher installs the same limits in the child and
    immediately execs the already-sandboxed command.
    """

    if not command:
        raise ValueError("resource-limited replay command cannot be empty")
    if os.name != "posix":
        return list(command)
    limits = {
        "file_bytes": max_file_bytes,
        "memory_bytes": max_memory_bytes,
        "cpu_seconds": cpu_seconds,
    }
    if any(not isinstance(value, int) or value <= 0 for value in limits.values()):
        raise ValueError("replay resource limits must be positive integers")
    resolved_command = list(command)
    executable = Path(resolved_command[0]).expanduser().resolve()
    resolved_command[0] = str(executable)
    return [
        sys.executable,
        "-I",
        "-c",
        _RESOURCE_LIMIT_EXEC_CODE,
        json.dumps(limits, sort_keys=True, separators=(",", ":")),
        json.dumps(resolved_command, separators=(",", ":")),
    ]


def replay_process_memory_bytes(process_id: int) -> int:
    if sys.platform != "darwin":
        return 0
    native_rss = _darwin_process_memory_bytes(process_id)
    if native_rss is not None:
        return native_rss
    ps = Path("/bin/ps")
    if not ps.is_file():
        raise ReplayCapabilityError("memory watchdog requires /bin/ps")
    try:
        result = subprocess.run(
            [str(ps), "-o", "rss=", "-p", str(process_id)],
            check=False,
            capture_output=True,
            text=True,
            timeout=1.0,
        )
    except subprocess.TimeoutExpired:
        # RSS is sampled repeatedly. A transient scheduler delay must not turn a
        # successfully completed replay into an infrastructure failure.
        return 0
    value = result.stdout.strip()
    if not value:
        return 0
    try:
        return int(value.splitlines()[-1].strip()) * 1024
    except ValueError as exc:
        raise ReplayCapabilityError("memory watchdog returned invalid RSS") from exc


def build_replay_sandboxed_command(
    command: Sequence[str],
    *,
    read_roots: Sequence[str | Path],
    writable_roots: Sequence[str | Path],
    allow_loopback: bool,
) -> list[str]:
    sandbox_executable = shutil.which("sandbox-exec") if sys.platform == "darwin" else None
    if sandbox_executable is None:
        raise ReplayCapabilityError(
            "skill-owned replay requires an available platform sandbox"
        )

    allowed_executables = {
        Path(sys.executable).resolve(),
        Path(command[0]).expanduser().resolve(),
    }
    profile: list[str] = [
        "(version 1)",
        "(deny default)",
        '(import "system.sb")',
        "(allow process-info*)",
        "(allow sysctl-read)",
        "(deny process-fork)",
        "(deny process-exec)",
    ]
    profile.extend(
        f'(allow process-exec (literal "{_sbpl_path(path)}"))'
        for path in sorted(allowed_executables)
    )
    if allow_loopback:
        profile.extend(
            [
                '(allow network-bind (local ip "localhost:*"))',
                '(allow network-inbound (local ip "localhost:*"))',
            ]
        )
    runtime_read_roots = {
        Path(sys.prefix).resolve(),
        Path(sys.base_prefix).resolve(),
        *(Path(path).resolve() for path in read_roots),
        *(Path(path).resolve() for path in writable_roots),
    }
    profile.extend(
        f'(allow file-read* (subpath "{_sbpl_path(path)}"))'
        for path in sorted(runtime_read_roots)
    )
    profile.extend(
        f'(allow file-write* (subpath "{_sbpl_path(Path(path).resolve())}"))'
        for path in writable_roots
    )
    return [sandbox_executable, "-p", " ".join(profile), *command]


def _directory_size_bytes(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except FileNotFoundError:
            continue
    return total


def _sbpl_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace('"', '\\"')


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _file_manifest_entry(path: Path, relative: str) -> dict[str, Any]:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "path": relative,
        "sha256": f"sha256:{digest}",
        "size": path.stat().st_size,
        "mode": path.stat().st_mode & 0o777,
    }


def _frozen_file(path: Path, relative: str) -> FrozenReplayFile:
    entry = _file_manifest_entry(path, relative)
    return FrozenReplayFile(
        path=relative,
        sha256=entry["sha256"],
        size=entry["size"],
    )


def _json_fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)
