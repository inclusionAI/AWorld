from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol, Sequence

from aworld.core.factory import Factory
from aworld.self_evolve.replay_adaptation import (
    REPLAY_BINDING_CONCURRENCY_MODES,
    ReplayCapabilityRequirement,
)
from aworld.self_evolve.replay_capability import (
    REPLAY_CAPABILITY_MANIFEST_PATH,
    REPLAY_CAPABILITY_PROTOCOL_VERSION,
    REPLAY_CAPABILITY_REQUEST_SCHEMA_VERSION,
    REPLAY_CAPABILITY_RESULT_SCHEMA_VERSION,
    REPLAY_CAPABILITY_SCHEMA_VERSION,
    REPLAY_CAPABILITY_SUPPORTED_REQUIREMENT_KINDS,
    REPLAY_CAPABILITY_SUPPORTED_READINESS_KINDS,
    REPLAY_CAPABILITY_SUPPORTED_SERVICE_TRANSPORTS,
    ReplayCapabilityError,
    discover_replay_capability,
)
from aworld.self_evolve.sanitization import sanitize_text
from aworld.self_evolve.types import CandidateVariant


FailureClass = Literal["candidate", "infrastructure"]


@dataclass(frozen=True)
class CandidateValidationDiagnostic:
    code: str
    stage: str
    failure_class: FailureClass
    repairable: bool
    field_path: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "code": self.code,
            "stage": self.stage,
            "failure_class": self.failure_class,
            "repairable": self.repairable,
        }
        if self.field_path is not None:
            result["field_path"] = self.field_path
        if self.reason is not None:
            result["reason"] = sanitize_text(self.reason, max_chars=240)
        return result


@dataclass(frozen=True)
class CapabilityValidationResult:
    capability_type: str
    passed: bool
    diagnostics: tuple[CandidateValidationDiagnostic, ...] = ()


class CandidateCapabilityContractProvider(Protocol):
    capability_type: str

    def applies_to(self, requirements: Sequence[object]) -> bool:
        """Return whether this provider can author the supplied requirements."""

    def authoring_contract(
        self,
        requirements: Sequence[object],
    ) -> Mapping[str, object]:
        """Return a bounded generation contract, never an implementation."""

    def validate_candidate(
        self,
        candidate: CandidateVariant,
        *,
        skill_root: str | Path | None = None,
    ) -> CapabilityValidationResult:
        """Validate candidate-owned capability files without importing them."""


capability_contract_factory: Factory[type[CandidateCapabilityContractProvider]] = (
    Factory("self-evolve capability contract provider")
)


