from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import unicodedata
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Mapping, Protocol, Sequence

from aworld.self_evolve.datasets import EvalCase, SelfEvolveDataset
from aworld.self_evolve.trajectory_context import task_input_requires_prior_context

if TYPE_CHECKING:
    from aworld.self_evolve.replay_capability import FrozenReplayCapability


REPLAY_ADAPTATION_SCHEMA_VERSION = "aworld.self_evolve.replay_adaptation.v1"
REPLAY_PREFLIGHT_SCHEMA_VERSION = "aworld.self_evolve.replay_preflight.v1"
ISOLATION_GRANT_SCHEMA_VERSION = "aworld.self_evolve.isolation_grant.v1"
REPLAY_WORKSPACE_PLACEHOLDER = "${AWORLD_REPLAY_WORKSPACE}"
REPLAY_ARTIFACT_PLACEHOLDER = "${AWORLD_REPLAY_ARTIFACT_DIR}"

_IGNORED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".aworld",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
    }
)
_IGNORED_FILE_SUFFIXES = (".pyc", ".pyo")
_SENSITIVE_NAMES = frozenset(
    {
        ".env",
        "credentials",
        "credentials.json",
        "secrets.json",
        "id_rsa",
        "id_ed25519",
    }
)
_SENSITIVE_ENV_KEY = re.compile(
    r"(?i)(?:secret|token|password|credential|authorization|cookie|api[_-]?key)"
)
_LOCAL_ENDPOINT = re.compile(
    r"https?://(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?[^\s\"'<>]*",
    re.IGNORECASE,
)
_HTTP_RESOURCE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_ABSOLUTE_LOCAL_PATH = re.compile(
    r"(?<![.:/\w${}])/(?!/)[^\s\"'<>|;,)}\]]+"
)
_NON_FILE_PATH_CONTEXT = re.compile(
    r"(?i)(?:"
    r"\b(?:get|post|put|patch|delete|head|options)"
    r"|\b(?:api\s+)?(?:route|endpoint|url|uri)"
    r"|\b(?:regex|pattern)"
    r")\s*(?::|=)?\s*$"
)
_STATEFUL_BROWSER_TOOL_TOKENS = frozenset(
    {
        "browser",
        "chrome",
        "chromium",
        "firefox",
        "safari",
        "playwright",
        "selenium",
        "puppeteer",
        "cdp",
    }
)
_STATEFUL_WEB_ACTION_TOKENS = frozenset(
    {"run", "search", "fetch", "open", "navigate", "click"}
)
class ReplayAdaptationError(RuntimeError):
    """Raised when a deterministic replay seed cannot be constructed."""


@dataclass(frozen=True)
class ReplayDependency:
    kind: str
    identifier: str
    status: str
    deterministic: bool
    adapter_id: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class ReplayCapabilityRequirement:
    requirement_id: str
    kind: str
    identifier: str
    case_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    status: str
    detail: str | None = None


@dataclass(frozen=True)
class ReplayPreflightReport:
    schema_version: str
    requirements: tuple[ReplayCapabilityRequirement, ...]
    fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReplayAdapterBinding:
    adapter_id: str
    dependency_id: str
    deterministic: bool
    environment: Mapping[str, str] = field(default_factory=dict)
    fixture_paths: tuple[str, ...] = ()
    concurrency_mode: Literal[
        "isolated", "shared_read_only", "exclusive"
    ] = "exclusive"
    resource_key: str | None = None
    binding_fingerprint: str | None = None


REPLAY_BINDING_CONCURRENCY_MODES = (
    "exclusive",
    "isolated",
    "shared_read_only",
)

IsolationAccessMode = Literal["isolated", "shared_read_only"]
IsolationFallbackCode = Literal[
    "binding_invalid",
    "binding_not_deterministic",
    "binding_requires_exclusive",
    "missing_workspace_identity",
    "missing_runtime_identity",
    "missing_browser_profile_identity",
    "missing_endpoint_namespace_identity",
    "missing_evidence_directory_identity",
    "missing_cleanup_owner",
    "invalid_service_identity",
    "invalid_resource_identity",
    "topology_identity_conflict",
    "binding_coverage_missing",
    "binding_coverage_invalid",
    "grant_set_incompatible",
    "grant_set_incomplete",
]
ISOLATION_TOPOLOGY_SCHEMA_VERSION = "aworld.self_evolve.isolation_topology.v1"
ISOLATION_GRANT_SET_SCHEMA_VERSION = "aworld.self_evolve.isolation_grant_set.v1"
ISOLATION_DECISION_SCHEMA_VERSION = "aworld.self_evolve.isolation_decision.v1"
_FULL_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_ISOLATION_DECISION_LANE_LIMIT = 64
_ISOLATION_GRANT_CLAIM_LIMIT = 256
_ISOLATION_FALLBACK_CODES = frozenset(
    {
        "binding_invalid",
        "binding_not_deterministic",
        "binding_requires_exclusive",
        "missing_workspace_identity",
        "missing_runtime_identity",
        "missing_browser_profile_identity",
        "missing_endpoint_namespace_identity",
        "missing_evidence_directory_identity",
        "missing_cleanup_owner",
        "invalid_service_identity",
        "invalid_resource_identity",
        "topology_identity_conflict",
        "binding_coverage_missing",
        "binding_coverage_invalid",
        "grant_set_incompatible",
        "grant_set_incomplete",
    }
)


@dataclass(frozen=True)
class IsolationServiceIdentity:
    """One service instance participating in a replay isolation topology."""

    service_id: str
    instance_identity: str
    access_mode: IsolationAccessMode = "isolated"
    cleanup_owner: str | None = None

    def __post_init__(self) -> None:
        _require_isolation_identity(self.service_id, "service_id")
        _require_isolation_identity(self.instance_identity, "service instance")
        _require_isolation_access_mode(self.access_mode)
        if self.access_mode == "isolated":
            _require_isolation_identity(self.cleanup_owner, "service cleanup owner")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> IsolationServiceIdentity:
        return cls(
            service_id=_required_isolation_text(value.get("service_id"), "service_id"),
            instance_identity=_required_isolation_text(
                value.get("instance_identity"), "service instance"
            ),
            access_mode=_isolation_access_mode(value.get("access_mode")),
            cleanup_owner=_optional_isolation_text(value.get("cleanup_owner")),
        )


@dataclass(frozen=True)
class IsolationResourceIdentity:
    """A typed resource claim used to prove cross-lane compatibility."""

    resource_kind: str
    identity: str
    access_mode: IsolationAccessMode = "isolated"
    cleanup_owner: str | None = None

    def __post_init__(self) -> None:
        _require_isolation_identity(self.resource_kind, "resource_kind")
        _require_isolation_identity(self.identity, "resource identity")
        _require_isolation_access_mode(self.access_mode)
        if self.access_mode == "isolated":
            _require_isolation_identity(self.cleanup_owner, "resource cleanup owner")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> IsolationResourceIdentity:
        return cls(
            resource_kind=_required_isolation_text(
                value.get("resource_kind"), "resource_kind"
            ),
            identity=_required_isolation_text(value.get("identity"), "resource identity"),
            access_mode=_isolation_access_mode(value.get("access_mode")),
            cleanup_owner=_optional_isolation_text(value.get("cleanup_owner")),
        )


