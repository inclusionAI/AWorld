from __future__ import annotations

import hashlib
import json
import math
import os
import signal
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Protocol, Sequence

from aworld.self_evolve.replay_adaptation import (
    ReplayAdapterBinding,
    ReplayAdapterContext,
    ReplayCapabilityRequirement,
    ReplayDependency,
    validate_replay_binding_concurrency,
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

REPLAY_CAPABILITY_SUPPORTED_READINESS_KINDS = tuple(
    sorted(_SUPPORTED_READINESS_KINDS)
)
REPLAY_CAPABILITY_SUPPORTED_PROTOCOL_PROBE_KINDS = tuple(
    sorted(_SUPPORTED_PROTOCOL_PROBE_KINDS)
)
REPLAY_CAPABILITY_SUPPORTED_SERVICE_TRANSPORTS = tuple(
    sorted(_SUPPORTED_SERVICE_TRANSPORTS)
)


class ReplayCapabilityError(RuntimeError):
    """Raised when a skill-owned replay capability violates the protocol."""


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


@dataclass(frozen=True)
class ReplayCapabilityCompileRequest:
    schema_version: str
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
        package_fingerprint = capability_package_fingerprint
        if package_fingerprint is None:
            capability = discover_replay_capability(root)
            if capability is None:
                raise ReplayCapabilityError(
                    f"replay capability manifest not found under: {root}"
                )
            package_fingerprint = capability.package_fingerprint
        normalized_derivations = {
            str(evidence_ref): [dict(item) for item in entries]
            for evidence_ref, entries in sorted(
                (evidence_derivations or {}).items()
            )
        }
        payload = {
            "schema_version": REPLAY_CAPABILITY_REQUEST_SCHEMA_VERSION,
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
            request_fingerprint=_json_fingerprint(payload),
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
                process = subprocess.Popen(
                    command,
                    cwd=run_root,
                    env=environment,
                    shell=False,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    start_new_session=True,
                    preexec_fn=replay_process_resource_limiter(
                        max_file_bytes=max(
                            self.max_output_chars,
                            _MAX_FIXTURE_FILE_BYTES,
                        ),
                        max_memory_bytes=512 * 1024 * 1024,
                        cpu_seconds=max(1, math.ceil(self.timeout_seconds)),
                    ),
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
        package_fingerprint=fingerprint_skill_package(root),
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
    except Exception:
        _remove_path(frozen_root)
        raise
    return frozen


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


def _parse_manifest(raw: Mapping[str, Any]) -> ReplayCapabilityManifest:
    schema_version = _required_string(raw, "schema_version", "manifest")
    if schema_version != REPLAY_CAPABILITY_SCHEMA_VERSION:
        raise ReplayCapabilityError(
            f"unsupported replay capability schema: {schema_version}"
        )
    protocol = _required_string(raw, "protocol", "manifest")
    if protocol != REPLAY_CAPABILITY_PROTOCOL_VERSION:
        raise ReplayCapabilityError(f"unsupported replay capability protocol: {protocol}")
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
        raise ReplayCapabilityError(
            f"unsupported replay capability requirement kinds: {unsupported}"
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
        raise ReplayCapabilityError(
            f"unsupported replay capability result schema: {schema_version}"
        )
    capability_id = _required_identifier(raw, "capability_id", "result")
    if capability_id != capability.manifest.capability_id:
        raise ReplayCapabilityError("replay capability result capability_id mismatch")
    deterministic = raw.get("deterministic")
    if not isinstance(deterministic, bool):
        raise ReplayCapabilityError("result deterministic must be a boolean")
    handled = _string_tuple(
        raw.get("handled_requirements", ()), label="handled_requirements"
    )
    unhandled = _string_tuple(
        raw.get("unhandled_requirements", ()), label="unhandled_requirements"
    )
    if len(set(handled)) != len(handled) or len(set(unhandled)) != len(unhandled):
        raise ReplayCapabilityError("result requirement lists contain duplicates")
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
        raise ReplayCapabilityError("result fixtures contain duplicates")
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
            raise ReplayCapabilityError(
                "runtime_required requirement must use skill_runtime: "
                f"{requirement_id}"
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
        raise ReplayCapabilityError("result fixture_evidence_refs must be an object")
    if set(raw) != set(fixtures):
        raise ReplayCapabilityError(
            "result must provide evidence provenance for every fixture exactly once"
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
            preview = value[:160].decode("utf-8", errors="replace")
            entries.append(
                {
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
            )
        if entries:
            catalog[evidence_ref] = tuple(entries)
    return catalog


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
    if not isinstance(raw, list):
        raise ReplayCapabilityError("result services must be an array")
    services: list[ReplayServiceSpec] = []
    seen: set[str] = set()
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
            if any(
                item.kind == "http" and item.validate_advertised_websockets
                for item in protocol_probes
            ) and not any(
                item.kind == "websocket" and _is_data_plane_probe(item)
                for item in protocol_probes
            ):
                raise ReplayCapabilityError(
                    "advertised WebSocket requires a websocket data-plane protocol "
                    f"probe: {service_id}"
                )
            fixture_bytes = _resolve_output_file(
                output_root,
                response_fixture,
            ).read_bytes()
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
                    expected_preview = sanitize_text(
                        expected,
                        max_chars=96,
                    ).replace("\n", " ")
                    raise ReplayCapabilityError(
                        "protocol probe response_contains must be derived from the "
                        f"declared fixture: {service_id} "
                        f"kind={probe.kind} path={probe.path} "
                        f"expected_preview={expected_preview} "
                        f"expected_sha256={hashlib.sha256(expected_bytes).hexdigest()}"
                    )
        elif runtime_entrypoint_raw is not None:
            raise ReplayCapabilityError(
                "fixture service cannot declare a runtime entrypoint"
            )
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
            raise ReplayCapabilityError("HTTP readiness path must start with /")
        services.append(
            ReplayServiceSpec(
                service_id=service_id,
                requirement_id=requirement_id,
                transport=transport,
                response_fixture=response_fixture,
                runtime_entrypoint=runtime_entrypoint,
                readiness=ReplayReadinessProbe(
                    kind=kind,
                    timeout_seconds=float(timeout),
                    path=path,
                ),
                protocol_probes=protocol_probes,
            )
        )
    return tuple(sorted(services, key=lambda item: item.service_id))


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
            raise ReplayCapabilityError("HTTP protocol probe path must start with /")
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
            max_chars=4_096,
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
        frozen_fixtures.append(_frozen_file(destination, relative))
    frozen_runtime: list[FrozenReplayFile] = []
    for source in capability.runtime_files:
        relative = source.relative_to(capability.skill_root).as_posix()
        destination = _destination_inside(runtime_root, relative, label="runtime file")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination, follow_symlinks=False)
        destination.chmod(source.stat().st_mode & 0o777)
        frozen_runtime.append(_frozen_file(destination, relative))
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
        raise ReplayCapabilityError(
            f"replay capability produced undeclared output files: {sorted(undeclared)}"
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


def replay_process_memory_bytes(process_id: int) -> int:
    if sys.platform != "darwin":
        return 0
    ps = Path("/bin/ps")
    if not ps.is_file():
        raise ReplayCapabilityError("memory watchdog requires /bin/ps")
    result = subprocess.run(
        [str(ps), "-o", "rss=", "-p", str(process_id)],
        check=False,
        capture_output=True,
        text=True,
        timeout=1.0,
    )
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
