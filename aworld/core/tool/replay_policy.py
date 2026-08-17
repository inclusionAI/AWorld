# coding: utf-8
# Copyright (c) 2025 inclusionAI.

"""Executable evidence-lifecycle policy for self-evolve replay tools."""

from __future__ import annotations

import json
import hashlib
import ipaddress
import os
import re
import shlex
import socket
import stat
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import unquote, urlsplit


REPLAY_EVIDENCE_POLICY_SCHEMA_VERSION = "aworld.replay.evidence_policy.v1"
EVIDENCE_POLICY_PROFILE_SCHEMA_VERSION = "aworld.evidence_policy_profile.v2"
FRAMEWORK_EVIDENCE_MANIFEST_SCHEMA_VERSION = "aworld.evidence_manifest.v2"
_PROFILE_ENV = "AWORLD_REPLAY_EVIDENCE_POLICY_PROFILE_JSON"
_PROFILE_FP_ENV = "AWORLD_REPLAY_EVIDENCE_POLICY_FINGERPRINT"
_POLICY_MODE_ENV = "AWORLD_REPLAY_EVIDENCE_POLICY_MODE"
_PRODUCER_REGISTRATIONS_ENV = "AWORLD_REPLAY_EVIDENCE_PRODUCERS_JSON"
_WRITER_ATTESTATION_ENV = "AWORLD_REPLAY_EVIDENCE_WRITER_ATTESTATION_JSON"
_RESOURCE_OWNERSHIP_TOKEN_ENV = "AWORLD_REPLAY_RESOURCE_OWNERSHIP_TOKEN"
_ISOLATION_IDENTITY_ENV = "AWORLD_REPLAY_ISOLATION_IDENTITY"
_RESOURCE_IDENTITY_ENV = "AWORLD_REPLAY_RESOURCE_IDENTITY"
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_PROFILE_ITEM_LIMIT = 64
_PROFILE_JSON_BYTE_LIMIT = 262_144
_MANIFEST_JSON_BYTE_LIMIT = 1_048_576
_ACTION_PARAMETER_BYTE_LIMIT = 262_144
_TASK_RESPONSE_ATTESTATION_BYTE_LIMIT = 65_536
_CONTROL_FILE_BYTE_LIMIT = 1_048_576
_ARTIFACT_FILE_LIMIT_MAX = 4_096
_ARTIFACT_ITEM_LIMIT_MAX = 1_000_000
_ARTIFACT_BYTE_LIMIT_MAX = 10_000_000_000
_PROJECTION_BYTE_LIMIT_MAX = 10_000_000
_REQUIRED_POLICY_MODES = frozenset({"required", "authoritative"})
_LEGACY_POLICY_MODES = frozenset({"legacy", "shadow"})
_MANIFEST_FIELDS = frozenset(
    {
        "handle_id",
        "artifact_type",
        "producer_id",
        "relative_path",
        "content_digest",
        "byte_count",
        "item_count",
        "projection_relative_path",
        "projection_digest",
    }
)
_CONTROL_FILES = frozenset(
    {
        "evidence_manifest.jsonl",
        "evidence_bundle.json",
        "execution_request.json",
        "framework_evidence_policy.jsonl",
        "framework_evidence_state.json",
        "metrics.json",
        "trajectory.json",
        "stdout.txt",
        "stderr.txt",
    }
)
_MANIFEST_PAYLOAD_KEYS = frozenset(
    {
        "excerpt",
        "excerpts",
        "bounded_excerpt",
        "bounded_excerpts",
        "field_list",
        "fields",
        "fields_extracted",
        "key_fields",
        "claims_supported",
        "claims_supported_by",
        "summary",
        "structured_summary",
        "metadata",
    }
)
_LOOPBACK_ENDPOINT_PATTERN = re.compile(
    r"(?i)(?P<scheme>https?|wss?|tcp)://"
    r"(?P<host>localhost|127(?:\.\d{1,3}){3}|\[::1\])"
    r"(?::(?P<port>\d{1,5}))?"
    r"(?P<path>/[^\s\"'<>]*)?"
)
_URL_ENDPOINT_PATTERN = re.compile(
    r"(?i)(?:https?|wss?|tcp)://[^\s\"'<>]+"
)
_WINDOWS_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul", "clock$"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)
_FRAMEWORK_CAPABILITY_SEAL = object()
_PROTECTED_RUNTIME_ASSIGNMENT = re.compile(
    r"(?i)(?<![A-Za-z0-9_])(?:export\s+)?"
    r"(?:HOME|TMPDIR|XDG_(?:CONFIG|CACHE|DATA|STATE)_HOME|AWORLD_MEMORY_ROOT)\s*="
)
_CONTROL_PLANE_COMMAND = re.compile(
    r"(?i)(?:^|[;&|]\s*|\bsudo\s+)"
    r"(?P<command>kill|pkill|killall|systemctl|service|launchctl)\b|"
    r"\b(?P<container>docker|podman)\s+"
    r"(?P<container_action>stop|restart|kill|rm)\b"
)
_HOST_DISCOVERY_COMMAND = re.compile(
    r"(?i)(?:^|[;&|]\s*|\bsudo\s+)"
    r"(?P<command>lsof|netstat|ss|nmap|pgrep|ps)\b"
)
_CONTROL_PLANE_ACTION_NAMES = frozenset(
    {
        "kill",
        "terminate",
        "restart",
        "stop",
        "reconfigure",
        "replace",
    }
)
_COMMAND_PARAMETER_KEYS = frozenset(
    {"command", "cmd", "script", "shell", "shell_command"}
)
_ENVIRONMENT_PARAMETER_KEYS = frozenset({"env", "environment"})
_PROTECTED_RUNTIME_ROOT_KEYS = frozenset(
    {
        "home",
        "tmpdir",
        "xdg_config_home",
        "xdg_cache_home",
        "xdg_data_home",
        "xdg_state_home",
        "aworld_memory_root",
    }
)
_REPLAY_OWNED_BROWSER_CLEANUP_BINARIES = frozenset(
    {"agent-browser", "browser-use"}
)
_REPLAY_OWNED_BROWSER_CLEANUP_ACTIONS = frozenset(
    {"close", "quit"}
)
_SAFE_CLEANUP_REDIRECTION = re.compile(
    r"^(?:(?:[012])?(?:>>?|<)/dev/null|[012]?>&[012]|[012]?<&[012])$"
)


class EvidenceLifecyclePhase(str, Enum):
    COLLECTING = "collecting"
    EVIDENCE_READY = "evidence_ready"
    FINALIZING = "finalizing"


@dataclass(frozen=True)
class EvidencePolicyIssue:
    code: str
    field: str
    ownership: str = "measurement"