@capability_contract_factory.register("replay")
class ReplayCapabilityContractProvider:
    capability_type = "replay"

    def applies_to(self, requirements: Sequence[object]) -> bool:
        supported = set(REPLAY_CAPABILITY_SUPPORTED_REQUIREMENT_KINDS)
        return any(
            isinstance(item, ReplayCapabilityRequirement) and item.kind in supported
            for item in requirements
        )

    def authoring_contract(
        self,
        requirements: Sequence[object],
    ) -> Mapping[str, object]:
        required_kinds = sorted(
            {
                item.kind
                for item in requirements
                if isinstance(item, ReplayCapabilityRequirement)
                and item.kind in REPLAY_CAPABILITY_SUPPORTED_REQUIREMENT_KINDS
            }
        )
        return {
            "capability_type": self.capability_type,
            "required_kinds": required_kinds,
            "manifest": {
                "path": REPLAY_CAPABILITY_MANIFEST_PATH,
                "schema_version": REPLAY_CAPABILITY_SCHEMA_VERSION,
                "required_fields": [
                    "schema_version",
                    "capability_id",
                    "protocol",
                    "entrypoint",
                    "handles",
                ],
                "optional_fields": [
                    "runtime_files",
                    "concurrency_mode",
                    "resource_key",
                    "binding_fingerprint",
                ],
                "supported_requirement_kinds": list(
                    REPLAY_CAPABILITY_SUPPORTED_REQUIREMENT_KINDS
                ),
                "field_constraints": {
                    "entrypoint": {
                        "type": "relative_file_path",
                        "suffix": ".py",
                        "must_exist": True,
                        "command_prefix_allowed": False,
                    },
                    "handles": {
                        "type": "array",
                        "items": {
                            "enum": list(
                                REPLAY_CAPABILITY_SUPPORTED_REQUIREMENT_KINDS
                            )
                        },
                        "min_items": 1,
                    },
                    "runtime_files": {
                        "type": "array",
                        "items": {
                            "type": "relative_file_path",
                            "must_exist": True,
                            "file_only": True,
                        },
                    },
                    "concurrency_mode": {
                        "enum": list(REPLAY_BINDING_CONCURRENCY_MODES),
                        "default": "exclusive",
                    },
                },
            },
            "compiler": {
                "protocol_version": REPLAY_CAPABILITY_PROTOCOL_VERSION,
                "arguments": [
                    "--request",
                    "<request-json>",
                    "--output",
                    "<output-directory>",
                ],
                "request_schema_version": (
                    REPLAY_CAPABILITY_REQUEST_SCHEMA_VERSION
                ),
                "result_schema_version": REPLAY_CAPABILITY_RESULT_SCHEMA_VERSION,
                "request_fields": [
                    "schema_version",
                    "requirements",
                    "context_snapshots",
                    "task_inputs",
                    "capability_root",
                    "capability_package_fingerprint",
                    "context_fingerprint",
                    "request_fingerprint",
                ],
                "result_fields": [
                    "schema_version",
                    "capability_id",
                    "deterministic",
                    "handled_requirements",
                    "unhandled_requirements",
                    "evidence_refs",
                    "fixture_evidence_refs",
                    "fixtures",
                    "endpoint_replacements",
                    "services",
                ],
                "result_shape": {
                    "schema_version": {
                        "const": REPLAY_CAPABILITY_RESULT_SCHEMA_VERSION,
                    },
                    "capability_id": "same value as manifest capability_id",
                    "deterministic": {"type": "boolean", "const": True},
                    "handled_requirements": {
                        "type": "array",
                        "items": "requirement_id",
                    },
                    "unhandled_requirements": {
                        "type": "array",
                        "items": "requirement_id",
                    },
                    "evidence_refs": {
                        "type": "object",
                        "keys": "handled requirement_id",
                        "values": "non-empty array of that request requirement's evidence_refs",
                    },
                    "fixture_evidence_refs": {
                        "type": "object",
                        "keys": "fixture_path",
                        "values": "non-empty array of recorded evidence_refs",
                    },
                    "fixtures": {
                        "type": "array",
                        "items": "relative output file path",
                    },
                    "endpoint_replacements": {
                        "type": "object",
                        "keys": "handled requirement identifier",
                        "values": "service_id",
                    },
                    "services": {
                        "type": "array",
                        "items": {
                            "service_id": "identifier",
                            "requirement_id": "handled requirement_id",
                            "transport": {
                                "enum": list(
                                    REPLAY_CAPABILITY_SUPPORTED_SERVICE_TRANSPORTS
                                )
                            },
                            "response_fixture": "declared fixture_path",
                            "readiness": {
                                "kind": {
                                    "enum": list(
                                        REPLAY_CAPABILITY_SUPPORTED_READINESS_KINDS
                                    )
                                },
                                "timeout_seconds": "number in (0, 30]",
                                "path": "optional absolute URL path beginning with /",
                            },
                        },
                    },
                },
            },
            "validation": {
                "compile_repetitions": 2,
                "must_be_deterministic": True,
                "must_classify_every_requirement": True,
                "must_preserve_evidence_provenance": True,
                "candidate_code_imported_by_framework": False,
                "entrypoint_invocation": (
                    "framework Python -I <entrypoint> --request <request-json> "
                    "--output <output-directory>"
                ),
                "fixture_bytes": "byte-for-byte recorded evidence derivation",
                "fixture_derivation_sources": [
                    "cited context snapshot task_input descendant",
                    "cited context snapshot steps descendant",
                    "cited context snapshot prior_turns descendant",
                    "cited request task_inputs descendant",
                ],
                "fixture_derivation_encoding": (
                    "raw UTF-8 string or compact sorted JSON UTF-8 bytes"
                ),
                "every_fixture_requires_one_service": True,
                "handled_local_endpoint_requires_endpoint_replacement": True,
            },
        }

    def validate_candidate(
        self,
        candidate: CandidateVariant,
        *,
        skill_root: str | Path | None = None,
    ) -> CapabilityValidationResult:
        if skill_root is not None:
            try:
                capability = discover_replay_capability(skill_root)
            except (ReplayCapabilityError, OSError, ValueError) as exc:
                return CapabilityValidationResult(
                    capability_type=self.capability_type,
                    passed=False,
                    diagnostics=(
                        CandidateValidationDiagnostic(
                            code="invalid_replay_capability_manifest",
                            stage="capability_manifest",
                            failure_class="candidate",
                            repairable=True,
                            field_path=REPLAY_CAPABILITY_MANIFEST_PATH,
                            reason=str(exc),
                        ),
                    ),
                )
            if capability is None:
                return self._missing_manifest_result()
            return CapabilityValidationResult(
                capability_type=self.capability_type,
                passed=True,
            )
        manifest = next(
            (
                item
                for item in candidate.files
                if item.path == REPLAY_CAPABILITY_MANIFEST_PATH
                and item.operation == "upsert"
            ),
            None,
        )
        if manifest is None:
            return self._missing_manifest_result()
        try:
            payload = json.loads(manifest.content or "")
        except json.JSONDecodeError:
            payload = None
        if not isinstance(payload, Mapping):
            return CapabilityValidationResult(
                capability_type=self.capability_type,
                passed=False,
                diagnostics=(
                    CandidateValidationDiagnostic(
                        code="invalid_capability_manifest_json",
                        stage="capability_manifest",
                        failure_class="candidate",
                        repairable=True,
                        field_path=REPLAY_CAPABILITY_MANIFEST_PATH,
                    ),
                ),
            )
        required = {
            "schema_version": REPLAY_CAPABILITY_SCHEMA_VERSION,
            "protocol": REPLAY_CAPABILITY_PROTOCOL_VERSION,
        }
        for field_path, expected in required.items():
            if payload.get(field_path) != expected:
                return CapabilityValidationResult(
                    capability_type=self.capability_type,
                    passed=False,
                    diagnostics=(
                        CandidateValidationDiagnostic(
                            code="invalid_capability_manifest_field",
                            stage="capability_manifest",
                            failure_class="candidate",
                            repairable=True,
                            field_path=f"{REPLAY_CAPABILITY_MANIFEST_PATH}:{field_path}",
                        ),
                    ),
                )
        return CapabilityValidationResult(
            capability_type=self.capability_type,
            passed=True,
        )

    def _missing_manifest_result(self) -> CapabilityValidationResult:
        return CapabilityValidationResult(
            capability_type=self.capability_type,
            passed=False,
            diagnostics=(
                CandidateValidationDiagnostic(
                    code="missing_capability_manifest",
                    stage="capability_manifest",
                    failure_class="candidate",
                    repairable=True,
                    field_path=REPLAY_CAPABILITY_MANIFEST_PATH,
                ),
            ),
        )


def discover_applicable_capability_contracts(
    requirements: Sequence[object],
) -> tuple[Mapping[str, object], ...]:
    contracts: list[Mapping[str, object]] = []
    for name in capability_contract_factory:
        provider = capability_contract_factory(name)
        if provider is not None and provider.applies_to(requirements):
            contracts.append(dict(provider.authoring_contract(requirements)))
    return tuple(contracts)


def applicable_capability_providers(
    requirements: Sequence[object],
) -> tuple[CandidateCapabilityContractProvider, ...]:
    providers: list[CandidateCapabilityContractProvider] = []
    for name in capability_contract_factory:
        provider = capability_contract_factory(name)
        if provider is not None and provider.applies_to(requirements):
            providers.append(provider)
    return tuple(providers)


def validate_applicable_capabilities(
    *,
    requirements: Sequence[object],
    candidate: CandidateVariant,
    skill_root: str | Path,
) -> tuple[CapabilityValidationResult, ...]:
    return tuple(
        provider.validate_candidate(candidate, skill_root=skill_root)
        for provider in applicable_capability_providers(requirements)
    )