@dataclass(frozen=True)
class IsolationBindingCoverage:
    """Materializer proof that one binding owns declared service/resources."""

    binding_fingerprint: str
    adapter_id: str
    dependency_id: str
    service_instance_identities: tuple[str, ...] = ()
    resource_identities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_isolation_fingerprint(
            self.binding_fingerprint, "binding fingerprint"
        )
        _require_isolation_identity(self.adapter_id, "covered adapter_id")
        _require_isolation_identity(self.dependency_id, "covered dependency_id")
        services = _canonical_identity_tuple(
            self.service_instance_identities, "covered service instance"
        )
        resources = _canonical_identity_tuple(
            self.resource_identities, "covered resource"
        )
        if not services and not resources:
            raise ValueError("binding coverage must name a service or resource")
        object.__setattr__(self, "service_instance_identities", services)
        object.__setattr__(self, "resource_identities", resources)

    def to_dict(self) -> dict[str, Any]:
        return {
            "binding_fingerprint": self.binding_fingerprint,
            "adapter_id": self.adapter_id,
            "dependency_id": self.dependency_id,
            "service_instance_identities": list(self.service_instance_identities),
            "resource_identities": list(self.resource_identities),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> IsolationBindingCoverage:
        return cls(
            binding_fingerprint=_required_isolation_text(
                value.get("binding_fingerprint"), "binding fingerprint"
            ),
            adapter_id=_required_isolation_text(
                value.get("adapter_id"), "covered adapter_id"
            ),
            dependency_id=_required_isolation_text(
                value.get("dependency_id"), "covered dependency_id"
            ),
            service_instance_identities=_isolation_string_tuple(
                value.get("service_instance_identities", ()),
                "covered service instances",
            ),
            resource_identities=_isolation_string_tuple(
                value.get("resource_identities", ()), "covered resources"
            ),
        )


@dataclass(frozen=True)
class ReplayIsolationTopology:
    """Canonical materializer output used to compile, but not imply, isolation."""

    materializer_id: str
    materializer_fingerprint: str
    workspace_identity: str
    runtime_identity: str
    browser_profile_identity: str
    endpoint_namespace_identity: str
    evidence_directory_identity: str
    services: tuple[IsolationServiceIdentity, ...] = ()
    resources: tuple[IsolationResourceIdentity, ...] = ()
    binding_coverage: tuple[IsolationBindingCoverage, ...] = ()
    cleanup_owner: str = ""
    topology_fingerprint: str = ""
    schema_version: str = ISOLATION_TOPOLOGY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != ISOLATION_TOPOLOGY_SCHEMA_VERSION:
            raise ValueError("unsupported isolation topology schema")
        _require_isolation_identity(self.materializer_id, "materializer_id")
        _require_isolation_fingerprint(
            self.materializer_fingerprint, "materializer fingerprint"
        )
        for field_name in _ISOLATION_NAMESPACE_FIELDS:
            _require_isolation_identity(getattr(self, field_name), field_name)
        _require_isolation_identity(self.cleanup_owner, "cleanup_owner")
        services = tuple(
            sorted(
                self.services,
                key=lambda item: (item.service_id, item.instance_identity),
            )
        )
        resources = tuple(
            sorted(
                self.resources,
                key=lambda item: (item.resource_kind, item.identity),
            )
        )
        coverage = tuple(
            sorted(self.binding_coverage, key=lambda item: item.binding_fingerprint)
        )
        if len({item.instance_identity for item in services}) != len(services):
            raise ValueError("isolation topology service instances must be unique")
        if len({item.identity for item in resources}) != len(resources):
            raise ValueError("isolation topology resource identities must be unique")
        if len({item.binding_fingerprint for item in coverage}) != len(coverage):
            raise ValueError("isolation topology binding coverage must be unique")
        object.__setattr__(self, "services", services)
        object.__setattr__(self, "resources", resources)
        object.__setattr__(self, "binding_coverage", coverage)
        expected = _json_fingerprint(_isolation_topology_payload(self))
        if self.topology_fingerprint != expected:
            raise ValueError("isolation topology fingerprint mismatch")

    @classmethod
    def create(
        cls,
        *,
        materializer_id: str,
        materializer_fingerprint: str,
        workspace_identity: str,
        runtime_identity: str,
        browser_profile_identity: str,
        endpoint_namespace_identity: str,
        evidence_directory_identity: str,
        services: Sequence[IsolationServiceIdentity] = (),
        resources: Sequence[IsolationResourceIdentity] = (),
        binding_coverage: Sequence[IsolationBindingCoverage] = (),
        cleanup_owner: str,
    ) -> ReplayIsolationTopology:
        values = {
            "schema_version": ISOLATION_TOPOLOGY_SCHEMA_VERSION,
            "materializer_id": materializer_id,
            "materializer_fingerprint": materializer_fingerprint,
            "workspace_identity": workspace_identity,
            "runtime_identity": runtime_identity,
            "browser_profile_identity": browser_profile_identity,
            "endpoint_namespace_identity": endpoint_namespace_identity,
            "evidence_directory_identity": evidence_directory_identity,
            "services": tuple(services),
            "resources": tuple(resources),
            "binding_coverage": tuple(binding_coverage),
            "cleanup_owner": cleanup_owner,
        }
        provisional = object.__new__(cls)
        for field_name, field_value in values.items():
            object.__setattr__(provisional, field_name, field_value)
        canonical_services = tuple(
            sorted(values["services"], key=lambda item: (item.service_id, item.instance_identity))
        )
        canonical_resources = tuple(
            sorted(values["resources"], key=lambda item: (item.resource_kind, item.identity))
        )
        canonical_coverage = tuple(
            sorted(values["binding_coverage"], key=lambda item: item.binding_fingerprint)
        )
        object.__setattr__(provisional, "services", canonical_services)
        object.__setattr__(provisional, "resources", canonical_resources)
        object.__setattr__(provisional, "binding_coverage", canonical_coverage)
        topology_fingerprint = _json_fingerprint(
            _isolation_topology_payload(provisional)
        )
        return cls(
            **values,
            topology_fingerprint=topology_fingerprint,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            **_isolation_topology_payload(self),
            "topology_fingerprint": self.topology_fingerprint,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ReplayIsolationTopology:
        if value.get("schema_version") != ISOLATION_TOPOLOGY_SCHEMA_VERSION:
            raise ValueError("unsupported isolation topology schema")
        return cls(
            materializer_id=_required_isolation_text(
                value.get("materializer_id"), "materializer_id"
            ),
            materializer_fingerprint=_required_isolation_text(
                value.get("materializer_fingerprint"), "materializer fingerprint"
            ),
            workspace_identity=_required_isolation_text(
                value.get("workspace_identity"), "workspace identity"
            ),
            runtime_identity=_required_isolation_text(
                value.get("runtime_identity"), "runtime identity"
            ),
            browser_profile_identity=_required_isolation_text(
                value.get("browser_profile_identity"), "browser profile identity"
            ),
            endpoint_namespace_identity=_required_isolation_text(
                value.get("endpoint_namespace_identity"), "endpoint namespace identity"
            ),
            evidence_directory_identity=_required_isolation_text(
                value.get("evidence_directory_identity"), "evidence directory identity"
            ),
            services=tuple(
                IsolationServiceIdentity.from_dict(_isolation_mapping(item, "service"))
                for item in _isolation_sequence(value.get("services", ()), "services")
            ),
            resources=tuple(
                IsolationResourceIdentity.from_dict(_isolation_mapping(item, "resource"))
                for item in _isolation_sequence(value.get("resources", ()), "resources")
            ),
            binding_coverage=tuple(
                IsolationBindingCoverage.from_dict(_isolation_mapping(item, "binding coverage"))
                for item in _isolation_sequence(
                    value.get("binding_coverage", ()), "binding_coverage"
                )
            ),
            cleanup_owner=_required_isolation_text(
                value.get("cleanup_owner"), "cleanup_owner"
            ),
            topology_fingerprint=_required_isolation_text(
                value.get("topology_fingerprint"), "topology fingerprint"
            ),
        )


@dataclass(frozen=True)
class IsolationExclusiveFallback:
    """Typed reason why the scheduler must retain one exclusive lane."""

    code: IsolationFallbackCode
    limiting_resource: str
    detail: str

    def __post_init__(self) -> None:
        if self.code not in _ISOLATION_FALLBACK_CODES:
            raise ValueError("unsupported isolation fallback code")
        _require_isolation_identity(self.limiting_resource, "limiting resource")
        _require_isolation_identity(self.detail, "fallback detail")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> IsolationExclusiveFallback:
        code = _required_isolation_text(value.get("code"), "fallback code")
        if code not in _ISOLATION_FALLBACK_CODES:
            raise ValueError("unsupported isolation fallback code")
        return cls(
            code=code,  # type: ignore[arg-type]
            limiting_resource=_required_isolation_text(
                value.get("limiting_resource"), "limiting resource"
            ),
            detail=_required_isolation_text(value.get("detail"), "fallback detail"),
        )


@dataclass(frozen=True)
class IsolationGrant:
    """Immutable proof that one replay lane owns a complete isolation scope."""

    schema_version: str
    workspace_identity: str
    runtime_identity: str
    browser_profile_identity: str
    endpoint_namespace_identity: str
    evidence_directory_identity: str
    services: tuple[IsolationServiceIdentity, ...]
    resources: tuple[IsolationResourceIdentity, ...]
    cleanup_owner: str
    materializer_id: str
    materializer_fingerprint: str
    topology_fingerprint: str
    binding_fingerprints: tuple[str, ...]
    fingerprint: str

    def __post_init__(self) -> None:
        if self.schema_version != ISOLATION_GRANT_SCHEMA_VERSION:
            raise ValueError("unsupported isolation grant schema")
        for field_name in _ISOLATION_NAMESPACE_FIELDS:
            _require_isolation_identity(getattr(self, field_name), field_name)
        _require_isolation_identity(self.cleanup_owner, "cleanup_owner")
        _require_isolation_identity(self.materializer_id, "materializer_id")
        _require_isolation_fingerprint(
            self.materializer_fingerprint, "materializer fingerprint"
        )
        _require_isolation_fingerprint(
            self.topology_fingerprint, "topology fingerprint"
        )
        canonical_services = tuple(
            sorted(self.services, key=lambda item: (item.service_id, item.instance_identity))
        )
        canonical_resources = tuple(
            sorted(self.resources, key=lambda item: (item.resource_kind, item.identity))
        )
        canonical_bindings = _canonical_fingerprint_tuple(
            self.binding_fingerprints, "binding fingerprint"
        )
        if any(
            len(items) > _ISOLATION_GRANT_CLAIM_LIMIT
            for items in (
                canonical_services,
                canonical_resources,
                canonical_bindings,
            )
        ):
            raise ValueError("isolation grant claim limit exceeded")
        if len({item.instance_identity for item in canonical_services}) != len(canonical_services):
            raise ValueError("isolation grant service instances must be unique")
        if len({item.identity for item in canonical_resources}) != len(canonical_resources):
            raise ValueError("isolation grant resource identities must be unique")
        object.__setattr__(self, "services", canonical_services)
        object.__setattr__(self, "resources", canonical_resources)
        object.__setattr__(self, "binding_fingerprints", canonical_bindings)
        expected = _json_fingerprint(_isolation_grant_payload(self))
        if self.fingerprint != expected:
            raise ValueError("isolation grant fingerprint mismatch")

    @classmethod
    def create(
        cls,
        *,
        topology: ReplayIsolationTopology,
        binding_fingerprints: Sequence[str],
    ) -> IsolationGrant:
        payload = {
            "schema_version": ISOLATION_GRANT_SCHEMA_VERSION,
            **{
                field_name: getattr(topology, field_name)
                for field_name in _ISOLATION_NAMESPACE_FIELDS
            },
            "services": [item.to_dict() for item in topology.services],
            "resources": [item.to_dict() for item in topology.resources],
            "cleanup_owner": topology.cleanup_owner,
            "materializer_id": topology.materializer_id,
            "materializer_fingerprint": topology.materializer_fingerprint,
            "topology_fingerprint": topology.topology_fingerprint,
            "binding_fingerprints": list(
                _canonical_fingerprint_tuple(binding_fingerprints, "binding fingerprint")
            ),
        }
        return cls(
            schema_version=ISOLATION_GRANT_SCHEMA_VERSION,
            workspace_identity=topology.workspace_identity,
            runtime_identity=topology.runtime_identity,
            browser_profile_identity=topology.browser_profile_identity,
            endpoint_namespace_identity=topology.endpoint_namespace_identity,
            evidence_directory_identity=topology.evidence_directory_identity,
            services=topology.services,
            resources=topology.resources,
            cleanup_owner=topology.cleanup_owner,
            materializer_id=topology.materializer_id,
            materializer_fingerprint=topology.materializer_fingerprint,
            topology_fingerprint=topology.topology_fingerprint,
            binding_fingerprints=tuple(payload["binding_fingerprints"]),
            fingerprint=_json_fingerprint(payload),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> IsolationGrant:
        return cls(
            schema_version=_required_isolation_text(
                value.get("schema_version"), "schema_version"
            ),
            workspace_identity=_required_isolation_text(
                value.get("workspace_identity"), "workspace identity"
            ),
            runtime_identity=_required_isolation_text(
                value.get("runtime_identity"), "runtime identity"
            ),
            browser_profile_identity=_required_isolation_text(
                value.get("browser_profile_identity"), "browser profile identity"
            ),
            endpoint_namespace_identity=_required_isolation_text(
                value.get("endpoint_namespace_identity"), "endpoint namespace identity"
            ),
            evidence_directory_identity=_required_isolation_text(
                value.get("evidence_directory_identity"), "evidence directory identity"
            ),
            services=tuple(
                IsolationServiceIdentity.from_dict(_isolation_mapping(item, "service"))
                for item in _isolation_sequence(value.get("services", ()), "services")
            ),
            resources=tuple(
                IsolationResourceIdentity.from_dict(_isolation_mapping(item, "resource"))
                for item in _isolation_sequence(value.get("resources", ()), "resources")
            ),
            cleanup_owner=_required_isolation_text(
                value.get("cleanup_owner"), "cleanup_owner"
            ),
            materializer_id=_required_isolation_text(
                value.get("materializer_id"), "materializer_id"
            ),
            materializer_fingerprint=_required_isolation_text(
                value.get("materializer_fingerprint"), "materializer fingerprint"
            ),
            topology_fingerprint=_required_isolation_text(
                value.get("topology_fingerprint"), "topology fingerprint"
            ),
            binding_fingerprints=_isolation_string_tuple(
                value.get("binding_fingerprints", ()), "binding_fingerprints"
            ),
            fingerprint=_required_isolation_text(
                value.get("fingerprint"), "grant fingerprint"
            ),
        )


@dataclass(frozen=True)
class IsolationGrantCompilation:
    """Exactly one of a verified grant or an exclusive fallback decision."""

    grant: IsolationGrant | None = None
    fallback: IsolationExclusiveFallback | None = None

    def __post_init__(self) -> None:
        if (self.grant is None) == (self.fallback is None):
            raise ValueError(
                "isolation compilation requires exactly one grant or fallback"
            )


@dataclass(frozen=True)
class IsolationGrantCompatibility:
    compatible: bool
    code: str
    limiting_resource: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class IsolationGrantPairDecision:
    left_grant_fingerprint: str
    right_grant_fingerprint: str
    compatible: bool
    code: str
    limiting_resource: str | None = None

    def __post_init__(self) -> None:
        _require_isolation_fingerprint(
            self.left_grant_fingerprint, "left grant fingerprint"
        )
        _require_isolation_fingerprint(
            self.right_grant_fingerprint, "right grant fingerprint"
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> IsolationGrantPairDecision:
        compatible = value.get("compatible")
        if not isinstance(compatible, bool):
            raise ValueError("pair compatibility must be boolean")
        return cls(
            left_grant_fingerprint=_required_isolation_text(
                value.get("left_grant_fingerprint"), "left grant fingerprint"
            ),
            right_grant_fingerprint=_required_isolation_text(
                value.get("right_grant_fingerprint"), "right grant fingerprint"
            ),
            compatible=compatible,
            code=_required_isolation_text(value.get("code"), "compatibility code"),
            limiting_resource=_optional_isolation_text(
                value.get("limiting_resource")
            ),
        )


@dataclass(frozen=True)
class IsolationGrantSet:
    schema_version: str
    grants: tuple[IsolationGrant, ...]
    pairwise_decisions: tuple[IsolationGrantPairDecision, ...]
    fingerprint: str

    def __post_init__(self) -> None:
        if self.schema_version != ISOLATION_GRANT_SET_SCHEMA_VERSION:
            raise ValueError("unsupported isolation grant-set schema")
        canonical_grants = tuple(sorted(self.grants, key=lambda item: item.fingerprint))
        if len(canonical_grants) > _ISOLATION_DECISION_LANE_LIMIT:
            raise ValueError("isolation grant-set lane limit exceeded")
        if len({item.fingerprint for item in canonical_grants}) != len(canonical_grants):
            raise ValueError("isolation grant set contains duplicate grants")
        expected_pairs = _isolation_pair_decisions(canonical_grants)
        if tuple(self.pairwise_decisions) != expected_pairs:
            raise ValueError("isolation grant-set pair decisions are not canonical")
        object.__setattr__(self, "grants", canonical_grants)
        object.__setattr__(self, "pairwise_decisions", expected_pairs)
        if self.fingerprint != _json_fingerprint(_isolation_grant_set_payload(self)):
            raise ValueError("isolation grant-set fingerprint mismatch")

    @classmethod
    def create(cls, grants: Sequence[IsolationGrant]) -> IsolationGrantSet:
        canonical = tuple(sorted(grants, key=lambda item: item.fingerprint))
        pairs = _isolation_pair_decisions(canonical)
        payload = {
            "schema_version": ISOLATION_GRANT_SET_SCHEMA_VERSION,
            "grants": [item.to_dict() for item in canonical],
            "pairwise_decisions": [item.to_dict() for item in pairs],
        }
        return cls(
            schema_version=ISOLATION_GRANT_SET_SCHEMA_VERSION,
            grants=canonical,
            pairwise_decisions=pairs,
            fingerprint=_json_fingerprint(payload),
        )

    @property
    def all_compatible(self) -> bool:
        return bool(self.grants) and all(
            item.compatible for item in self.pairwise_decisions
        )

    def to_dict(self) -> dict[str, Any]:
        return {**_isolation_grant_set_payload(self), "fingerprint": self.fingerprint}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> IsolationGrantSet:
        return cls(
            schema_version=_required_isolation_text(
                value.get("schema_version"), "schema_version"
            ),
            grants=tuple(
                IsolationGrant.from_dict(_isolation_mapping(item, "grant"))
                for item in _isolation_sequence(value.get("grants", ()), "grants")
            ),
            pairwise_decisions=tuple(
                IsolationGrantPairDecision.from_dict(
                    _isolation_mapping(item, "pair decision")
                )
                for item in _isolation_sequence(
                    value.get("pairwise_decisions", ()), "pairwise_decisions"
                )
            ),
            fingerprint=_required_isolation_text(
                value.get("fingerprint"), "grant-set fingerprint"
            ),
        )


@dataclass(frozen=True)
class IsolationDecision:
    schema_version: str
    requested_lane_count: int
    safe_lane_count: int
    grant_set: IsolationGrantSet
    fallback: IsolationExclusiveFallback | None
    fingerprint: str

    def __post_init__(self) -> None:
        if self.schema_version != ISOLATION_DECISION_SCHEMA_VERSION:
            raise ValueError("unsupported isolation decision schema")
        if (
            isinstance(self.requested_lane_count, bool)
            or not isinstance(self.requested_lane_count, int)
            or self.requested_lane_count <= 0
            or self.requested_lane_count > _ISOLATION_DECISION_LANE_LIMIT
        ):
            raise ValueError("requested lane count is outside the supported bound")
        complete_pairwise_proof = (
            len(self.grant_set.grants) >= self.requested_lane_count
            and self.grant_set.all_compatible
        )
        expected_safe = self.requested_lane_count if complete_pairwise_proof else 1
        if self.safe_lane_count != expected_safe:
            raise ValueError("isolation decision safe lane count is not canonical")
        if isinstance(self.safe_lane_count, bool) or not isinstance(
            self.safe_lane_count, int
        ):
            raise ValueError("safe lane count must be an integer")
        expected_fallback = not complete_pairwise_proof
        if expected_fallback != (self.fallback is not None):
            raise ValueError("degraded isolation decision requires one fallback")
        if self.fingerprint != _json_fingerprint(_isolation_decision_payload(self)):
            raise ValueError("isolation decision fingerprint mismatch")

    @classmethod
    def create(
        cls,
        *,
        requested_lane_count: int,
        grants: Sequence[IsolationGrant],
    ) -> IsolationDecision:
        grant_set = IsolationGrantSet.create(grants)
        if len(grant_set.grants) < requested_lane_count:
            safe = 1
            fallback = IsolationExclusiveFallback(
                code="grant_set_incomplete",
                limiting_resource="isolation_grant_set",
                detail="not every requested lane has a verified grant",
            )
        elif not grant_set.all_compatible:
            first = next(item for item in grant_set.pairwise_decisions if not item.compatible)
            safe = 1
            fallback = IsolationExclusiveFallback(
                code="grant_set_incompatible",
                limiting_resource=first.limiting_resource or "isolation_grant_set",
                detail=f"lane grants are incompatible: {first.code}",
            )
        else:
            safe = requested_lane_count
            fallback = None
        provisional = {
            "schema_version": ISOLATION_DECISION_SCHEMA_VERSION,
            "requested_lane_count": requested_lane_count,
            "safe_lane_count": safe,
            "grant_set": grant_set.to_dict(),
            "fallback": fallback.to_dict() if fallback is not None else None,
        }
        return cls(
            schema_version=ISOLATION_DECISION_SCHEMA_VERSION,
            requested_lane_count=requested_lane_count,
            safe_lane_count=safe,
            grant_set=grant_set,
            fallback=fallback,
            fingerprint=_json_fingerprint(provisional),
        )

    @classmethod
    def exclusive_fallback(
        cls,
        *,
        requested_lane_count: int,
        fallback: IsolationExclusiveFallback,
        grants: Sequence[IsolationGrant] = (),
    ) -> IsolationDecision:
        """Create a canonical exclusive decision from a typed proof failure."""

        if not isinstance(fallback, IsolationExclusiveFallback):
            raise TypeError("exclusive isolation decision requires a typed fallback")
        grant_set = IsolationGrantSet.create(grants)
        if (
            len(grant_set.grants) >= requested_lane_count
            and grant_set.all_compatible
        ):
            raise ValueError("complete compatible grants cannot use exclusive fallback")
        payload = {
            "schema_version": ISOLATION_DECISION_SCHEMA_VERSION,
            "requested_lane_count": requested_lane_count,
            "safe_lane_count": 1,
            "grant_set": grant_set.to_dict(),
            "fallback": fallback.to_dict(),
        }
        return cls(
            schema_version=ISOLATION_DECISION_SCHEMA_VERSION,
            requested_lane_count=requested_lane_count,
            safe_lane_count=1,
            grant_set=grant_set,
            fallback=fallback,
            fingerprint=_json_fingerprint(payload),
        )

    def to_dict(self) -> dict[str, Any]:
        return {**_isolation_decision_payload(self), "fingerprint": self.fingerprint}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> IsolationDecision:
        requested = value.get("requested_lane_count")
        safe = value.get("safe_lane_count")
        if isinstance(requested, bool) or not isinstance(requested, int):
            raise ValueError("requested lane count must be an integer")
        if isinstance(safe, bool) or not isinstance(safe, int):
            raise ValueError("safe lane count must be an integer")
        fallback_value = value.get("fallback")
        return cls(
            schema_version=_required_isolation_text(
                value.get("schema_version"), "schema_version"
            ),
            requested_lane_count=requested,
            safe_lane_count=safe,
            grant_set=IsolationGrantSet.from_dict(
                _isolation_mapping(value.get("grant_set"), "grant_set")
            ),
            fallback=(
                None
                if fallback_value is None
                else IsolationExclusiveFallback.from_dict(
                    _isolation_mapping(fallback_value, "fallback")
                )
            ),
            fingerprint=_required_isolation_text(
                value.get("fingerprint"), "decision fingerprint"
            ),
        )


def compile_isolation_decision_artifact(
    *,
    requested_lane_count: int,
    lane_compilations: Sequence[IsolationGrantCompilation],
) -> IsolationDecision:
    """Compile bounded lane proofs into one canonical scheduling artifact.

    Callers must provide typed grant compilations.  A proof failure always
    produces an exclusive decision that retains the typed limiting reason;
    no boolean or caller-supplied fingerprint can enable multiple lanes.
    """

    compilations = tuple(lane_compilations)
    if len(compilations) > _ISOLATION_DECISION_LANE_LIMIT:
        raise ValueError("isolation decision compilation limit exceeded")
    if any(not isinstance(item, IsolationGrantCompilation) for item in compilations):
        raise TypeError("isolation decisions require typed grant compilations")
    grants = tuple(item.grant for item in compilations if item.grant is not None)
    fallbacks = tuple(
        item.fallback for item in compilations if item.fallback is not None
    )
    if fallbacks:
        fallback = min(
            fallbacks,
            key=lambda item: (item.code, item.limiting_resource, item.detail),
        )
        return IsolationDecision.exclusive_fallback(
            requested_lane_count=requested_lane_count,
            fallback=fallback,
            grants=grants,
        )
    return IsolationDecision.create(
        requested_lane_count=requested_lane_count,
        grants=grants,
    )


def compile_isolation_grant(
    *,
    topology: ReplayIsolationTopology,
    bindings: Sequence[ReplayAdapterBinding],
) -> IsolationGrantCompilation:
    """Compile a complete isolation proof or a deterministic exclusive fallback.

    This function never turns an ``exclusive`` binding into an isolated claim.
    It only verifies the topology supplied by adaptation.  Scheduling remains a
    downstream concern and must consume the returned grant explicitly.
    """

    validated_bindings: list[ReplayAdapterBinding] = []
    for binding in bindings:
        try:
            validated = validate_replay_binding_concurrency(binding)
        except ValueError as exc:
            return _isolation_fallback(
                "binding_invalid",
                binding.adapter_id or "binding",
                f"replay binding is invalid: {exc}",
            )
        if not validated.deterministic:
            return _isolation_fallback(
                "binding_not_deterministic",
                validated.resource_key or validated.adapter_id,
                "a non-deterministic replay binding cannot prove lane isolation",
            )
        if validated.concurrency_mode == "exclusive":
            return _isolation_fallback(
                "binding_requires_exclusive",
                validated.resource_key or validated.adapter_id,
                "the replay binding explicitly requires exclusive execution",
            )
        validated_bindings.append(validated)

    namespace_values = tuple(
        getattr(topology, field_name) for field_name in _ISOLATION_NAMESPACE_FIELDS
    )
    if len(namespace_values) != len(set(namespace_values)):
        return _isolation_fallback(
            "topology_identity_conflict",
            "topology",
            "workspace, runtime, browser, endpoint, and evidence identities must be distinct",
        )

    expected_bindings = {
        item.binding_fingerprint for item in validated_bindings
        if item.binding_fingerprint is not None
    }
    coverage_by_binding = {
        item.binding_fingerprint: item for item in topology.binding_coverage
    }
    if set(coverage_by_binding) != expected_bindings:
        missing = sorted(expected_bindings - set(coverage_by_binding))
        return _isolation_fallback(
            "binding_coverage_missing",
            missing[0] if missing else "binding_coverage",
            "materialized topology does not exactly cover validated bindings",
        )
    service_by_id = {item.instance_identity: item for item in topology.services}
    service_ids = set(service_by_id)
    resource_by_id = {item.identity: item for item in topology.resources}
    for binding in validated_bindings:
        fingerprint = binding.binding_fingerprint
        if fingerprint is None:  # pragma: no cover - validator derives it
            return _isolation_fallback(
                "binding_invalid", binding.adapter_id, "binding fingerprint missing"
            )
        coverage = coverage_by_binding[fingerprint]
        if (
            coverage.adapter_id != binding.adapter_id
            or coverage.dependency_id != binding.dependency_id
        ):
            return _isolation_fallback(
                "binding_coverage_invalid",
                fingerprint,
                "binding coverage provenance does not match the validated binding",
            )
        if (
            not set(coverage.service_instance_identities).issubset(service_ids)
            or not set(coverage.resource_identities).issubset(resource_by_id)
        ):
            return _isolation_fallback(
                "binding_coverage_invalid",
                fingerprint,
                "binding coverage references an undeclared materialized identity",
            )
        covered = (
            set(coverage.service_instance_identities)
            | set(coverage.resource_identities)
        )
        if not covered:
            return _isolation_fallback(
                "binding_coverage_invalid",
                fingerprint,
                "binding coverage did not materialize a service or resource",
            )
        if binding.concurrency_mode == "shared_read_only":
            resource_key = binding.resource_key
            resource = resource_by_id.get(resource_key or "")
            if resource is None or resource.access_mode != "shared_read_only":
                return _isolation_fallback(
                    "binding_coverage_invalid",
                    resource_key or fingerprint,
                    "shared read-only binding lacks its matching share-safe resource",
                )
            if resource.identity not in coverage.resource_identities:
                return _isolation_fallback(
                    "binding_coverage_invalid",
                    resource.identity,
                    "shared binding coverage omits its declared resource key",
                )
        elif not any(
            item.access_mode == "isolated"
            for item in (
                *(service_by_id[identity] for identity in coverage.service_instance_identities),
                *(resource_by_id[identity] for identity in coverage.resource_identities),
            )
        ):
            return _isolation_fallback(
                "binding_coverage_invalid",
                fingerprint,
                "isolated binding coverage does not own an isolated identity",
            )

    return IsolationGrantCompilation(
        grant=IsolationGrant.create(
            topology=topology,
            binding_fingerprints=tuple(sorted(expected_bindings)),
        )
    )


def isolation_grants_compatible(
    left: IsolationGrant,
    right: IsolationGrant,
) -> IsolationGrantCompatibility:
    """Return whether two verified grants may execute at the same time."""

    left_claims = _isolation_claims(left)
    right_claims = _isolation_claims(right)
    for left_claim in left_claims:
        for right_claim in right_claims:
            if not _isolation_identity_conflicts(
                left_claim[1], right_claim[1]
            ):
                continue
            same_explicit_read_only = (
                left_claim[0] == right_claim[0]
                and
                left_claim[1] == right_claim[1]
                and left_claim[2] == "shared_read_only"
                and right_claim[2] == "shared_read_only"
            )
            if same_explicit_read_only:
                continue
            return IsolationGrantCompatibility(
                compatible=False,
                code="resource_identity_conflict",
                limiting_resource=f"{left_claim[0]}:{left_claim[1]}",
                detail=(
                    "mutable isolation claims overlap across lanes: "
                    f"{left_claim[0]} vs {right_claim[0]}"
                ),
            )
    return IsolationGrantCompatibility(compatible=True, code="compatible")


def _isolation_fallback(
    code: IsolationFallbackCode,
    limiting_resource: str,
    detail: str,
) -> IsolationGrantCompilation:
    return IsolationGrantCompilation(
        fallback=IsolationExclusiveFallback(
            code=code,
            limiting_resource=limiting_resource,
            detail=detail,
        )
    )


def _bounded_identity(value: object, *, max_chars: int = 1_024) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > max_chars:
        return None
    return normalized


def _isolation_identity_conflicts(left: str, right: str) -> bool:
    if left == right:
        return True
    # When identities are absolute locations, nested roots are not independent.
    if not (os.path.isabs(left) and os.path.isabs(right)):
        return False
    try:
        left_path = Path(left).resolve(strict=False)
        right_path = Path(right).resolve(strict=False)
        return left_path in right_path.parents or right_path in left_path.parents
    except OSError:
        return True


_ISOLATION_NAMESPACE_FIELDS = (
    "workspace_identity",
    "runtime_identity",
    "browser_profile_identity",
    "endpoint_namespace_identity",
    "evidence_directory_identity",
)


def _isolation_topology_payload(topology: ReplayIsolationTopology) -> dict[str, Any]:
    return {
        "schema_version": ISOLATION_TOPOLOGY_SCHEMA_VERSION,
        "materializer_id": topology.materializer_id,
        "materializer_fingerprint": topology.materializer_fingerprint,
        **{
            field_name: getattr(topology, field_name)
            for field_name in _ISOLATION_NAMESPACE_FIELDS
        },
        "services": [item.to_dict() for item in topology.services],
        "resources": [item.to_dict() for item in topology.resources],
        "binding_coverage": [item.to_dict() for item in topology.binding_coverage],
        "cleanup_owner": topology.cleanup_owner,
    }


def _isolation_grant_payload(grant: IsolationGrant) -> dict[str, Any]:
    return {
        "schema_version": ISOLATION_GRANT_SCHEMA_VERSION,
        **{
            field_name: getattr(grant, field_name)
            for field_name in _ISOLATION_NAMESPACE_FIELDS
        },
        "services": [item.to_dict() for item in grant.services],
        "resources": [item.to_dict() for item in grant.resources],
        "cleanup_owner": grant.cleanup_owner,
        "materializer_id": grant.materializer_id,
        "materializer_fingerprint": grant.materializer_fingerprint,
        "topology_fingerprint": grant.topology_fingerprint,
        "binding_fingerprints": list(grant.binding_fingerprints),
    }


def _isolation_claims(
    grant: IsolationGrant,
) -> tuple[tuple[str, str, IsolationAccessMode], ...]:
    claims: list[tuple[str, str, IsolationAccessMode]] = [
        (field_name, getattr(grant, field_name), "isolated")
        for field_name in _ISOLATION_NAMESPACE_FIELDS
    ]
    claims.extend(
        (f"service:{item.service_id}", item.instance_identity, item.access_mode)
        for item in grant.services
    )
    claims.extend(
        (f"resource:{item.resource_kind}", item.identity, item.access_mode)
        for item in grant.resources
    )
    return tuple(claims)


def _isolation_pair_decisions(
    grants: Sequence[IsolationGrant],
) -> tuple[IsolationGrantPairDecision, ...]:
    decisions: list[IsolationGrantPairDecision] = []
    for index, left in enumerate(grants):
        for right in grants[index + 1 :]:
            compatibility = isolation_grants_compatible(left, right)
            decisions.append(
                IsolationGrantPairDecision(
                    left_grant_fingerprint=left.fingerprint,
                    right_grant_fingerprint=right.fingerprint,
                    compatible=compatibility.compatible,
                    code=compatibility.code,
                    limiting_resource=compatibility.limiting_resource,
                )
            )
    return tuple(decisions)


def _isolation_grant_set_payload(value: IsolationGrantSet) -> dict[str, Any]:
    return {
        "schema_version": ISOLATION_GRANT_SET_SCHEMA_VERSION,
        "grants": [item.to_dict() for item in value.grants],
        "pairwise_decisions": [item.to_dict() for item in value.pairwise_decisions],
    }


def _isolation_decision_payload(value: IsolationDecision) -> dict[str, Any]:
    return {
        "schema_version": ISOLATION_DECISION_SCHEMA_VERSION,
        "requested_lane_count": value.requested_lane_count,
        "safe_lane_count": value.safe_lane_count,
        "grant_set": value.grant_set.to_dict(),
        "fallback": value.fallback.to_dict() if value.fallback is not None else None,
    }


def _require_isolation_access_mode(value: object) -> None:
    if value not in {"isolated", "shared_read_only"}:
        raise ValueError("invalid isolation access mode")


def _isolation_access_mode(value: object) -> IsolationAccessMode:
    _require_isolation_access_mode(value)
    return value  # type: ignore[return-value]


def _require_isolation_identity(value: object, label: str) -> None:
    if _bounded_identity(value) is None:
        raise ValueError(f"{label} must be a bounded non-empty identity")


def _require_isolation_fingerprint(value: object, label: str) -> None:
    if not isinstance(value, str) or not _FULL_SHA256.fullmatch(value):
        raise ValueError(f"{label} must be a full sha256 fingerprint")


def _required_isolation_text(value: object, label: str) -> str:
    normalized = _bounded_identity(value)
    if normalized is None:
        raise ValueError(f"{label} must be a bounded non-empty string")
    return normalized


def _optional_isolation_text(value: object) -> str | None:
    if value is None:
        return None
    return _required_isolation_text(value, "optional identity")


def _isolation_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _isolation_sequence(value: object, label: str) -> Sequence[object]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{label} must be an array")
    return value


def _isolation_string_tuple(value: object, label: str) -> tuple[str, ...]:
    return tuple(
        _required_isolation_text(item, label)
        for item in _isolation_sequence(value, label)
    )


def _canonical_identity_tuple(values: Sequence[str], label: str) -> tuple[str, ...]:
    normalized = tuple(sorted(_required_isolation_text(item, label) for item in values))
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{label} identities must be unique")
    return normalized


def _canonical_fingerprint_tuple(
    values: Sequence[str], label: str
) -> tuple[str, ...]:
    normalized = tuple(sorted(values))
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{label} values must be unique")
    for value in normalized:
        _require_isolation_fingerprint(value, label)
    return normalized


def validate_replay_binding_concurrency(
    binding: ReplayAdapterBinding,
) -> ReplayAdapterBinding:
    """Validate generic skill-owned scheduling metadata and fill safe defaults."""

    if binding.concurrency_mode not in REPLAY_BINDING_CONCURRENCY_MODES:
        raise ValueError(
            f"unsupported replay binding concurrency mode: {binding.concurrency_mode}"
        )
    resource_key = binding.resource_key
    if resource_key is not None:
        resource_key = resource_key.strip()
        if not resource_key:
            raise ValueError("replay binding resource_key must not be empty")
    if binding.concurrency_mode == "isolated":
        if resource_key is not None:
            raise ValueError(
                "isolated replay binding cannot declare a shared resource_key"
            )
        if not binding.deterministic:
            raise ValueError("isolated replay binding must be deterministic")
    elif resource_key is None:
        resource_key = f"replay-adapter:{binding.adapter_id}"
    supplied_fingerprint = binding.binding_fingerprint
    if supplied_fingerprint is not None:
        supplied_fingerprint = supplied_fingerprint.strip()
        if not supplied_fingerprint:
            raise ValueError("external binding fingerprint must not be empty")
    # The binding identity is always derived from the normalized safety contract.
    # A legacy caller-provided fingerprint is never trusted as proof; excluding it
    # from the projection also keeps repeated validation idempotent.
    binding_fingerprint = _json_fingerprint(
        {
            "adapter_id": binding.adapter_id,
            "dependency_id": binding.dependency_id,
            "deterministic": binding.deterministic,
            "environment": _safe_adapter_environment(binding.environment),
            "fixture_paths": sorted(str(item) for item in binding.fixture_paths),
            "concurrency_mode": binding.concurrency_mode,
            "resource_key": resource_key,
        }
    )
    return replace(
        binding,
        resource_key=resource_key,
        binding_fingerprint=binding_fingerprint,
    )


@dataclass(frozen=True)
class ReplayAdapterContext:
    case_id: str
    task_input: Any
    workspace_root: Path
    workspace_seed: Path
    artifact_root: Path


class ReplayDependencyAdapter(Protocol):
    adapter_id: str

    def bind(
        self,
        dependency: ReplayDependency,
        *,
        context: ReplayAdapterContext,
    ) -> ReplayAdapterBinding | None:
        """Return a deterministic fixture binding for a detected dependency."""


@dataclass(frozen=True)
class ReplayCaseAdaptation:
    case_id: str
    adapted_task_input: Any
    task_input_fingerprint: str
    dependencies: tuple[ReplayDependency, ...]
    bindings: tuple[ReplayAdapterBinding, ...]
    tool_names: tuple[str, ...]
    readiness: str
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReplayAdaptationBundle:
    schema_version: str
    source_workspace_root: str
    workspace_seed: str
    workspace_seed_fingerprint: str
    manifest_path: str
    environment_snapshot_path: str
    environment_fingerprint: str
    cases: tuple[ReplayCaseAdaptation, ...]
    adaptation_fingerprint: str
    ready: bool
    replay_capability: FrozenReplayCapability | None = None

    def case(self, case_id: str) -> ReplayCaseAdaptation:
        for item in self.cases:
            if item.case_id == case_id:
                return item
        raise KeyError(f"replay adaptation case not found: {case_id}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ReplayAdaptationCompiler:
    def __init__(
        self,
        *,
        adapters: Sequence[ReplayDependencyAdapter] = (),
        max_external_file_bytes: int = 10 * 1024 * 1024,
        max_workspace_files: int = 50_000,
        max_workspace_bytes: int = 2 * 1024 * 1024 * 1024,
    ) -> None:
        if max_external_file_bytes <= 0:
            raise ValueError("max_external_file_bytes must be positive")
        if max_workspace_files <= 0:
            raise ValueError("max_workspace_files must be positive")
        if max_workspace_bytes <= 0:
            raise ValueError("max_workspace_bytes must be positive")
        self.adapters = tuple(adapters)
        self.max_external_file_bytes = max_external_file_bytes
        self.max_workspace_files = max_workspace_files
        self.max_workspace_bytes = max_workspace_bytes

    def preflight(
        self,
        *,
        dataset: SelfEvolveDataset,
        workspace_root: str | Path,
    ) -> ReplayPreflightReport:
        workspace = Path(workspace_root).expanduser().resolve()
        if not workspace.is_dir():
            raise ReplayAdaptationError(f"replay workspace does not exist: {workspace}")
        grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
        for case in dataset.cases:
            task_input = _normalize_value(
                case.input,
                lambda text: _normalize_workspace_paths(
                    text,
                    workspace_root=workspace,
                ),
            )
            for dependency, _raw_path in self._analyze_case_dependencies(
                case,
                task_input=task_input,
                workspace_root=workspace,
            ):
                if dependency.deterministic:
                    continue
                key = (
                    dependency.kind,
                    dependency.identifier,
                    dependency.status,
                )
                item = grouped.setdefault(
                    key,
                    {"case_ids": [], "evidence_refs": [], "detail": dependency.detail},
                )
                if case.case_id not in item["case_ids"]:
                    item["case_ids"].append(case.case_id)
                evidence_ref = _context_evidence_ref(case)
                if evidence_ref not in item["evidence_refs"]:
                    item["evidence_refs"].append(evidence_ref)
        requirements = tuple(
            ReplayCapabilityRequirement(
                requirement_id=_requirement_id(kind, identifier),
                kind=kind,
                identifier=identifier,
                case_ids=tuple(value["case_ids"]),
                evidence_refs=tuple(value["evidence_refs"]),
                status=status,
                detail=value["detail"],
            )
            for (kind, identifier, status), value in sorted(grouped.items())
        )
        payload = {
            "schema_version": REPLAY_PREFLIGHT_SCHEMA_VERSION,
            "requirements": [asdict(item) for item in requirements],
        }
        return ReplayPreflightReport(
            schema_version=REPLAY_PREFLIGHT_SCHEMA_VERSION,
            requirements=requirements,
            fingerprint=_json_fingerprint(payload),
        )

    def compile(
        self,
        *,
        dataset: SelfEvolveDataset,
        workspace_root: str | Path,
        artifact_root: str | Path,
        additional_adapters: Sequence[ReplayDependencyAdapter] = (),
        replay_capability: FrozenReplayCapability | None = None,
    ) -> ReplayAdaptationBundle:
        workspace = Path(workspace_root).expanduser().resolve()
        if not workspace.is_dir():
            raise ReplayAdaptationError(f"replay workspace does not exist: {workspace}")
        artifact = Path(artifact_root).expanduser().resolve()
        artifact.mkdir(parents=True, exist_ok=True)
        seed = artifact / "workspace_seed"
        if seed.is_symlink():
            seed.unlink()
        elif seed.exists():
            shutil.rmtree(seed)
        try:
            self._copy_workspace_seed(workspace, seed, artifact_root=artifact)
            cases = tuple(
                self._compile_case(
                    case,
                    workspace_root=workspace,
                    workspace_seed=seed,
                    artifact_root=artifact,
                    adapters=(*self.adapters, *additional_adapters),
                )
                for case in dataset.cases
            )
            self._assert_workspace_seed_limits(seed)
        except Exception:
            if seed.is_symlink():
                seed.unlink()
            elif seed.exists():
                shutil.rmtree(seed)
            raise
        manifest_path = artifact / "workspace_manifest.json"
        manifest = _workspace_manifest(seed)
        _write_json_atomic(manifest_path, manifest)
        seed_fingerprint = _json_fingerprint(manifest)
        environment_snapshot_path = artifact / "environment_snapshot.json"
        environment_snapshot = _environment_snapshot(cases)
        _write_json_atomic(environment_snapshot_path, environment_snapshot)
        environment_fingerprint = _environment_identity_fingerprint(
            environment_snapshot
        )
        adaptation_payload = {
            "schema_version": REPLAY_ADAPTATION_SCHEMA_VERSION,
            "source_workspace_root": str(workspace),
            "workspace_seed_fingerprint": seed_fingerprint,
            "environment_fingerprint": environment_fingerprint,
            "cases": [asdict(case) for case in cases],
            "replay_capability_fingerprint": (
                replay_capability.fingerprint
                if replay_capability is not None
                else None
            ),
        }
        bundle = ReplayAdaptationBundle(
            schema_version=REPLAY_ADAPTATION_SCHEMA_VERSION,
            source_workspace_root=str(workspace),
            workspace_seed=str(seed),
            workspace_seed_fingerprint=seed_fingerprint,
            manifest_path=str(manifest_path),
            environment_snapshot_path=str(environment_snapshot_path),
            environment_fingerprint=environment_fingerprint,
            cases=cases,
            adaptation_fingerprint=_json_fingerprint(adaptation_payload),
            ready=bool(cases) and all(case.readiness == "ready" for case in cases),
            replay_capability=replay_capability,
        )
        _write_json_atomic(artifact / "bundle.json", bundle.to_dict())
        return bundle

    def _compile_case(
        self,
        case: EvalCase,
        *,
        workspace_root: Path,
        workspace_seed: Path,
        artifact_root: Path,
        adapters: Sequence[ReplayDependencyAdapter],
    ) -> ReplayCaseAdaptation:
        task_input = _normalize_value(
            case.input,
            lambda text: _normalize_workspace_paths(text, workspace_root=workspace_root),
        )
        analyzed_dependencies = self._analyze_case_dependencies(
            case,
            task_input=task_input,
            workspace_root=workspace_root,
        )
        dependencies: list[ReplayDependency] = []
        diagnostics: list[str] = []

        for dependency, raw_path in analyzed_dependencies:
            if raw_path is not None:
                task_input, dependency = self._adapt_external_path(
                    task_input,
                    raw_path=raw_path,
                    workspace_seed=workspace_seed,
                    dependency=dependency,
                )
            dependencies.append(dependency)

        tool_names = _case_tool_names(case)

        context = ReplayAdapterContext(
            case_id=case.case_id,
            task_input=task_input,
            workspace_root=workspace_root,
            workspace_seed=workspace_seed,
            artifact_root=artifact_root,
        )
        bindings: list[ReplayAdapterBinding] = []
        adapted_dependencies: list[ReplayDependency] = []
        for dependency in dependencies:
            binding = self._bind_dependency(
                dependency,
                context=context,
                adapters=adapters,
            )
            if binding is None:
                adapted_dependencies.append(dependency)
                continue
            safe_binding = validate_replay_binding_concurrency(
                self._snapshot_adapter_fixtures(
                    replace(
                        binding,
                        environment=_safe_adapter_environment(binding.environment),
                    ),
                    workspace_seed=workspace_seed,
                )
            )
            bindings.append(safe_binding)
            adapted_dependencies.append(
                replace(
                    dependency,
                    status="adapter_bound",
                    deterministic=safe_binding.deterministic,
                    adapter_id=safe_binding.adapter_id,
                    detail="dependency is provided by a registered replay adapter",
                )
            )

        readiness = _case_readiness(adapted_dependencies)
        if readiness != "ready":
            diagnostics.append(f"replay adaptation is {readiness}")
        return ReplayCaseAdaptation(
            case_id=case.case_id,
            adapted_task_input=task_input,
            task_input_fingerprint=_json_fingerprint(task_input),
            dependencies=tuple(adapted_dependencies),
            bindings=tuple(bindings),
            tool_names=tool_names,
            readiness=readiness,
            diagnostics=tuple(diagnostics),
        )

    def _analyze_case_dependencies(
        self,
        case: EvalCase,
        *,
        task_input: Any,
        workspace_root: Path,
    ) -> tuple[tuple[ReplayDependency, str | None], ...]:
        dependency_input = _case_dependency_input(
            case,
            normalized_task_input=task_input,
            workspace_root=workspace_root,
        )
        analyzed: list[tuple[ReplayDependency, str | None]] = [
            (dependency, None)
            for dependency in _detected_runtime_dependencies(case, dependency_input)
        ]
        for raw_path in _absolute_local_path_references(
            _text_fragments(dependency_input)
        ):
            analyzed.append((self._external_path_dependency(raw_path), raw_path))
        return tuple(analyzed)

    def _external_path_dependency(self, raw_path: str) -> ReplayDependency:
        source = Path(raw_path).expanduser()
        identifier = "local-file:" + hashlib.sha256(
            raw_path.encode("utf-8")
        ).hexdigest()[:16]
        try:
            can_snapshot = (
                source.is_file()
                and not source.is_symlink()
                and not _is_sensitive_path(source)
                and source.stat().st_size <= self.max_external_file_bytes
            )
        except OSError:
            can_snapshot = False
        if not can_snapshot:
            return ReplayDependency(
                kind="local_file",
                identifier=identifier,
                status="unresolved",
                deterministic=False,
                detail="external path cannot be included in the replay seed",
            )
        return ReplayDependency(
            kind="local_file",
            identifier=identifier,
            status="snapshotted",
            deterministic=True,
            detail="bounded local file copied into the replay seed",
        )

    def _adapt_external_path(
        self,
        task_input: Any,
        *,
        raw_path: str,
        workspace_seed: Path,
        dependency: ReplayDependency | None = None,
    ) -> tuple[Any, ReplayDependency]:
        source = Path(raw_path).expanduser()
        dependency = dependency or self._external_path_dependency(raw_path)
        if not dependency.deterministic:
            replacement = "${AWORLD_REPLAY_UNRESOLVED_PATH}"
            return (
                _replace_in_value(task_input, raw_path, replacement),
                dependency,
            )
        fixture_name = (
            hashlib.sha256(source.read_bytes()).hexdigest()[:12]
            + "-"
            + source.name
        )
        relative = Path(".aworld_replay_fixtures") / fixture_name
        destination = workspace_seed / relative
        if not destination.exists():
            self._ensure_workspace_seed_capacity(
                workspace_seed,
                additional_files=1,
                additional_bytes=source.stat().st_size,
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(source, destination)
            except Exception:
                destination.unlink(missing_ok=True)
                raise
        replacement = f"{REPLAY_WORKSPACE_PLACEHOLDER}/{relative.as_posix()}"
        return (
            _replace_in_value(task_input, raw_path, replacement),
            dependency,
        )

    def _bind_dependency(
        self,
        dependency: ReplayDependency,
        *,
        context: ReplayAdapterContext,
        adapters: Sequence[ReplayDependencyAdapter],
    ) -> ReplayAdapterBinding | None:
        for adapter in adapters:
            binding = adapter.bind(dependency, context=context)
            if binding is not None:
                return binding
        return None

    def _snapshot_adapter_fixtures(
        self,
        binding: ReplayAdapterBinding,
        *,
        workspace_seed: Path,
    ) -> ReplayAdapterBinding:
        environment = dict(binding.environment)
        snapshotted_paths: list[str] = []
        adapter_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", binding.adapter_id).strip(
            ".-"
        ) or "adapter"
        for raw_path in binding.fixture_paths:
            source = Path(raw_path).expanduser()
            if (
                not source.is_file()
                or source.is_symlink()
                or _is_sensitive_path(source)
                or source.stat().st_size > self.max_external_file_bytes
            ):
                raise ReplayAdaptationError(
                    "replay adapter fixture must be a bounded non-secret regular file: "
                    f"{binding.adapter_id}"
                )
            content = source.read_bytes()
            fixture_name = hashlib.sha256(content).hexdigest()[:12] + "-" + source.name
            relative = (
                Path(".aworld_replay_adapter_fixtures")
                / adapter_name
                / fixture_name
            )
            destination = workspace_seed / relative
            if not destination.exists():
                self._ensure_workspace_seed_capacity(
                    workspace_seed,
                    additional_files=1,
                    additional_bytes=len(content),
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(source, destination)
                except Exception:
                    destination.unlink(missing_ok=True)
                    raise
            fixture_ref = f"{REPLAY_WORKSPACE_PLACEHOLDER}/{relative.as_posix()}"
            snapshotted_paths.append(fixture_ref)
            environment = {
                key: value.replace(str(source), fixture_ref)
                for key, value in environment.items()
            }
        return replace(
            binding,
            environment=environment,
            fixture_paths=tuple(snapshotted_paths),
        )

    def _copy_workspace_seed(
        self,
        source: Path,
        destination: Path,
        *,
        artifact_root: Path,
    ) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        tracked_paths = _git_tracked_workspace_paths(source)
        if tracked_paths is not None:
            self._copy_tracked_workspace_seed(
                source,
                destination,
                artifact_root=artifact_root,
                tracked_paths=tracked_paths,
            )
            self._assert_workspace_seed_limits(destination)
            return

        file_count = 0
        byte_count = 0
        for current_root, directory_names, file_names in os.walk(source):
            current = Path(current_root)
            relative_root = current.relative_to(source)
            target_root = destination / relative_root
            target_root.mkdir(parents=True, exist_ok=True)
            retained_directories: list[str] = []
            for name in directory_names:
                source_path = current / name
                if (
                    name in _IGNORED_DIRECTORY_NAMES
                    or _is_within(source_path, artifact_root)
                    or _is_sensitive_path(source_path)
                ):
                    continue
                try:
                    metadata = source_path.lstat()
                except OSError as exc:
                    raise ReplayAdaptationError(
                        f"cannot inspect replay seed input: {source_path.name}: {exc}"
                    ) from exc
                if stat.S_ISLNK(metadata.st_mode):
                    self._copy_internal_symlink(
                        source_path,
                        source_root=source,
                        destination_root=destination,
                        destination_path=target_root / name,
                        target_is_directory=True,
                    )
                    continue
                retained_directories.append(name)
            directory_names[:] = retained_directories
            for file_name in file_names:
                source_path = current / file_name
                if (
                    file_name.endswith(_IGNORED_FILE_SUFFIXES)
                    or _is_sensitive_path(source_path)
                    or _is_within(source_path, artifact_root)
                ):
                    continue
                try:
                    metadata = source_path.lstat()
                except OSError as exc:
                    raise ReplayAdaptationError(
                        f"cannot inspect replay seed input: {source_path.name}: {exc}"
                    ) from exc
                if stat.S_ISLNK(metadata.st_mode):
                    self._copy_internal_symlink(
                        source_path,
                        source_root=source,
                        destination_root=destination,
                        destination_path=target_root / file_name,
                        target_is_directory=False,
                    )
                    continue
                if not stat.S_ISREG(metadata.st_mode):
                    continue
                file_count += 1
                byte_count += metadata.st_size
                if file_count > self.max_workspace_files:
                    raise ReplayAdaptationError("workspace snapshot file limit exceeded")
                if byte_count > self.max_workspace_bytes:
                    raise ReplayAdaptationError("workspace snapshot byte limit exceeded")
                shutil.copy2(source_path, target_root / file_name)

        self._assert_workspace_seed_limits(destination)

    def _copy_tracked_workspace_seed(
        self,
        source: Path,
        destination: Path,
        *,
        artifact_root: Path,
        tracked_paths: Sequence[Path],
    ) -> None:
        file_count = 0
        byte_count = 0
        for relative in tracked_paths:
            if any(part in _IGNORED_DIRECTORY_NAMES for part in relative.parts):
                continue
            source_path = source / relative
            if (
                _is_within(source_path, artifact_root)
                or _is_sensitive_path(source_path)
                or source_path.name.endswith(_IGNORED_FILE_SUFFIXES)
            ):
                continue
            try:
                metadata = source_path.lstat()
            except FileNotFoundError:
                # A tracked deletion in the current working tree is part of the
                # snapshot state and therefore remains absent from the seed.
                continue
            except OSError as exc:
                raise ReplayAdaptationError(
                    f"cannot inspect replay seed input: {source_path.name}: {exc}"
                ) from exc
            destination_path = destination / relative
            if stat.S_ISLNK(metadata.st_mode):
                self._copy_internal_symlink(
                    source_path,
                    source_root=source,
                    destination_root=destination,
                    destination_path=destination_path,
                    target_is_directory=False,
                )
                continue
            if not stat.S_ISREG(metadata.st_mode):
                continue
            file_count += 1
            byte_count += metadata.st_size
            if file_count > self.max_workspace_files:
                raise ReplayAdaptationError("workspace snapshot file limit exceeded")
            if byte_count > self.max_workspace_bytes:
                raise ReplayAdaptationError("workspace snapshot byte limit exceeded")
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination_path)

    def _copy_internal_symlink(
        self,
        source_path: Path,
        *,
        source_root: Path,
        destination_root: Path,
        destination_path: Path,
        target_is_directory: bool,
    ) -> None:
        try:
            resolved = source_path.resolve(strict=True)
        except OSError:
            return
        if not _is_within(resolved, source_root) or _is_sensitive_path(resolved):
            return
        relative_target = resolved.relative_to(source_root.resolve())
        seeded_target = destination_root / relative_target
        rebased_target = os.path.relpath(seeded_target, start=destination_path.parent)
        destination_path.symlink_to(
            rebased_target,
            target_is_directory=target_is_directory,
        )

    def _assert_workspace_seed_limits(self, seed: Path) -> None:
        file_count, byte_count = self._workspace_seed_usage(seed)
        if file_count > self.max_workspace_files:
            raise ReplayAdaptationError("workspace snapshot file limit exceeded")
        if byte_count > self.max_workspace_bytes:
            raise ReplayAdaptationError("workspace snapshot byte limit exceeded")

    def _ensure_workspace_seed_capacity(
        self,
        seed: Path,
        *,
        additional_files: int,
        additional_bytes: int,
    ) -> None:
        file_count, byte_count = self._workspace_seed_usage(seed)
        if file_count + additional_files > self.max_workspace_files:
            raise ReplayAdaptationError("workspace snapshot file limit exceeded")
        if byte_count + additional_bytes > self.max_workspace_bytes:
            raise ReplayAdaptationError("workspace snapshot byte limit exceeded")

    @staticmethod
    def _workspace_seed_usage(seed: Path) -> tuple[int, int]:
        file_count = 0
        byte_count = 0
        for path in seed.rglob("*"):
            try:
                metadata = path.lstat()
            except OSError as exc:
                raise ReplayAdaptationError(
                    f"cannot inspect replay seed output: {path.name}: {exc}"
                ) from exc
            if stat.S_ISLNK(metadata.st_mode):
                file_count += 1
                byte_count += metadata.st_size
            elif stat.S_ISREG(metadata.st_mode):
                file_count += 1
                byte_count += metadata.st_size
            else:
                continue
        return file_count, byte_count


def materialize_replay_workspace(
    bundle: ReplayAdaptationBundle,
    destination: str | Path,
) -> Path:
    """Create a clean rollout workspace from a verified adaptation seed."""

    seed = Path(bundle.workspace_seed).expanduser().resolve()
    target = Path(os.path.abspath(str(Path(destination).expanduser())))
    if not seed.is_dir():
        raise ReplayAdaptationError(f"replay workspace seed does not exist: {seed}")
    if any(parent.is_symlink() for parent in target.parents):
        raise ReplayAdaptationError(
            "rollout workspace cannot have a symlinked parent"
        )
    if (
        target == seed
        or _is_within(target, seed)
        or _is_within(seed, target)
    ):
        raise ReplayAdaptationError(
            "rollout workspace and replay seed cannot overlap"
        )
    current_fingerprint = _json_fingerprint(_workspace_manifest(seed))
    if current_fingerprint != bundle.workspace_seed_fingerprint:
        raise ReplayAdaptationError(
            "replay workspace seed changed after adaptation compilation"
        )
    if target.is_symlink():
        target.unlink()
    elif target.exists():
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    target.parent.mkdir(parents=True, exist_ok=True)
    _clone_or_copy_workspace(seed, target)
    return target


def _clone_or_copy_workspace(seed: Path, target: Path) -> None:
    """Materialize a writable rollout using filesystem copy-on-write when available."""

    clone_command: list[str] | None = None
    if sys.platform == "darwin":
        clone_command = ["cp", "-cR", f"{seed}/.", str(target)]
    elif sys.platform.startswith("linux"):
        clone_command = [
            "cp",
            "--reflink=always",
            "-a",
            f"{seed}/.",
            str(target),
        ]
    if clone_command is not None:
        target.mkdir(parents=True, exist_ok=False)
        try:
            completed = subprocess.run(
                clone_command,
                text=True,
                capture_output=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            completed = None
        if completed is not None and completed.returncode == 0:
            return
        shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(seed, target, symlinks=True)


def _normalize_workspace_paths(text: str, *, workspace_root: Path) -> str:
    normalized = text.replace(str(workspace_root), REPLAY_WORKSPACE_PLACEHOLDER)
    repository_name = re.escape(workspace_root.name)
    stale_pattern = (
        rf"/(?:Users|home)/[^/\s]+/Documents/workspace/{repository_name}"
    )
    return re.sub(stale_pattern, REPLAY_WORKSPACE_PLACEHOLDER, normalized)


def _git_tracked_workspace_paths(source: Path) -> tuple[Path, ...] | None:
    """Return current tracked paths for a Git-backed replay seed.

    The current working-tree bytes are copied, so local edits to tracked source are
    preserved. Untracked files are excluded because they cannot be attributed to
    the recorded initial state; explicit external inputs are snapshotted later by
    dependency adaptation.
    """

    try:
        root_result = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if root_result.returncode != 0:
        return None
    try:
        git_root = Path(os.fsdecode(root_result.stdout).strip()).resolve()
        source_prefix = source.relative_to(git_root)
    except (OSError, ValueError):
        return None
    try:
        files_result = subprocess.run(
            ["git", "-C", str(git_root), "ls-files", "--cached", "--full-name", "-z"],
            check=False,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if files_result.returncode != 0:
        return None
    tracked: list[Path] = []
    for raw_path in files_result.stdout.split(b"\0"):
        if not raw_path:
            continue
        root_relative = Path(os.fsdecode(raw_path))
        try:
            source_relative = root_relative.relative_to(source_prefix)
        except ValueError:
            continue
        if source_relative.parts:
            tracked.append(source_relative)
    return tuple(sorted(set(tracked), key=lambda item: item.as_posix()))


def _absolute_local_path_references(text: str) -> tuple[str, ...]:
    paths: list[str] = []
    for match in _ABSOLUTE_LOCAL_PATH.finditer(text):
        raw_path = match.group(0)
        prefix = text[max(0, match.start() - 64) : match.start()]
        if "{" in raw_path or "}" in raw_path:
            continue
        if _NON_FILE_PATH_CONTEXT.search(prefix):
            continue
        if raw_path not in paths:
            paths.append(raw_path)
    return tuple(paths)


def _normalize_value(value: Any, transform) -> Any:
    if isinstance(value, str):
        return transform(value)
    if isinstance(value, Mapping):
        return {str(key): _normalize_value(item, transform) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_value(item, transform) for item in value]
    if isinstance(value, tuple):
        return tuple(_normalize_value(item, transform) for item in value)
    return value


def _replace_in_value(value: Any, source: str, destination: str) -> Any:
    return _normalize_value(value, lambda text: text.replace(source, destination))


def _text_fragments(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return "\n".join(_text_fragments(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return "\n".join(_text_fragments(item) for item in value)
    return ""


def _case_tool_names(case: EvalCase) -> tuple[str, ...]:
    if case.trace_pack is None:
        return ()
    return tuple(
        dict.fromkeys(
            name
            for step in case.trace_pack.steps
            for name in step.tool_names
            if name
        )
    )


def _is_stateful_tool_name(tool_name: str) -> bool:
    tokens = tuple(
        token
        for token in re.split(r"[._:/-]+", tool_name.lower())
        if token
    )
    if any(token in _STATEFUL_BROWSER_TOOL_TOKENS for token in tokens):
        return True
    return any(
        (token == "computer" and next_token == "use")
        or (token == "web" and next_token in _STATEFUL_WEB_ACTION_TOKENS)
        for token, next_token in zip(tokens, tokens[1:])
    ) or tokens == ("web",)


def _detected_runtime_dependencies(
    case: EvalCase,
    task_input: Any,
) -> tuple[ReplayDependency, ...]:
    task_text = _text_fragments(task_input)
    dependencies: list[ReplayDependency] = []
    context_incomplete = _case_context_incomplete(case, task_input)
    if context_incomplete is not None:
        dependencies.append(
            ReplayDependency(
                kind="conversation_context",
                identifier="prior-task-context",
                status="context_incomplete",
                deterministic=False,
                detail=context_incomplete,
            )
        )
    local_endpoints = tuple(
        dict.fromkeys(
            _normalize_detected_url(value)
            for value in _LOCAL_ENDPOINT.findall(task_text)
        )
    )
    for endpoint in local_endpoints:
        dependencies.append(
            ReplayDependency(
                kind="local_endpoint",
                identifier=endpoint,
                status="runtime_required",
                deterministic=False,
                detail="stateful local endpoint requires a replay capability",
            )
        )
    for url in dict.fromkeys(
        _normalize_detected_url(value)
        for value in _HTTP_RESOURCE.findall(task_text)
    ):
        if url in local_endpoints:
            continue
        dependencies.append(
            ReplayDependency(
                kind="http_resource",
                identifier=url,
                status="runtime_required",
                deterministic=False,
                detail="live HTTP content requires a deterministic replay capability",
            )
        )
    for tool_name in _case_tool_names(case):
        if not _is_stateful_tool_name(tool_name):
            continue
        dependencies.append(
            ReplayDependency(
                kind="stateful_tool",
                identifier=tool_name,
                status="runtime_required",
                deterministic=False,
                detail="stateful trace tool requires a replay capability",
            )
        )
    return tuple(dependencies)


def _case_dependency_input(
    case: EvalCase,
    *,
    normalized_task_input: Any,
    workspace_root: Path,
) -> Any:
    """Use the current task for dependencies; prior turns remain replay evidence."""

    snapshot = case.context_snapshot
    if snapshot is None or not snapshot.prior_turns:
        return normalized_task_input
    transcript = "\n".join(
        f"{turn.role.title()}: {turn.content}"
        for turn in snapshot.prior_turns
    )
    reconstructed_prefix = (
        "Recorded prior task context "
        f"[{snapshot.link_strategy or 'recorded'}]:\n{transcript}\n\n"
        "Current task:\n"
    )
    if isinstance(normalized_task_input, str) and normalized_task_input.startswith(
        reconstructed_prefix
    ):
        return normalized_task_input[len(reconstructed_prefix) :]
    if isinstance(normalized_task_input, Mapping):
        content = normalized_task_input.get("content")
        if isinstance(content, str) and content.startswith(reconstructed_prefix):
            return {
                **dict(normalized_task_input),
                "content": content[len(reconstructed_prefix) :],
            }
    return _normalize_value(
        snapshot.task_input,
        lambda text: _normalize_workspace_paths(
            text,
            workspace_root=workspace_root,
        ),
    )


def _normalize_detected_url(value: str) -> str:
    # URL regexes that stop only at ASCII whitespace can absorb adjacent
    # natural-language prose in scripts that use full-width punctuation. Raw
    # non-ASCII punctuation is not a legal URI delimiter unless percent-
    # encoded, so treat the first such punctuation mark as the evidence URL
    # boundary while preserving Unicode letters in valid IRIs.
    for index, character in enumerate(value):
        if (
            ord(character) > 127
            and unicodedata.category(character).startswith("P")
        ):
            value = value[:index]
            break
    normalized = value.rstrip(".,")
    for opening, closing in (("(", ")"), ("[", "]"), ("{", "}")):
        while normalized.endswith(closing) and normalized.count(closing) > normalized.count(
            opening
        ):
            normalized = normalized[:-1]
    return normalized


def _case_has_reconstructed_context(case: EvalCase) -> bool:
    snapshot = case.context_snapshot
    return bool(snapshot is not None and snapshot.prior_turns)


def _case_context_incomplete(case: EvalCase, task_input: Any) -> str | None:
    snapshot = case.context_snapshot
    if snapshot is not None and snapshot.context_status == "incomplete":
        if snapshot.context_reason == "inherited_incomplete_context":
            return "required prior task context inherits an incomplete conversation root"
        return "required prior task context is absent"
    if (
        task_input_requires_prior_context(task_input)
        and not _case_has_reconstructed_context(case)
    ):
        return "required prior task context is absent"
    return None


def _context_evidence_ref(case: EvalCase) -> str:
    snapshot = case.context_snapshot
    if snapshot is not None:
        return f"context:{case.case_id}:{snapshot.fingerprint}"
    return f"case:{case.case_id}:input"


def _requirement_id(kind: str, identifier: str) -> str:
    digest = hashlib.sha256(
        f"{kind}\0{identifier}".encode("utf-8")
    ).hexdigest()[:20]
    return f"requirement-{digest}"


def _case_readiness(dependencies: Sequence[ReplayDependency]) -> str:
    statuses = {dependency.status for dependency in dependencies}
    if "context_incomplete" in statuses:
        return "context_incomplete"
    if "unresolved" in statuses:
        return "unresolved"
    if "runtime_required" in statuses or any(
        not dependency.deterministic for dependency in dependencies
    ):
        return "runtime_required"
    return "ready"


def _safe_adapter_environment(environment: Mapping[str, str]) -> dict[str, str]:
    return {
        str(key): str(value)
        for key, value in environment.items()
        if not _SENSITIVE_ENV_KEY.search(str(key))
    }


def _is_sensitive_path(path: Path) -> bool:
    name = path.name.lower()
    return (
        name in _SENSITIVE_NAMES
        or name.startswith(".env.")
        or "credential" in name
        or "secret" in name
    )


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def _workspace_manifest(root: Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            entries.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "type": "symlink",
                    "target": os.readlink(path),
                }
            )
            continue
        if not path.is_file():
            continue
        data = path.read_bytes()
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "type": "file",
                "size": len(data),
                "mode": stat.S_IMODE(path.stat().st_mode),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    return {
        "schema_version": "aworld.self_evolve.workspace_manifest.v1",
        "entries": entries,
    }


def _environment_snapshot(
    cases: Sequence[ReplayCaseAdaptation],
) -> dict[str, Any]:
    environment_keys = ("LANG", "LC_ALL", "LC_CTYPE", "TZ")
    return {
        "schema_version": "aworld.self_evolve.environment_snapshot.v1",
        "runtime": {
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "platform": sys.platform,
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "environment": {
            key: os.environ[key]
            for key in environment_keys
            if key in os.environ
        },
        "tool_names": sorted(
            {
                tool_name
                for case in cases
                for tool_name in case.tool_names
            }
        ),
    }


def _environment_identity_fingerprint(
    snapshot: Mapping[str, Any],
) -> str:
    """Fingerprint immutable runtime identity, not workload requirements.

    ``tool_names`` describes the selected dataset/case panel. Staged screening
    intentionally expands that panel, so treating its tools as environment
    identity creates false drift within a healthy run. Tool requirements remain
    protected by the adaptation fingerprint, which includes every compiled case.
    """

    return _json_fingerprint(
        {
            "schema_version": snapshot.get("schema_version"),
            "runtime": snapshot.get("runtime"),
            "environment": snapshot.get("environment"),
        }
    )


def _json_fingerprint(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str),
        encoding="utf-8",
    )
    temporary.replace(path)
