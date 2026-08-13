from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

from aworld.self_evolve.counterexamples import normalize_counterexample
from aworld.self_evolve.replay_capability import (
    FrozenReplayCapability,
    REPLAY_CAPABILITY_SCHEMA_VERSION,
    REPLAY_RESPONSE_INDEX_CONSUMER,
    REPLAY_RESPONSE_REQUIREMENT_ID_ENV,
    REPLAY_RESPONSE_SELECTOR_POLICY,
    REPLAY_RESPONSE_SERVICE_ID_ENV,
    ReplayProtocolProbe,
    ReplayServiceSpec,
    recorded_response_index_source_behavior_proof,
)
from aworld.self_evolve.sanitization import sanitize_path_ref, sanitize_text
from aworld.self_evolve.schema_diagnostics import SchemaFieldRepairConstraint
from aworld.self_evolve.types import CandidateVariant


_SOURCE_SUFFIXES = frozenset(
    {".c", ".cc", ".go", ".java", ".js", ".jsx", ".py", ".rb", ".rs", ".sh", ".ts", ".tsx"}
)
_MAX_CONTRACT_FILES = 16
_MAX_OBSERVED_OPERATIONS = 8
_MAX_CONFORMANCE_REPORT_CASES = 100
_MAX_CONFORMANCE_REPORT_GROUPS = 64
_ARTIFACT_LIFECYCLE_CONSTRAINT_SCHEMA_VERSION = (
    "aworld.self_evolve.artifact_lifecycle_constraint.v1"
)


@dataclass(frozen=True)
class ArtifactLifecycleConstraint:
    """Executable admission limits learned from an evidence-policy failure.

    The constraint contains only bounded lifecycle counters.  It deliberately
    excludes artifact paths and payloads so it can cross report and Campaign
    boundaries, while the screening replay supplies the behavioral proof.
    """

    max_artifact_files: int = 1
    max_artifact_bytes: int = 2_000_000
    max_collection_tool_calls: int = 8
    require_manifest: bool = True
    require_artifact_reuse: bool = True
    require_stop_after_evidence_ready: bool = True
    schema_version: str = _ARTIFACT_LIFECYCLE_CONSTRAINT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != _ARTIFACT_LIFECYCLE_CONSTRAINT_SCHEMA_VERSION:
            raise ValueError("artifact lifecycle constraint schema is unsupported")
        for field_name, value, upper_bound in (
            ("max_artifact_files", self.max_artifact_files, 256),
            ("max_artifact_bytes", self.max_artifact_bytes, 128 * 1024 * 1024),
            ("max_collection_tool_calls", self.max_collection_tool_calls, 1_024),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value <= 0
                or value > upper_bound
            ):
                raise ValueError(
                    f"artifact lifecycle constraint {field_name} is invalid"
                )
        for field_name in (
            "require_manifest",
            "require_artifact_reuse",
            "require_stop_after_evidence_ready",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(
                    f"artifact lifecycle constraint {field_name} must be boolean"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "max_artifact_files": self.max_artifact_files,
            "max_artifact_bytes": self.max_artifact_bytes,
            "max_collection_tool_calls": self.max_collection_tool_calls,
            "require_manifest": self.require_manifest,
            "require_artifact_reuse": self.require_artifact_reuse,
            "require_stop_after_evidence_ready": (
                self.require_stop_after_evidence_ready
            ),
        }

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, object],
    ) -> "ArtifactLifecycleConstraint":
        def required_int(field_name: str, default: int) -> int:
            raw = value.get(field_name, default)
            if not isinstance(raw, int) or isinstance(raw, bool):
                raise ValueError(
                    f"artifact lifecycle constraint {field_name} is invalid"
                )
            return raw

        return cls(
            schema_version=str(
                value.get("schema_version")
                or _ARTIFACT_LIFECYCLE_CONSTRAINT_SCHEMA_VERSION
            ),
            max_artifact_files=required_int("max_artifact_files", 1),
            max_artifact_bytes=required_int(
                "max_artifact_bytes",
                2_000_000,
            ),
            max_collection_tool_calls=required_int(
                "max_collection_tool_calls",
                8,
            ),
            require_manifest=value.get("require_manifest", True) is True,
            require_artifact_reuse=(
                value.get("require_artifact_reuse", True) is True
            ),
            require_stop_after_evidence_ready=(
                value.get("require_stop_after_evidence_ready", True) is True
            ),
        )


@dataclass(frozen=True)
class ExactRepairProbe:
    kind: str
    path: str
    expected_response: str


@dataclass(frozen=True)
class FixtureDerivedProbeConstraint:
    """Content-free location contract for a fixture-derived probe assertion."""

    requirement_id: str | None = field(compare=False)
    kind: str
    path: str
    max_response_chars: int = 4_096
    requirement_identity_digest: str | None = None

    def __post_init__(self) -> None:
        if self.requirement_id is not None and (
            not self.requirement_id.strip() or len(self.requirement_id) > 240
        ):
            raise ValueError(
                "fixture probe constraint requires a bounded requirement id"
            )
        identity_digest = self.requirement_identity_digest
        if identity_digest is not None and re.fullmatch(
            r"[0-9a-f]{64}",
            identity_digest,
        ) is None:
            raise ValueError("fixture probe requirement identity digest is invalid")
        if self.requirement_id is None and identity_digest is None:
            raise ValueError("fixture probe constraint requires a requirement identity")
        if self.requirement_id is not None:
            computed_digest = self._digest_requirement_id(self.requirement_id)
            if identity_digest is not None and identity_digest != computed_digest:
                raise ValueError(
                    "fixture probe requirement identity digest conflicts with raw "
                    "identity"
                )
            object.__setattr__(
                self,
                "requirement_identity_digest",
                computed_digest,
            )
        if self.kind not in {"http", "tcp", "websocket"}:
            raise ValueError("fixture probe constraint kind is unsupported")
        if not self.path.startswith("/") or len(self.path) > 2_048:
            raise ValueError(
                "fixture probe constraint path must be bounded and absolute"
            )
        if (
            not isinstance(self.max_response_chars, int)
            or isinstance(self.max_response_chars, bool)
            or self.max_response_chars <= 0
            or self.max_response_chars > 16_384
        ):
            raise ValueError("fixture probe response bound is invalid")

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "requirement_identity_digest": self.requirement_identity_digest,
            "kind": self.kind,
            "path": self.path,
            "max_response_chars": self.max_response_chars,
        }
        if self.requirement_id is not None:
            result["requirement_id"] = self.requirement_id
        return result

    def to_public_dict(self) -> dict[str, object]:
        """Return the stable identity needed to inherit the constraint safely."""

        return {
            "requirement_identity_digest": self.requirement_identity_digest,
            "kind": self.kind,
            "path": self.path,
            "max_response_chars": self.max_response_chars,
        }

    def matches_requirement_id(self, requirement_id: str) -> bool:
        return self.requirement_identity_digest == self._digest_requirement_id(
            requirement_id
        )

    @staticmethod
    def _digest_requirement_id(requirement_id: str) -> str:
        return hashlib.sha256(requirement_id.strip().encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, object],
    ) -> "FixtureDerivedProbeConstraint":
        raw_max = value.get("max_response_chars", 4_096)
        if not isinstance(raw_max, int) or isinstance(raw_max, bool):
            raise ValueError("fixture probe response bound is invalid")
        return cls(
            requirement_id=(
                str(value.get("requirement_id"))
                if isinstance(value.get("requirement_id"), str)
                else None
            ),
            kind=str(value.get("kind") or ""),
            path=str(value.get("path") or ""),
            max_response_chars=raw_max,
            requirement_identity_digest=(
                str(value.get("requirement_identity_digest"))
                if isinstance(value.get("requirement_identity_digest"), str)
                else None
            ),
        )


@dataclass(frozen=True)
class RuntimeResponseConstraint:
    """Payload-free runtime response semantics discovered by an executable probe."""

    constraint_kind: str
    response_source: str
    minimum_recorded_value_matches: int
    maximum_response_bytes: int
    preserve_decoded_container: bool
    allow_bounded_projection: bool
    projection_minimum_scalar_descendants: int
    probe_kind: str
    probe_path: str

    def __post_init__(self) -> None:
        if self.constraint_kind != "recorded_response_context":
            raise ValueError("runtime response constraint kind is unsupported")
        if self.response_source != "AWORLD_REPLAY_RESPONSE_INDEX":
            raise ValueError("runtime response constraint source is unsupported")
        if self.preserve_decoded_container is not True:
            raise ValueError(
                "runtime response constraint must preserve the decoded container"
            )
        if self.allow_bounded_projection is not True:
            raise ValueError(
                "runtime response constraint must allow a bounded projection"
            )
        for field_name, value, upper_bound in (
            (
                "minimum_recorded_value_matches",
                self.minimum_recorded_value_matches,
                16,
            ),
            ("maximum_response_bytes", self.maximum_response_bytes, 1024 * 1024),
            (
                "projection_minimum_scalar_descendants",
                self.projection_minimum_scalar_descendants,
                16,
            ),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value <= 0
                or value > upper_bound
            ):
                raise ValueError(f"runtime response constraint {field_name} is invalid")
        if self.probe_kind not in {
            "http",
            "tcp",
            "websocket",
            "task_plane_json",
        }:
            raise ValueError("runtime response constraint probe kind is unsupported")
        if not self.probe_path.startswith("/") or len(self.probe_path) > 2_048:
            raise ValueError("runtime response constraint probe path is invalid")

    @property
    def identity_digest(self) -> str:
        encoded = json.dumps(
            self.to_dict(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "aworld.self_evolve.runtime_response_constraint.v1",
            "constraint_kind": self.constraint_kind,
            "response_source": self.response_source,
            "minimum_recorded_value_matches": (
                self.minimum_recorded_value_matches
            ),
            "maximum_response_bytes": self.maximum_response_bytes,
            "preserve_decoded_container": self.preserve_decoded_container,
            "allow_bounded_projection": self.allow_bounded_projection,
            "projection_minimum_scalar_descendants": (
                self.projection_minimum_scalar_descendants
            ),
            "probe_kind": self.probe_kind,
            "probe_path": self.probe_path,
        }

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, object],
    ) -> "RuntimeResponseConstraint":
        expected_schema = "aworld.self_evolve.runtime_response_constraint.v1"
        if value.get("schema_version") not in {None, expected_schema}:
            raise ValueError("runtime response constraint schema is unsupported")

        def required_int(field_name: str) -> int:
            raw = value.get(field_name)
            if not isinstance(raw, int) or isinstance(raw, bool):
                raise ValueError(
                    f"runtime response constraint {field_name} is invalid"
                )
            return raw

        return cls(
            constraint_kind=str(value.get("constraint_kind") or ""),
            response_source=str(value.get("response_source") or ""),
            minimum_recorded_value_matches=required_int(
                "minimum_recorded_value_matches"
            ),
            maximum_response_bytes=required_int("maximum_response_bytes"),
            preserve_decoded_container=(
                value.get("preserve_decoded_container") is True
            ),
            allow_bounded_projection=(
                value.get("allow_bounded_projection") is True
            ),
            projection_minimum_scalar_descendants=required_int(
                "projection_minimum_scalar_descendants"
            ),
            probe_kind=str(value.get("probe_kind") or ""),
            probe_path=str(value.get("probe_path") or ""),
        )