class EvidencePolicyValidationError(ValueError):
    def __init__(self, issues: Sequence[EvidencePolicyIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__(
            "invalid evidence policy: "
            + ", ".join(f"{item.field}:{item.code}" for item in self.issues)
        )


@dataclass(frozen=True)
class ArtifactPolicy:
    """Typed source and projection budget for one artifact class."""

    artifact_type: str
    registered_producers: tuple[str, ...]
    max_files: int
    max_items: int
    max_bytes: int
    projection: str = "summary"
    projection_byte_limit: int = 65_536
    required: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifact_type", self.artifact_type.strip())
        object.__setattr__(
            self,
            "registered_producers",
            tuple(sorted({str(item).strip() for item in self.registered_producers})),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_type": self.artifact_type,
            "registered_producers": list(self.registered_producers),
            "max_files": self.max_files,
            "max_items": self.max_items,
            "max_bytes": self.max_bytes,
            "projection": self.projection,
            "projection_byte_limit": self.projection_byte_limit,
            "required": self.required,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ArtifactPolicy":
        return cls(
            artifact_type=_strict_text(value.get("artifact_type"), "artifact_type"),
            registered_producers=_strict_string_tuple(
                value.get("registered_producers"), "registered_producers"
            ),
            max_files=_strict_int(value.get("max_files"), "max_files"),
            max_items=_strict_int(value.get("max_items"), "max_items"),
            max_bytes=_strict_int(value.get("max_bytes"), "max_bytes"),
            projection=_strict_text(value.get("projection"), "projection"),
            projection_byte_limit=_strict_int(
                value.get("projection_byte_limit"), "projection_byte_limit"
            ),
            required=_strict_bool(value.get("required"), "required"),
        )


@dataclass(frozen=True)
class DynamicEndpointBinding:
    """Stable logical identity for a resolved dynamic loopback endpoint."""

    binding_id: str
    service_identity: str
    endpoint: str
    path_scope: str = "prefix"

    def __post_init__(self) -> None:
        object.__setattr__(self, "binding_id", self.binding_id.strip())
        object.__setattr__(self, "service_identity", self.service_identity.strip())
        object.__setattr__(self, "endpoint", _normalize_loopback(self.endpoint))
        scope = str(self.path_scope).strip().casefold()
        if scope not in {"exact", "prefix"}:
            raise ValueError("dynamic endpoint path scope must be exact or prefix")
        object.__setattr__(self, "path_scope", scope)

    @property
    def authority(self) -> str:
        return next(iter(_loopback_endpoints(self.endpoint)))

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.to_dict())

    @property
    def environment_name(self) -> str:
        suffix = re.sub(r"[^A-Z0-9]+", "_", self.binding_id.upper()).strip("_")
        return f"AWORLD_REPLAY_ENDPOINT_{suffix}"

    def to_dict(self) -> dict[str, str]:
        return {
            "binding_id": self.binding_id,
            "service_identity": self.service_identity,
            "endpoint": self.endpoint,
            "path_scope": self.path_scope,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DynamicEndpointBinding":
        return cls(
            _strict_text(value.get("binding_id"), "binding_id"),
            _strict_text(value.get("service_identity"), "service_identity"),
            _strict_text(value.get("endpoint"), "endpoint"),
            _strict_text(value.get("path_scope"), "path_scope"),
        )


@dataclass(frozen=True)
class EvidenceContractIdentity:
    """One authority-bearing compiler input included in policy identity."""

    contract_kind: str
    fingerprint: str

    def __post_init__(self) -> None:
        kind = str(self.contract_kind).strip()
        if not _IDENTIFIER.fullmatch(kind):
            raise ValueError("evidence contract kind is invalid")
        if not _DIGEST.fullmatch(str(self.fingerprint)):
            raise ValueError("evidence contract fingerprint is invalid")
        object.__setattr__(self, "contract_kind", kind)

    def to_dict(self) -> dict[str, str]:
        return {
            "contract_kind": self.contract_kind,
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvidenceContractIdentity":
        return cls(
            contract_kind=_strict_text(
                value.get("contract_kind"), "contract_kind"
            ),
            fingerprint=_strict_text(value.get("fingerprint"), "fingerprint"),
        )


@dataclass(frozen=True)
class EvidencePolicyProfileV2:
    """Immutable evidence contract bound to measurement identity."""

    artifact_policies: tuple[ArtifactPolicy, ...]
    endpoint_bindings: tuple[DynamicEndpointBinding, ...] = ()
    contract_identities: tuple[EvidenceContractIdentity, ...] = ()
    required_task_response_fields: tuple[str, ...] = ()
    required_manifest_fields: tuple[str, ...] = (
        "handle_id",
        "artifact_type",
        "producer_id",
        "content_digest",
        "byte_count",
        "item_count",
    )
    allowed_control_actions: tuple[str, ...] = ()
    max_consecutive_failed_actions: int = 2
    redaction_version: str = "redaction.v1"
    projection_version: str = "projection.v1"
    schema_version: str = EVIDENCE_POLICY_PROFILE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name, key in (
            ("artifact_policies", lambda item: item.artifact_type),
            ("endpoint_bindings", lambda item: item.binding_id),
            ("contract_identities", lambda item: item.contract_kind),
        ):
            object.__setattr__(self, name, tuple(sorted(getattr(self, name), key=key)))
        for name in (
            "required_task_response_fields",
            "required_manifest_fields",
            "allowed_control_actions",
        ):
            object.__setattr__(
                self,
                name,
                tuple(sorted({str(item).strip() for item in getattr(self, name)})),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "artifact_policies": [item.to_dict() for item in self.artifact_policies],
            "endpoint_bindings": [item.to_dict() for item in self.endpoint_bindings],
            "contract_identities": [
                item.to_dict() for item in self.contract_identities
            ],
            "required_task_response_fields": list(self.required_task_response_fields),
            "required_manifest_fields": list(self.required_manifest_fields),
            "allowed_control_actions": list(self.allowed_control_actions),
            "max_consecutive_failed_actions": self.max_consecutive_failed_actions,
            "redaction_version": self.redaction_version,
            "projection_version": self.projection_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvidencePolicyProfileV2":
        artifact_values = _strict_mapping_tuple(
            value.get("artifact_policies"), "artifact_policies"
        )
        endpoint_values = _strict_mapping_tuple(
            value.get("endpoint_bindings"), "endpoint_bindings"
        )
        profile = cls(
            artifact_policies=tuple(
                ArtifactPolicy.from_dict(item)
                for item in artifact_values
            ),
            endpoint_bindings=tuple(
                DynamicEndpointBinding.from_dict(item)
                for item in endpoint_values
            ),
            contract_identities=tuple(
                EvidenceContractIdentity.from_dict(item)
                for item in _strict_mapping_tuple(
                    value.get("contract_identities", ()),
                    "contract_identities",
                )
            ),
            required_task_response_fields=_strict_string_tuple(
                value.get("required_task_response_fields"),
                "required_task_response_fields",
            ),
            required_manifest_fields=_strict_string_tuple(
                value.get("required_manifest_fields"), "required_manifest_fields"
            ),
            allowed_control_actions=_strict_string_tuple(
                value.get("allowed_control_actions"), "allowed_control_actions"
            ),
            max_consecutive_failed_actions=_strict_int(
                value.get("max_consecutive_failed_actions"),
                "max_consecutive_failed_actions",
            ),
            redaction_version=_strict_text(
                value.get("redaction_version"), "redaction_version"
            ),
            projection_version=_strict_text(
                value.get("projection_version"), "projection_version"
            ),
            schema_version=_strict_text(value.get("schema_version"), "schema_version"),
        )
        _require_valid_profile(profile)
        return profile

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.to_dict())

    def to_environment(
        self,
        *,
        mode: str = "required",
        writer_attestation: "FrameworkEvidenceWriterAttestationV2 | None" = None,
        producer_capabilities: Iterable[
            "ProducerRegistrationCapabilityV2"
        ] = (),
        resource_ownership_token: str | None = None,
    ) -> dict[str, str]:
        if mode not in _REQUIRED_POLICY_MODES | _LEGACY_POLICY_MODES:
            raise ValueError("unsupported replay evidence policy mode")
        runtime = ReplayRuntimePolicy.from_profile(self)
        values = {
            "AWORLD_REPLAY_EVIDENCE_POLICY": "1",
            _POLICY_MODE_ENV: mode,
            _PROFILE_ENV: _canonical_json(self.to_dict()),
            _PROFILE_FP_ENV: self.fingerprint,
            "AWORLD_REPLAY_ARTIFACT_FILE_LIMIT": str(runtime.artifact_file_limit),
            "AWORLD_REPLAY_ARTIFACT_BYTE_LIMIT": str(runtime.artifact_byte_limit),
            "AWORLD_REPLAY_MAX_CONSECUTIVE_FAILED_ACTIONS": str(
                runtime.max_consecutive_failed_actions
            ),
            "AWORLD_REPLAY_ALLOWED_CONTROL_ACTIONS": ",".join(
                sorted(runtime.allowed_control_actions)
            ),
        }
        values.update(
            {binding.environment_name: binding.endpoint for binding in self.endpoint_bindings}
        )
        capabilities = tuple(producer_capabilities)
        if writer_attestation is not None:
            _require_writer_attestation(self, writer_attestation)
            values[_WRITER_ATTESTATION_ENV] = _canonical_json(
                {
                    **writer_attestation.to_dict(),
                    "attestation_fingerprint": writer_attestation.fingerprint,
                }
            )
            values[_ISOLATION_IDENTITY_ENV] = writer_attestation.isolation_identity
            values[_RESOURCE_IDENTITY_ENV] = writer_attestation.resource_identity
        if capabilities:
            if writer_attestation is None:
                raise ValueError("producer capabilities require writer attestation")
            normalized = _validated_producer_capabilities(
                self, writer_attestation, capabilities
            )
            values[_PRODUCER_REGISTRATIONS_ENV] = json.dumps(
                [
                    {**item.to_dict(), "capability_fingerprint": item.fingerprint}
                    for item in normalized
                ],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
        if resource_ownership_token is not None:
            values[_RESOURCE_OWNERSHIP_TOKEN_ENV] = _strict_text(
                resource_ownership_token, "resource_ownership_token"
            )
        return values

    def public_projection(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "fingerprint": self.fingerprint,
            "artifact_policies": [
                {
                    "artifact_type": item.artifact_type,
                    "required": item.required,
                    "max_files": item.max_files,
                    "max_items": item.max_items,
                    "max_bytes": item.max_bytes,
                    "projection": item.projection,
                    "projection_byte_limit": item.projection_byte_limit,
                    "producer_count": len(item.registered_producers),
                }
                for item in self.artifact_policies[:_PROFILE_ITEM_LIMIT]
            ],
            "endpoint_binding_count": len(self.endpoint_bindings),
            "contract_identity_count": len(self.contract_identities),
            "endpoint_paths_enforced": bool(self.endpoint_bindings),
            "required_task_response_field_count": len(
                self.required_task_response_fields
            ),
            "required_manifest_field_count": len(self.required_manifest_fields),
            "allowed_control_action_count": len(self.allowed_control_actions),
            "redaction_version": self.redaction_version,
            "projection_version": self.projection_version,
        }


@dataclass(frozen=True)
class EvidenceHandleV2:
    handle_id: str
    artifact_type: str
    producer_id: str
    relative_path: str | None
    content_digest: str
    byte_count: int
    item_count: int = 1
    projection_relative_path: str | None = None
    projection_digest: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dict(vars(self))


@dataclass(frozen=True)
class FrameworkEvidenceWriterAttestationV2:
    """Opaque framework-issued identity for the canonical manifest writer."""

    evidence_policy_fingerprint: str
    writer_identity: str
    isolation_identity: str
    resource_identity: str
    _seal: object = None

    def __post_init__(self) -> None:
        if self._seal is not _FRAMEWORK_CAPABILITY_SEAL:
            raise ValueError("writer attestation must be framework issued")
        if not _DIGEST.fullmatch(self.evidence_policy_fingerprint):
            raise ValueError("writer attestation policy fingerprint is invalid")
        for name in ("writer_identity", "isolation_identity", "resource_identity"):
            _safe_public_identity(getattr(self, name), name)

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_policy_fingerprint": self.evidence_policy_fingerprint,
            "writer_identity": self.writer_identity,
            "isolation_identity": self.isolation_identity,
            "resource_identity": self.resource_identity,
        }


@dataclass(frozen=True)
class ProducerRegistrationCapabilityV2:
    """Opaque producer capability with typed roots for runtime inventory."""

    evidence_policy_fingerprint: str
    writer_attestation_fingerprint: str
    producer_id: str
    artifact_roots: tuple[tuple[str, str], ...]
    _seal: object = None

    def __post_init__(self) -> None:
        if self._seal is not _FRAMEWORK_CAPABILITY_SEAL:
            raise ValueError("producer capability must be framework issued")
        if not _DIGEST.fullmatch(self.evidence_policy_fingerprint) or not _DIGEST.fullmatch(
            self.writer_attestation_fingerprint
        ):
            raise ValueError("producer capability identity is invalid")
        if not _IDENTIFIER.fullmatch(self.producer_id):
            raise ValueError("producer capability producer id is invalid")
        roots = tuple(sorted(self.artifact_roots))
        if not roots or len(roots) > _PROFILE_ITEM_LIMIT:
            raise ValueError("producer capability roots must be bounded")
        if len({item[0] for item in roots}) != len(roots):
            raise ValueError("producer capability artifact roots must be unique")
        for artifact_type, relative_root in roots:
            if not _IDENTIFIER.fullmatch(artifact_type) or not _safe_relative_path(
                relative_root
            ):
                raise ValueError("producer capability root is invalid")
        object.__setattr__(self, "artifact_roots", roots)

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_policy_fingerprint": self.evidence_policy_fingerprint,
            "writer_attestation_fingerprint": self.writer_attestation_fingerprint,
            "producer_id": self.producer_id,
            "artifact_roots": [
                {"artifact_type": artifact_type, "relative_root": relative_root}
                for artifact_type, relative_root in self.artifact_roots
            ],
        }

    def root_for(self, artifact_type: str) -> str | None:
        return next(
            (root for declared_type, root in self.artifact_roots if declared_type == artifact_type),
            None,
        )


@dataclass(frozen=True)
class TaskResponseAttestationV2:
    writer_attestation_fingerprint: str
    task_response_fields: tuple[str, ...]
    task_response_digest: str
    _seal: object = None

    def __post_init__(self) -> None:
        if self._seal is not _FRAMEWORK_CAPABILITY_SEAL:
            raise ValueError("task response attestation must be framework issued")
        if not _DIGEST.fullmatch(self.writer_attestation_fingerprint) or not _DIGEST.fullmatch(
            self.task_response_digest
        ):
            raise ValueError("task response attestation identity is invalid")
        fields = tuple(sorted(set(self.task_response_fields)))
        if len(fields) > _PROFILE_ITEM_LIMIT or any(
            not _IDENTIFIER.fullmatch(item) for item in fields
        ):
            raise ValueError("task response attestation fields are invalid")
        object.__setattr__(self, "task_response_fields", fields)

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "writer_attestation_fingerprint": self.writer_attestation_fingerprint,
            "task_response_fields": list(self.task_response_fields),
            "task_response_digest": self.task_response_digest,
        }


def issue_framework_evidence_writer_attestation_v2(
    profile: EvidencePolicyProfileV2,
    *,
    writer_identity: str,
    isolation_identity: str,
    resource_identity: str,
) -> FrameworkEvidenceWriterAttestationV2:
    _require_valid_profile(profile)
    return FrameworkEvidenceWriterAttestationV2(
        evidence_policy_fingerprint=profile.fingerprint,
        writer_identity=writer_identity,
        isolation_identity=isolation_identity,
        resource_identity=resource_identity,
        _seal=_FRAMEWORK_CAPABILITY_SEAL,
    )


def issue_producer_registration_capability_v2(
    profile: EvidencePolicyProfileV2,
    writer_attestation: FrameworkEvidenceWriterAttestationV2,
    *,
    producer_id: str,
    artifact_roots: Mapping[str, str],
) -> ProducerRegistrationCapabilityV2:
    _require_writer_attestation(profile, writer_attestation)
    capability = ProducerRegistrationCapabilityV2(
        evidence_policy_fingerprint=profile.fingerprint,
        writer_attestation_fingerprint=writer_attestation.fingerprint,
        producer_id=producer_id,
        artifact_roots=tuple(artifact_roots.items()),
        _seal=_FRAMEWORK_CAPABILITY_SEAL,
    )
    policies = {item.artifact_type: item for item in profile.artifact_policies}
    if any(
        artifact_type not in policies
        or capability.producer_id not in policies[artifact_type].registered_producers
        for artifact_type, _root in capability.artifact_roots
    ):
        raise ValueError("producer capability is outside the evidence policy")
    return capability


def attest_task_response_v2(
    profile: EvidencePolicyProfileV2,
    writer_attestation: FrameworkEvidenceWriterAttestationV2,
    task_response: Mapping[str, Any],
) -> TaskResponseAttestationV2:
    _require_writer_attestation(profile, writer_attestation)
    projection = _task_response_projection(profile, task_response)
    return TaskResponseAttestationV2(
        writer_attestation_fingerprint=writer_attestation.fingerprint,
        task_response_fields=tuple(projection),
        task_response_digest=_fingerprint({"task_response": projection}),
        _seal=_FRAMEWORK_CAPABILITY_SEAL,
    )


@dataclass(frozen=True)
class EvidencePreflightResult:
    passed: bool
    issues: tuple[EvidencePolicyIssue, ...]
    evidence_policy_fingerprint: str

    def public_projection(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "evidence_policy_fingerprint": self.evidence_policy_fingerprint,
            "issues": [vars(item) for item in self.issues[:_PROFILE_ITEM_LIMIT]],
        }


@dataclass(frozen=True)
class EvidenceLifecycleDecision:
    phase: EvidenceLifecyclePhase
    stop_task_tool_calls: bool
    missing_requirements: tuple[str, ...]


def compile_evidence_policy_profile_v2(
    *,
    artifact_policies: Iterable[ArtifactPolicy | Mapping[str, Any]],
    endpoint_bindings: Iterable[DynamicEndpointBinding | Mapping[str, Any]] = (),
    contract_identities: Iterable[
        EvidenceContractIdentity | Mapping[str, Any]
    ] = (),
    required_task_response_fields: Iterable[str] = (),
    required_manifest_fields: Iterable[str] = tuple(
        _MANIFEST_FIELDS
        - {"relative_path", "projection_relative_path", "projection_digest"}
    ),
    allowed_control_actions: Iterable[str] = (),
    max_consecutive_failed_actions: int = 2,
    redaction_version: str = "redaction.v1",
    projection_version: str = "projection.v1",
) -> EvidencePolicyProfileV2:
    profile = EvidencePolicyProfileV2(
        artifact_policies=tuple(
            item if isinstance(item, ArtifactPolicy) else ArtifactPolicy.from_dict(item)
            for item in artifact_policies
        ),
        endpoint_bindings=tuple(
            item
            if isinstance(item, DynamicEndpointBinding)
            else DynamicEndpointBinding.from_dict(item)
            for item in endpoint_bindings
        ),
        contract_identities=tuple(
            item
            if isinstance(item, EvidenceContractIdentity)
            else EvidenceContractIdentity.from_dict(item)
            for item in contract_identities
        ),
        required_task_response_fields=tuple(required_task_response_fields),
        required_manifest_fields=tuple(required_manifest_fields),
        allowed_control_actions=tuple(allowed_control_actions),
        max_consecutive_failed_actions=max_consecutive_failed_actions,
        redaction_version=redaction_version,
        projection_version=projection_version,
    )
    _require_valid_profile(profile)
    return profile


def validate_evidence_policy_profile_v2(
    profile: EvidencePolicyProfileV2,
) -> tuple[EvidencePolicyIssue, ...]:
    issues: list[EvidencePolicyIssue] = []
    if profile.schema_version != EVIDENCE_POLICY_PROFILE_SCHEMA_VERSION:
        issues.append(EvidencePolicyIssue("unsupported_schema", "schema_version"))
    if not profile.artifact_policies or len(profile.artifact_policies) > _PROFILE_ITEM_LIMIT:
        issues.append(EvidencePolicyIssue("invalid_policy_count", "artifact_policies"))
    types: set[str] = set()
    for index, item in enumerate(profile.artifact_policies):
        field = f"artifact_policies[{index}]"
        if not _IDENTIFIER.fullmatch(item.artifact_type):
            issues.append(EvidencePolicyIssue("invalid_artifact_type", field))
        if item.artifact_type in types:
            issues.append(EvidencePolicyIssue("duplicate_artifact_type", field))
        types.add(item.artifact_type)
        if (
            not item.registered_producers
            or len(item.registered_producers) > _PROFILE_ITEM_LIMIT
            or any(
            not _IDENTIFIER.fullmatch(value) for value in item.registered_producers
            )
        ):
            issues.append(EvidencePolicyIssue("invalid_producers", field))
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in (
                item.max_files,
                item.max_items,
                item.max_bytes,
                item.projection_byte_limit,
            )
        ):
            issues.append(EvidencePolicyIssue("invalid_budget", field))
        if (
            item.max_files > _ARTIFACT_FILE_LIMIT_MAX
            or item.max_items > _ARTIFACT_ITEM_LIMIT_MAX
            or item.max_bytes > _ARTIFACT_BYTE_LIMIT_MAX
            or item.projection_byte_limit > _PROJECTION_BYTE_LIMIT_MAX
        ):
            issues.append(EvidencePolicyIssue("budget_exceeds_safe_bound", field))
        if item.projection_byte_limit > item.max_bytes:
            issues.append(EvidencePolicyIssue("projection_exceeds_budget", field))
        if item.projection not in {"index", "stream", "summary", "truncate"}:
            issues.append(EvidencePolicyIssue("invalid_projection", field))
        if not isinstance(item.required, bool):
            issues.append(EvidencePolicyIssue("invalid_required_flag", field))
    ids: set[str] = set()
    env_names: set[str] = set()
    if len(profile.endpoint_bindings) > _PROFILE_ITEM_LIMIT:
        issues.append(EvidencePolicyIssue("invalid_binding_count", "endpoint_bindings"))
    for index, item in enumerate(profile.endpoint_bindings):
        field = f"endpoint_bindings[{index}]"
        if not _IDENTIFIER.fullmatch(item.binding_id) or not _IDENTIFIER.fullmatch(
            item.service_identity
        ):
            issues.append(EvidencePolicyIssue("invalid_endpoint_identity", field))
        if item.binding_id in ids or item.environment_name in env_names:
            issues.append(EvidencePolicyIssue("duplicate_endpoint_identity", field))
        ids.add(item.binding_id)
        env_names.add(item.environment_name)
    contract_kinds: set[str] = set()
    if len(profile.contract_identities) > _PROFILE_ITEM_LIMIT:
        issues.append(
            EvidencePolicyIssue(
                "invalid_contract_identity_count", "contract_identities"
            )
        )
    for index, item in enumerate(profile.contract_identities):
        field = f"contract_identities[{index}]"
        if item.contract_kind in contract_kinds:
            issues.append(EvidencePolicyIssue("duplicate_contract_kind", field))
        contract_kinds.add(item.contract_kind)
        if not _IDENTIFIER.fullmatch(item.contract_kind) or not _DIGEST.fullmatch(
            item.fingerprint
        ):
            issues.append(EvidencePolicyIssue("invalid_contract_identity", field))
    fields = profile.required_task_response_fields + profile.required_manifest_fields
    if len(fields) > _PROFILE_ITEM_LIMIT or any(
        not _IDENTIFIER.fullmatch(value) for value in fields
    ):
        issues.append(EvidencePolicyIssue("invalid_required_fields", "required_fields"))
    if set(profile.required_manifest_fields) - _MANIFEST_FIELDS:
        issues.append(
            EvidencePolicyIssue(
                "unsupported_manifest_field", "required_manifest_fields"
            )
        )
    if len(profile.allowed_control_actions) > _PROFILE_ITEM_LIMIT:
        issues.append(
            EvidencePolicyIssue(
                "invalid_control_action_count", "allowed_control_actions"
            )
        )
    if (
        isinstance(profile.max_consecutive_failed_actions, bool)
        or not isinstance(profile.max_consecutive_failed_actions, int)
        or profile.max_consecutive_failed_actions <= 0
        or profile.max_consecutive_failed_actions > 100
    ):
        issues.append(
            EvidencePolicyIssue(
                "invalid_failure_budget", "max_consecutive_failed_actions"
            )
        )
    if any(
        not _IDENTIFIER.fullmatch(value)
        for value in (profile.redaction_version, profile.projection_version)
    ):
        issues.append(EvidencePolicyIssue("unsafe_policy_version", "policy_versions"))
    return tuple(issues)


def evidence_policy_profile_v2_from_environment(
    environment: Mapping[str, str] | None = None,
) -> EvidencePolicyProfileV2 | None:
    values = environment if environment is not None else os.environ
    raw = values.get(_PROFILE_ENV)
    if not raw:
        return None
    if len(raw.encode("utf-8")) > _PROFILE_JSON_BYTE_LIMIT:
        raise EvidencePolicyValidationError(
            (EvidencePolicyIssue("environment_profile_oversized", _PROFILE_ENV),)
        )
    try:
        decoded = json.loads(raw)
        if not isinstance(decoded, Mapping):
            raise TypeError
        profile = EvidencePolicyProfileV2.from_dict(decoded)
    except (TypeError, ValueError) as exc:
        raise EvidencePolicyValidationError(
            (EvidencePolicyIssue("invalid_environment_profile", _PROFILE_ENV),)
        ) from exc
    if values.get(_PROFILE_FP_ENV) != profile.fingerprint:
        raise EvidencePolicyValidationError(
            (EvidencePolicyIssue("fingerprint_mismatch", _PROFILE_FP_ENV),)
        )
    return profile


def make_evidence_handle_v2(**values: Any) -> EvidenceHandleV2:
    handle = EvidenceHandleV2(**values)
    issues = _handle_issues(handle)
    if issues:
        raise EvidencePolicyValidationError(issues)
    return handle


def build_framework_evidence_manifest_v2(
    profile: EvidencePolicyProfileV2,
    handles: Iterable[EvidenceHandleV2],
    task_response: Mapping[str, Any],
    *,
    artifact_root: Path,
    writer_attestation: FrameworkEvidenceWriterAttestationV2,
    producer_capabilities: Iterable[ProducerRegistrationCapabilityV2],
    task_response_attestation: TaskResponseAttestationV2,
) -> dict[str, Any]:
    _require_writer_attestation(profile, writer_attestation)
    capabilities = _validated_producer_capabilities(
        profile, writer_attestation, tuple(producer_capabilities)
    )
    expected_response = attest_task_response_v2(
        profile, writer_attestation, task_response
    )
    if task_response_attestation != expected_response:
        raise EvidencePolicyValidationError(
            (EvidencePolicyIssue("task_response_attestation_mismatch", "task_response"),)
        )
    items = tuple(sorted(handles, key=lambda item: item.handle_id))
    issues = list(_handle_policy_issues(profile, items, require_required=True))
    issues.extend(_verify_handle_files(artifact_root, items, capabilities, profile))
    issues.extend(
        EvidencePolicyIssue("required_task_response_missing", field)
        for field in profile.required_task_response_fields
        if not _present(task_response.get(field))
    )
    if issues:
        raise EvidencePolicyValidationError(issues)
    return {
        "schema_version": FRAMEWORK_EVIDENCE_MANIFEST_SCHEMA_VERSION,
        "evidence_policy_fingerprint": profile.fingerprint,
        "writer_attestation_fingerprint": writer_attestation.fingerprint,
        "producer_capability_fingerprints": sorted(
            item.fingerprint for item in capabilities
        ),
        "task_response_attestation": {
            **task_response_attestation.to_dict(),
            "attestation_fingerprint": task_response_attestation.fingerprint,
        },
        "handles": [item.to_dict() for item in items],
        "task_response_fields": [
            field
            for field in profile.required_task_response_fields[:_PROFILE_ITEM_LIMIT]
            if _present(task_response.get(field))
        ],
    }


def preflight_evidence_policy_v2(
    profile: EvidencePolicyProfileV2,
    *,
    artifact_root: Path,
    available_producers: Iterable[str],
    resolved_endpoint_bindings: Mapping[str, str] | None = None,
    producer_capabilities: Iterable[ProducerRegistrationCapabilityV2] = (),
) -> EvidencePreflightResult:
    _require_valid_profile(profile)
    issues: list[EvidencePolicyIssue] = []
    root = Path(artifact_root)
    if not _safe_existing_directory(root):
        issues.append(
            EvidencePolicyIssue(
                "artifact_directory_unavailable",
                "artifact_root",
                "infrastructure",
            )
        )
    elif not os.access(root, os.W_OK | os.X_OK):
        issues.append(
            EvidencePolicyIssue(
                "artifact_directory_not_writable",
                "artifact_root",
                "infrastructure",
            )
        )
    producers = {str(item) for item in available_producers}
    capability_pairs = {
        (capability.producer_id, artifact_type)
        for capability in producer_capabilities
        for artifact_type, _root in capability.artifact_roots
    }
    for item in profile.artifact_policies:
        if item.required and not any(
            producer in producers
            and (
                not capability_pairs
                or (producer, item.artifact_type) in capability_pairs
            )
            for producer in item.registered_producers
        ):
            issues.append(EvidencePolicyIssue("required_producer_unavailable", item.artifact_type))
    for capability in producer_capabilities:
        if capability.producer_id not in producers:
            continue
        for artifact_type, relative_root in capability.artifact_roots:
            producer_root = root / relative_root
            if not _safe_existing_directory(producer_root):
                issues.append(
                    EvidencePolicyIssue(
                        "producer_root_unavailable",
                        f"{capability.producer_id}:{artifact_type}",
                        "infrastructure",
                    )
                )
    resolved = resolved_endpoint_bindings or {}
    for item in profile.endpoint_bindings:
        try:
            actual = _normalize_loopback(resolved.get(item.binding_id, ""))
        except ValueError:
            actual = ""
        if actual != item.endpoint:
            issues.append(
                EvidencePolicyIssue(
                    "endpoint_binding_mismatch",
                    item.binding_id,
                    "infrastructure",
                )
            )
    return EvidencePreflightResult(not issues, tuple(issues), profile.fingerprint)


def determine_evidence_lifecycle_v2(
    profile: EvidencePolicyProfileV2,
    *,
    handles: Iterable[EvidenceHandleV2],
    task_response: Mapping[str, Any] | None,
    finalization_started: bool = False,
) -> EvidenceLifecycleDecision:
    items = tuple(handles)
    missing = [
        f"invalid:{issue.field}:{issue.code}"
        for issue in _handle_policy_issues(profile, items)
    ]
    present_types = {item.artifact_type for item in items}
    missing.extend(
        f"artifact:{item.artifact_type}"
        for item in profile.artifact_policies
        if item.required and item.artifact_type not in present_types
    )
    response = task_response or {}
    missing.extend(
        f"task_response:{field}"
        for field in profile.required_task_response_fields
        if not _present(response.get(field))
    )
    ready = not missing
    phase = (
        EvidenceLifecyclePhase.FINALIZING
        if ready and finalization_started
        else EvidenceLifecyclePhase.EVIDENCE_READY
        if ready
        else EvidenceLifecyclePhase.COLLECTING
    )
    return EvidenceLifecycleDecision(phase, ready, tuple(sorted(set(missing))))


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _fingerprint(value: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _normalize_loopback(value: str) -> str:
    raw = str(value).strip()
    if len(raw) > 2_048:
        raise ValueError("dynamic endpoint exceeds bounded length")
    parsed = urlsplit(raw)
    scheme = parsed.scheme.casefold()
    host = (parsed.hostname or "").casefold()
    if scheme not in {"http", "https", "ws", "wss", "tcp"} or host not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise ValueError("dynamic endpoint must be explicit loopback")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("dynamic endpoint contains forbidden components")
    try:
        port = parsed.port or (443 if scheme in {"https", "wss"} else 80)
    except ValueError as exc:
        raise ValueError("invalid dynamic endpoint port") from exc
    if not 1 <= port <= 65_535:
        raise ValueError("invalid dynamic endpoint port")
    path = parsed.path.rstrip("/")
    decoded_path = unquote(path)
    if "\\" in decoded_path or any(
        part == ".." for part in PurePosixPath(decoded_path).parts
    ):
        raise ValueError("dynamic endpoint path cannot traverse")
    rendered_host = f"[{host}]" if host == "::1" else host
    return f"{scheme}://{rendered_host}:{port}{path}"


def _require_valid_profile(profile: EvidencePolicyProfileV2) -> None:
    issues = validate_evidence_policy_profile_v2(profile)
    if issues:
        raise EvidencePolicyValidationError(issues)


def _handle_issues(handle: EvidenceHandleV2) -> tuple[EvidencePolicyIssue, ...]:
    issues: list[EvidencePolicyIssue] = []
    for name in ("handle_id", "artifact_type", "producer_id"):
        if not _IDENTIFIER.fullmatch(str(getattr(handle, name))):
            issues.append(EvidencePolicyIssue("invalid_identifier", name))
    if not _DIGEST.fullmatch(handle.content_digest):
        issues.append(EvidencePolicyIssue("invalid_digest", "content_digest"))
    if handle.projection_digest is not None and not _DIGEST.fullmatch(handle.projection_digest):
        issues.append(EvidencePolicyIssue("invalid_digest", "projection_digest"))
    if handle.relative_path is None or not _safe_relative_path(handle.relative_path):
        issues.append(EvidencePolicyIssue("invalid_relative_path", "relative_path"))
    if handle.projection_relative_path is not None and not _safe_relative_path(
        handle.projection_relative_path
    ):
        issues.append(
            EvidencePolicyIssue(
                "invalid_relative_path", "projection_relative_path"
            )
        )
    if (
        isinstance(handle.byte_count, bool)
        or not isinstance(handle.byte_count, int)
        or handle.byte_count < 0
        or isinstance(handle.item_count, bool)
        or not isinstance(handle.item_count, int)
        or handle.item_count <= 0
    ):
        issues.append(EvidencePolicyIssue("invalid_handle_budget", "handle"))
    return tuple(issues)


def _handle_policy_issues(
    profile: EvidencePolicyProfileV2,
    handles: Sequence[EvidenceHandleV2],
    *,
    require_required: bool = False,
) -> tuple[EvidencePolicyIssue, ...]:
    _require_valid_profile(profile)
    issues: list[EvidencePolicyIssue] = []
    policies = {item.artifact_type: item for item in profile.artifact_policies}
    ids: set[str] = set()
    totals: dict[str, list[int]] = {}
    for index, handle in enumerate(handles):
        field = f"handles[{index}]"
        issues.extend(_handle_issues(handle))
        if handle.handle_id in ids:
            issues.append(EvidencePolicyIssue("duplicate_handle", field))
        ids.add(handle.handle_id)
        policy = policies.get(handle.artifact_type)
        if policy is None:
            issues.append(EvidencePolicyIssue("undeclared_artifact", field))
            continue
        if handle.producer_id not in policy.registered_producers:
            issues.append(EvidencePolicyIssue("unregistered_producer", field))
        for required_field in profile.required_manifest_fields:
            if not _present(handle.to_dict().get(required_field)):
                issues.append(EvidencePolicyIssue("manifest_field_missing", required_field))
        if handle.byte_count > policy.projection_byte_limit and (
            not handle.projection_relative_path or not handle.projection_digest
        ):
            issues.append(EvidencePolicyIssue("bounded_projection_required", field))
        if bool(handle.projection_relative_path) != bool(handle.projection_digest):
            issues.append(EvidencePolicyIssue("incomplete_projection_identity", field))
        total = totals.setdefault(handle.artifact_type, [0, 0, 0])
        total[0] += 1
        total[1] += max(0, handle.item_count)
        total[2] += max(0, handle.byte_count)
    for artifact_type, policy in policies.items():
        total = totals.get(artifact_type, [0, 0, 0])
        if total[0] > policy.max_files:
            issues.append(EvidencePolicyIssue("artifact_file_budget_exceeded", artifact_type))
        if total[1] > policy.max_items:
            issues.append(EvidencePolicyIssue("artifact_item_budget_exceeded", artifact_type))
        if total[2] > policy.max_bytes:
            issues.append(EvidencePolicyIssue("artifact_byte_budget_exceeded", artifact_type))
        if require_required and policy.required and not total[1]:
            issues.append(EvidencePolicyIssue("required_artifact_missing", artifact_type))
    return tuple(issues)


def _present(value: Any) -> bool:
    return value is not None and (not hasattr(value, "__len__") or len(value) > 0)


def _strict_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 2_048:
        raise ValueError(f"{field} must be a bounded non-empty string")
    return value.strip()


def _strict_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _strict_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _strict_string_tuple(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > _PROFILE_ITEM_LIMIT:
        raise ValueError(f"{field} must be a bounded array")
    return tuple(_strict_text(item, field) for item in value)


def _strict_mapping_tuple(
    value: Any, field: str
) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, (list, tuple)) or len(value) > _PROFILE_ITEM_LIMIT:
        raise ValueError(f"{field} must be a bounded array")
    if any(not isinstance(item, Mapping) for item in value):
        raise ValueError(f"{field} must contain objects")
    return tuple(value)


def _safe_relative_path(value: str) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 2_048
        or "\\" in value
        or ":" in value
    ):
        return False
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    parts = posix.parts
    return bool(
        not posix.is_absolute()
        and not windows.is_absolute()
        and not windows.drive
        and not windows.anchor
        and all(
            part not in {"", ".", ".."}
            and not part.endswith((".", " "))
            and part.split(".", 1)[0].casefold() not in _WINDOWS_RESERVED_NAMES
            for part in parts
        )
    )


def _safe_public_identity(value: str, field: str) -> str:
    if not isinstance(value, str) or not (
        _IDENTIFIER.fullmatch(value) or _DIGEST.fullmatch(value)
    ):
        raise ValueError(f"{field} must be a safe id or fingerprint")
    return value


def _safe_existing_directory(path: Path) -> bool:
    absolute = Path(path).absolute()
    if not absolute.is_dir() or absolute.is_symlink():
        return False
    return not any(component.is_symlink() for component in (absolute, *absolute.parents))


def _require_writer_attestation(
    profile: EvidencePolicyProfileV2,
    writer: FrameworkEvidenceWriterAttestationV2,
) -> None:
    if not isinstance(writer, FrameworkEvidenceWriterAttestationV2):
        raise TypeError("framework writer attestation must be typed")
    if writer._seal is not _FRAMEWORK_CAPABILITY_SEAL:
        raise ValueError("framework writer attestation is not issued")
    if writer.evidence_policy_fingerprint != profile.fingerprint:
        raise ValueError("framework writer attestation policy drifted")


def _validated_producer_capabilities(
    profile: EvidencePolicyProfileV2,
    writer: FrameworkEvidenceWriterAttestationV2,
    capabilities: Iterable[ProducerRegistrationCapabilityV2],
) -> tuple[ProducerRegistrationCapabilityV2, ...]:
    _require_writer_attestation(profile, writer)
    result = tuple(sorted(capabilities, key=lambda item: item.producer_id))
    if not result or len(result) > _PROFILE_ITEM_LIMIT:
        raise ValueError("producer capabilities must be a bounded non-empty set")
    if len({item.producer_id for item in result}) != len(result):
        raise ValueError("producer capabilities must have unique producer ids")
    declared_roots: set[str] = set()
    for item in result:
        if not isinstance(item, ProducerRegistrationCapabilityV2):
            raise TypeError("producer capability must be typed")
        if (
            item._seal is not _FRAMEWORK_CAPABILITY_SEAL
            or item.evidence_policy_fingerprint != profile.fingerprint
            or item.writer_attestation_fingerprint != writer.fingerprint
        ):
            raise ValueError("producer capability identity drifted")
        for _artifact_type, relative_root in item.artifact_roots:
            if relative_root in declared_roots:
                raise ValueError("producer roots must not be shared across artifact types")
            declared_roots.add(relative_root)
    return result


def _task_response_projection(
    profile: EvidencePolicyProfileV2,
    task_response: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(task_response, Mapping):
        raise ValueError("task response must be a mapping")
    projection = {
        field: task_response[field]
        for field in profile.required_task_response_fields
        if field in task_response and _present(task_response[field])
    }
    try:
        encoded = _canonical_json({"task_response": projection}).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("task response projection must be canonical JSON") from exc
    if len(encoded) > _TASK_RESPONSE_ATTESTATION_BYTE_LIMIT:
        raise ValueError("task response projection exceeds attestation bound")
    return projection


def _open_relative_file_fd(root: Path, relative_path: str) -> int:
    if not _safe_existing_directory(root) or not _safe_relative_path(relative_path):
        raise OSError("unsafe evidence path")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    directory_flags = flags | getattr(os, "O_DIRECTORY", 0)
    descriptors: list[int] = []
    try:
        current = os.open(root, directory_flags)
        descriptors.append(current)
        parts = PurePosixPath(relative_path).parts
        for part in parts[:-1]:
            current = os.open(part, directory_flags, dir_fd=current)
            descriptors.append(current)
        result = os.open(parts[-1], flags, dir_fd=current)
        if not stat.S_ISREG(os.fstat(result).st_mode):
            os.close(result)
            raise OSError("evidence path is not a regular file")
        return result
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _digest_open_file(descriptor: int, *, max_bytes: int) -> tuple[int, str | None]:
    details = os.fstat(descriptor)
    size = details.st_size
    if size < 0 or size > max_bytes:
        return size, None
    digest = hashlib.sha256()
    os.lseek(descriptor, 0, os.SEEK_SET)
    remaining = size
    while remaining:
        chunk = os.read(descriptor, min(1_048_576, remaining))
        if not chunk:
            raise OSError("evidence file changed during bounded read")
        digest.update(chunk)
        remaining -= len(chunk)
    if os.fstat(descriptor).st_size != size:
        raise OSError("evidence file changed during bounded read")
    return size, "sha256:" + digest.hexdigest()


def _read_bounded_relative_file(root: Path, relative_path: str, limit: int) -> bytes:
    descriptor = _open_relative_file_fd(root, relative_path)
    try:
        size = os.fstat(descriptor).st_size
        if size < 0 or size > limit:
            raise ValueError("bounded file exceeds limit")
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                raise OSError("bounded file changed during read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.fstat(descriptor).st_size != size:
            raise OSError("bounded file changed during read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _path_is_within_root(relative_path: str, relative_root: str) -> bool:
    if not _safe_relative_path(relative_path) or not _safe_relative_path(relative_root):
        return False
    path_parts = PurePosixPath(relative_path).parts
    root_parts = PurePosixPath(relative_root).parts
    return path_parts[: len(root_parts)] == root_parts


def _verify_handle_files(
    artifact_root: Path,
    handles: Sequence[EvidenceHandleV2],
    capabilities: Sequence[ProducerRegistrationCapabilityV2],
    profile: EvidencePolicyProfileV2,
) -> tuple[EvidencePolicyIssue, ...]:
    issues: list[EvidencePolicyIssue] = []
    root = Path(artifact_root)
    policies = {item.artifact_type: item for item in profile.artifact_policies}
    if not _safe_existing_directory(root):
        return (EvidencePolicyIssue("unsafe_artifact_root", "artifact_root"),)
    by_producer = {item.producer_id: item for item in capabilities}
    for index, handle in enumerate(handles):
        field = f"handles[{index}]"
        capability = by_producer.get(handle.producer_id)
        producer_root = capability.root_for(handle.artifact_type) if capability else None
        if producer_root is None or not _path_is_within_root(
            handle.relative_path or "", producer_root
        ):
            issues.append(EvidencePolicyIssue("producer_capability_mismatch", field))
        policy = policies.get(handle.artifact_type)
        try:
            source_fd = _open_relative_file_fd(root, handle.relative_path or "")
        except OSError:
            issues.append(EvidencePolicyIssue("artifact_file_unavailable", field))
        else:
            try:
                source_size, source_digest = _digest_open_file(
                    source_fd,
                    max_bytes=(policy.max_bytes if policy else 0),
                )
                if source_size != handle.byte_count:
                    issues.append(EvidencePolicyIssue("artifact_byte_count_mismatch", field))
                elif source_digest is None:
                    issues.append(EvidencePolicyIssue("artifact_byte_budget_exceeded", field))
                elif source_digest != handle.content_digest:
                    issues.append(EvidencePolicyIssue("artifact_digest_mismatch", field))
            finally:
                os.close(source_fd)
        if handle.projection_relative_path:
            try:
                projection_fd = _open_relative_file_fd(
                    root, handle.projection_relative_path
                )
            except OSError:
                issues.append(EvidencePolicyIssue("projection_file_unavailable", field))
            else:
                try:
                    if policy is not None:
                        _projection_size, projection_digest = _digest_open_file(
                            projection_fd, max_bytes=policy.projection_byte_limit
                        )
                        if projection_digest is None:
                            issues.append(EvidencePolicyIssue("projection_budget_exceeded", field))
                        elif projection_digest != handle.projection_digest:
                            issues.append(EvidencePolicyIssue("projection_digest_mismatch", field))
                finally:
                    os.close(projection_fd)
    return tuple(issues)


def _handle_from_dict(value: Mapping[str, Any]) -> EvidenceHandleV2:
    projection_path = value.get("projection_relative_path")
    projection_digest = value.get("projection_digest")
    handle = EvidenceHandleV2(
        handle_id=_strict_text(value.get("handle_id"), "handle_id"),
        artifact_type=_strict_text(value.get("artifact_type"), "artifact_type"),
        producer_id=_strict_text(value.get("producer_id"), "producer_id"),
        relative_path=_strict_text(value.get("relative_path"), "relative_path"),
        content_digest=_strict_text(value.get("content_digest"), "content_digest"),
        byte_count=_strict_int(value.get("byte_count"), "byte_count"),
        item_count=_strict_int(value.get("item_count"), "item_count"),
        projection_relative_path=(
            None
            if projection_path is None
            else _strict_text(projection_path, "projection_relative_path")
        ),
        projection_digest=(
            None
            if projection_digest is None
            else _strict_text(projection_digest, "projection_digest")
        ),
    )
    issues = _handle_issues(handle)
    if issues:
        raise EvidencePolicyValidationError(issues)
    return handle


def _writer_attestation_from_environment(
    profile: EvidencePolicyProfileV2,
) -> FrameworkEvidenceWriterAttestationV2:
    raw = os.environ.get(_WRITER_ATTESTATION_ENV)
    if not raw or len(raw.encode("utf-8")) > _PROFILE_JSON_BYTE_LIMIT:
        raise ValueError("writer attestation is missing or oversized")
    value = json.loads(raw)
    if not isinstance(value, Mapping):
        raise ValueError("writer attestation must be an object")
    writer = FrameworkEvidenceWriterAttestationV2(
        evidence_policy_fingerprint=_strict_text(
            value.get("evidence_policy_fingerprint"), "evidence_policy_fingerprint"
        ),
        writer_identity=_strict_text(value.get("writer_identity"), "writer_identity"),
        isolation_identity=_strict_text(
            value.get("isolation_identity"), "isolation_identity"
        ),
        resource_identity=_strict_text(
            value.get("resource_identity"), "resource_identity"
        ),
        _seal=_FRAMEWORK_CAPABILITY_SEAL,
    )
    _require_writer_attestation(profile, writer)
    if value.get("attestation_fingerprint") != writer.fingerprint:
        raise ValueError("writer attestation fingerprint drifted")
    if (
        os.environ.get(_ISOLATION_IDENTITY_ENV) != writer.isolation_identity
        or os.environ.get(_RESOURCE_IDENTITY_ENV) != writer.resource_identity
    ):
        raise ValueError("writer resource identity drifted")
    return writer


def _producer_capabilities_from_environment(
    profile: EvidencePolicyProfileV2,
    writer: FrameworkEvidenceWriterAttestationV2,
) -> tuple[ProducerRegistrationCapabilityV2, ...]:
    raw = os.environ.get(_PRODUCER_REGISTRATIONS_ENV)
    if not raw or len(raw.encode("utf-8")) > _PROFILE_JSON_BYTE_LIMIT:
        raise ValueError("producer capabilities are missing or oversized")
    values = json.loads(raw)
    if not isinstance(values, list) or len(values) > _PROFILE_ITEM_LIMIT:
        raise ValueError("producer capabilities must be a bounded array")
    result: list[ProducerRegistrationCapabilityV2] = []
    for value in values:
        if not isinstance(value, Mapping):
            raise ValueError("producer capability must be an object")
        root_values = _strict_mapping_tuple(value.get("artifact_roots"), "artifact_roots")
        capability = ProducerRegistrationCapabilityV2(
            evidence_policy_fingerprint=_strict_text(
                value.get("evidence_policy_fingerprint"),
                "evidence_policy_fingerprint",
            ),
            writer_attestation_fingerprint=_strict_text(
                value.get("writer_attestation_fingerprint"),
                "writer_attestation_fingerprint",
            ),
            producer_id=_strict_text(value.get("producer_id"), "producer_id"),
            artifact_roots=tuple(
                (
                    _strict_text(item.get("artifact_type"), "artifact_type"),
                    _strict_text(item.get("relative_root"), "relative_root"),
                )
                for item in root_values
            ),
            _seal=_FRAMEWORK_CAPABILITY_SEAL,
        )
        if value.get("capability_fingerprint") != capability.fingerprint:
            raise ValueError("producer capability fingerprint drifted")
        result.append(capability)
    return _validated_producer_capabilities(profile, writer, result)


def _task_response_attestation_from_dict(
    value: Mapping[str, Any],
    *,
    writer_attestation_fingerprint: str,
) -> TaskResponseAttestationV2:
    attestation = TaskResponseAttestationV2(
        writer_attestation_fingerprint=_strict_text(
            value.get("writer_attestation_fingerprint"),
            "writer_attestation_fingerprint",
        ),
        task_response_fields=_strict_string_tuple(
            value.get("task_response_fields"), "task_response_fields"
        ),
        task_response_digest=_strict_text(
            value.get("task_response_digest"), "task_response_digest"
        ),
        _seal=_FRAMEWORK_CAPABILITY_SEAL,
    )
    if (
        attestation.writer_attestation_fingerprint
        != writer_attestation_fingerprint
        or value.get("attestation_fingerprint") != attestation.fingerprint
    ):
        raise ValueError("task response attestation identity drifted")
    return attestation


def _load_framework_manifest_v2(
    manifest_path: Path,
    *,
    artifact_root: Path,
    profile: EvidencePolicyProfileV2,
    writer_attestation: FrameworkEvidenceWriterAttestationV2,
    producer_capabilities: Sequence[ProducerRegistrationCapabilityV2],
) -> tuple[tuple[EvidenceHandleV2, ...], tuple[str, ...]]:
    if not _safe_existing_directory(artifact_root):
        raise ValueError("framework evidence artifact root is unsafe")
    try:
        relative_manifest = manifest_path.absolute().relative_to(
            artifact_root.absolute()
        ).as_posix()
    except ValueError as exc:
        raise ValueError("framework evidence manifest escaped artifact root") from exc
    raw = _read_bounded_relative_file(
        artifact_root, relative_manifest, _MANIFEST_JSON_BYTE_LIMIT
    )
    value = json.loads(raw)
    if not isinstance(value, Mapping):
        raise ValueError("framework evidence manifest must be an object")
    if value.get("schema_version") != FRAMEWORK_EVIDENCE_MANIFEST_SCHEMA_VERSION:
        raise ValueError("framework evidence manifest schema is invalid")
    if value.get("evidence_policy_fingerprint") != profile.fingerprint:
        raise ValueError("framework evidence manifest policy fingerprint drifted")
    if value.get("writer_attestation_fingerprint") != writer_attestation.fingerprint:
        raise ValueError("framework evidence manifest writer identity drifted")
    capability_fingerprints = _strict_string_tuple(
        value.get("producer_capability_fingerprints"),
        "producer_capability_fingerprints",
    )
    if capability_fingerprints != tuple(
        sorted(item.fingerprint for item in producer_capabilities)
    ):
        raise ValueError("framework evidence producer capabilities drifted")
    task_response_value = value.get("task_response_attestation")
    if not isinstance(task_response_value, Mapping):
        raise ValueError("task response attestation is missing")
    task_attestation = _task_response_attestation_from_dict(
        task_response_value,
        writer_attestation_fingerprint=writer_attestation.fingerprint,
    )
    handle_values = _strict_mapping_tuple(value.get("handles"), "handles")
    handles = tuple(_handle_from_dict(item) for item in handle_values)
    fields = _strict_string_tuple(
        value.get("task_response_fields"), "task_response_fields"
    )
    if set(fields) - set(profile.required_task_response_fields):
        raise ValueError("manifest declares unrequested task response fields")
    if fields != task_attestation.task_response_fields:
        raise ValueError("manifest task fields do not match task response attestation")
    issues = list(_handle_policy_issues(profile, handles, require_required=True))
    issues.extend(
        _verify_handle_files(
            artifact_root,
            handles,
            producer_capabilities,
            profile,
        )
    )
    if issues:
        raise EvidencePolicyValidationError(issues)
    return handles, fields


@dataclass(frozen=True)
class ReplayRuntimePolicy:
    """Typed, payload-free policy compiled from the replay environment."""

    artifact_file_limit: int
    artifact_byte_limit: int
    max_consecutive_failed_actions: int
    allowed_loopback_endpoints: frozenset[str]
    allowed_control_actions: frozenset[str]
    allowed_loopback_bindings: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_profile(cls, profile: EvidencePolicyProfileV2) -> "ReplayRuntimePolicy":
        _require_valid_profile(profile)
        return cls(
            artifact_file_limit=sum(item.max_files for item in profile.artifact_policies),
            artifact_byte_limit=sum(item.max_bytes for item in profile.artifact_policies),
            max_consecutive_failed_actions=profile.max_consecutive_failed_actions,
            allowed_loopback_endpoints=frozenset(
                item.authority for item in profile.endpoint_bindings
            ),
            allowed_control_actions=frozenset(profile.allowed_control_actions),
            allowed_loopback_bindings=tuple(
                sorted((item.endpoint, item.path_scope) for item in profile.endpoint_bindings)
            ),
        )

    @classmethod
    def from_environment(cls) -> "ReplayRuntimePolicy":
        profile = evidence_policy_profile_v2_from_environment()
        if profile is not None:
            return cls.from_profile(profile)
        return cls(
            artifact_file_limit=_positive_limit(
                "AWORLD_REPLAY_ARTIFACT_FILE_LIMIT",
                default=8,
            ),
            artifact_byte_limit=_positive_limit(
                "AWORLD_REPLAY_ARTIFACT_BYTE_LIMIT",
                default=2_000_000,
            ),
            max_consecutive_failed_actions=_positive_limit(
                "AWORLD_REPLAY_MAX_CONSECUTIVE_FAILED_ACTIONS",
                default=2,
            ),
            allowed_loopback_endpoints=frozenset(
                endpoint
                for name, value in os.environ.items()
                if name.startswith("AWORLD_REPLAY_ENDPOINT_")
                for endpoint in _loopback_endpoints(value)
            ),
            allowed_control_actions=frozenset(
                token.strip().casefold()
                for token in os.environ.get(
                    "AWORLD_REPLAY_ALLOWED_CONTROL_ACTIONS", ""
                ).split(",")
                if token.strip()
            ),
            allowed_loopback_bindings=(),
        )

    def public_state(self) -> dict[str, Any]:
        return {
            "artifact_file_limit": self.artifact_file_limit,
            "artifact_byte_limit": self.artifact_byte_limit,
            "max_consecutive_failed_actions": (
                self.max_consecutive_failed_actions
            ),
            "allowed_loopback_endpoint_count": len(
                self.allowed_loopback_endpoints
            ),
            "allowed_control_action_count": len(
                self.allowed_control_actions
            ),
        }


def enforce_replay_evidence_runtime_policy(
    tool_name: str,
    actions: Iterable[Any],
    message: Any,
) -> str | None:
    """Enforce replay evidence collection and return a violation code.

    The policy is opt-in for self-evolve replay subprocesses. It operates on
    bounded lifecycle metadata only; tool arguments and evidence payloads are
    never persisted in its state or violation records.
    """

    if os.environ.get("AWORLD_REPLAY_EVIDENCE_POLICY") != "1":
        return None
    mode = str(os.environ.get(_POLICY_MODE_ENV) or "").casefold()
    if mode not in _REQUIRED_POLICY_MODES | _LEGACY_POLICY_MODES:
        return "evidence_policy_mode_required"
    required_v2 = mode in _REQUIRED_POLICY_MODES
    artifact_root_value = os.environ.get(
        "AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR"
    )
    manifest_value = os.environ.get("AWORLD_SELF_EVOLVE_EVIDENCE_MANIFEST")
    if not artifact_root_value or not manifest_value:
        if required_v2:
            return (
                "evidence_policy_artifact_root_missing"
                if not artifact_root_value
                else "evidence_policy_manifest_path_missing"
            )
        return None
    action_items = tuple(actions or ())
    artifact_root = Path(artifact_root_value)
    manifest_path = Path(manifest_value)
    if artifact_root.exists() and not _safe_existing_directory(artifact_root):
        return "evidence_policy_artifact_root_unsafe"
    if not artifact_root.exists():
        if required_v2:
            return "evidence_policy_artifact_root_unavailable"
        artifact_root.mkdir(parents=True, exist_ok=True)
    if required_v2:
        try:
            manifest_relative = manifest_path.absolute().relative_to(
                artifact_root.absolute()
            )
        except ValueError:
            return "evidence_policy_manifest_path_unsafe"
        if not _safe_relative_path(manifest_relative.as_posix()) or not _safe_existing_directory(
            manifest_path.parent
        ):
            return "evidence_policy_manifest_path_unsafe"
    try:
        profile = evidence_policy_profile_v2_from_environment()
    except EvidencePolicyValidationError:
        return "evidence_policy_profile_invalid"
    if required_v2 and profile is None:
        return "evidence_policy_profile_missing"
    preflight_violation: str | None = None
    manifest_entry_count = 0
    lifecycle_phase = EvidenceLifecyclePhase.COLLECTING
    writer_attestation: FrameworkEvidenceWriterAttestationV2 | None = None
    producer_capabilities: tuple[ProducerRegistrationCapabilityV2, ...] = ()
    typed_inventory: dict[str, tuple[int, int]] = {}
    inventory_issues: tuple[EvidencePolicyIssue, ...] = ()
    if required_v2:
        try:
            writer_attestation = _writer_attestation_from_environment(profile)  # type: ignore[arg-type]
            producer_capabilities = _producer_capabilities_from_environment(
                profile, writer_attestation  # type: ignore[arg-type]
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            return "evidence_policy_producer_registration_missing"
        resolved_bindings = {
            item.binding_id: os.environ.get(item.environment_name, "")
            for item in profile.endpoint_bindings  # type: ignore[union-attr]
        }
        preflight = preflight_evidence_policy_v2(
            profile,  # type: ignore[arg-type]
            artifact_root=artifact_root,
            available_producers=(item.producer_id for item in producer_capabilities),
            resolved_endpoint_bindings=resolved_bindings,
            producer_capabilities=producer_capabilities,
        )
        if not preflight.passed:
            return "evidence_policy_preflight_failed"
        typed_inventory, inventory_issues = _producer_inventory(
            artifact_root,
            profile,  # type: ignore[arg-type]
            producer_capabilities,
        )
        if manifest_path.exists():
            try:
                handles, task_fields = _load_framework_manifest_v2(
                    manifest_path,
                    artifact_root=artifact_root,
                    profile=profile,  # type: ignore[arg-type]
                    writer_attestation=writer_attestation,
                    producer_capabilities=producer_capabilities,
                )
                lifecycle = determine_evidence_lifecycle_v2(
                    profile,  # type: ignore[arg-type]
                    handles=handles,
                    task_response={field: True for field in task_fields},
                )
                lifecycle_phase = lifecycle.phase
                manifest_entry_count = len(handles)
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                preflight_violation = "evidence_manifest_v2_invalid"
        # A missing manifest file means evidence is still collecting. A legacy
        # JSONL file never becomes ready because v2 parsing above fails closed.
    else:
        manifest_entry_count = _valid_manifest_entry_count(
            manifest_path,
            artifact_root=artifact_root,
        )
        if manifest_entry_count:
            lifecycle_phase = EvidenceLifecyclePhase.EVIDENCE_READY
    if required_v2:
        artifact_file_count = sum(item[0] for item in typed_inventory.values())
        artifact_bytes = sum(item[1] for item in typed_inventory.values())
    else:
        artifact_file_count, artifact_bytes = _artifact_inventory(artifact_root)
    try:
        policy = ReplayRuntimePolicy.from_environment()
    except EvidencePolicyValidationError:
        return "evidence_policy_profile_invalid"
    owner = getattr(message, "context", None) or message
    try:
        prior_state = _read_state(artifact_root, strict=required_v2)
    except (OSError, TypeError, ValueError):
        return "evidence_policy_state_invalid"
    attempt_count = max(
        int(prior_state.get("tool_call_attempt_count", 0) or 0),
        int(
            getattr(owner, "_aworld_replay_evidence_policy_attempt_count", 0)
            or 0
        ),
    ) + len(action_items)
    setattr(
        owner,
        "_aworld_replay_evidence_policy_attempt_count",
        attempt_count,
    )
    phase = lifecycle_phase.value
    state = {
        "schema_version": REPLAY_EVIDENCE_POLICY_SCHEMA_VERSION,
        "enforcement": "tool_boundary",
        "phase": phase,
        "tool_call_attempt_count": attempt_count,
        "manifest_entry_count": manifest_entry_count,
        "evidence_policy_mode": mode,
        "evidence_policy_profile_fingerprint": (
            profile.fingerprint if profile is not None else None
        ),
        "artifact_file_count": artifact_file_count,
        "artifact_bytes": artifact_bytes,
        "artifact_type_inventory": {
            artifact_type: {"file_count": values[0], "byte_count": values[1]}
            for artifact_type, values in sorted(typed_inventory.items())
        },
        "writer_attestation_fingerprint": (
            writer_attestation.fingerprint if writer_attestation else None
        ),
        "isolation_identity": os.environ.get(_ISOLATION_IDENTITY_ENV),
        "resource_identity": os.environ.get(_RESOURCE_IDENTITY_ENV),
        "finalization_action_count": int(
            prior_state.get("finalization_action_count", 0) or 0
        ),
        "last_failed_action_fingerprint": (
            getattr(
                owner,
                "_aworld_replay_last_failed_action_fingerprint",
                None,
            )
            or None
        ),
        "consecutive_failed_action_count": int(
            getattr(
                owner,
                "_aworld_replay_consecutive_failed_action_count",
                0,
            )
            or 0
        ),
        **policy.public_state(),
    }
    if not _write_state(artifact_root, state) and required_v2:
        return "evidence_policy_state_persistence_failed"

    violation_code: str | None = None
    violation_metadata: dict[str, Any] = {}
    if phase == EvidenceLifecyclePhase.EVIDENCE_READY.value and _allow_single_evidence_ready_cleanup(
        action_items,
        owner=owner,
        state=state,
    ):
        state.update(
            {
                "phase": "finalizing",
                "finalization_action_count": 1,
            }
        )
        if not _write_state(artifact_root, state):
            return "evidence_policy_state_persistence_failed"
        setattr(owner, "_aworld_replay_finalization_action_count", 1)
        return None
    if preflight_violation is not None:
        violation_code = preflight_violation
    elif phase == EvidenceLifecyclePhase.EVIDENCE_READY.value:
        violation_code = "tool_call_after_evidence_ready"
    elif inventory_issues:
        violation_code = inventory_issues[0].code
        violation_metadata = {"artifact_type": inventory_issues[0].field}
    elif (
        artifact_file_count >= policy.artifact_file_limit
        and not _actions_are_provably_artifact_read_only(action_items)
    ):
        violation_code = "artifact_file_limit_exhausted"
    elif (
        artifact_bytes >= policy.artifact_byte_limit
        and not _actions_are_provably_artifact_read_only(action_items)
    ):
        violation_code = "artifact_byte_limit_exhausted"
    else:
        for item in action_items:
            violation_code, violation_metadata = _action_policy_violation(
                item,
                owner=owner,
                policy=policy,
            )
            if violation_code is not None:
                break
    if violation_code is None:
        return None

    first_action = action_items[0] if action_items else None
    violation = {
        **state,
        "code": violation_code,
        "tool_name": str(tool_name or "unknown")[:128],
        "action_name": str(
            getattr(first_action, "action_name", None) or "unknown"
        )[:128],
        **violation_metadata,
        "required_transition": _required_transition(
            violation_code,
            phase=phase,
        ),
    }
    if not _append_violation(artifact_root, violation) and required_v2:
        return "evidence_policy_violation_persistence_failed"
    return violation_code


def _allow_single_evidence_ready_cleanup(
    actions: tuple[Any, ...],
    *,
    owner: Any,
    state: Mapping[str, Any],
) -> bool:
    """Allow one narrow cleanup of a replay-owned browser after evidence.

    Evidence-ready blocks further collection, but replay-created resources may
    still be released.  The prior blanket denial converted ``agent-browser
    close`` into a task failure and triggered an expensive evidence retry.  A
    single exact cleanup action is safe because replay subprocesses use isolated
    runtime roots; arbitrary shell, host-control, and repeated actions remain
    denied.
    """

    if len(actions) != 1:
        return False
    if int(state.get("finalization_action_count", 0) or 0) or int(
        getattr(owner, "_aworld_replay_finalization_action_count", 0) or 0
    ):
        return False
    expected_token = os.environ.get(_RESOURCE_OWNERSHIP_TOKEN_ENV)
    expected_isolation = os.environ.get(_ISOLATION_IDENTITY_ENV)
    expected_resource = os.environ.get(_RESOURCE_IDENTITY_ENV)
    if (
        not expected_token
        or not expected_isolation
        or not expected_resource
        or not _cleanup_ownership_matches(
            actions[0], expected_token, expected_isolation, expected_resource
        )
    ):
        return False
    if not _is_replay_owned_browser_cleanup(actions[0]):
        return False
    return True


def _cleanup_ownership_matches(
    action: Any,
    expected_token: str,
    expected_isolation: str,
    expected_resource: str,
) -> bool:
    params = getattr(action, "params", None)
    return bool(
        isinstance(params, Mapping)
        and params.get("resource_ownership_token") == expected_token
        and params.get("isolation_identity") == expected_isolation
        and params.get("resource_identity") == expected_resource
    )


def _is_replay_owned_browser_cleanup(action: Any) -> bool:
    command_texts = _command_texts(action)
    if len(command_texts) != 1:
        return False
    try:
        tokens = shlex.split(command_texts[0])
    except ValueError:
        return False
    if len(tokens) < 2 or len(tokens) > 5:
        return False
    binary = Path(tokens[0]).name.casefold()
    cleanup_action = tokens[1].casefold()
    return bool(
        binary in _REPLAY_OWNED_BROWSER_CLEANUP_BINARIES
        and cleanup_action in _REPLAY_OWNED_BROWSER_CLEANUP_ACTIONS
        and all(
            _SAFE_CLEANUP_REDIRECTION.fullmatch(token) is not None
            for token in tokens[2:]
        )
    )


def record_replay_runtime_tool_result(
    actions: Iterable[Any],
    result: Any,
    message: Any,
) -> None:
    """Record consecutive failed action paths without retaining payloads."""

    if os.environ.get("AWORLD_REPLAY_EVIDENCE_POLICY") != "1":
        return
    artifact_root_value = os.environ.get(
        "AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR"
    )
    if not artifact_root_value:
        return
    action_items = tuple(actions or ())
    action_results = _action_results(result)
    if not action_items or not action_results:
        return
    owner = getattr(message, "context", None) or message
    last_fingerprint = str(
        getattr(owner, "_aworld_replay_last_failed_action_fingerprint", "")
        or ""
    )
    consecutive_count = int(
        getattr(owner, "_aworld_replay_consecutive_failed_action_count", 0)
        or 0
    )
    for action, action_result in zip(action_items, action_results):
        fingerprint = _action_fingerprint(action)
        if _action_result_succeeded(action_result):
            last_fingerprint = ""
            consecutive_count = 0
            continue
        if fingerprint == last_fingerprint:
            consecutive_count += 1
        else:
            last_fingerprint = fingerprint
            consecutive_count = 1
    setattr(
        owner,
        "_aworld_replay_last_failed_action_fingerprint",
        last_fingerprint,
    )
    setattr(
        owner,
        "_aworld_replay_consecutive_failed_action_count",
        consecutive_count,
    )
    artifact_root = Path(artifact_root_value)
    required_v2 = str(os.environ.get(_POLICY_MODE_ENV) or "").casefold() in (
        _REQUIRED_POLICY_MODES
    )
    state = _read_state(artifact_root, strict=required_v2)
    state.update(
        {
            "schema_version": REPLAY_EVIDENCE_POLICY_SCHEMA_VERSION,
            "last_failed_action_fingerprint": last_fingerprint or None,
            "consecutive_failed_action_count": consecutive_count,
        }
    )
    if not _write_state(artifact_root, state) and required_v2:
        raise RuntimeError("required evidence policy state persistence failed")


def _action_policy_violation(
    action: Any,
    *,
    owner: Any,
    policy: ReplayRuntimePolicy,
) -> tuple[str | None, dict[str, Any]]:
    fingerprint = _action_fingerprint(action)
    last_fingerprint = str(
        getattr(owner, "_aworld_replay_last_failed_action_fingerprint", "")
        or ""
    )
    consecutive_failures = int(
        getattr(owner, "_aworld_replay_consecutive_failed_action_count", 0)
        or 0
    )
    if (
        fingerprint == last_fingerprint
        and consecutive_failures >= policy.max_consecutive_failed_actions
    ):
        return "repeated_failed_action_limit", {
            "action_fingerprint": fingerprint,
            "consecutive_failure_count": consecutive_failures,
        }

    serialized = _checked_action_parameters(action)
    if serialized is None:
        return "action_parameters_uninspectable", {
            "action_fingerprint": fingerprint,
        }
    observed_bindings = _loopback_bindings(serialized)
    observed_endpoints = frozenset(
        _normalized_endpoint_authority(item) for item in observed_bindings
    )
    if policy.allowed_loopback_bindings:
        undeclared_endpoints = frozenset(
            item
            for item in observed_bindings
            if not any(
                _endpoint_binding_allows(declared, item, path_scope=scope)
                for declared, scope in policy.allowed_loopback_bindings
            )
        )
    else:
        undeclared_endpoints = observed_endpoints - policy.allowed_loopback_endpoints
    if undeclared_endpoints:
        return "undeclared_loopback_endpoint", {
            "action_fingerprint": fingerprint,
            "observed_endpoint_count": len(observed_bindings),
            "undeclared_endpoint_count": len(undeclared_endpoints),
        }

    command_texts = _command_texts(action)
    if _protected_runtime_root_override(action, command_texts):
        return "protected_runtime_root_override", {
            "action_fingerprint": fingerprint,
        }

    control_actions = _control_plane_actions(action, command_texts)
    unauthorized = {
        item
        for item in control_actions
        if "*" not in policy.allowed_control_actions
        and item not in policy.allowed_control_actions
    }
    if unauthorized:
        return "unauthorized_control_plane_action", {
            "action_fingerprint": fingerprint,
            "control_action_count": len(unauthorized),
        }
    discovery_actions = _host_discovery_actions(command_texts)
    unauthorized_discovery = {
        item
        for item in discovery_actions
        if "*" not in policy.allowed_control_actions
        and item not in policy.allowed_control_actions
    }
    if unauthorized_discovery:
        return "host_discovery_forbidden", {
            "action_fingerprint": fingerprint,
            "control_action_count": len(unauthorized_discovery),
        }
    return None, {}


def _required_transition(code: str, *, phase: str) -> str:
    transitions = {
        "tool_call_after_evidence_ready": "finalize_task_response",
        "artifact_file_limit_exhausted": (
            "persist_bounded_evidence_or_reduce_collection"
        ),
        "artifact_byte_limit_exhausted": (
            "persist_bounded_evidence_or_reduce_collection"
        ),
        "repeated_failed_action_limit": (
            "switch_strategy_or_fail_with_observed_reason"
        ),
        "undeclared_loopback_endpoint": (
            "use_declared_replay_endpoint_or_fail_prerequisite"
        ),
        "protected_runtime_root_override": "preserve_isolated_runtime_roots",
        "unauthorized_control_plane_action": (
            "attach_without_control_plane_mutation"
        ),
        "host_discovery_forbidden": (
            "use_declared_replay_endpoint_or_fail_prerequisite"
        ),
        "action_parameters_uninspectable": "reduce_action_parameter_size",
        "evidence_manifest_v2_invalid": "rebuild_framework_evidence_manifest",
    }
    return transitions.get(
        code,
        (
            "finalize_task_response"
            if phase == "evidence_ready"
            else "satisfy_replay_runtime_policy"
        ),
    )


def _action_fingerprint(action: Any) -> str:
    payload = {
        "tool_name": str(getattr(action, "tool_name", None) or ""),
        "action_name": str(getattr(action, "action_name", None) or ""),
        "params": getattr(action, "params", None),
    }
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    except (TypeError, ValueError):
        encoded = repr(payload).encode("utf-8", errors="replace")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


_ARTIFACT_READ_ONLY_ACTION_PREFIXES = (
    "get",
    "inspect",
    "list",
    "query",
    "read",
    "search",
    "view",
)
_ARTIFACT_MUTATING_PARAMETER_MARKERS = (
    "append",
    "command",
    "create",
    "destination",
    "download",
    "output",
    "save",
    "script",
    "target",
    "write",
)


def _actions_are_provably_artifact_read_only(actions: tuple[Any, ...]) -> bool:
    """Allow bounded analysis after quota while keeping collection fail-closed.

    Artifact quotas constrain growth, not use of evidence already collected.
    Only actions with a recognized read-only verb, no shell command, and no
    output-like parameter are admitted. Unknown actions remain mutating.
    """

    if not actions:
        return False
    for action in actions:
        action_name = re.sub(
            r"[^a-z0-9]+",
            "_",
            str(getattr(action, "action_name", None) or "").casefold(),
        ).strip("_")
        if not action_name or not any(
            action_name == prefix or action_name.startswith(prefix + "_")
            for prefix in _ARTIFACT_READ_ONLY_ACTION_PREFIXES
        ):
            return False
        if _command_texts(action):
            return False
        params = getattr(action, "params", None)
        if params is not None and not isinstance(params, Mapping):
            return False
        if isinstance(params, Mapping):
            for raw_key in params:
                normalized_key = re.sub(
                    r"[^a-z0-9]+", "_", str(raw_key).casefold()
                ).strip("_")
                if any(
                    marker in normalized_key
                    for marker in _ARTIFACT_MUTATING_PARAMETER_MARKERS
                ):
                    return False
    return True


def _checked_action_parameters(action: Any) -> str | None:
    try:
        serialized = json.dumps(
            getattr(action, "params", None),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    except (TypeError, ValueError):
        serialized = repr(getattr(action, "params", None))
    if len(serialized.encode("utf-8", errors="replace")) > _ACTION_PARAMETER_BYTE_LIMIT:
        return None
    return serialized


def _loopback_bindings(value: str) -> frozenset[str]:
    result: set[str] = set()
    for match in _URL_ENDPOINT_PATTERN.finditer(str(value or "")):
        raw = match.group(0)
        try:
            result.add(_normalize_loopback(raw))
        except ValueError:
            if _looks_like_loopback_alias(raw):
                # Numeric aliases and malformed canonical loopback URLs are
                # observable violations, not external endpoints to ignore.
                result.add("invalid:" + raw.casefold())
    return frozenset(result)


def _endpoint_binding_allows(
    declared: str,
    observed: str,
    *,
    path_scope: str,
) -> bool:
    try:
        expected = urlsplit(_normalize_loopback(declared))
        actual = urlsplit(_normalize_loopback(observed))
    except ValueError:
        return False
    if (expected.scheme, expected.hostname, expected.port) != (
        actual.scheme,
        actual.hostname,
        actual.port,
    ):
        return False
    expected_path = expected.path.rstrip("/")
    actual_path = actual.path.rstrip("/")
    if path_scope == "exact":
        return actual_path == expected_path
    if path_scope != "prefix":
        return False
    return not expected_path or actual_path == expected_path or actual_path.startswith(
        expected_path + "/"
    )


def _loopback_endpoints(value: str) -> frozenset[str]:
    return frozenset(
        _normalized_endpoint_authority(item)
        for item in _loopback_bindings(str(value or ""))
        if not item.startswith("invalid:")
    )


def _normalized_endpoint_authority(value: str) -> str:
    if value.startswith("invalid:"):
        return value
    parsed = urlsplit(_normalize_loopback(value))
    host = parsed.hostname or ""
    rendered_host = f"[{host}]" if host == "::1" else host
    return f"{rendered_host}:{parsed.port}"


def _looks_like_loopback_alias(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").casefold()
    except ValueError:
        return False
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    if re.fullmatch(r"[0-9a-fx.:]+", host) is None:
        return False
    try:
        packed = socket.inet_aton(host)
        return packed[0] == 127
    except OSError:
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            try:
                numeric = int(host, 0)
            except ValueError:
                return False
            return 0 <= numeric <= 0xFFFFFFFF and (numeric >> 24) == 127


def _loopback_authority(match: re.Match[str]) -> str:
    scheme = match.group("scheme").casefold()
    host = match.group("host").casefold()
    port = match.group("port")
    if port is None:
        port = "443" if scheme in {"https", "wss"} else "80"
    return f"{host}:{port}"


def _protected_runtime_root_override(
    action: Any,
    command_texts: Iterable[str],
) -> bool:
    if any(_PROTECTED_RUNTIME_ASSIGNMENT.search(text) for text in command_texts):
        return True
    params = getattr(action, "params", None)
    if not isinstance(params, Mapping):
        return False
    for raw_key, value in params.items():
        key = str(raw_key).casefold()
        if key in _PROTECTED_RUNTIME_ROOT_KEYS:
            return True
        if key not in _ENVIRONMENT_PARAMETER_KEYS or not isinstance(value, Mapping):
            continue
        if any(
            str(environment_key).casefold() in _PROTECTED_RUNTIME_ROOT_KEYS
            for environment_key in value
        ):
            return True
    return False


def _command_texts(action: Any) -> tuple[str, ...]:
    params = getattr(action, "params", None)
    result: list[str] = []

    def visit(value: Any, *, key: str | None = None, depth: int = 0) -> None:
        if depth > 4 or len(result) >= 16:
            return
        if isinstance(value, Mapping):
            for raw_key, nested in list(value.items())[:64]:
                visit(nested, key=str(raw_key).casefold(), depth=depth + 1)
        elif isinstance(value, (list, tuple)):
            for nested in value[:64]:
                visit(nested, key=key, depth=depth + 1)
        elif key in _COMMAND_PARAMETER_KEYS and isinstance(value, str):
            result.append(value)

    visit(params)
    return tuple(result)


def _control_plane_actions(
    action: Any,
    command_texts: Iterable[str],
) -> frozenset[str]:
    result: set[str] = set()
    action_name = str(getattr(action, "action_name", None) or "").casefold()
    if action_name in _CONTROL_PLANE_ACTION_NAMES:
        result.add(action_name)
    for text in command_texts:
        for match in _CONTROL_PLANE_COMMAND.finditer(text):
            command = match.group("command")
            if command:
                result.add(command.casefold())
                continue
            container = match.group("container")
            container_action = match.group("container_action")
            if container and container_action:
                result.add(
                    f"{container.casefold()}:{container_action.casefold()}"
                )
    return frozenset(result)


def _host_discovery_actions(
    command_texts: Iterable[str],
) -> frozenset[str]:
    return frozenset(
        match.group("command").casefold()
        for text in command_texts
        for match in _HOST_DISCOVERY_COMMAND.finditer(text)
        if match.group("command")
    )


def _action_results(result: Any) -> tuple[Any, ...]:
    if not isinstance(result, tuple) or not result:
        return ()
    observation = result[0]
    raw = getattr(observation, "action_result", None)
    return tuple(raw) if isinstance(raw, list) else ()


def _action_result_succeeded(result: Any) -> bool:
    return bool(
        getattr(result, "success", False)
        and not getattr(result, "error", None)
    )


def _positive_limit(name: str, *, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _valid_manifest_entry_count(
    manifest_path: Path,
    *,
    artifact_root: Path,
) -> int:
    try:
        raw = manifest_path.read_bytes()[:131_072].decode(
            "utf-8", errors="replace"
        )
    except OSError:
        return 0
    count = 0
    root = artifact_root.resolve(strict=False)
    for line in raw.splitlines()[:32]:
        try:
            entry = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(entry, Mapping):
            continue
        if not str(entry.get("source_id") or "").strip():
            continue
        if not str(entry.get("extraction_method") or "").strip():
            continue
        if not any(entry.get(key) for key in _MANIFEST_PAYLOAD_KEYS):
            continue
        artifact_path = entry.get("artifact_path")
        if isinstance(artifact_path, str) and artifact_path.strip():
            candidate = Path(artifact_path).expanduser()
            if not candidate.is_absolute():
                candidate = artifact_root / candidate
            resolved = candidate.resolve(strict=False)
            if not resolved.is_relative_to(root) or not resolved.is_file():
                continue
        count += 1
    return count


def _artifact_inventory(artifact_root: Path) -> tuple[int, int]:
    file_count = 0
    total_bytes = 0
    for current_root, directories, filenames in os.walk(
        artifact_root,
        followlinks=False,
    ):
        directories[:] = [
            name
            for name in directories[:64]
            if name != "logs" and not (Path(current_root) / name).is_symlink()
        ]
        for name in filenames[:256]:
            if name in _CONTROL_FILES:
                continue
            path = Path(current_root) / name
            try:
                size = path.stat().st_size
            except OSError:
                continue
            file_count += 1
            total_bytes += max(0, size)
            if file_count >= 256:
                return file_count, total_bytes
    return file_count, total_bytes


def _producer_inventory(
    artifact_root: Path,
    profile: EvidencePolicyProfileV2,
    capabilities: Sequence[ProducerRegistrationCapabilityV2],
) -> tuple[dict[str, tuple[int, int]], tuple[EvidencePolicyIssue, ...]]:
    policies = {item.artifact_type: item for item in profile.artifact_policies}
    totals: dict[str, tuple[int, int]] = {}
    issues: list[EvidencePolicyIssue] = []
    for capability in capabilities:
        for artifact_type, relative_root in capability.artifact_roots:
            policy = policies.get(artifact_type)
            if policy is None:
                issues.append(EvidencePolicyIssue("undeclared_artifact", artifact_type))
                continue
            producer_root = artifact_root / relative_root
            if not _safe_existing_directory(producer_root):
                issues.append(
                    EvidencePolicyIssue("producer_root_unavailable", artifact_type, "infrastructure")
                )
                continue
            prior_file_count, prior_byte_count = totals.get(
                artifact_type, (0, 0)
            )
            file_count = 0
            byte_count = 0
            directory_count = 0
            directory_limit = max(64, policy.max_files * 4)
            for current_root, directories, filenames in os.walk(
                producer_root, followlinks=False
            ):
                directory_count += 1
                if directory_count > directory_limit:
                    issues.append(
                        EvidencePolicyIssue("producer_inventory_directory_limit", artifact_type)
                    )
                    break
                unsafe_directory = any(
                    (Path(current_root) / name).is_symlink() for name in directories
                )
                directories[:] = [
                    name
                    for name in directories
                    if not (Path(current_root) / name).is_symlink()
                ]
                if unsafe_directory:
                    issues.append(
                        EvidencePolicyIssue("producer_inventory_symlink", artifact_type)
                    )
                for name in filenames:
                    path = Path(current_root) / name
                    try:
                        details = path.lstat()
                    except OSError:
                        issues.append(
                            EvidencePolicyIssue("producer_inventory_unreadable", artifact_type)
                        )
                        continue
                    if not stat.S_ISREG(details.st_mode) or path.is_symlink():
                        issues.append(
                            EvidencePolicyIssue("producer_inventory_unsafe_file", artifact_type)
                        )
                        continue
                    file_count += 1
                    byte_count += max(0, details.st_size)
                    if (
                        prior_file_count + file_count > policy.max_files
                        or prior_byte_count + byte_count > policy.max_bytes
                    ):
                        break
                if (
                    prior_file_count + file_count > policy.max_files
                    or prior_byte_count + byte_count > policy.max_bytes
                ):
                    break
            totals[artifact_type] = (
                prior_file_count + file_count,
                prior_byte_count + byte_count,
            )
    for artifact_type, (file_count, byte_count) in totals.items():
        policy = policies[artifact_type]
        if file_count > policy.max_files:
            issues.append(
                EvidencePolicyIssue(
                    "artifact_file_budget_exceeded", artifact_type
                )
            )
        if byte_count > policy.max_bytes:
            issues.append(
                EvidencePolicyIssue(
                    "artifact_byte_budget_exceeded", artifact_type
                )
            )
    return totals, tuple(issues)


def _write_state(
    artifact_root: Path,
    state: Mapping[str, Any],
) -> bool:
    try:
        encoded = json.dumps(
            state,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > _CONTROL_FILE_BYTE_LIMIT:
            return False
        _secure_atomic_control_write(
            artifact_root, "framework_evidence_state.json", encoded
        )
        return True
    except (OSError, ValueError):
        return False


def _read_state(
    artifact_root: Path,
    *,
    strict: bool = False,
) -> dict[str, Any]:
    path = artifact_root / "framework_evidence_state.json"
    if not path.exists():
        return {}
    try:
        value = json.loads(
            _read_bounded_relative_file(
                artifact_root,
                "framework_evidence_state.json",
                _CONTROL_FILE_BYTE_LIMIT,
            )
        )
    except (OSError, ValueError, json.JSONDecodeError):
        if strict:
            raise ValueError("evidence policy state is invalid")
        return {}
    if not isinstance(value, Mapping):
        if strict:
            raise ValueError("evidence policy state must be an object")
        return {}
    return dict(value)


def _append_violation(
    artifact_root: Path,
    violation: Mapping[str, Any],
) -> bool:
    try:
        path = artifact_root / "framework_evidence_policy.jsonl"
        existing = (
            _read_bounded_relative_file(
                artifact_root,
                "framework_evidence_policy.jsonl",
                _CONTROL_FILE_BYTE_LIMIT,
            )
            if path.exists()
            else b""
        )
        encoded = (
            json.dumps(
                violation,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        if len(existing) + len(encoded) > _CONTROL_FILE_BYTE_LIMIT:
            return False
        _secure_atomic_control_write(
            artifact_root,
            "framework_evidence_policy.jsonl",
            existing + encoded,
        )
        return True
    except (OSError, ValueError):
        return False


def _secure_atomic_control_write(
    artifact_root: Path,
    filename: str,
    payload: bytes,
) -> None:
    if filename not in {
        "framework_evidence_state.json",
        "framework_evidence_policy.jsonl",
    } or not _safe_existing_directory(artifact_root):
        raise ValueError("unsafe evidence control destination")
    target = artifact_root / filename
    if target.is_symlink():
        raise ValueError("evidence control destination cannot be a symlink")
    temporary_name = f".{filename}.{uuid.uuid4().hex}.tmp"
    root_fd = os.open(
        artifact_root,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary_name,
            os.O_CREAT
            | os.O_EXCL
            | os.O_WRONLY
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=root_fd,
        )
        view = memoryview(payload)
        offset = 0
        while offset < len(view):
            written = os.write(descriptor, view[offset:])
            if written <= 0:
                raise OSError("evidence control short write made no progress")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        if target.is_symlink():
            raise ValueError("evidence control destination became a symlink")
        os.replace(temporary_name, filename, src_dir_fd=root_fd, dst_dir_fd=root_fd)
        os.fsync(root_fd)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=root_fd)
        except FileNotFoundError:
            pass
        os.close(root_fd)