@dataclass(frozen=True)
class RepairConformanceContract:
    focus_candidate_id: str
    failure_codes: tuple[str, ...]
    interaction_progress: int
    base_file_fingerprints: Mapping[str, str]
    required_branch_paths: tuple[str, ...]
    base_branch_fingerprints: Mapping[str, str]
    base_fixture_selector_fingerprints: Mapping[str, str] = field(
        default_factory=dict
    )
    manifest_path: str | None = None
    compiler_path: str | None = None
    runtime_paths: tuple[str, ...] = ()
    exact_probe: ExactRepairProbe | None = None
    late_observed_operations: tuple[str, ...] = ()
    requires_compiler_fixture_reconstruction: bool = False
    requires_fixture_derived_probe: bool = False
    required_fixture_probe_operations: tuple[str, ...] = ()
    fixture_probe_constraints: tuple[FixtureDerivedProbeConstraint, ...] = ()
    schema_field_constraints: tuple[SchemaFieldRepairConstraint, ...] = ()
    runtime_response_constraints: tuple[RuntimeResponseConstraint, ...] = ()
    required_runtime_transitions: tuple[str, ...] = ()
    artifact_lifecycle_constraint: ArtifactLifecycleConstraint | None = None

    @property
    def contract_identity(self) -> str:
        """Return the immutable identity used to lease repair work.

        Private expected payloads and unrelated candidate ids are deliberately
        excluded. A lease changes whenever the actionable constraint set, its
        source owner, or the focused baseline being repaired changes.
        """

        return repair_conformance_contract_identity(self)

    def to_dict(self) -> dict[str, object]:
        """Return the private execution contract.

        This representation may contain exact assertions and must only travel
        through :class:`OptimizerResult.private_context`.  Reports, prompts,
        feedback, and optimizer diagnostics must use :meth:`to_public_dict`.
        """
        return {
            "contract_identity": self.contract_identity,
            "focus_candidate_id": self.focus_candidate_id,
            "failure_codes": list(self.failure_codes),
            "interaction_progress": self.interaction_progress,
            "base_file_fingerprints": dict(self.base_file_fingerprints),
            "required_branch_paths": list(self.required_branch_paths),
            "base_branch_fingerprints": dict(self.base_branch_fingerprints),
            "base_fixture_selector_fingerprints": dict(
                self.base_fixture_selector_fingerprints
            ),
            "manifest_path": self.manifest_path,
            "compiler_path": self.compiler_path,
            "runtime_paths": list(self.runtime_paths),
            "exact_probe": (
                {
                    "kind": self.exact_probe.kind,
                    "path": self.exact_probe.path,
                    "expected_response": self.exact_probe.expected_response,
                }
                if self.exact_probe is not None
                else None
            ),
            "late_observed_operations": list(self.late_observed_operations),
            "requires_compiler_fixture_reconstruction": (
                self.requires_compiler_fixture_reconstruction
            ),
            "requires_fixture_derived_probe": self.requires_fixture_derived_probe,
            "required_fixture_probe_operations": list(
                self.required_fixture_probe_operations
            ),
            "fixture_probe_constraints": [
                item.to_dict() for item in self.fixture_probe_constraints
            ],
            "schema_field_constraints": [
                item.to_dict() for item in self.schema_field_constraints
            ],
            "runtime_response_constraints": [
                item.to_dict() for item in self.runtime_response_constraints
            ],
            "required_runtime_transitions": list(
                self.required_runtime_transitions
            ),
            "artifact_lifecycle_constraint": (
                self.artifact_lifecycle_constraint.to_dict()
                if self.artifact_lifecycle_constraint is not None
                else None
            ),
        }

    def to_public_dict(self) -> dict[str, object]:
        """Return a content-free projection safe for prompts and artifacts."""

        exact_probe = None
        if self.exact_probe is not None:
            encoded = self.exact_probe.expected_response.encode("utf-8")
            exact_probe = {
                "kind": self.exact_probe.kind,
                "path": self.exact_probe.path,
                "expected_response_fingerprint": (
                    "sha256:" + hashlib.sha256(encoded).hexdigest()
                ),
                "expected_response_shape": {
                    "kind": "text",
                    "size_bucket": max(1, len(encoded)).bit_length(),
                },
                "private_contract_ref": (
                    "repair-contract:"
                    + hashlib.sha256(
                        (
                            self.focus_candidate_id
                            + "\0"
                            + self.exact_probe.kind
                            + "\0"
                            + self.exact_probe.path
                            + "\0"
                            + self.exact_probe.expected_response
                        ).encode("utf-8")
                    ).hexdigest()[:24]
                ),
            }
        return {
            "projection_schema_version": (
                "aworld.self_evolve.repair_conformance.public.v1"
            ),
            "contract_identity": self.contract_identity,
            "focus_candidate_id": self.focus_candidate_id,
            "failure_codes": list(self.failure_codes),
            "interaction_progress": self.interaction_progress,
            "base_file_fingerprints": dict(self.base_file_fingerprints),
            "required_branch_paths": list(self.required_branch_paths),
            "base_branch_fingerprints": dict(self.base_branch_fingerprints),
            "base_fixture_selector_fingerprints": dict(
                self.base_fixture_selector_fingerprints
            ),
            "manifest_path": self.manifest_path,
            "compiler_path": self.compiler_path,
            "runtime_paths": list(self.runtime_paths),
            "exact_probe": exact_probe,
            "late_observed_operations": list(self.late_observed_operations),
            "requires_compiler_fixture_reconstruction": (
                self.requires_compiler_fixture_reconstruction
            ),
            "requires_fixture_derived_probe": self.requires_fixture_derived_probe,
            "required_fixture_probe_operations": list(
                self.required_fixture_probe_operations
            ),
            "fixture_probe_constraints": [
                item.to_public_dict() for item in self.fixture_probe_constraints
            ],
            "schema_field_constraints": [
                item.to_dict() for item in self.schema_field_constraints
            ],
            "runtime_response_constraints": [
                item.to_dict() for item in self.runtime_response_constraints
            ],
            "required_runtime_transitions": list(
                self.required_runtime_transitions
            ),
            "artifact_lifecycle_constraint": (
                self.artifact_lifecycle_constraint.to_dict()
                if self.artifact_lifecycle_constraint is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "RepairConformanceContract":
        if value.get("projection_schema_version") is not None:
            raise ValueError(
                "public repair conformance projections are not execution contracts"
            )
        exact_raw = value.get("exact_probe")
        exact_probe = (
            ExactRepairProbe(
                kind=str(exact_raw.get("kind") or ""),
                path=str(exact_raw.get("path") or "/"),
                expected_response=str(exact_raw.get("expected_response") or ""),
            )
            if isinstance(exact_raw, Mapping)
            else None
        )
        raw_fingerprints = value.get("base_file_fingerprints")
        fingerprints = (
            {
                str(path): str(fingerprint)
                for path, fingerprint in raw_fingerprints.items()
                if isinstance(path, str) and isinstance(fingerprint, str)
            }
            if isinstance(raw_fingerprints, Mapping)
            else {}
        )
        raw_branch_fingerprints = value.get("base_branch_fingerprints")
        branch_fingerprints = (
            {
                str(key): str(fingerprint)
                for key, fingerprint in raw_branch_fingerprints.items()
                if isinstance(key, str) and isinstance(fingerprint, str)
            }
            if isinstance(raw_branch_fingerprints, Mapping)
            else {}
        )
        raw_selector_fingerprints = value.get(
            "base_fixture_selector_fingerprints"
        )
        selector_fingerprints = (
            {
                str(key): str(fingerprint)
                for key, fingerprint in raw_selector_fingerprints.items()
                if isinstance(key, str) and isinstance(fingerprint, str)
            }
            if isinstance(raw_selector_fingerprints, Mapping)
            else {}
        )
        raw_probe_constraints = value.get("fixture_probe_constraints", ())
        if not isinstance(raw_probe_constraints, (list, tuple)):
            raise ValueError("fixture probe constraints must be an array")
        probe_constraints = tuple(
            FixtureDerivedProbeConstraint.from_dict(item)
            for item in raw_probe_constraints
            if isinstance(item, Mapping)
        )
        if len(probe_constraints) != len(raw_probe_constraints):
            raise ValueError("fixture probe constraints contain invalid entries")
        raw_schema_constraints = value.get("schema_field_constraints", ())
        if not isinstance(raw_schema_constraints, (list, tuple)):
            raise ValueError("schema field constraints must be an array")
        schema_constraints = tuple(
            SchemaFieldRepairConstraint.from_dict(item)
            for item in raw_schema_constraints
            if isinstance(item, Mapping)
        )
        if len(schema_constraints) != len(raw_schema_constraints):
            raise ValueError("schema field constraints contain invalid entries")
        raw_runtime_response_constraints = value.get(
            "runtime_response_constraints",
            (),
        )
        if not isinstance(raw_runtime_response_constraints, (list, tuple)):
            raise ValueError("runtime response constraints must be an array")
        runtime_response_constraints = tuple(
            RuntimeResponseConstraint.from_dict(item)
            for item in raw_runtime_response_constraints
            if isinstance(item, Mapping)
        )
        if len(runtime_response_constraints) != len(
            raw_runtime_response_constraints
        ):
            raise ValueError("runtime response constraints contain invalid entries")
        raw_artifact_lifecycle_constraint = value.get(
            "artifact_lifecycle_constraint"
        )
        artifact_lifecycle_constraint = (
            ArtifactLifecycleConstraint.from_dict(
                raw_artifact_lifecycle_constraint
            )
            if isinstance(raw_artifact_lifecycle_constraint, Mapping)
            else None
        )
        if raw_artifact_lifecycle_constraint is not None and (
            artifact_lifecycle_constraint is None
        ):
            raise ValueError("artifact lifecycle constraint must be a mapping")
        return cls(
            focus_candidate_id=str(value.get("focus_candidate_id") or ""),
            failure_codes=_string_tuple(value.get("failure_codes")),
            interaction_progress=_non_negative_int(value.get("interaction_progress")),
            base_file_fingerprints=fingerprints,
            required_branch_paths=_string_tuple(value.get("required_branch_paths")),
            base_branch_fingerprints=branch_fingerprints,
            base_fixture_selector_fingerprints=selector_fingerprints,
            manifest_path=(
                str(value.get("manifest_path"))
                if isinstance(value.get("manifest_path"), str)
                else None
            ),
            compiler_path=(
                str(value.get("compiler_path"))
                if isinstance(value.get("compiler_path"), str)
                else None
            ),
            runtime_paths=_string_tuple(value.get("runtime_paths")),
            exact_probe=exact_probe,
            late_observed_operations=_string_tuple(
                value.get("late_observed_operations")
            ),
            requires_compiler_fixture_reconstruction=(
                value.get("requires_compiler_fixture_reconstruction") is True
            ),
            requires_fixture_derived_probe=(
                value.get("requires_fixture_derived_probe") is True
            ),
            required_fixture_probe_operations=_string_tuple(
                value.get("required_fixture_probe_operations")
            ),
            fixture_probe_constraints=probe_constraints,
            schema_field_constraints=schema_constraints,
            runtime_response_constraints=runtime_response_constraints,
            required_runtime_transitions=_string_tuple(
                value.get("required_runtime_transitions")
            ),
            artifact_lifecycle_constraint=artifact_lifecycle_constraint,
        )

    @classmethod
    def from_public_dict(
        cls,
        value: Mapping[str, object],
    ) -> "RepairConformanceContract":
        """Restore the executable, payload-free part of a public contract.

        Exact response assertions intentionally remain private and therefore
        cannot be reconstructed here.  Every typed structural constraint and
        source-owner locator is safe to restore and must survive feedback,
        report, and Campaign boundaries without depending on diagnostic nesting.
        """

        if (
            value.get("projection_schema_version")
            != "aworld.self_evolve.repair_conformance.public.v1"
        ):
            raise ValueError("unsupported public repair conformance projection")
        private_shape = dict(value)
        private_shape.pop("projection_schema_version", None)
        private_shape["exact_probe"] = None
        return cls.from_dict(private_shape)


def repair_conformance_contract_identity(
    contract: RepairConformanceContract | Mapping[str, object],
) -> str:
    """Fingerprint the complete actionable repair lease.

    This is intentionally broader than the historical schema-field fingerprint:
    required source paths and the focused baseline are part of correctness, not
    reporting metadata.
    """

    if isinstance(contract, RepairConformanceContract):
        payload = {
            "focus_candidate_id": contract.focus_candidate_id,
            "failure_codes": list(contract.failure_codes),
            "base_file_fingerprints": dict(contract.base_file_fingerprints),
            "required_branch_paths": list(contract.required_branch_paths),
            "base_branch_fingerprints": dict(contract.base_branch_fingerprints),
            "base_fixture_selector_fingerprints": dict(
                contract.base_fixture_selector_fingerprints
            ),
            "manifest_path": contract.manifest_path,
            "compiler_path": contract.compiler_path,
            "runtime_paths": list(contract.runtime_paths),
            "fixture_probe_constraints": [
                item.to_public_dict() for item in contract.fixture_probe_constraints
            ],
            "schema_field_constraints": [
                item.to_dict() for item in contract.schema_field_constraints
            ],
            "runtime_response_constraints": [
                item.to_dict() for item in contract.runtime_response_constraints
            ],
            "required_runtime_transitions": list(
                contract.required_runtime_transitions
            ),
        }
    else:
        payload = {
            key: contract.get(key)
            for key in (
                "focus_candidate_id",
                "failure_codes",
                "base_file_fingerprints",
                "required_branch_paths",
                "base_branch_fingerprints",
                "base_fixture_selector_fingerprints",
                "manifest_path",
                "compiler_path",
                "runtime_paths",
                "fixture_probe_constraints",
                "schema_field_constraints",
                "runtime_response_constraints",
                "required_runtime_transitions",
            )
        }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return "repair-contract:sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class RepairConformanceResult:
    passed: bool
    code: str
    reason: str
    details: Mapping[str, object]
    failure_class: str | None = "candidate"
    repairable: bool = True

    def __post_init__(self) -> None:
        if self.passed:
            object.__setattr__(self, "failure_class", None)
            object.__setattr__(self, "repairable", False)
            return
        if self.failure_class not in {
            "budget",
            "candidate",
            "framework",
            "infrastructure",
        }:
            raise ValueError("failed repair conformance requires a failure class")

    @property
    def failure_fingerprint(self) -> str | None:
        if self.passed:
            return None
        return repair_conformance_failure_fingerprint(self)

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "passed": self.passed,
            "code": self.code,
            "reason": self.reason,
            "details": dict(self.details),
            "failure_class": (
                None if self.passed else self.failure_class
            ),
            "repairable": bool(not self.passed and self.repairable),
        }
        if self.failure_fingerprint is not None:
            result["failure_fingerprint"] = self.failure_fingerprint
        return result


def evaluate_artifact_lifecycle_conformance(
    observations: Sequence[Mapping[str, object]],
    contract: RepairConformanceContract,
) -> RepairConformanceResult | None:
    """Validate a repaired evidence lifecycle from bounded screening telemetry.

    Source edits cannot prove that an agent will stop collecting.  This gate
    therefore consumes only framework-emitted runtime counters from an executed
    representative replay.  Missing telemetry is a measurement defect; observed
    limit or lifecycle violations remain candidate-owned repair failures.
    """

    constraint = contract.artifact_lifecycle_constraint
    if constraint is None:
        return None
    if not observations:
        return RepairConformanceResult(
            passed=False,
            code="artifact_lifecycle_evidence_unavailable",
            reason=(
                "artifact lifecycle admission requires an executed screening "
                "member with runtime-policy telemetry"
            ),
            details={
                "artifact_lifecycle_constraint": constraint.to_dict(),
                "observation_count": 0,
            },
            failure_class="framework",
            repairable=False,
        )

    violations: list[dict[str, object]] = []
    unavailable: list[dict[str, object]] = []
    for index, observation in enumerate(observations):
        identity = {
            "observation_index": index,
            **(
                {"case_id": observation["case_id"]}
                if isinstance(observation.get("case_id"), str)
                else {}
            ),
        }
        required_fields = (
            "policy_active",
            "policy_passed",
            "artifact_file_count",
            "artifact_bytes",
            "tool_call_attempt_count",
            "manifest_entry_count",
            "manifest_valid",
            "execution_succeeded",
        )
        missing = [
            field_name
            for field_name in required_fields
            if observation.get(field_name) is None
        ]
        if missing:
            unavailable.append({**identity, "missing_fields": missing})
            continue

        artifact_file_count = _non_negative_int(
            observation.get("artifact_file_count")
        )
        artifact_bytes = _non_negative_int(observation.get("artifact_bytes"))
        tool_call_attempt_count = _non_negative_int(
            observation.get("tool_call_attempt_count")
        )
        manifest_entry_count = _non_negative_int(
            observation.get("manifest_entry_count")
        )
        failed_checks: list[str] = []
        if observation.get("policy_active") is not True:
            failed_checks.append("runtime_policy_inactive")
        if observation.get("policy_passed") is not True:
            failed_checks.append("runtime_policy_violation")
        if artifact_file_count > constraint.max_artifact_files:
            failed_checks.append("artifact_reuse_not_proven")
        if artifact_bytes > constraint.max_artifact_bytes:
            failed_checks.append("artifact_byte_bound_exceeded")
        if tool_call_attempt_count > constraint.max_collection_tool_calls:
            failed_checks.append("collection_attempt_bound_exceeded")
        if constraint.require_manifest and (
            manifest_entry_count <= 0
            or observation.get("manifest_valid") is not True
        ):
            failed_checks.append("valid_manifest_missing")
        if constraint.require_artifact_reuse and artifact_file_count != 1:
            failed_checks.append("single_reusable_artifact_not_observed")
        if constraint.require_stop_after_evidence_ready and (
            observation.get("execution_succeeded") is not True
            or observation.get("policy_passed") is not True
            or manifest_entry_count <= 0
        ):
            failed_checks.append("evidence_ready_finalization_not_proven")
        if failed_checks:
            violations.append(
                {
                    **identity,
                    "failed_checks": failed_checks,
                    "artifact_file_count": artifact_file_count,
                    "artifact_bytes": artifact_bytes,
                    "tool_call_attempt_count": tool_call_attempt_count,
                    "manifest_entry_count": manifest_entry_count,
                }
            )

    if unavailable:
        return RepairConformanceResult(
            passed=False,
            code="artifact_lifecycle_evidence_unavailable",
            reason=(
                "screening did not expose the complete framework-owned artifact "
                "lifecycle telemetry required for admission"
            ),
            details={
                "artifact_lifecycle_constraint": constraint.to_dict(),
                "observation_count": len(observations),
                "unavailable_observations": unavailable[:32],
            },
            failure_class="framework",
            repairable=False,
        )
    if violations:
        return RepairConformanceResult(
            passed=False,
            code="artifact_lifecycle_conformance_failed",
            reason=(
                "candidate screening did not prove bounded collection, one "
                "reusable artifact, and finalization after evidence became ready"
            ),
            details={
                "artifact_lifecycle_constraint": constraint.to_dict(),
                "observation_count": len(observations),
                "violations": violations[:32],
            },
        )
    return _passed(
        "artifact_lifecycle_conformance_passed",
        (
            "screening proved bounded collection, one reusable artifact, and "
            "finalization after evidence became ready"
        ),
        artifact_lifecycle_constraint=constraint.to_dict(),
        observation_count=len(observations),
    )


def repair_conformance_failure_fingerprint(
    result: RepairConformanceResult,
) -> str:
    """Fingerprint a typed failure shape without names, lines, or payloads.

    Candidate generators often produce the same invalid control/data-flow
    topology with renamed helpers and shifted line numbers.  The repair
    frontier must treat those variants as one failure while still separating
    materially different constructs, affected package paths, and typed schema
    constraints.
    """

    scalar_keys = {
        "construct",
        "field_path",
        "kind",
        "path",
        "probe_kind",
        "probe_path",
        "reader_kind",
        "rule",
        "schema_layer",
        "violation_code",
    }
    sequence_keys = {
        "forbidden_operations",
        "missing_gateway_keys",
        "missing_operations",
        "missing_payload_keys",
        "removed_paths",
        "required_changed_paths",
        "required_operations",
        "runtime_paths",
        "unsupported_boundary_kinds",
    }
    atoms: set[tuple[str, str]] = set()
    visited = 0

    def visit(value: object) -> None:
        nonlocal visited
        if visited >= 2_048 or len(atoms) >= 256:
            return
        visited += 1
        if isinstance(value, Mapping):
            for raw_key, nested in value.items():
                key = str(raw_key)
                if key in scalar_keys and isinstance(
                    nested,
                    (str, int, float, bool),
                ):
                    atoms.add((key, str(nested)))
                    continue
                if key in sequence_keys and isinstance(nested, (list, tuple)):
                    for item in nested:
                        if isinstance(item, (str, int, float, bool)):
                            atoms.add((key, str(item)))
                    continue
                if key == "schema_field_constraints" and isinstance(
                    nested,
                    (list, tuple),
                ):
                    for item in nested:
                        if not isinstance(item, Mapping):
                            continue
                        encoded = json.dumps(
                            dict(item),
                            ensure_ascii=True,
                            separators=(",", ":"),
                            sort_keys=True,
                            default=str,
                        )
                        atoms.add(
                            (
                                "schema_field_constraint",
                                hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
                            )
                        )
                    continue
                if key == "runtime_response_constraints" and isinstance(
                    nested,
                    (list, tuple),
                ):
                    for item in nested:
                        if not isinstance(item, Mapping):
                            continue
                        encoded = json.dumps(
                            dict(item),
                            ensure_ascii=True,
                            separators=(",", ":"),
                            sort_keys=True,
                            default=str,
                        )
                        atoms.add(
                            (
                                "runtime_response_constraint",
                                hashlib.sha256(
                                    encoded.encode("utf-8")
                                ).hexdigest(),
                            )
                        )
                    continue
                visit(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                visit(nested)

    visit(result.details)
    shape = {
        "code": result.code,
        "failure_class": result.failure_class,
        "atoms": sorted(atoms),
    }
    encoded = json.dumps(
        shape,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class RepairConformanceProbeGroup:
    """One semantic runtime contract shared by one or more dataset cases.

    The fingerprint is deliberately content-free: payload-bearing probe and
    fixture fields contribute only through hashes or structural fingerprints.
    Case IDs are accounting metadata and therefore do not change semantic
    equivalence.
    """

    fingerprint: str
    requirement_id: str
    service_id: str
    transport: str
    probe_kind: str
    probe_path: str
    operation: str | None
    case_ids: tuple[str, ...]
    selector: "RepairConformanceProbeSelector" = field(
        repr=False,
        compare=False,
    )

    def to_dict(self) -> dict[str, object]:
        case_identity_report = _case_identity_report(self.case_ids)
        return {
            "fingerprint": self.fingerprint,
            "requirement_id": self.requirement_id,
            "service_id": self.service_id,
            "transport": self.transport,
            "probe_kind": self.probe_kind,
            "probe_path": self.probe_path,
            "operation": self.operation,
            **case_identity_report,
        }


@dataclass(frozen=True)
class RepairConformanceProbeSelector:
    """Private selector used to project one semantic group for execution."""

    service_index: int
    probe_index: int | None


def project_replay_capability_for_probe_group(
    capability: FrozenReplayCapability,
    group: RepairConformanceProbeGroup,
) -> FrozenReplayCapability:
    """Project a frozen capability onto exactly one conformance group.

    The projection retains runtime files required to launch the selected
    service, while filtering service, fixture, evidence, endpoint, and handled
    requirement views.  The selector is deliberately omitted from persisted
    group reports.
    """

    selector = group.selector
    if selector.service_index < 0 or selector.service_index >= len(capability.services):
        raise ValueError("conformance group service selector is out of range")
    service = capability.services[selector.service_index]
    if (
        service.service_id != group.service_id
        or service.requirement_id != group.requirement_id
    ):
        raise ValueError("conformance group selector does not match frozen service")
    if selector.probe_index is None:
        projected_service = replace(service, protocol_probes=())
    else:
        if selector.probe_index < 0 or selector.probe_index >= len(service.protocol_probes):
            raise ValueError("conformance group probe selector is out of range")
        projected_service = replace(
            service,
            protocol_probes=(service.protocol_probes[selector.probe_index],),
        )
    fixture_paths = {service.response_fixture}
    return replace(
        capability,
        handled_requirements=tuple(
            item
            for item in capability.handled_requirements
            if item == group.requirement_id
        ),
        unhandled_requirements=(),
        evidence_refs={
            key: value
            for key, value in capability.evidence_refs.items()
            if key == group.requirement_id
        },
        fixture_evidence_refs={
            key: value
            for key, value in capability.fixture_evidence_refs.items()
            if key in fixture_paths
        },
        fixtures=tuple(
            item for item in capability.fixtures if item.path in fixture_paths
        ),
        endpoint_replacements={
            key: value
            for key, value in capability.endpoint_replacements.items()
            if key == group.requirement_id
        },
        services=(projected_service,),
    )


@dataclass(frozen=True)
class RepairConformanceProbePlan:
    total_case_count: int
    covered_case_ids: tuple[str, ...]
    groups: tuple[RepairConformanceProbeGroup, ...]

    def to_dict(self) -> dict[str, object]:
        reported_groups = self.groups[:_MAX_CONFORMANCE_REPORT_GROUPS]
        covered_identity_report = _case_identity_report(
            self.covered_case_ids,
            field_prefix="covered_case",
        )
        return {
            "total_case_count": self.total_case_count,
            "covered_case_count": len(self.covered_case_ids),
            **covered_identity_report,
            "distinct_probe_group_count": len(self.groups),
            "reported_probe_group_count": len(reported_groups),
            "probe_groups_truncated": len(self.groups) > len(reported_groups),
            "groups": [group.to_dict() for group in reported_groups],
        }


def build_repair_conformance_probe_plan(
    *,
    capability_id: str,
    services: Sequence[ReplayServiceSpec],
    requirements: Sequence[object],
    fixture_shape_fingerprints: Mapping[str, str],
    contract: RepairConformanceContract,
    dataset_case_ids: Sequence[str] = (),
) -> RepairConformanceProbePlan:
    """Build a deterministic dataset-wide plan without retaining payloads.

    Requirements are accepted structurally to keep this module independent of
    replay adaptation orchestration.  A service/probe contract is deduplicated
    only when every semantic field matches; all affected case IDs are then
    retained on the group.
    """

    requirements_by_id = {
        str(getattr(requirement, "requirement_id", "")): requirement
        for requirement in requirements
        if str(getattr(requirement, "requirement_id", ""))
    }
    grouped: dict[str, dict[str, object]] = {}
    authoritative_case_ids = bool(dataset_case_ids)
    all_case_ids: list[str] = [
        normalized
        for item in dataset_case_ids
        for normalized in (_case_identity(item),)
        if normalized
    ]
    for requirement in requirements:
        for case_id in tuple(getattr(requirement, "case_ids", ()) or ()):
            normalized_case_id = _case_identity(case_id)
            if (
                not authoritative_case_ids
                and normalized_case_id
                and normalized_case_id not in all_case_ids
            ):
                all_case_ids.append(normalized_case_id)

    exact_probe_fingerprint = _conformance_sensitive_fingerprint(
        (
            {
                "kind": contract.exact_probe.kind,
                "path": contract.exact_probe.path,
                "expected_response_sha256": hashlib.sha256(
                    contract.exact_probe.expected_response.encode("utf-8")
                ).hexdigest(),
            }
            if contract.exact_probe is not None
            else None
        )
    )
    task_plane_required_nonempty = bool(
        contract.late_observed_operations
        and (
            contract.requires_fixture_derived_probe
            or contract.exact_probe is not None
        )
    )
    required_recorded_operations = tuple(
        contract.required_fixture_probe_operations
        or (
            contract.late_observed_operations[-1:]
            if contract.requires_fixture_derived_probe
            else ()
        )
    )
    for service_index, service in enumerate(services):
        requirement = requirements_by_id.get(service.requirement_id)
        case_ids = tuple(
            dict.fromkeys(
                _case_identity(item)
                for item in tuple(getattr(requirement, "case_ids", ()) or ())
                if str(item).strip()
            )
        )
        if authoritative_case_ids:
            case_ids = tuple(
                case_id for case_id in all_case_ids if case_id in case_ids
            )
        probes: tuple[ReplayProtocolProbe | None, ...] = (
            tuple(service.protocol_probes) or (None,)
        )
        for probe_index, probe in enumerate(probes):
            operation = _repair_probe_operation(
                probe.request_text if probe is not None else None
            )
            matching_fixture_constraints = tuple(
                constraint
                for constraint in contract.fixture_probe_constraints
                if constraint.matches_requirement_id(service.requirement_id)
                and probe is not None
                and constraint.kind == probe.kind
                and constraint.path == probe.path
            )
            semantic = {
                "capability_id": capability_id,
                "requirement": {
                    "id": service.requirement_id,
                    "kind": str(getattr(requirement, "kind", "")),
                    "identifier": str(getattr(requirement, "identifier", "")),
                    "status": str(getattr(requirement, "status", "")),
                    "detail_fingerprint": _conformance_sensitive_fingerprint(
                        str(getattr(requirement, "detail", "") or "")
                    ),
                },
                "service": {
                    "id": service.service_id,
                    "transport": service.transport,
                },
                "probe": {
                    "kind": probe.kind if probe is not None else "readiness",
                    "path": probe.path if probe is not None else service.readiness.path,
                    "operation": operation,
                    "request_fingerprint": _conformance_sensitive_fingerprint(
                        probe.request_text if probe is not None else None
                    ),
                    "response_assertion_fingerprint": (
                        _conformance_sensitive_fingerprint(probe.response_contains)
                        if probe is not None
                        else _conformance_sensitive_fingerprint(None)
                    ),
                    "validate_advertised_websockets": bool(
                        probe is not None
                        and probe.validate_advertised_websockets
                    ),
                },
                "fixture_shape_fingerprint": fixture_shape_fingerprints.get(
                    service.response_fixture,
                    "sha256:missing",
                ),
                "exact_probe_fingerprint": exact_probe_fingerprint,
                "fixture_probe_constraints_fingerprint": (
                    _conformance_sensitive_fingerprint(
                        [
                            item.to_public_dict()
                            for item in matching_fixture_constraints
                        ]
                    )
                ),
                "assertions": {
                    "requires_nonempty": (
                        task_plane_required_nonempty
                        or bool(matching_fixture_constraints)
                    ),
                    "requires_recorded": (
                        operation in required_recorded_operations
                        or bool(matching_fixture_constraints)
                    ),
                },
            }
            fingerprint = _conformance_sensitive_fingerprint(semantic)
            group = grouped.setdefault(
                fingerprint,
                {
                    "requirement_id": service.requirement_id,
                    "service_id": service.service_id,
                    "transport": service.transport,
                    "probe_kind": (
                        probe.kind if probe is not None else "readiness"
                    ),
                    "probe_path": (
                        probe.path if probe is not None else service.readiness.path
                    ),
                    "operation": operation,
                    "case_ids": [],
                    "selector": RepairConformanceProbeSelector(
                        service_index=service_index,
                        probe_index=(probe_index if probe is not None else None),
                    ),
                },
            )
            grouped_case_ids = group["case_ids"]
            assert isinstance(grouped_case_ids, list)
            for case_id in case_ids:
                if case_id not in grouped_case_ids:
                    grouped_case_ids.append(case_id)

    groups = tuple(
        RepairConformanceProbeGroup(
            fingerprint=fingerprint,
            requirement_id=str(value["requirement_id"]),
            service_id=str(value["service_id"]),
            transport=str(value["transport"]),
            probe_kind=str(value["probe_kind"]),
            probe_path=str(value["probe_path"]),
            operation=(
                str(value["operation"])
                if isinstance(value["operation"], str)
                else None
            ),
            case_ids=tuple(value["case_ids"]),
            selector=value["selector"],
        )
        for fingerprint, value in sorted(grouped.items())
    )
    covered_case_ids = tuple(
        case_id
        for case_id in all_case_ids
        if any(case_id in group.case_ids for group in groups)
    )
    return RepairConformanceProbePlan(
        total_case_count=len(all_case_ids),
        covered_case_ids=covered_case_ids,
        groups=groups,
    )


def _conformance_sensitive_fingerprint(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _case_identity(value: object) -> str:
    """Normalize whitespace without truncating semantic dataset identity."""

    return str(value).strip()


def _case_identity_report(
    case_ids: Sequence[str],
    *,
    field_prefix: str = "case",
) -> dict[str, object]:
    sampled = tuple(case_ids[:_MAX_CONFORMANCE_REPORT_CASES])
    return {
        field_prefix + "_ids": [
            sanitize_text(case_id, max_chars=160) for case_id in sampled
        ],
        field_prefix + "_id_count": len(case_ids),
        field_prefix + "_ids_truncated": (
            len(case_ids) > len(sampled)
            or any(len(case_id) > 160 for case_id in sampled)
        ),
        field_prefix + "_ids_fingerprint": _conformance_sensitive_fingerprint(
            list(case_ids)
        ),
    }


def _repair_probe_operation(request_text: str | None) -> str | None:
    if not isinstance(request_text, str) or not request_text.strip():
        return None
    try:
        parsed = json.loads(request_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, Mapping):
        return None
    for key in ("operation", "method", "action", "command"):
        value = parsed.get(key)
        if isinstance(value, str) and value.strip():
            return sanitize_text(value.strip(), max_chars=160)
    return None


def _typed_constraint_owner_paths(
    *,
    manifest_path: str | None,
    compiler_path: str | None,
    runtime_paths: Sequence[str],
    schema_field_constraints: Sequence[SchemaFieldRepairConstraint],
    runtime_response_constraints: Sequence[RuntimeResponseConstraint],
) -> tuple[str, ...]:
    """Resolve every typed constraint to its source-producing layer.

    A repair may carry preservation constraints from an older frontier together
    with a newly observed failure.  Returning the owner union keeps both layers
    visible; choosing one by diagnostic order can send the next mutation to a
    valid runtime while hiding the compiler that produced the failing schema (or
    vice versa).
    """

    owners: list[str] = []
    layers = {
        constraint.schema_layer for constraint in schema_field_constraints
    }
    if "manifest" in layers and manifest_path is not None:
        owners.append(manifest_path)
    if layers & {"compile_result", "compiler_output"} and compiler_path is not None:
        owners.append(compiler_path)
    if "runtime" in layers or runtime_response_constraints:
        owners.extend(runtime_paths)
    return tuple(dict.fromkeys(path for path in owners if path))


def _direct_repair_diagnostics(
    diagnostics: Sequence[Mapping[str, object]],
) -> tuple[Mapping[str, object], ...]:
    """Remove inherited contract envelopes from current failure evidence."""

    def strip_inherited_contracts(value: object) -> object | None:
        if isinstance(value, Mapping):
            if (
                value.get("projection_schema_version")
                == "aworld.self_evolve.repair_conformance.public.v1"
                or value.get("code") == "inherited_typed_repair_constraints"
            ):
                return None
            stripped: dict[str, object] = {}
            for key, nested in value.items():
                if key == "repair_conformance":
                    continue
                projected = strip_inherited_contracts(nested)
                if projected is not None:
                    stripped[str(key)] = projected
            return stripped
        if isinstance(value, (list, tuple)):
            return [
                projected
                for nested in value
                if (projected := strip_inherited_contracts(nested)) is not None
            ]
        return value

    direct: list[Mapping[str, object]] = []
    for diagnostic in diagnostics:
        stripped = strip_inherited_contracts(diagnostic)
        if isinstance(stripped, Mapping) and stripped:
            direct.append(stripped)
    return tuple(direct)


def compile_repair_conformance_contract(
    repair_focus: Mapping[str, object] | None,
) -> RepairConformanceContract | None:
    if not isinstance(repair_focus, Mapping):
        return None
    package = repair_focus.get("repair_candidate_package")
    if not isinstance(package, Mapping):
        return None
    focus_candidate_id = package.get("candidate_id")
    raw_files = package.get("files")
    if (
        not isinstance(focus_candidate_id, str)
        or not focus_candidate_id
        or not isinstance(raw_files, list)
    ):
        return None

    counterexamples = _repair_counterexamples(repair_focus)
    direct_artifact_lifecycle_constraint = (
        _artifact_lifecycle_constraint_from_counterexamples(counterexamples)
    )
    package_content = package.get("content")
    if not raw_files and not (
        direct_artifact_lifecycle_constraint is not None
        and isinstance(package_content, str)
        and package_content.strip()
    ):
        return None

    base_sources: dict[str, str] = {}
    for item in raw_files[:_MAX_CONTRACT_FILES]:
        if not isinstance(item, Mapping):
            continue
        path = _bounded_relative_path(item.get("path"))
        content = item.get("content")
        if path is None or not isinstance(content, str):
            continue
        base_sources[path] = content
    if (
        direct_artifact_lifecycle_constraint is not None
        and isinstance(package_content, str)
        and package_content.strip()
    ):
        base_sources.setdefault("SKILL.md", package_content)
    if not base_sources:
        return None

    diagnostics = tuple(_diagnostic_mappings(repair_focus))
    direct_diagnostics = _direct_repair_diagnostics(diagnostics)
    direct_failure_codes = _diagnostic_failure_codes(direct_diagnostics)
    counterexample_failure_codes = tuple(
        str(item["failure_code"])
        for item in counterexamples
        if isinstance(item.get("failure_code"), str)
    )
    required_runtime_transitions = tuple(
        dict.fromkeys(
            str(item["required_transition"])
            for item in counterexamples
            if isinstance(item.get("required_transition"), str)
        )
    )
    inherited_contract = _inherited_repair_conformance_contract(diagnostics)
    artifact_lifecycle_constraint = _merge_artifact_lifecycle_constraints(
        (
            inherited_contract.artifact_lifecycle_constraint
            if inherited_contract is not None
            else None
        ),
        direct_artifact_lifecycle_constraint,
    )
    failure_codes = tuple(
        dict.fromkeys(
            (
                *direct_failure_codes,
                *counterexample_failure_codes,
                *(
                    inherited_contract.failure_codes
                    if inherited_contract is not None
                    else ()
                ),
            )
        )
    )
    if {
        "finalize_after_successful_endpoint_interaction",
        "target_behavior_completion_missing",
    } & set(direct_failure_codes):
        # The replay implementation already completed a bidirectional data-plane
        # interaction. This repair belongs to the target skill content, not to a
        # candidate-owned compiler/runtime branch, so source conformance must not
        # force an unrelated protocol edit.
        return None
    manifest_path, branch_paths = _replay_implementation_paths(base_sources)
    runtime_paths = branch_paths
    if (
        artifact_lifecycle_constraint is not None
        and "SKILL.md" in base_sources
    ):
        branch_paths = tuple(dict.fromkeys((*branch_paths, "SKILL.md")))
    compiler_path = _replay_compiler_path(
        base_sources,
        manifest_path=manifest_path,
    )
    direct_failure_paths: tuple[str, ...] = ()
    requires_selector_alignment = (
        "align_compiler_runtime_recorded_response_selection"
        in direct_failure_codes
    )
    if requires_selector_alignment and compiler_path is not None:
        branch_paths = tuple(
            dict.fromkeys((compiler_path, *branch_paths))
        )
        direct_failure_paths = branch_paths
    else:
        compile_failure_paths = _compile_failure_branch_paths(
            direct_diagnostics,
            manifest_path=manifest_path,
            compiler_path=compiler_path,
            runtime_paths=branch_paths,
        )
        if compile_failure_paths:
            branch_paths = compile_failure_paths
            direct_failure_paths = compile_failure_paths
    exact_probe = _exact_probe_constraint(diagnostics) or (
        inherited_contract.exact_probe
        if inherited_contract is not None
        else None
    )
    direct_probe_constraints = _fixture_probe_constraints(direct_diagnostics)
    inherited_probe_constraints = (
        inherited_contract.fixture_probe_constraints
        if inherited_contract is not None
        else ()
    )
    fixture_probe_constraints = tuple(
        {
            (
                item.requirement_identity_digest,
                item.kind,
                item.path,
                item.max_response_chars,
            ): item
            for item in (
                *inherited_probe_constraints,
                *direct_probe_constraints,
            )
        }.values()
    )
    direct_schema_constraints = _schema_field_constraints(direct_diagnostics)
    transported_schema_constraints = _schema_field_constraints(diagnostics)
    inherited_schema_constraints = tuple(
        {
            item.identity_digest: item
            for item in (
                *(
                    inherited_contract.schema_field_constraints
                    if inherited_contract is not None
                    else ()
                ),
                *transported_schema_constraints,
            )
        }.values()
    )
    schema_field_constraints = tuple(
        {
            item.identity_digest: item
            for item in (
                *inherited_schema_constraints,
                *direct_schema_constraints,
            )
        }.values()
    )
    direct_runtime_response_constraints = _runtime_response_constraints(
        direct_diagnostics
    )
    transported_runtime_response_constraints = _runtime_response_constraints(
        diagnostics
    )
    inherited_runtime_response_constraints = tuple(
        {
            item.identity_digest: item
            for item in (
                *(
                    inherited_contract.runtime_response_constraints
                    if inherited_contract is not None
                    else ()
                ),
                *transported_runtime_response_constraints,
            )
        }.values()
    )
    runtime_response_constraints = tuple(
        {
            item.identity_digest: item
            for item in (
                *inherited_runtime_response_constraints,
                *direct_runtime_response_constraints,
            )
        }.values()
    )
    direct_owner_paths = _typed_constraint_owner_paths(
        manifest_path=manifest_path,
        compiler_path=compiler_path,
        runtime_paths=runtime_paths,
        schema_field_constraints=(
            ()
            if direct_runtime_response_constraints
            else direct_schema_constraints
        ),
        runtime_response_constraints=direct_runtime_response_constraints,
    )
    inherited_owner_paths = _typed_constraint_owner_paths(
        manifest_path=manifest_path,
        compiler_path=compiler_path,
        runtime_paths=runtime_paths,
        schema_field_constraints=schema_field_constraints,
        runtime_response_constraints=runtime_response_constraints,
    )
    inherited_failure_codes = set(
        inherited_contract.failure_codes
        if inherited_contract is not None
        else ()
    )
    compiler_fixture_failure_active = bool(
        {
            "protocol_probe_not_fixture_derived",
            "align_compiler_runtime_recorded_response_selection",
        }
        & (set(direct_failure_codes) | inherited_failure_codes)
    )
    unresolved_owner_paths = (
        (compiler_path,)
        if compiler_fixture_failure_active and compiler_path is not None
        else ()
    )
    if direct_failure_paths:
        branch_paths = tuple(
            dict.fromkeys(
                (
                    *direct_failure_paths,
                    *direct_owner_paths,
                    *unresolved_owner_paths,
                )
            )
        )
    elif direct_owner_paths:
        # Multiple constraints discovered by the same failure are co-owners.
        # Historical constraints remain validation invariants but cannot redirect
        # the active mutation away from the current failure producer.
        branch_paths = tuple(
            dict.fromkeys((*direct_owner_paths, *unresolved_owner_paths))
        )
    elif unresolved_owner_paths:
        branch_paths = tuple(
            dict.fromkeys((*unresolved_owner_paths, *inherited_owner_paths))
        )
    elif inherited_contract is not None and inherited_owner_paths:
        branch_paths = inherited_owner_paths
    elif inherited_contract is not None and inherited_contract.required_branch_paths:
        branch_paths = inherited_contract.required_branch_paths
    directly_observed_operations = _observed_operations(direct_diagnostics)
    observed_operations = directly_observed_operations or (
        inherited_contract.late_observed_operations
        if inherited_contract is not None
        else ()
    )
    requires_fixture_probe = (
        "implement_observed_endpoint_interactions" in direct_failure_codes
        or bool(
            inherited_contract is not None
            and inherited_contract.requires_fixture_derived_probe
        )
    )
    inherited_verified_operations = (
        inherited_contract.required_fixture_probe_operations
        if inherited_contract is not None
        else ()
    )
    frontier_operations = tuple(
        operation
        for operation in directly_observed_operations
        if operation not in inherited_verified_operations
    )
    if requires_fixture_probe and frontier_operations:
        required_fixture_probe_operations = tuple(
            dict.fromkeys(
                (*inherited_verified_operations, frontier_operations[-1])
            )
        )[-_MAX_OBSERVED_OPERATIONS:]
    elif (
        requires_fixture_probe
        and inherited_contract is not None
        and inherited_contract.required_fixture_probe_operations
    ):
        required_fixture_probe_operations = (
            inherited_contract.required_fixture_probe_operations
        )
    elif requires_fixture_probe:
        required_fixture_probe_operations = observed_operations[-1:]
    else:
        required_fixture_probe_operations = ()
    interaction_progress = max(
        _non_negative_int(repair_focus.get("interaction_progress")),
        (
            inherited_contract.interaction_progress
            if inherited_contract is not None
            else 0
        ),
    )
    return RepairConformanceContract(
        focus_candidate_id=sanitize_text(focus_candidate_id, max_chars=160),
        failure_codes=failure_codes,
        interaction_progress=interaction_progress,
        base_file_fingerprints={
            path: _source_fingerprint(content)
            for path, content in sorted(base_sources.items())
        },
        required_branch_paths=branch_paths,
        base_branch_fingerprints=_base_branch_fingerprints(
            base_sources,
            branch_paths=branch_paths,
            markers=observed_operations,
        ),
        base_fixture_selector_fingerprints=(
            _fixture_selector_fingerprints(
                base_sources,
                branch_paths=branch_paths,
                markers=observed_operations,
            )
            if requires_fixture_probe or fixture_probe_constraints
            else {}
        ),
        manifest_path=manifest_path,
        compiler_path=compiler_path,
        runtime_paths=runtime_paths,
        exact_probe=exact_probe,
        late_observed_operations=observed_operations,
        requires_compiler_fixture_reconstruction=bool(
            exact_probe is not None
            or fixture_probe_constraints
            or compiler_fixture_failure_active
        ),
        requires_fixture_derived_probe=requires_fixture_probe,
        required_fixture_probe_operations=required_fixture_probe_operations,
        fixture_probe_constraints=fixture_probe_constraints,
        schema_field_constraints=schema_field_constraints,
        runtime_response_constraints=runtime_response_constraints,
        required_runtime_transitions=tuple(
            dict.fromkeys(
                (
                    *(
                        inherited_contract.required_runtime_transitions
                        if inherited_contract is not None
                        else ()
                    ),
                    *required_runtime_transitions,
                    *(
                        ("preserve_recorded_response_context",)
                        if runtime_response_constraints
                        else ()
                    ),
                )
            )
        ),
        artifact_lifecycle_constraint=artifact_lifecycle_constraint,
    )


def _artifact_lifecycle_constraint_from_counterexamples(
    counterexamples: Sequence[Mapping[str, object]],
) -> ArtifactLifecycleConstraint | None:
    """Compile evidence-policy failures into a behavioral admission contract."""

    relevant = tuple(
        item
        for item in counterexamples
        if item.get("failure_code")
        in {
            "artifact_file_limit_exhausted",
            "artifact_byte_limit_exhausted",
            "tool_call_after_evidence_ready",
        }
    )
    if not relevant:
        return None

    def positive_values(field_name: str) -> tuple[int, ...]:
        return tuple(
            int(value)
            for item in relevant
            if isinstance((value := item.get(field_name)), int)
            and not isinstance(value, bool)
            and value > 0
        )

    artifact_byte_limits = positive_values("artifact_byte_limit")
    collection_attempts = positive_values("tool_call_attempt_count")
    # An exhaustion repair must demonstrate reuse rather than merely stopping
    # one file below the previous quota. Screening admits one reusable evidence
    # artifact, a bounded manifest, and no collection after evidence is ready.
    return ArtifactLifecycleConstraint(
        max_artifact_files=1,
        max_artifact_bytes=(
            min(artifact_byte_limits) if artifact_byte_limits else 2_000_000
        ),
        max_collection_tool_calls=(
            min(max(1, value - 1) for value in collection_attempts)
            if collection_attempts
            else 8
        ),
    )


def _merge_artifact_lifecycle_constraints(
    *constraints: ArtifactLifecycleConstraint | None,
) -> ArtifactLifecycleConstraint | None:
    active = tuple(item for item in constraints if item is not None)
    if not active:
        return None
    return ArtifactLifecycleConstraint(
        max_artifact_files=min(item.max_artifact_files for item in active),
        max_artifact_bytes=min(item.max_artifact_bytes for item in active),
        max_collection_tool_calls=min(
            item.max_collection_tool_calls for item in active
        ),
        require_manifest=any(item.require_manifest for item in active),
        require_artifact_reuse=any(
            item.require_artifact_reuse for item in active
        ),
        require_stop_after_evidence_ready=any(
            item.require_stop_after_evidence_ready for item in active
        ),
    )


def merge_repair_conformance_constraint_context(
    inherited: Mapping[str, object] | None,
    *diagnostics: Mapping[str, object],
) -> dict[str, object] | None:
    """Merge public typed repair constraints across a causal feedback boundary.

    ``inherited`` is the validation input contract, while ``diagnostics`` may
    contain constraints discovered only after compiling or probing that input.
    The result remains payload-free and public: fixture requirement identities
    are retained only by digest, and schema rules are canonical typed values.
    """

    sources = tuple(
        value
        for value in (inherited, *diagnostics)
        if isinstance(value, Mapping)
    )
    fixture_constraints = _fixture_probe_constraints(sources)
    schema_constraints = _schema_field_constraints(sources)
    runtime_response_constraints = _runtime_response_constraints(sources)
    artifact_lifecycle_constraints = list(
        _artifact_lifecycle_constraints(sources)
    )
    artifact_lifecycle_constraint = _merge_artifact_lifecycle_constraints(
        *artifact_lifecycle_constraints
    )
    direct_diagnostics = _direct_repair_diagnostics(
        tuple(value for value in diagnostics if isinstance(value, Mapping))
    )
    direct_schema_constraints = _schema_field_constraints(direct_diagnostics)
    direct_runtime_response_constraints = _runtime_response_constraints(
        direct_diagnostics
    )
    if (
        inherited is None
        and not fixture_constraints
        and not schema_constraints
        and not runtime_response_constraints
        and artifact_lifecycle_constraint is None
    ):
        return None
    merged = dict(inherited or {})
    failure_codes = _diagnostic_failure_codes(sources)
    if failure_codes:
        merged["failure_codes"] = list(failure_codes)
    required_runtime_transitions = tuple(
        dict.fromkeys(
            transition
            for source in sources
            for transition in _string_tuple(
                source.get("required_runtime_transitions")
            )
        )
    )
    if required_runtime_transitions:
        merged["required_runtime_transitions"] = list(
            required_runtime_transitions
        )
    if fixture_constraints:
        merged["fixture_probe_constraints"] = [
            item.to_public_dict() for item in fixture_constraints
        ]
    if schema_constraints:
        merged["schema_field_constraints"] = [
            item.to_dict() for item in schema_constraints
        ]
    if runtime_response_constraints:
        merged["runtime_response_constraints"] = [
            item.to_dict() for item in runtime_response_constraints
        ]
    if artifact_lifecycle_constraint is not None:
        merged["artifact_lifecycle_constraint"] = (
            artifact_lifecycle_constraint.to_dict()
        )
    manifest_path = (
        str(merged.get("manifest_path"))
        if isinstance(merged.get("manifest_path"), str)
        else None
    )
    compiler_path = (
        str(merged.get("compiler_path"))
        if isinstance(merged.get("compiler_path"), str)
        else None
    )
    runtime_paths = _string_tuple(merged.get("runtime_paths"))
    owner_paths = _typed_constraint_owner_paths(
        manifest_path=manifest_path,
        compiler_path=compiler_path,
        runtime_paths=runtime_paths,
        schema_field_constraints=(
            ()
            if direct_runtime_response_constraints
            else direct_schema_constraints
        ),
        runtime_response_constraints=direct_runtime_response_constraints,
    )
    if not owner_paths and not _string_tuple(merged.get("required_branch_paths")):
        owner_paths = _typed_constraint_owner_paths(
            manifest_path=manifest_path,
            compiler_path=compiler_path,
            runtime_paths=runtime_paths,
            schema_field_constraints=schema_constraints,
            runtime_response_constraints=runtime_response_constraints,
        )
    if owner_paths:
        merged["required_branch_paths"] = list(owner_paths)
    return merged


def _artifact_lifecycle_constraints(
    values: Sequence[Mapping[str, object]],
) -> tuple[ArtifactLifecycleConstraint, ...]:
    constraints: list[ArtifactLifecycleConstraint] = []
    pending: list[object] = list(values)
    inspected = 0
    while pending and inspected < 512:
        current = pending.pop()
        inspected += 1
        if isinstance(current, Mapping):
            raw_constraint = current.get("artifact_lifecycle_constraint")
            if isinstance(raw_constraint, Mapping):
                constraint = ArtifactLifecycleConstraint.from_dict(raw_constraint)
                if constraint not in constraints:
                    constraints.append(constraint)
            pending.extend(
                nested
                for nested in current.values()
                if isinstance(nested, (Mapping, list, tuple))
            )
        elif isinstance(current, (list, tuple)):
            pending.extend(current[:64])
    return tuple(constraints)


def _compile_failure_branch_paths(
    diagnostics: Sequence[Mapping[str, object]],
    *,
    manifest_path: str | None,
    compiler_path: str | None,
    runtime_paths: tuple[str, ...],
) -> tuple[str, ...]:
    """Attribute compile diagnostics to the manifest, compiler, or runtime layer."""

    compile_diagnostics = [
        diagnostic
        for diagnostic in diagnostics
        if str(diagnostic.get("code") or "") in {
            "invalid_replay_capability_compile",
            "repair_capability_compile_failed",
        }
    ]
    if not compile_diagnostics:
        return ()
    # A protocol assertion is emitted by the capability compiler.  Inherited
    # runtime invariants may be nested in the same diagnostic, but they are
    # preservation constraints rather than an alternative repair target.  If
    # they select the change path here, candidates can repeatedly edit a valid
    # runtime while leaving the failing compiler output unchanged.
    fixture_assertion_compile_failure = any(
        str(diagnostic.get("capability_error_code") or "")
        == "protocol_probe_not_fixture_derived"
        or (
            str(diagnostic.get("code") or "")
            == "protocol_probe_not_fixture_derived"
        )
        for diagnostic in compile_diagnostics
    )
    if fixture_assertion_compile_failure and compiler_path is not None:
        return (compiler_path,)
    schema_layers = {
        constraint.schema_layer
        for constraint in _schema_field_constraints(compile_diagnostics)
    }
    typed_paths: list[str] = []
    if "manifest" in schema_layers and manifest_path is not None:
        typed_paths.append(manifest_path)
    if (
        schema_layers & {"compile_result", "compiler_output"}
        and compiler_path is not None
    ):
        typed_paths.append(compiler_path)
    if "runtime" in schema_layers:
        typed_paths.extend(runtime_paths)
    if typed_paths:
        return tuple(dict.fromkeys(typed_paths))
    reason_text = " ".join(
        str(diagnostic.get("reason") or "").casefold()
        for diagnostic in compile_diagnostics
    )
    if any(
        marker in reason_text
        for marker in (
            "unsupported replay capability schema",
            "manifest protocol",
            "manifest handles",
            "manifest runtime_files",
            "capability manifest",
        )
    ):
        return (manifest_path,) if manifest_path is not None else ()
    if any(
        marker in reason_text
        for marker in (
            "skill runtime",
            "aworld_replay_response_index",
            "runtime entrypoint",
            "runtime file",
        )
    ):
        return runtime_paths
    if compiler_path is not None:
        return (compiler_path,)
    return ()


def _repair_contract_consistency_failure(
    contract: RepairConformanceContract,
) -> RepairConformanceResult | None:
    """Fail closed when a framework-authored contract hides its failure owner.

    A candidate cannot repair a compiler-produced assertion when the contract
    only authorizes runtime files.  Treat that shape as a shared framework
    defect so it does not consume candidate or Campaign repair frontiers.
    ``requires_fixture_derived_probe`` intentionally remains a task-plane flag;
    compiler reconstruction is represented by the dedicated reconstruction
    flag and typed fixture constraints.
    """

    failures = set(contract.failure_codes)
    protocol_fixture_failure = "protocol_probe_not_fixture_derived" in failures
    selector_alignment_failure = (
        "align_compiler_runtime_recorded_response_selection" in failures
    )
    if not protocol_fixture_failure and not selector_alignment_failure:
        return None
    missing: list[str] = []
    if contract.compiler_path is None:
        missing.append("compiler_path")
    elif contract.compiler_path not in contract.required_branch_paths:
        missing.append("required_branch_paths.compiler")
    if (
        protocol_fixture_failure
        and not contract.requires_compiler_fixture_reconstruction
    ):
        missing.append("requires_compiler_fixture_reconstruction")
    if not missing:
        return None
    return RepairConformanceResult(
        passed=False,
        code="repair_contract_owner_inconsistent",
        reason=(
            "framework repair contract does not authorize the source owner or "
            "typed reconstruction evidence required by its unresolved compiler "
            "failure"
        ),
        details={
            "failure_codes": sorted(failures),
            "compiler_path": contract.compiler_path,
            "required_branch_paths": list(contract.required_branch_paths),
            "missing_contract_fields": missing,
        },
        failure_class="framework",
        repairable=False,
    )


def evaluate_candidate_source_conformance(
    candidate: CandidateVariant,
    contract: RepairConformanceContract,
) -> RepairConformanceResult:
    consistency_failure = _repair_contract_consistency_failure(contract)
    if consistency_failure is not None:
        return consistency_failure
    # ``CandidateVariant.files`` is a delta, not a materialized package.  A
    # missing runtime path therefore means that the candidate inherited the
    # baseline implementation.  Treating a missing path as an empty source
    # would fingerprint it as changed and let a rationale-only (or
    # compiler-only) candidate through the static gate.
    candidate_sources = {
        item.path: item.content
        for item in candidate.files
        if item.operation == "upsert" and isinstance(item.content, str)
    }
    candidate_sources.setdefault("SKILL.md", candidate.content)
    removed_branch_paths = sorted(
        path
        for path in contract.required_branch_paths
        if any(
            item.path == path and item.operation == "delete"
            for item in candidate.files
        )
    )
    if removed_branch_paths:
        return RepairConformanceResult(
            passed=False,
            code="repair_branch_removed",
            reason=(
                "candidate deletes the focused replay implementation instead of "
                "providing a replacement implementation"
            ),
            details={
                "focus_candidate_id": contract.focus_candidate_id,
                "removed_paths": removed_branch_paths,
            },
        )
    changed_file_paths = [
        path
        for path in contract.required_branch_paths
        if path in candidate_sources
        and _source_fingerprint(candidate_sources[path])
        != contract.base_file_fingerprints.get(path)
    ]
    changed_branch_slices = _changed_branch_slices(
        candidate_sources,
        contract.base_branch_fingerprints,
    )
    changed_selector_slices = _changed_fixture_selector_slices(
        candidate_sources,
        contract.base_fixture_selector_fingerprints,
    )
    if changed_branch_slices or changed_selector_slices or (
        not contract.base_branch_fingerprints and changed_file_paths
    ):
        source_behavior_failure = _source_behavior_constraint_failure(
            candidate_sources,
            contract=contract,
        )
        if source_behavior_failure is not None:
            return source_behavior_failure
        selector_alignment_failure = (
            _compiler_runtime_selector_alignment_failure(
                changed_file_paths=changed_file_paths,
                changed_branch_slices=changed_branch_slices,
                changed_selector_slices=changed_selector_slices,
                contract=contract,
            )
        )
        if selector_alignment_failure is not None:
            return selector_alignment_failure
        structural_failure = _fixture_probe_structure_failure(
            candidate_sources,
            contract=contract,
        )
        if structural_failure is not None:
            return structural_failure
        violations = _fixture_probe_derivation_violations(
            candidate_sources,
            required=(
                contract.requires_fixture_derived_probe
                or bool(contract.fixture_probe_constraints)
            ),
            operation_names=(
                contract.required_fixture_probe_operations
                or contract.late_observed_operations
            ),
        )
        if violations:
            required_change = (
                "load AWORLD_REPLAY_RESPONSE_INDEX, select a non_empty record "
                "for the incoming operation, and return its decoded value or "
                "protocol projection; remove any helper that returns "
                "FIXTURE_DATA or FIXTURE_DATA.get(key, [])"
                if any(
                    item.get("construct") == "top_level_fixture_projection"
                    for item in violations
                )
                else (
                    "select any deterministic non-empty recorded response leaf "
                    "after a complete recursive gateway-discovery phase and a "
                    "separate payload traversal phase"
                )
            )
            return RepairConformanceResult(
                passed=False,
                code="forbidden_fixture_probe_derivation",
                reason=(
                    "the changed fixture-probe branch still filters recorded "
                    "scalars by shape, combines multiple leaves, derives an assertion "
                    "from a hash, or skips nested sequence roots or payload selection "
                    "during response-gateway reconstruction"
                ),
                details={
                    "focus_candidate_id": contract.focus_candidate_id,
                    "violations": violations,
                    "required_change": required_change,
                    "forbidden_derivations": [
                        "regex scalar filters",
                        "narrow scalar length filters",
                    "fixture hash assertion fallbacks",
                    "joining multiple fixture scalars into one probe assertion",
                    "returning a non-mapping composite before traversing sequences",
                        "passing a gateway directly to a scalar selector before entering a payload key",
                        "falling through from a non-empty gateway branch into a parsed-root scalar fallback",
                    ],
                },
            )
        operation_failure = _operation_response_correlation_failure(
            candidate_sources,
            contract=contract,
        )
        if operation_failure is not None:
            return operation_failure
        return _passed(
            "repair_branch_changed",
            "candidate materially changes the focused replay implementation",
            changed_paths=(
                changed_branch_slices
                or changed_selector_slices
                or changed_file_paths
            ),
        )

    redirected_paths: tuple[str, ...] = ()
    if contract.manifest_path is not None:
        manifest_content = candidate_sources.get(contract.manifest_path)
        if (
            isinstance(manifest_content, str)
            and _source_fingerprint(manifest_content)
            != contract.base_file_fingerprints.get(contract.manifest_path)
        ):
            _, redirected_paths = _replay_implementation_paths(
                {contract.manifest_path: manifest_content, **candidate_sources}
            )
            if (
                redirected_paths
                and redirected_paths != contract.required_branch_paths
                and all(
                    isinstance(candidate_sources.get(path), str)
                    and bool(_canonical_source(candidate_sources[path]))
                    for path in redirected_paths
                )
            ):
                return _passed(
                    "repair_branch_redirected",
                    "candidate redirects the replay manifest to a new implementation",
                    changed_paths=[contract.manifest_path, *redirected_paths],
                )

    return RepairConformanceResult(
        passed=False,
        code="repair_branch_unchanged",
        reason=(
            "candidate rationale is not evidence: the focused replay implementation "
            "source is unchanged"
        ),
        details={
            "focus_candidate_id": contract.focus_candidate_id,
            "required_changed_paths": list(contract.required_branch_paths),
            "observed_request_operations": list(
                contract.late_observed_operations
            ),
            "observed_candidate_paths": sorted(candidate_sources)[:32],
        },
    )


def _source_behavior_constraint_failure(
    candidate_sources: Mapping[str, str],
    *,
    contract: RepairConformanceContract,
) -> RepairConformanceResult | None:
    """Run registered static proofs before expensive capability compilation."""

    constraints = tuple(
        constraint
        for constraint in contract.schema_field_constraints
        if constraint.value_domain == "source_behavior"
    )
    for constraint in constraints:
        analyzer = _source_behavior_analyzer(constraint)
        if analyzer is None:
            continue
        proofs: list[dict[str, object]] = []
        for path in _schema_constraint_source_paths(
            candidate_sources,
            contract=contract,
            schema_layer=constraint.schema_layer,
        ):
            source = candidate_sources.get(path)
            if not isinstance(source, str) or not source.strip():
                continue
            proof = dict(analyzer(source))
            proof["path"] = path
            proofs.append(proof)
        if any(proof.get("proven") is True for proof in proofs):
            continue
        missing_operations = sorted(
            {
                str(operation)
                for proof in proofs
                for operation in proof.get("missing_operations", ())
                if isinstance(operation, str) and operation
            }
        )
        boundary_kinds = sorted(
            {
                str(boundary.get("kind") or "")
                for proof in proofs
                for boundary in proof.get("unsupported_boundaries", ())
                if isinstance(boundary, Mapping) and boundary.get("kind")
            }
        )
        return RepairConformanceResult(
            passed=False,
            code="source_behavior_proof_failed",
            reason=(
                "candidate source does not prove every required source-behavior "
                "operation through supported local or explicit-parameter data flow"
            ),
            details={
                "focus_candidate_id": contract.focus_candidate_id,
                "schema_field_constraints": [constraint.to_dict()],
                "source_behavior_proofs": proofs[:16],
                "proof_fingerprints": [
                    proof["proof_fingerprint"]
                    for proof in proofs
                    if isinstance(proof.get("proof_fingerprint"), str)
                ],
                "missing_operations": missing_operations,
                "unsupported_boundary_kinds": boundary_kinds,
                "required_change": (
                    "repair every false operation_status item; replace unsupported "
                    "state propagation with local assignments or explicit function "
                    "parameters, then preserve direct records/value projection"
                ),
            },
        )
    return None


def _schema_constraint_source_paths(
    candidate_sources: Mapping[str, str],
    *,
    contract: RepairConformanceContract,
    schema_layer: str,
) -> tuple[str, ...]:
    """Resolve invariant checks independently from the required change path."""

    if schema_layer == "manifest" and contract.manifest_path is not None:
        scoped = (contract.manifest_path,)
    elif schema_layer in {"compile_result", "compiler_output"}:
        scoped = (
            (contract.compiler_path,)
            if contract.compiler_path is not None
            else contract.required_branch_paths
        )
    elif schema_layer == "runtime":
        scoped = contract.runtime_paths or contract.required_branch_paths
    else:
        scoped = contract.required_branch_paths
    present = tuple(path for path in scoped if path in candidate_sources)
    return present or tuple(
        path for path in contract.required_branch_paths if path in candidate_sources
    )


def _source_behavior_analyzer(
    constraint: SchemaFieldRepairConstraint,
):
    """Resolve an analyzer by typed predicate identity, not diagnostic prose."""

    if (
        constraint.schema_layer == "runtime"
        and constraint.field_path
        == "environment.AWORLD_REPLAY_RESPONSE_INDEX.consumer"
        and REPLAY_RESPONSE_INDEX_CONSUMER in constraint.expected
    ):
        return recorded_response_index_source_behavior_proof
    return None


def _operation_response_correlation_failure(
    sources: Mapping[str, str],
    *,
    contract: RepairConformanceContract,
) -> RepairConformanceResult | None:
    """Reject a task-plane branch that ignores the request it is repairing.

    A declaration-only candidate can pass a readiness probe while returning one
    fixture container for every operation.  The generic conformance contract has
    no protocol-specific schema, but it can still require the changed handler to
    consume either the observed operation's request parameters or a deterministic
    response map/cursor.  This catches the common failure before an expensive
    task rollout without imposing a browser/CDP implementation on the framework.
    """

    operations = tuple(
        operation
        for operation in (
            contract.required_fixture_probe_operations
            or contract.late_observed_operations[-1:]
        )
        if operation
    )
    # A shallow synthetic feedback package may only prove that a selector
    # changed.  Require operation correlation once the replay has actually
    # reached the task/data plane (the interaction progress counter is emitted
    # by the runner from the protocol trace), preserving selector-focused
    # conformance checks for transport-only repairs.
    if not contract.requires_fixture_derived_probe or not operations:
        return None
    source_text = "\n".join(sources.values())
    has_index_binding = "AWORLD_REPLAY_RESPONSE_INDEX" in source_text
    projection_sources = {
        path: source
        for path, source in sources.items()
        if not contract.required_branch_paths
        or path in contract.required_branch_paths
    }
    if has_index_binding and not _projects_response_index_record_value(
        projection_sources
    ):
        return RepairConformanceResult(
            passed=False,
            code="operation_response_uncorrelated",
            reason=(
                "task-plane response traverses response-index metadata instead "
                "of projecting the indexed recorded payload"
            ),
            details={
                "operation": next(iter(operations), "unknown"),
                "required_change": (
                    "project response-index record['value'] (or resolve "
                    "record['payload_path'] from the immutable fixture) before "
                    "constructing the protocol result"
                ),
            },
        )
    # Once a candidate advertises the framework index, do not let a shallow
    # synthetic contract (which may lack a numeric progress counter) hide a
    # response branch that still falls back to a module-global container.  The
    # index must participate in the returned data flow, not merely be loaded by
    # an unrelated helper.  Candidates without an index retain the historical
    # selector-only path until a real task-plane timeout supplies progress.
    if has_index_binding and contract.interaction_progress < 4:
        operation_names = {operation.casefold() for operation in operations}
        for path, source in sorted(sources.items()):
            if PurePosixPath(path).suffix.casefold() != ".py":
                continue
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            loaded_names = {
                node.id.casefold()
                for node in ast.walk(tree)
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
            }
            if not any(
                name in loaded_names
                for name in ("response_container", "fixture_container", "response_token")
            ):
                continue
            for function in ast.walk(tree):
                if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                branch = _operation_branch(function, operation_names)
                if branch is None:
                    continue
                branch_names = {
                    node.id.casefold()
                    for node in ast.walk(branch)
                    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
                }
                if any(
                    name in branch_names
                    for name in ("response_container", "fixture_container", "response_token")
                ) and "response_index" not in branch_names:
                    return RepairConformanceResult(
                        passed=False,
                        code="operation_response_uncorrelated",
                        reason=(
                            "indexed task-plane branch still falls back to a "
                            "module-global response container"
                        ),
                        details={
                            "path": path,
                            "function": function.name,
                            "operation": next(iter(operations), "unknown"),
                            "required_change": (
                                "make the selected non_empty response-index record "
                                "reach the returned protocol payload; do not fall "
                                "back to RESPONSE_CONTAINER or RESPONSE_TOKEN"
                            ),
                        },
                    )
        if contract.interaction_progress < 4:
            return None
    # Without a response index, retain the selector-focused contract until a
    # real task-plane timeout supplies progress.  At that point the index is
    # mandatory and the candidate is rejected below.
    if contract.interaction_progress < 4:
        return None
    if not has_index_binding:
        return RepairConformanceResult(
            passed=False,
            code="operation_response_uncorrelated",
            reason=(
                "task-plane fixture response does not consume the framework's "
                "immutable operation-response index"
            ),
            details={
                "operation": next(iter(operations), "unknown"),
                "required_change": (
                    "load AWORLD_REPLAY_RESPONSE_INDEX (and, when needed, "
                    "AWORLD_REPLAY_FIXTURE_PATH), select a non_empty record for "
                    "the incoming operation, and project its recorded value"
                ),
            },
        )
    operation_names = {operation.casefold() for operation in operations}
    parameter_names = {
        "params",
        "parameters",
        "arguments",
        "request",
        "request_data",
        "payload",
        "query",
    }
    map_markers = (
        "cursor",
        "offset",
        "index",
        "operation_map",
        "responses_by_operation",
        "response_by_operation",
        "per_operation",
        "recorded_responses",
        "response_records",
    )
    for path, source in sorted(sources.items()):
        if PurePosixPath(path).suffix.casefold() != ".py":
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            function_name = function.name.casefold()
            if not any(
                marker in function_name
                for marker in ("handle", "dispatch", "request", "command", "response")
            ):
                continue
            branch = _operation_branch(function, operation_names)
            if branch is None:
                continue
            loaded_names = {
                node.id.casefold()
                for node in ast.walk(branch)
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
            }
            response_loaded_names: set[str] = set()
            for return_node in ast.walk(branch):
                if not isinstance(return_node, ast.Return) or return_node.value is None:
                    continue
                response_loaded_names.update(
                    node.id.casefold()
                    for node in ast.walk(return_node.value)
                    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
                )
            request_aliases: set[str] = set()
            for assignment in ast.walk(branch):
                if not isinstance(assignment, ast.Assign):
                    continue
                value_names = {
                    node.id.casefold()
                    for node in ast.walk(assignment.value)
                    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
                }
                if not (value_names & parameter_names):
                    continue
                for target in assignment.targets:
                    if isinstance(target, ast.Name):
                        request_aliases.add(target.id.casefold())
            uses_request = bool(
                response_loaded_names & (parameter_names | request_aliases)
            )
            uses_operation_map = bool(
                loaded_names
                and any(
                    any(marker in name for marker in map_markers)
                    for name in loaded_names
                )
            )
            indexes_by_operation = any(
                isinstance(node, ast.Subscript)
                and isinstance(node.slice, ast.Name)
                and node.slice.id.casefold()
                in {"method", "operation", "op", "command", "route"}
                for node in ast.walk(branch)
            )
            gets_by_operation = any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id.casefold()
                in {"method", "operation", "op", "command", "route"}
                for node in ast.walk(branch)
            )
            if uses_request or (uses_operation_map and (indexes_by_operation or gets_by_operation)):
                if not has_index_binding and not uses_operation_map:
                    return RepairConformanceResult(
                        passed=False,
                        code="operation_response_uncorrelated",
                        reason=(
                            "task-plane fixture response is not bound to the "
                            "framework operation-response index"
                        ),
                        details={
                            "path": path,
                            "function": function.name,
                            "operation": next(iter(operations), "unknown"),
                            "required_change": (
                                "consume AWORLD_REPLAY_RESPONSE_INDEX (or an "
                                "equivalent deterministic per-operation response "
                                "map) and project its recorded value"
                            ),
                        },
                    )
                continue
            operation = next(iter(operations), "unknown")
            return RepairConformanceResult(
                passed=False,
                code="operation_response_uncorrelated",
                reason=(
                    "task-plane operation branch returns a global response without "
                    "consuming request parameters or a deterministic per-operation "
                    "recorded-response map"
                ),
                details={
                    "path": path,
                    "function": function.name,
                    "operation": operation,
                    "required_operations": list(operations),
                    "required_change": [
                        "read bounded request parameters for the observed operation",
                        "or select a response from a deterministic operation map/cursor",
                        "preserve the recorded response shape instead of returning one global container",
                    ],
                },
            )
    return None


def _projects_response_index_record_value(
    sources: Mapping[str, str],
) -> bool:
    """Return whether changed source reads the sidecar payload, not its metadata."""

    for path, source in sources.items():
        if PurePosixPath(path).suffix.casefold() != ".py":
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            key: object | None = None
            if isinstance(node, ast.Subscript):
                slice_node = node.slice
                if isinstance(slice_node, ast.Constant):
                    key = slice_node.value
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                key = node.args[0].value
            if key in {"value", "payload_path"}:
                return True
    return False


def _operation_branch(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    operation_names: set[str],
) -> ast.If | None:
    """Find an operation-specific branch without assuming a protocol name."""

    for node in ast.walk(function):
        if not isinstance(node, ast.If):
            continue
        literals = {
            str(item.value).casefold()
            for item in ast.walk(node.test)
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        }
        if literals & operation_names:
            return node
    return None


def _diagnostic_failure_codes(
    diagnostics: Sequence[Mapping[str, object]],
) -> tuple[str, ...]:
    codes: list[str] = []
    pending: list[object] = list(diagnostics)
    visited = 0
    while pending and visited < 512:
        current = pending.pop()
        visited += 1
        if isinstance(current, Mapping):
            for field_name in ("code", "capability_error_code"):
                code = current.get(field_name)
                if isinstance(code, str) and code and code != "failed_gate":
                    normalized = sanitize_text(code, max_chars=120)
                    if normalized not in codes:
                        codes.append(normalized)
            for field_name in ("failure_codes", "constraint_failure_codes"):
                raw_codes = current.get(field_name)
                if not isinstance(raw_codes, (list, tuple)):
                    continue
                for code in raw_codes[:64]:
                    if not isinstance(code, str) or not code or code == "failed_gate":
                        continue
                    normalized = sanitize_text(code, max_chars=120)
                    if normalized not in codes:
                        codes.append(normalized)
            pending.extend(current.values())
        elif isinstance(current, (list, tuple)):
            pending.extend(current)
    return tuple(codes)


def _fixture_probe_constraints(
    diagnostics: Sequence[Mapping[str, object]],
) -> tuple[FixtureDerivedProbeConstraint, ...]:
    """Collect content-free probe constraints from direct and inherited feedback."""

    collected: dict[
        tuple[str, str, str, int],
        FixtureDerivedProbeConstraint,
    ] = {}
    pending: list[object] = list(diagnostics)
    visited = 0
    while pending and visited < 512 and len(collected) < 64:
        current = pending.pop()
        visited += 1
        if isinstance(current, Mapping):
            raw_constraints = current.get("fixture_probe_constraints")
            if isinstance(raw_constraints, (list, tuple)):
                for raw_constraint in raw_constraints[:64]:
                    if not isinstance(raw_constraint, Mapping):
                        continue
                    try:
                        constraint = FixtureDerivedProbeConstraint.from_dict(
                            raw_constraint
                        )
                    except ValueError:
                        continue
                    key = (
                        str(constraint.requirement_identity_digest),
                        constraint.kind,
                        constraint.path,
                        constraint.max_response_chars,
                    )
                    collected[key] = constraint
            pending.extend(current.values())
        elif isinstance(current, (list, tuple)):
            pending.extend(current)
    return tuple(collected[key] for key in sorted(collected))


def _schema_field_constraints(
    diagnostics: Sequence[Mapping[str, object]],
) -> tuple[SchemaFieldRepairConstraint, ...]:
    """Collect typed schema rules from direct and projected repair feedback."""

    collected: dict[str, SchemaFieldRepairConstraint] = {}
    pending: list[object] = list(diagnostics)
    visited = 0
    while pending and visited < 512 and len(collected) < 100:
        current = pending.pop()
        visited += 1
        if isinstance(current, Mapping):
            raw_constraints = current.get("schema_field_constraints")
            if isinstance(raw_constraints, (list, tuple)):
                for raw_constraint in raw_constraints[:100]:
                    if not isinstance(raw_constraint, Mapping):
                        continue
                    try:
                        constraint = SchemaFieldRepairConstraint.from_dict(
                            raw_constraint
                        )
                    except ValueError:
                        continue
                    collected[constraint.identity_digest] = constraint
            pending.extend(current.values())
        elif isinstance(current, (list, tuple)):
            pending.extend(current)
    return tuple(collected[key] for key in sorted(collected))


def _runtime_response_constraints(
    diagnostics: Sequence[Mapping[str, object]],
) -> tuple[RuntimeResponseConstraint, ...]:
    """Collect payload-free runtime response semantics from nested feedback."""

    collected: dict[str, RuntimeResponseConstraint] = {}
    pending: list[object] = list(diagnostics)
    visited = 0
    while pending and visited < 512 and len(collected) < 64:
        current = pending.pop()
        visited += 1
        if isinstance(current, Mapping):
            raw_constraints = current.get("runtime_response_constraints")
            if isinstance(raw_constraints, (list, tuple)):
                for raw_constraint in raw_constraints[:64]:
                    if not isinstance(raw_constraint, Mapping):
                        continue
                    try:
                        constraint = RuntimeResponseConstraint.from_dict(
                            raw_constraint
                        )
                    except ValueError:
                        continue
                    collected[constraint.identity_digest] = constraint
            pending.extend(current.values())
        elif isinstance(current, (list, tuple)):
            pending.extend(current)
    return tuple(collected[key] for key in sorted(collected))


def _fixture_probe_structure_failure(
    sources: Mapping[str, str],
    *,
    contract: RepairConformanceContract,
) -> RepairConformanceResult | None:
    if (
        not contract.requires_fixture_derived_probe
        and not contract.fixture_probe_constraints
    ):
        return None
    literals: set[str] = set()
    for path, source in sources.items():
        if PurePosixPath(path).suffix.lower() not in _SOURCE_SUFFIXES:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        literals.update(
            value.value
            for value in ast.walk(tree)
            if isinstance(value, ast.Constant) and isinstance(value.value, str)
        )
    gateway_keys = ("action_result", "tool_outputs")
    payload_keys = ("content", "response", "result", "output", "body", "data")
    requires_gateway_repair = (
        "late_fixture_probe_outside_recorded_payload"
        in contract.failure_codes
        or any(key in literals for key in gateway_keys)
    )
    if not requires_gateway_repair:
        return None
    missing_gateway_keys = [key for key in gateway_keys if key not in literals]
    observed_payload_keys = [key for key in payload_keys if key in literals]
    if not missing_gateway_keys and observed_payload_keys:
        return None
    return RepairConformanceResult(
        passed=False,
        code="fixture_gateway_discovery_missing",
        reason=(
            "the changed selector still lacks an explicit recorded-response "
            "gateway phase before payload scalar selection"
        ),
        details={
            "focus_candidate_id": contract.focus_candidate_id,
            "missing_gateway_keys": missing_gateway_keys,
            "required_gateway_keys": list(gateway_keys),
            "required_payload_keys": list(payload_keys),
            "observed_payload_keys": observed_payload_keys,
            "required_phase_order": [
                "discover every action_result/tool_outputs subtree",
                "then enter content/response/result/output/body/data",
                "then decode nested containers and select a non-empty leaf",
            ],
        },
    )


def _reachable_operation_functions(
    tree: ast.AST,
    *,
    operation_names: Sequence[str],
) -> set[str]:
    """Return operation handlers and helpers reachable from their branches."""

    normalized_operations = {
        str(operation).casefold()
        for operation in operation_names
        if str(operation).strip()
    }
    if not normalized_operations:
        return set()
    functions = {
        function.name: function
        for function in ast.walk(tree)
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    reachable: set[str] = set()
    pending: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for function in functions.values():
        if _operation_branch(function, normalized_operations) is not None:
            reachable.add(function.name)
            pending.append(function)
    while pending:
        function = pending.pop()
        branch = _operation_branch(function, normalized_operations)
        if branch is not None:
            nodes = (
                node
                for root in (branch.test, *branch.body)
                for node in ast.walk(root)
            )
        else:
            nodes = ast.walk(function)
        for node in nodes:
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            helper = functions.get(node.func.id)
            if helper is not None and helper.name not in reachable:
                reachable.add(helper.name)
                pending.append(helper)
    return reachable


def _fixture_probe_derivation_violations(
    sources: Mapping[str, str],
    *,
    required: bool,
    operation_names: Sequence[str] = (),
) -> list[dict[str, object]]:
    if not required:
        return []
    violations: list[dict[str, object]] = []
    for path, source in sorted(sources.items()):
        if PurePosixPath(path).suffix.lower() != ".py":
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        reachable_functions = _reachable_operation_functions(
            tree,
            operation_names=operation_names,
        )
        unused_payload_keys = _unused_payload_key_declarations(tree)
        if unused_payload_keys:
            selector = next(
                (
                    function
                    for function in ast.walk(tree)
                    if isinstance(
                        function,
                        (ast.FunctionDef, ast.AsyncFunctionDef),
                    )
                    and any(
                        marker in function.name.casefold()
                        for marker in (
                            "payload",
                            "select",
                            "fixture",
                            "response",
                            "probe",
                        )
                    )
                    and any(
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in {"items", "values"}
                        for node in ast.walk(function)
                    )
                ),
                None,
            )
            if selector is not None:
                violations.append(
                    {
                        "path": path,
                        "function": selector.name,
                        "line": int(selector.lineno),
                        "construct": "payload_key_gate_declared_but_unused",
                    }
                )
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _fixture_selector_function_name(function.name):
                continue
            # Only enforce fixture derivation rules on the changed operation
            # branch and helpers it calls.  A runtime may legitimately retain
            # a separate selector for an unrelated protocol method; rejecting
            # that dead/unrelated helper would violate the conformance
            # contract's "failed branch" boundary.
            if reachable_functions and function.name not in reachable_functions:
                continue
            top_level_projection = _top_level_fixture_projection(function)
            if top_level_projection is not None:
                violations.append(
                    {
                        "path": path,
                        "function": function.name,
                        "line": int(top_level_projection.lineno),
                        "construct": "top_level_fixture_projection",
                    }
                )
            gateway_container = _gateway_container_selected_instead_of_subtree(
                function
            )
            if gateway_container is not None:
                violations.append(
                    {
                        "path": path,
                        "function": function.name,
                        "line": int(gateway_container.lineno),
                        "construct": "gateway_container_selected_instead_of_subtree",
                    }
                )
            boolean_metadata = _boolean_metadata_not_excluded(function)
            if boolean_metadata is not None:
                violations.append(
                    {
                        "path": path,
                        "function": function.name,
                        "line": int(boolean_metadata.lineno),
                        "construct": "boolean_metadata_not_excluded",
                    }
                )
            combined_scalars = _multiple_fixture_scalars_combined(function)
            if combined_scalars is not None:
                violations.append(
                    {
                        "path": path,
                        "function": function.name,
                        "line": int(combined_scalars.lineno),
                        "construct": "multiple_fixture_scalars_combined",
                    }
                )
            direct_gateway_scalar = _direct_gateway_scalar_selection(function)
            if direct_gateway_scalar is not None:
                violations.append(
                    {
                        "path": path,
                        "function": function.name,
                        "line": int(direct_gateway_scalar.lineno),
                        "construct": "gateway_scalar_selected_before_payload",
                    }
                )
            root_fallback = _root_fallback_reachable_after_gateway(function)
            if root_fallback is not None:
                violations.append(
                    {
                        "path": path,
                        "function": function.name,
                        "line": int(root_fallback.lineno),
                        "construct": "root_fallback_reachable_after_gateway",
                    }
                )
            skipped_sequence = _gateway_discovery_sequence_skip(function)
            if skipped_sequence is not None:
                violations.append(
                    {
                        "path": path,
                        "function": function.name,
                        "line": int(
                            getattr(skipped_sequence, "lineno", function.lineno)
                        ),
                        "construct": (
                            "gateway_discovery_skips_nested_sequences"
                        ),
                    }
                )
            docstring_node = (
                function.body[0].value
                if function.body
                and isinstance(function.body[0], ast.Expr)
                and isinstance(function.body[0].value, ast.Constant)
                and isinstance(function.body[0].value.value, str)
                else None
            )
            for node in ast.walk(function):
                if node is docstring_node:
                    continue
                construct = _forbidden_fixture_derivation_construct(node)
                if construct is None:
                    continue
                violations.append(
                    {
                        "path": path,
                        "function": function.name,
                        "line": int(getattr(node, "lineno", function.lineno)),
                        "construct": construct,
                    }
                )
    return violations[:32]


def _top_level_fixture_projection(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> ast.Return | ast.Call | None:
    """Reject task data helpers that expose the outer fixture envelope.

    A trajectory fixture is commonly a list of action records.  Returning
    ``FIXTURE_DATA`` or ``FIXTURE_DATA.get(key, [])`` from a task-plane helper
    is non-empty but not a recorded response: it discards the gateway/payload
    correlation and makes callers observe action metadata instead of task
    records.  This check is deliberately limited to helpers that advertise
    fixture/list/response normalization; arbitrary protocol code may still use
    a local list or a concrete response projection.
    """

    name = function.name.casefold()
    if not any(
        marker in name
        for marker in ("normalize", "fixture_list", "fixture_data", "response_data")
    ):
        return None
    fixture_names = {"fixture_data", "fixture", "root"}
    for node in ast.walk(function):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Name):
            if node.value.id.casefold() in fixture_names:
                return node
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "get" or not isinstance(node.func.value, ast.Name):
            continue
        if node.func.value.id.casefold() not in fixture_names:
            continue
        if node.args and isinstance(node.args[-1], ast.List):
            return node
    return None


def _gateway_container_selected_instead_of_subtree(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> ast.Call | None:
    """Find ``gateways.append(root)``-style container selection.

    Gateway discovery must retain the value below ``action_result`` or
    ``tool_outputs``.  Appending the surrounding root/container causes later
    scalar selection to see envelope metadata instead of the recorded payload.
    The check is intentionally structural and protocol-neutral: it only uses
    gateway-shaped names and the observed gateway key literals.
    """

    gateway_names = {
        argument.arg
        for argument in (*function.args.posonlyargs, *function.args.args)
        if "gateway" in argument.arg.casefold()
    }
    gateway_names.update(
        node.id
        for node in ast.walk(function)
        if isinstance(node, ast.Name) and "gateway" in node.id.casefold()
    )
    root_names = {
        argument.arg
        for argument in (*function.args.posonlyargs, *function.args.args)
        if argument.arg.casefold() in {"root", "obj", "value", "item"}
    }
    gateway_literals = {
        node.value
        for node in ast.walk(function)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.casefold() in {"action_result", "tool_outputs"}
    }
    if not gateway_literals or not gateway_names or not root_names:
        return None
    for call in ast.walk(function):
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
            continue
        if call.func.attr != "append" or not call.args:
            continue
        receiver = call.func.value
        argument = call.args[0]
        if (
            isinstance(receiver, ast.Name)
            and receiver.id in gateway_names
            and isinstance(argument, ast.Name)
            and argument.id in root_names
        ):
            return call
    return None


def _boolean_metadata_not_excluded(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> ast.Call | None:
    """Find scalar selectors that accept ``int`` without excluding ``bool``."""
    scalar_subjects: dict[str, ast.Call] = {}
    for call in ast.walk(function):
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
            continue
        if call.func.id != "isinstance" or len(call.args) < 2:
            continue
        subject = call.args[0]
        if not isinstance(subject, ast.Name):
            continue
        scalar_types = {
            name.id
            for name in ast.walk(call.args[1])
            if isinstance(name, ast.Name)
        }
        if not scalar_types & {"int", "float"} or "bool" in scalar_types:
            continue
        scalar_subjects.setdefault(subject.id, call)
    if not scalar_subjects:
        return None

    # A selector is conforming when it explicitly rejects/continues on bool
    # before the broad scalar check.  Infer the subject name from the actual
    # isinstance call rather than relying on conventional names such as
    # ``value``; generated candidates commonly call it ``data`` or ``node``.
    for node in ast.walk(function):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not (
            isinstance(test, ast.Call)
            and isinstance(test.func, ast.Name)
            and test.func.id == "isinstance"
            and len(test.args) >= 2
            and isinstance(test.args[0], ast.Name)
            and any(
                isinstance(name, ast.Name) and name.id == "bool"
                for name in ast.walk(test.args[1])
            )
            and test.args[0].id in scalar_subjects
        ):
            continue
        if any(
            isinstance(statement, (ast.Return, ast.Raise, ast.Continue, ast.Break))
            for statement in node.body
        ):
            scalar_subjects.pop(test.args[0].id, None)
    return next(iter(scalar_subjects.values()), None)


def _multiple_fixture_scalars_combined(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> ast.Call | None:
    """Find probe assertions formed by joining multiple fixture leaves.

    ``response_contains`` proves one scalar descendant of the recorded
    payload. Joining two individually recorded values creates a new string
    that need not occur anywhere in the fixture, even though each input was
    fixture-derived. Keep the check bounded to selector-shaped functions and
    collection-shaped join inputs so ordinary string normalization remains
    valid.
    """

    assignments: dict[str, ast.AST] = {}
    for node in ast.walk(function):
        if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
        for target in targets:
            if isinstance(target, ast.Name):
                assignments[target.id] = node.value

    def collection_source(value: ast.AST, *, depth: int = 0) -> bool:
        if depth > 4:
            return False
        if isinstance(value, ast.Name):
            assigned = assignments.get(value.id)
            if assigned is not None:
                return collection_source(assigned, depth=depth + 1)
            normalized = value.id.casefold()
            return any(
                marker in normalized
                for marker in ("leaves", "scalars", "selected", "values")
            )
        if isinstance(value, (ast.List, ast.Tuple, ast.Set, ast.ListComp, ast.SetComp)):
            return True
        if isinstance(value, ast.Subscript) and isinstance(value.slice, ast.Slice):
            upper = value.slice.upper
            return not (
                isinstance(upper, ast.Constant)
                and isinstance(upper.value, int)
                and not isinstance(upper.value, bool)
                and upper.value <= 1
            )
        if isinstance(value, ast.Call):
            called_name = (
                value.func.id
                if isinstance(value.func, ast.Name)
                else value.func.attr
                if isinstance(value.func, ast.Attribute)
                else None
            )
            return bool(
                called_name
                and any(
                    marker in called_name.casefold()
                    for marker in ("collect", "descendant", "leaves", "scalars")
                )
            )
        return False

    for call in ast.walk(function):
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "join"
            and call.args
            and collection_source(call.args[0])
        ):
            return call
    return None


def _direct_gateway_scalar_selection(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> ast.Call | None:
    """Find a selector that feeds a gateway itself into a scalar/leaf walk."""

    gateway_names = {
        argument.arg
        for argument in (*function.args.posonlyargs, *function.args.args)
        if argument.arg.casefold() in {"gateway", "gw"}
        or "gateway" in argument.arg.casefold()
    }
    gateway_names.update(
        node.id
        for node in ast.walk(function)
        if isinstance(node, ast.Name)
        and (
            node.id.casefold() in {"gateway", "gw"}
            or "gateway" in node.id.casefold()
        )
    )
    if not gateway_names:
        return None
    for call in ast.walk(function):
        if not isinstance(call, ast.Call) or not call.args:
            continue
        callee = call.func
        callee_name = (
            callee.id
            if isinstance(callee, ast.Name)
            else callee.attr
            if isinstance(callee, ast.Attribute)
            else ""
        ).casefold()
        if not any(marker in callee_name for marker in ("leaf", "scalar")):
            continue
        first_argument = call.args[0]
        if isinstance(first_argument, ast.Name) and first_argument.id in gateway_names:
            return call
    return None


def _root_fallback_reachable_after_gateway(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> ast.Call | None:
    """Find a root scalar walk reachable after a truthy gateway branch.

    A fixture selector may inspect the parsed root only when the complete gateway
    list is empty.  This catches the common rationale/source mismatch where an
    ``if gateways`` block performs the right payload walk but then falls through
    to an unconditional ``select_scalar(root)`` statement.
    """

    pending: list[list[ast.stmt]] = [function.body]
    while pending:
        statements = pending.pop()
        for index, statement in enumerate(statements):
            pending.extend(_nested_statement_blocks(statement))
            if not isinstance(statement, ast.If):
                continue
            gateway_names = _positive_gateway_test_names(statement.test)
            if not gateway_names or _block_guaranteed_terminates(statement.body):
                continue
            root_names = _gateway_source_names(
                statements[:index],
                gateway_names=gateway_names,
            )
            if not root_names:
                continue
            for following in statements[index + 1 :]:
                if _statement_guards_gateway_empty(
                    following,
                    gateway_names=gateway_names,
                ):
                    continue
                fallback = _direct_scalar_call_on_names(
                    following,
                    names=root_names,
                )
                if fallback is not None:
                    return fallback
                if _statement_guaranteed_terminates(following):
                    break
    return None


def _nested_statement_blocks(statement: ast.stmt) -> list[list[ast.stmt]]:
    blocks: list[list[ast.stmt]] = []
    for attribute in ("body", "orelse", "finalbody"):
        value = getattr(statement, attribute, None)
        if isinstance(value, list) and value:
            blocks.append(value)
    handlers = getattr(statement, "handlers", None)
    if isinstance(handlers, list):
        for handler in handlers:
            body = getattr(handler, "body", None)
            if isinstance(body, list) and body:
                blocks.append(body)
    return blocks


def _positive_gateway_test_names(test: ast.expr) -> set[str]:
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return set()
    return {
        node.id
        for node in ast.walk(test)
        if isinstance(node, ast.Name) and "gateway" in node.id.casefold()
    }


def _gateway_source_names(
    statements: Sequence[ast.stmt],
    *,
    gateway_names: set[str],
) -> set[str]:
    roots: set[str] = set()
    for statement in statements:
        for call in ast.walk(statement):
            if not isinstance(call, ast.Call):
                continue
            argument_names = {
                argument.id
                for argument in call.args
                if isinstance(argument, ast.Name)
            }
            if not (argument_names & gateway_names):
                continue
            roots.update(argument_names - gateway_names)
        if isinstance(statement, (ast.Assign, ast.AnnAssign)):
            assigned = set(_assigned_names(statement))
            value = statement.value
            if assigned & gateway_names and isinstance(value, ast.Call):
                roots.update(
                    argument.id
                    for argument in value.args
                    if isinstance(argument, ast.Name)
                    and argument.id not in gateway_names
                )
    return roots


def _statement_guards_gateway_empty(
    statement: ast.stmt,
    *,
    gateway_names: set[str],
) -> bool:
    if not isinstance(statement, ast.If):
        return False
    test = statement.test
    return (
        isinstance(test, ast.UnaryOp)
        and isinstance(test.op, ast.Not)
        and any(
            isinstance(node, ast.Name) and node.id in gateway_names
            for node in ast.walk(test.operand)
        )
    )


def _direct_scalar_call_on_names(
    statement: ast.stmt,
    *,
    names: set[str],
) -> ast.Call | None:
    # Nested conditionals have their own path predicate. They are deliberately
    # not flattened here, so an explicit ``if not gateways`` remains valid.
    if isinstance(statement, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try)):
        return None
    for call in ast.walk(statement):
        if not isinstance(call, ast.Call) or not call.args:
            continue
        callee = call.func
        callee_name = (
            callee.id
            if isinstance(callee, ast.Name)
            else callee.attr
            if isinstance(callee, ast.Attribute)
            else ""
        ).casefold()
        if not any(marker in callee_name for marker in ("leaf", "scalar")):
            continue
        first_argument = call.args[0]
        if isinstance(first_argument, ast.Name) and first_argument.id in names:
            return call
    return None


def _block_guaranteed_terminates(statements: Sequence[ast.stmt]) -> bool:
    return any(_statement_guaranteed_terminates(statement) for statement in statements)


def _statement_guaranteed_terminates(statement: ast.stmt) -> bool:
    if isinstance(statement, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
        return True
    if isinstance(statement, ast.If):
        return bool(statement.orelse) and _block_guaranteed_terminates(
            statement.body
        ) and _block_guaranteed_terminates(statement.orelse)
    return False


def _unused_payload_key_declarations(tree: ast.Module) -> set[str]:
    payload_keys = {"content", "response", "result", "output", "body", "data"}
    declared: set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        names = _assigned_names(node)
        if not any("payload" in name.casefold() for name in names):
            continue
        value = node.value
        literals = {
            item.value
            for item in ast.walk(value)
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        }
        if payload_keys & literals:
            declared.update(names)
    loaded = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    return declared - loaded


def _fixture_selector_function_name(name: str) -> bool:
    normalized = name.casefold()
    return any(
        marker in normalized
        for marker in (
            "derive",
            "extract",
            "fixture",
            "gateway",
            "payload",
            "probe",
            "recorded",
            "response",
            "scalar",
            "select",
            "token",
        )
    )


def _gateway_discovery_sequence_skip(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> ast.If | None:
    literals = {
        node.value
        for node in ast.walk(function)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    if not {"action_result", "tool_outputs"}.issubset(literals):
        return None
    sequence_types = {"list", "tuple", "Sequence", "MutableSequence"}
    if any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "isinstance"
        and len(node.args) >= 2
        and any(
            isinstance(name, ast.Name) and name.id in sequence_types
            for name in ast.walk(node.args[1])
        )
        for node in ast.walk(function)
    ):
        return None
    for node in ast.walk(function):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not (
            isinstance(test, ast.UnaryOp)
            and isinstance(test.op, ast.Not)
            and isinstance(test.operand, ast.Call)
            and isinstance(test.operand.func, ast.Name)
            and test.operand.func.id == "isinstance"
            and len(test.operand.args) >= 2
            and isinstance(test.operand.args[0], ast.Name)
            and any(
                isinstance(name, ast.Name) and name.id == "dict"
                for name in ast.walk(test.operand.args[1])
            )
        ):
            continue
        subject = test.operand.args[0].id
        if any(
            isinstance(value, ast.Return)
            and isinstance(value.value, ast.Name)
            and value.value.id == subject
            for statement in node.body
            for value in ast.walk(statement)
        ):
            return node
    return None


def _forbidden_fixture_derivation_construct(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        normalized = node.value.strip().casefold()
        if "placeholder" in normalized or "default_token" in normalized:
            return "literal_probe_fallback"
    if isinstance(node, ast.Call):
        function = node.func
        if isinstance(function, ast.Attribute) and function.attr in {
            "findall",
            "finditer",
            "fullmatch",
            "match",
            "search",
        }:
            return "regex_scalar_filter"
        if (
            isinstance(function, ast.Attribute)
            and function.attr.casefold().startswith(("md5", "sha"))
            and _hash_call_is_assertion_fallback(node)
        ):
            return "fixture_hash_assertion_fallback"
    if isinstance(node, ast.Compare) and _narrow_scalar_length_filter(node):
        return "narrow_scalar_length_filter"
    return None


def _hash_call_is_assertion_fallback(call: ast.Call) -> bool:
    # A hash used as a source identifier is harmless. In a selector function,
    # hashing fixture/token/scalar inputs is an assertion fallback and cannot
    # prove response reconstruction.
    return any(
        isinstance(name, ast.Name)
        and any(
            marker in name.id.casefold()
            for marker in ("fixture", "payload", "response", "scalar", "token")
        )
        for argument in call.args
        for name in ast.walk(argument)
    )


def _narrow_scalar_length_filter(compare: ast.Compare) -> bool:
    nodes = (compare.left, *compare.comparators)
    for value in nodes:
        for call in ast.walk(value):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "len"
                and call.args
                and isinstance(call.args[0], ast.Name)
                and call.args[0].id.casefold()
                in {"candidate", "leaf", "s", "scalar", "token", "value"}
            ):
                return True
    return False


def _fixture_probe_constraint_failure(
    services: Sequence[ReplayServiceSpec],
    contract: RepairConformanceContract,
    *,
    fixture_leaf_values: Mapping[str, Sequence[str]] | None,
) -> RepairConformanceResult | None:
    constraints = contract.fixture_probe_constraints
    if not constraints:
        return None
    if fixture_leaf_values is None:
        return RepairConformanceResult(
            passed=False,
            code="fixture_probe_evidence_unavailable",
            reason=(
                "fixture-derived probe constraints require frozen fixture leaf "
                "evidence for conformance validation"
            ),
            details={"constraint_count": len(constraints)},
            failure_class="framework",
            repairable=False,
        )

    missing: list[dict[str, object]] = []
    violations: list[dict[str, object]] = []
    for constraint in constraints:
        matching = [
            (service, probe)
            for service in services
            if constraint.matches_requirement_id(service.requirement_id)
            for probe in service.protocol_probes
            if probe.kind == constraint.kind
            and probe.path == constraint.path
            and not (
                probe.kind == "http"
                and probe.validate_advertised_websockets
            )
        ]
        if not matching:
            missing.append(constraint.to_public_dict())
            continue
        for service, probe in matching:
            response_contains = probe.response_contains
            recorded_values = fixture_leaf_values.get(
                service.response_fixture,
                (),
            )
            violation_code: str | None = None
            if not isinstance(response_contains, str) or not response_contains:
                violation_code = "empty_response_contains"
            elif len(response_contains) > constraint.max_response_chars:
                violation_code = "response_contains_exceeds_bound"
            elif not any(
                _fixture_value_matches(response_contains, value)
                for value in recorded_values
                if isinstance(value, str) and value
            ):
                violation_code = "response_contains_not_fixture_scalar"
            if violation_code is None:
                continue
            response_fingerprint = (
                _conformance_sensitive_fingerprint(response_contains)
                if isinstance(response_contains, str)
                else _conformance_sensitive_fingerprint(None)
            )
            violations.append(
                {
                    "requirement_identity_digest": (
                        constraint.requirement_identity_digest
                    ),
                    "service_id": service.service_id,
                    "probe_kind": constraint.kind,
                    "probe_path": constraint.path,
                    "recorded_leaf_count": len(recorded_values),
                    "declared_response_fingerprint": response_fingerprint,
                    "response_record_id": probe.response_record_id,
                    "violation_code": violation_code,
                }
            )
    if missing:
        return RepairConformanceResult(
            passed=False,
            code="fixture_derived_probe_missing",
            reason=(
                "compiled candidate omits a probe location required to repair "
                "fixture-derived response assertions"
            ),
            details={
                "missing_constraints": missing,
                "constraint_count": len(constraints),
            },
        )
    if violations:
        counterexample_contracts = [
            _fixture_probe_counterexample_contract(item)
            for item in violations[:64]
        ]
        return RepairConformanceResult(
            passed=False,
            code="fixture_derived_probe_not_recorded",
            reason=(
                "every constrained protocol probe must use a bounded non-empty "
                "scalar derived from its own declared fixture"
            ),
            details={
                "violation_count": len(violations),
                "violations": violations[:64],
                "constraint_count": len(constraints),
                "counterexample_contracts": counterexample_contracts,
            },
        )
    return None


def _fixture_probe_counterexample_contract(
    violation: Mapping[str, object],
) -> dict[str, object]:
    """Build a payload-free executable contract for one failed fixture probe."""

    identity_payload = {
        key: violation.get(key)
        for key in (
            "requirement_identity_digest",
            "service_id",
            "probe_kind",
            "probe_path",
            "response_record_id",
            "violation_code",
        )
    }
    encoded = json.dumps(
        identity_payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return {
        "schema_version": (
            "aworld.self_evolve.fixture_probe_counterexample.v1"
        ),
        "counterexample_id": (
            "fixture-probe-counterexample-"
            + hashlib.sha256(encoded).hexdigest()
        ),
        **identity_payload,
        "selector_policy": REPLAY_RESPONSE_SELECTOR_POLICY,
        "required_runtime_bindings": [
            REPLAY_RESPONSE_REQUIREMENT_ID_ENV,
            REPLAY_RESPONSE_SERVICE_ID_ENV,
            "AWORLD_REPLAY_RESPONSE_INDEX.records[*].record_id",
        ],
        "required_checks": [
            "compiler_probe_bound_to_framework_record",
            "declared_assertion_equals_canonical_record_scalar",
            "runtime_response_contains_declared_assertion",
        ],
    }


def evaluate_compiled_probe_conformance(
    services: Sequence[ReplayServiceSpec],
    contract: RepairConformanceContract,
    *,
    fixture_leaf_values: Mapping[str, Sequence[str]] | None = None,
    fixture_response_leaf_values: Mapping[str, Sequence[str]] | None = None,
) -> RepairConformanceResult:
    service_probes = tuple(
        (service, probe)
        for service in services
        if service.transport == "skill_runtime"
        for probe in service.protocol_probes
    )
    probes = tuple(probe for _, probe in service_probes)
    constraint_failure = _fixture_probe_constraint_failure(
        services,
        contract,
        fixture_leaf_values=fixture_leaf_values,
    )
    if constraint_failure is not None:
        return constraint_failure
    if contract.exact_probe is not None:
        exact = contract.exact_probe
        location_matching = [
            (service, probe)
            for service, probe in service_probes
            if probe.kind == exact.kind
            and probe.path == exact.path
            and isinstance(probe.response_contains, str)
            and bool(probe.response_contains.strip())
        ]
        recorded_values = fixture_leaf_values
        if not location_matching:
            return RepairConformanceResult(
                passed=False,
                code="exact_repair_probe_missing",
                reason=(
                    "compiled candidate does not declare the exact fixture-derived "
                    "probe required by the failed branch"
                ),
                details={
                    "probe_kind": exact.kind,
                    "probe_path": exact.path,
                    **_expected_response_public_fields(exact.expected_response),
                    "declared_probe_count": len(probes),
                },
            )
        if recorded_values is None:
            matching = [
                (service, probe)
                for service, probe in location_matching
                if _fixture_value_matches(
                    exact.expected_response,
                    str(probe.response_contains),
                )
            ]
        else:
            matching = [
                (service, probe)
                for service, probe in location_matching
                if not _placeholder_probe_value(probe.response_contains)
                and any(
                    _fixture_value_matches(
                        str(probe.response_contains),
                        value,
                    )
                    for value in recorded_values.get(
                        service.response_fixture, ()
                    )
                    if isinstance(value, str) and value
                )
            ]
        if not matching:
            return RepairConformanceResult(
                passed=False,
                code=(
                    "exact_repair_probe_missing"
                    if recorded_values is None
                    else "exact_repair_probe_not_recorded"
                ),
                reason=(
                    "compiled candidate does not declare the exact fixture-derived "
                    "probe required by the failed branch"
                    if recorded_values is None
                    else (
                        "exact repair probe must use a recorded scalar value rather "
                        "than a mapping key, raw token, or hard-coded diagnostic preview"
                    )
                ),
                details={
                    "probe_kind": exact.kind,
                    "probe_path": exact.path,
                    **_expected_response_public_fields(exact.expected_response),
                    "matching_location_count": len(location_matching),
                    "required_reconstruction_algorithm": [
                        "parse the recorded fixture as JSON or JSONL",
                        "recursively traverse mapping values and list items",
                        "select a deterministic non-empty scalar leaf",
                        "reuse one selected leaf across probes unless distinct values are required",
                        "reuse the same selector in compiler and runtime",
                        "place the selected leaf inside the protocol result payload",
                    ],
                    "forbidden_derivations": [
                        "mapping keys",
                        "raw-byte regex tokens",
                        "hard-coded diagnostic previews",
                        "placeholder literals",
                    ],
                },
            )

    if contract.requires_fixture_derived_probe:
        required_operations = (
            contract.required_fixture_probe_operations
            or contract.late_observed_operations[-1:]
        )
        if not required_operations:
            # A task-plane repair without an observed operation cannot prove
            # that its probe reaches the failed branch.  Treat missing trace
            # evidence as a typed conformance failure instead of allowing an
            # arbitrary readiness/data-plane probe to pass vacuously.
            return RepairConformanceResult(
                passed=False,
                code="late_observed_operation_missing",
                reason=(
                    "task-plane repair requires at least one observed operation "
                    "to bind a non-empty fixture-derived probe"
                ),
                details={
                    "late_observed_operations": list(
                        contract.late_observed_operations
                    ),
                    "required_fixture_probe_operations": list(
                        contract.required_fixture_probe_operations
                    ),
                    "declared_probe_count": len(probes),
                },
            )
        matching_by_operation = {
            operation: [
                (service, probe)
                for service, probe in service_probes
                if isinstance(probe.request_text, str)
                and _request_covers_operation(probe.request_text, operation)
                and isinstance(probe.response_contains, str)
                and bool(probe.response_contains.strip())
            ]
            for operation in required_operations
        }
        missing_operations = [
            operation
            for operation, matching_probes in matching_by_operation.items()
            if not matching_probes
        ]
        if missing_operations:
            return RepairConformanceResult(
                passed=False,
                code="late_fixture_probe_missing",
                reason=(
                    "task-plane repair must declare a non-empty fixture-derived probe "
                    "covering the latest observed operation"
                ),
                details={
                    "required_probe_operation": missing_operations[-1],
                    "missing_probe_operations": missing_operations,
                    "latest_observed_operation": required_operations[-1],
                    "required_fixture_probe_operations": list(
                        contract.required_fixture_probe_operations
                    ),
                    "late_observed_operations": list(
                        contract.late_observed_operations
                    ),
                    "interaction_progress": contract.interaction_progress,
                    "declared_probe_count": len(probes),
                },
            )
        matching = [
            item
            for operation in required_operations
            for item in matching_by_operation[operation]
        ]
        recorded_values = fixture_leaf_values or {}
        response_values = fixture_response_leaf_values or {}
        # ``None`` means the caller has no response-context evidence (the
        # backwards-compatible unit-test path).  Once the capability compiler
        # supplies a response map, an empty/missing fixture entry is a hard
        # failure: falling back to all fixture leaves would allow request or
        # envelope metadata to masquerade as recorded task output.
        response_context_supplied = fixture_response_leaf_values is not None

        def response_context_for(service: ReplayServiceSpec) -> tuple[str, ...]:
            if response_context_supplied:
                return tuple(response_values.get(service.response_fixture, ()))
            return tuple(recorded_values.get(service.response_fixture, ()))

        missing_recorded_operations = [
            operation
            for operation, matching_probes in matching_by_operation.items()
            if not any(
                not _placeholder_probe_value(probe.response_contains)
                and any(
                    _fixture_value_matches(probe.response_contains, value)
                    for value in response_context_for(service)
                    if isinstance(value, str) and value
                )
                for service, probe in matching_probes
            )
        ]
        if missing_recorded_operations:
            outside_payload_matches = [
                probe.response_contains
                for service, probe in matching
                if isinstance(probe.response_contains, str)
                and probe.response_contains
                and any(
                    _fixture_value_matches(probe.response_contains, value)
                    for value in recorded_values.get(service.response_fixture, ())
                    if isinstance(value, str) and value
                )
                and not any(
                    _fixture_value_matches(probe.response_contains, value)
                    for value in response_context_for(service)
                    if isinstance(value, str) and value
                )
            ]
            return RepairConformanceResult(
                passed=False,
                code=(
                    "late_fixture_probe_outside_recorded_payload"
                    if outside_payload_matches
                    else "late_fixture_probe_not_recorded"
                ),
                reason=(
                    "task-plane repair probe selected a real fixture scalar outside "
                    "the recorded payload; perform gateway discovery first and never "
                    "select request/action or action-result metadata"
                    if outside_payload_matches
                    else (
                        "task-plane repair probe must recursively decode JSON/JSONL and "
                        "JSON-encoded output containers, then select a deterministic "
                        "non-empty recorded response leaf; request/envelope scalars, "
                        "mapping keys, and raw-byte regex tokens do not prove fixture "
                        "reconstruction"
                    )
                ),
                details={
                    "latest_observed_operation": required_operations[-1],
                    "missing_recorded_probe_operations": (
                        missing_recorded_operations
                    ),
                    "matching_probe_count": len(matching),
                    "declared_response_contains": [
                        probe.response_contains
                        for _, probe in matching[:16]
                        if probe.response_contains
                    ],
                    "declared_value_classification": (
                        "fixture_scalar_outside_recorded_payload"
                        if outside_payload_matches
                        else "not_a_recorded_fixture_scalar"
                    ),
                    "fixture_leaf_counts": {
                        path: len(values)
                        for path, values in recorded_values.items()
                    },
                    "recorded_response_leaf_counts": {
                        path: len(values)
                        for path, values in response_values.items()
                    },
                    "required_reconstruction_algorithm": [
                        "parse the recorded fixture as JSON or JSONL",
                        "recursively decode bounded JSON object or array strings",
                        "search arbitrary fixture nesting with a bounded node count rather than a shallow depth cutoff",
                        "use a gateway-discovery pass before scalar selection and never fall back to non-output trajectory branches when a gateway exists",
                        "during discovery collect gateway subtrees only: never collect or return any scalar until the complete gateway list is known",
                        "keep trajectory gateway keys limited to action_result and tool_outputs; treat content, response, result, output, body, and data only as payload keys after a gateway",
                        "when gateways exist, call the payload collector on each gateway and call the scalar selector only on those payload subtrees; never scalar-walk a gateway directly",
                        "treat payload selection inside gateways as phase 2; only use a generic parsed-root fallback when the complete gateway list is empty",
                        "recursively traverse mapping values and list items",
                        "for trajectory envelopes enter through action_result or tool_outputs at any depth, then ignore action-result metadata until reaching a content, response, result, output, body, or data payload",
                        "when a gateway value is a list, apply payload-key selection to each item instead of sending the whole list to generic scalar traversal",
                        "select a deterministic non-empty scalar leaf without arbitrary alphanumeric or narrow length filters",
                        "reuse one selected leaf across probes unless distinct values are required",
                        "reuse the same selector in compiler and runtime",
        (
            "return the surrounding decoded recorded container in the protocol result "
            "payload when it fits; otherwise return a deterministic bounded projection "
            "under 48 KiB that retains at least two non-empty scalar descendants from "
            "that same container when available"
        ),
                        "choose probe request inputs that execute the fixture-derived handler branch rather than a constant-result branch",
                    ],
                    "forbidden_derivations": [
                        "mapping keys",
                        "raw-byte regex tokens",
                        "request or envelope-only scalar values when recorded output values exist",
                        "action-result metadata such as tool names, call ids, success flags, or timing fields",
                        "hash or placeholder fallbacks when no leaf matches an arbitrary token regex",
                        "globally treating result or output keys in trajectory request/action records as recorded responses",
                        "placeholder literals",
                        "empty arrays or objects",
                    ],
                },
            )

    return _passed(
        "repair_probes_conform",
        "compiled candidate declares the required repair probes",
        declared_probe_count=len(probes),
    )


def _diagnostic_mappings(value: Mapping[str, object]) -> Sequence[Mapping[str, object]]:
    raw = value.get("candidate_validation_diagnostics")
    diagnostics = (
        [item for item in raw[:32] if isinstance(item, Mapping)]
        if isinstance(raw, list)
        else []
    )
    # New feedback transports the typed contract as a first-class field.  Keep
    # reading diagnostic-embedded contracts for reports produced by older runs.
    first_class_contract = value.get("repair_conformance")
    if isinstance(first_class_contract, Mapping):
        diagnostics.insert(0, {"repair_conformance": first_class_contract})
    return tuple(diagnostics)


def _repair_counterexamples(
    value: Mapping[str, object],
) -> tuple[dict[str, object], ...]:
    raw = value.get("replay_counterexamples")
    if not isinstance(raw, list):
        return ()
    result: list[dict[str, object]] = []
    for item in raw[:16]:
        normalized = normalize_counterexample(item)
        if normalized is not None and normalized not in result:
            result.append(normalized)
    return tuple(result)


def _inherited_repair_conformance_contract(
    diagnostics: Sequence[Mapping[str, object]],
) -> RepairConformanceContract | None:
    inherited: list[RepairConformanceContract] = []

    def collect(value: object) -> None:
        if isinstance(value, Mapping):
            raw_contract = value.get("repair_conformance")
            if isinstance(raw_contract, Mapping):
                try:
                    contract = (
                        RepairConformanceContract.from_public_dict(raw_contract)
                        if raw_contract.get("projection_schema_version")
                        is not None
                        else RepairConformanceContract.from_dict(raw_contract)
                    )
                except ValueError:
                    contract = None
                if contract is not None and contract.focus_candidate_id:
                    inherited.append(contract)
            if (
                value.get("projection_schema_version")
                == "aworld.self_evolve.repair_conformance.public.v1"
            ):
                try:
                    direct_contract = RepairConformanceContract.from_public_dict(
                        value
                    )
                except ValueError:
                    direct_contract = None
                if (
                    direct_contract is not None
                    and direct_contract.focus_candidate_id
                ):
                    inherited.append(direct_contract)
            for key, nested in value.items():
                if key == "repair_conformance":
                    continue
                if isinstance(nested, (Mapping, list, tuple)):
                    collect(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                collect(nested)

    collect(diagnostics)
    if not inherited:
        return None

    # Feedback is deliberately projected at several causal boundaries.  A
    # complete first-class contract and one or more bounded diagnostic copies
    # can therefore coexist in the same summary.  Treating the last copy as
    # authoritative is lossy: the bounded copy may retain routing metadata but
    # omit schema or runtime constraints.  Keep the newest contract as the
    # lineage/routing base while losslessly joining every typed invariant.
    base = inherited[-1]
    fixture_constraints: dict[
        tuple[str, str, str, int],
        FixtureDerivedProbeConstraint,
    ] = {}
    schema_constraints: dict[str, SchemaFieldRepairConstraint] = {}
    runtime_constraints: dict[str, RuntimeResponseConstraint] = {}
    for contract in inherited:
        for constraint in contract.fixture_probe_constraints:
            fixture_constraints[
                (
                    str(constraint.requirement_identity_digest),
                    constraint.kind,
                    constraint.path,
                    constraint.max_response_chars,
                )
            ] = constraint
        for constraint in contract.schema_field_constraints:
            schema_constraints[constraint.identity_digest] = constraint
        for constraint in contract.runtime_response_constraints:
            runtime_constraints[constraint.identity_digest] = constraint

    return replace(
        base,
        failure_codes=tuple(
            dict.fromkeys(
                code
                for contract in inherited
                for code in contract.failure_codes
            )
        ),
        interaction_progress=max(
            contract.interaction_progress for contract in inherited
        ),
        manifest_path=next(
            (
                contract.manifest_path
                for contract in reversed(inherited)
                if contract.manifest_path
            ),
            None,
        ),
        compiler_path=next(
            (
                contract.compiler_path
                for contract in reversed(inherited)
                if contract.compiler_path
            ),
            None,
        ),
        runtime_paths=next(
            (
                contract.runtime_paths
                for contract in reversed(inherited)
                if contract.runtime_paths
            ),
            (),
        ),
        exact_probe=next(
            (
                contract.exact_probe
                for contract in reversed(inherited)
                if contract.exact_probe is not None
            ),
            None,
        ),
        late_observed_operations=tuple(
            dict.fromkeys(
                operation
                for contract in inherited
                for operation in contract.late_observed_operations
            )
        )[-_MAX_OBSERVED_OPERATIONS:],
        requires_compiler_fixture_reconstruction=any(
            contract.requires_compiler_fixture_reconstruction
            for contract in inherited
        ),
        requires_fixture_derived_probe=any(
            contract.requires_fixture_derived_probe for contract in inherited
        ),
        required_fixture_probe_operations=tuple(
            dict.fromkeys(
                operation
                for contract in inherited
                for operation in contract.required_fixture_probe_operations
            )
        )[-_MAX_OBSERVED_OPERATIONS:],
        fixture_probe_constraints=tuple(fixture_constraints.values()),
        schema_field_constraints=tuple(
            schema_constraints[key] for key in sorted(schema_constraints)
        ),
        runtime_response_constraints=tuple(
            runtime_constraints[key] for key in sorted(runtime_constraints)
        ),
        required_runtime_transitions=tuple(
            dict.fromkeys(
                transition
                for contract in inherited
                for transition in contract.required_runtime_transitions
            )
        ),
        artifact_lifecycle_constraint=_merge_artifact_lifecycle_constraints(
            *(
                contract.artifact_lifecycle_constraint
                for contract in inherited
            )
        ),
    )


def _exact_probe_constraint(
    diagnostics: Sequence[Mapping[str, object]],
) -> ExactRepairProbe | None:
    # Persisted and legacy diagnostics are a public channel.  Even if an older
    # record contains an ``expected_preview``, copying it into an executable
    # contract would turn a report back into a private payload transport.  New
    # exact assertions arrive only through RepairConformanceContract instances
    # on OptimizerResult.private_context.
    del diagnostics
    return None


def _expected_response_public_fields(expected: str) -> dict[str, object]:
    encoded = expected.encode("utf-8")
    return {
        "expected_response_fingerprint": (
            "sha256:" + hashlib.sha256(encoded).hexdigest()
        ),
        "expected_response_bytes": len(encoded),
        "expected_response_shape": {
            "kind": "text",
            "size_bucket": max(1, len(encoded)).bit_length(),
        },
    }


def _observed_operations(
    diagnostics: Sequence[Mapping[str, object]],
) -> tuple[str, ...]:
    operations: list[str] = []
    def collect(item: object) -> None:
        if isinstance(item, Mapping):
            raw = item.get("observed_request_operations")
            if isinstance(raw, list):
                for value in raw:
                    if not isinstance(value, str) or not value.strip():
                        continue
                    normalized = sanitize_text(value, max_chars=120).strip()
                    if normalized in operations:
                        operations.remove(normalized)
                    operations.append(normalized)
            for value in item.values():
                if isinstance(value, (Mapping, list, tuple)):
                    collect(value)
        elif isinstance(item, (list, tuple)):
            for value in item:
                collect(value)

    collect(diagnostics)
    return tuple(operations[-_MAX_OBSERVED_OPERATIONS:])


def _base_branch_fingerprints(
    sources: Mapping[str, str],
    *,
    branch_paths: Sequence[str],
    markers: Sequence[str],
) -> dict[str, str]:
    fingerprints: dict[str, str] = {}
    for path in branch_paths:
        source = sources.get(path)
        if not isinstance(source, str):
            continue
        for marker in markers:
            branch_slice = _source_branch_slice(source, marker)
            if not branch_slice:
                continue
            fingerprints[_branch_key(path, marker)] = _source_fingerprint(
                branch_slice
            )
    return fingerprints


def _changed_branch_slices(
    candidate_sources: Mapping[str, str],
    base_fingerprints: Mapping[str, str],
) -> list[str]:
    changed: list[str] = []
    for key, base_fingerprint in base_fingerprints.items():
        path, separator, marker = key.partition("\n")
        if not separator:
            continue
        # Candidate sources are deltas.  An omitted path inherits the baseline
        # file and is not evidence that this branch changed.
        if path not in candidate_sources:
            continue
        candidate_source = candidate_sources[path]
        candidate_fingerprint = _source_fingerprint(
            _source_branch_slice(candidate_source, marker)
        )
        if candidate_fingerprint != base_fingerprint:
            changed.append(f"{path}#{marker}")
    return changed


def _fixture_selector_fingerprints(
    sources: Mapping[str, str],
    *,
    branch_paths: Sequence[str] | None = None,
    markers: Sequence[str] = (),
) -> dict[str, str]:
    fingerprints: dict[str, str] = {}
    selected_paths = set(branch_paths) if branch_paths is not None else None
    for path, source in sorted(sources.items()):
        if PurePosixPath(path).suffix.lower() != ".py":
            continue
        if selected_paths is not None and path not in selected_paths:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        relevant_names = (
            _relevant_python_dependency_functions(tree, source, markers)
            if markers
            else None
        )
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _fixture_selector_function_name(node.name):
                continue
            if relevant_names is not None and node.name not in relevant_names:
                continue
            segment = ast.get_source_segment(source, node) or ""
            fingerprints[_branch_key(path, node.name)] = _source_fingerprint(
                segment
            )
    return fingerprints


def _relevant_python_dependency_functions(
    tree: ast.Module,
    source: str,
    markers: Sequence[str],
) -> set[str]:
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    top_assignments: dict[str, list[ast.AST]] = {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            for name in _assigned_names(node):
                top_assignments.setdefault(name, []).append(node)

    parent_by_node: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parent_by_node[child] = parent

    seed_nodes: list[ast.stmt] = []
    enclosing_functions: set[str] = set()
    for marker in markers:
        candidates: list[tuple[int, ast.stmt]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.stmt) or not hasattr(node, "end_lineno"):
                continue
            segment = ast.get_source_segment(source, node) or ""
            if marker not in segment:
                continue
            span = int(getattr(node, "end_lineno", node.lineno)) - node.lineno
            candidates.append((span, node))
        if not candidates:
            continue
        _, seed = min(candidates, key=lambda item: item[0])
        seed_nodes.append(seed)
        parent: ast.AST | None = seed
        while parent is not None:
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                enclosing_functions.add(parent.name)
                break
            parent = parent_by_node.get(parent)

    local_assignments: dict[str, list[ast.AST]] = {}
    for function_name in enclosing_functions:
        function = functions.get(function_name)
        if function is None:
            continue
        for node in ast.walk(function):
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                for name in _assigned_names(node):
                    local_assignments.setdefault(name, []).append(node)

    global_mutators: dict[str, list[ast.AST]] = {}
    for function in functions.values():
        declared_globals = {
            name
            for node in ast.walk(function)
            if isinstance(node, ast.Global)
            for name in node.names
        }
        assigned = {
            name
            for node in ast.walk(function)
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign))
            for name in _assigned_names(node)
        }
        for name in declared_globals & assigned:
            global_mutators.setdefault(name, []).append(function)

    pending = list(_loaded_names(seed_nodes))
    visited_names: set[str] = set()
    relevant_functions: set[str] = set()
    while pending and len(visited_names) < 512:
        name = pending.pop()
        if name in visited_names:
            continue
        visited_names.add(name)
        dependencies: list[ast.AST] = []
        function = functions.get(name)
        if function is not None:
            relevant_functions.add(name)
            dependencies.append(function)
        dependencies.extend(top_assignments.get(name, ()))
        dependencies.extend(local_assignments.get(name, ()))
        for mutator in global_mutators.get(name, ()):
            if isinstance(mutator, (ast.FunctionDef, ast.AsyncFunctionDef)):
                relevant_functions.add(mutator.name)
            dependencies.append(mutator)
        pending.extend(_loaded_names(dependencies))
    return relevant_functions


def _assigned_names(node: ast.AST) -> set[str]:
    targets: list[ast.AST] = []
    if isinstance(node, ast.Assign):
        targets.extend(node.targets)
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        targets.append(node.target)
    return {
        value.id
        for target in targets
        for value in ast.walk(target)
        if isinstance(value, ast.Name)
    }


def _loaded_names(nodes: Sequence[ast.AST]) -> set[str]:
    return {
        value.id
        for node in nodes
        for value in ast.walk(node)
        if isinstance(value, ast.Name) and isinstance(value.ctx, ast.Load)
    }


def _changed_fixture_selector_slices(
    candidate_sources: Mapping[str, str],
    base_fingerprints: Mapping[str, str],
) -> list[str]:
    candidate_fingerprints = _fixture_selector_fingerprints(candidate_sources)
    changed: list[str] = []
    for key, base_fingerprint in base_fingerprints.items():
        path, separator, function_name = key.partition("\n")
        if not separator:
            continue
        # A selector in an omitted file is still the baseline selector.  Only
        # compare fingerprints for files explicitly supplied by the candidate;
        # a deleted file is handled by the focused-branch deletion check above.
        if path not in candidate_sources:
            continue
        if candidate_fingerprints.get(key) != base_fingerprint:
            changed.append(f"{path}#{function_name}")
    return changed


def _source_branch_slice(source: str, marker: str) -> str:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        tree = None
    if tree is not None:
        candidates: list[tuple[int, ast.stmt, str]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.stmt) or not hasattr(node, "end_lineno"):
                continue
            segment = ast.get_source_segment(source, node) or ""
            if marker not in segment:
                continue
            line_span = int(getattr(node, "end_lineno", node.lineno)) - node.lineno
            candidates.append((line_span, node, segment))
        if candidates:
            _, node, branch = min(candidates, key=lambda item: item[0])
            called_names = {
                call.func.id
                for call in ast.walk(node)
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
            }
            dependencies = [
                block
                for name in sorted(called_names)
                if (block := _top_level_python_definition(source, name))
            ]
            return "\n".join((branch, *dependencies))

    lines = source.splitlines()
    marker_indexes = [
        index for index, line in enumerate(lines) if marker in line
    ]
    if not marker_indexes:
        return ""
    selected_lines: list[str] = []
    for index in marker_indexes[:8]:
        selected_lines.extend(lines[max(0, index - 3) : min(len(lines), index + 6)])
    window = "\n".join(selected_lines)
    called_names = {
        match.group(1)
        for match in re.finditer(r"\b([A-Za-z_]\w*)\s*\(", window)
        if match.group(1) not in {"if", "for", "return", "while"}
    }
    dependencies = [
        block
        for name in sorted(called_names)
        if (block := _top_level_python_definition(source, name))
    ]
    return "\n".join((window, *dependencies))


def _top_level_python_definition(source: str, name: str) -> str:
    pattern = re.compile(
        rf"(?m)^(?:async\s+def|def)\s+{re.escape(name)}\s*\("
    )
    match = pattern.search(source)
    if match is None:
        return ""
    next_definition = re.search(
        r"(?m)^(?:async\s+def|def|class)\s+[A-Za-z_]\w*",
        source[match.end() :],
    )
    end = (
        match.end() + next_definition.start()
        if next_definition is not None
        else len(source)
    )
    return source[match.start() : end].rstrip()


def _branch_key(path: str, marker: str) -> str:
    return f"{path}\n{marker}"


def _replay_implementation_paths(
    sources: Mapping[str, str],
) -> tuple[str | None, tuple[str, ...]]:
    for path, content in sources.items():
        if PurePosixPath(path).suffix.lower() != ".json":
            continue
        try:
            manifest = json.loads(content)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if (
            not isinstance(manifest, Mapping)
            or manifest.get("schema_version") != REPLAY_CAPABILITY_SCHEMA_VERSION
        ):
            continue
        raw_runtime_files = manifest.get("runtime_files")
        runtime_files = tuple(
            normalized
            for value in raw_runtime_files
            if (normalized := _bounded_relative_path(value)) is not None
        ) if isinstance(raw_runtime_files, list) else ()
        return path, tuple(dict.fromkeys(runtime_files))

    fallback = tuple(
        path
        for path in sources
        if PurePosixPath(path).suffix.lower() in _SOURCE_SUFFIXES
    )
    return None, fallback


def _replay_compiler_path(
    sources: Mapping[str, str],
    *,
    manifest_path: str | None,
) -> str | None:
    if manifest_path is None:
        return None
    content = sources.get(manifest_path)
    if not isinstance(content, str):
        return None
    try:
        manifest = json.loads(content)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(manifest, Mapping):
        return None
    return _bounded_relative_path(manifest.get("entrypoint"))


def _compiler_runtime_selector_alignment_failure(
    *,
    changed_file_paths: Sequence[str],
    changed_branch_slices: Sequence[str],
    changed_selector_slices: Sequence[str],
    contract: RepairConformanceContract,
) -> RepairConformanceResult | None:
    """Require both sides of a proven compiler/runtime selector drift to move."""

    if (
        "align_compiler_runtime_recorded_response_selection"
        not in contract.failure_codes
        or not contract.required_branch_paths
    ):
        return None
    changed_paths = set(changed_file_paths)
    for item in (*changed_branch_slices, *changed_selector_slices):
        path, _, _ = item.partition("#")
        if path:
            changed_paths.add(path)
    compiler_path = contract.required_branch_paths[0]
    runtime_paths = contract.required_branch_paths[1:]
    compiler_changed = compiler_path in changed_paths
    runtime_changed = (
        any(path in changed_paths for path in runtime_paths)
        if runtime_paths
        else compiler_changed
    )
    if compiler_changed and runtime_changed:
        return None
    return RepairConformanceResult(
        passed=False,
        code="compiler_runtime_selector_drift_unresolved",
        reason=(
            "recorded-response selector drift requires a coordinated compiler "
            "and runtime source change"
        ),
        details={
            "focus_candidate_id": contract.focus_candidate_id,
            "compiler_path": compiler_path,
            "runtime_paths": list(runtime_paths),
            "changed_paths": sorted(changed_paths),
            "required_change": (
                "derive the declared probe assertion and runtime response from "
                "one canonical recorded-response selection algorithm"
            ),
        },
    )


def _request_covers_operation(request_text: str, operation: str) -> bool:
    try:
        payload = json.loads(request_text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return operation in request_text
    pending: list[Any] = [payload]
    operation_keys = {"action", "command", "method", "operation", "path", "route"}
    while pending:
        current = pending.pop()
        if isinstance(current, Mapping):
            for key, value in current.items():
                if str(key).lower() in operation_keys and value == operation:
                    return True
                if isinstance(value, (Mapping, list, tuple)):
                    pending.append(value)
        elif isinstance(current, (list, tuple)):
            pending.extend(current)
    return False


def _placeholder_probe_value(value: str | None) -> bool:
    if not isinstance(value, str):
        return True
    normalized = value.strip().casefold()
    return normalized in {
        "",
        "[]",
        "{}",
        "null",
        "none",
        "placeholder",
        "replay_placeholder",
    }


def _fixture_value_matches(expected: str, recorded: str) -> bool:
    """Match a probe assertion to a recorded leaf or decoded container.

    Substring matching is unsafe here: a mapping key such as ``result`` would
    match every encoded response object.  Scalar assertions therefore require
    exact text equality, while JSON object/array assertions may differ only in
    serialization whitespace and must compare as decoded values.
    """

    if not isinstance(expected, str) or not isinstance(recorded, str):
        return False
    if expected == recorded:
        return True
    expected_text = expected.strip()
    recorded_text = recorded.strip()
    if not expected_text or not recorded_text:
        return False
    if expected_text[:1] not in "[{" or recorded_text[:1] not in "[{":
        return False
    try:
        return json.loads(expected_text) == json.loads(recorded_text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False


def _source_fingerprint(content: str) -> str:
    return "sha256:" + hashlib.sha256(
        _canonical_source(content).encode("utf-8")
    ).hexdigest()


def _canonical_source(content: str) -> str:
    return content.replace("\r\n", "\n").replace("\r", "\n").rstrip()


def _bounded_relative_path(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 400:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        return None
    normalized = path.as_posix()
    return sanitize_path_ref(normalized) if normalized not in {"", "."} else None


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str) and item)


def _passed(code: str, reason: str, **details: object) -> RepairConformanceResult:
    return RepairConformanceResult(
        passed=True,
        code=code,
        reason=reason,
        details=details,
    )
