from __future__ import annotations

import ast
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
    REPLAY_CAPABILITY_SUPPORTED_PROTOCOL_PROBE_KINDS,
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
                        "relative_to": "skill_root",
                        "example": "replay/compiler.py",
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
                "result_delivery": {
                    "path": "<output-directory>/result.json",
                    "stdout_is_result": False,
                },
                "request_fields": [
                    "schema_version",
                    "requirements",
                    "context_snapshots",
                    "task_inputs",
                    "evidence_derivations",
                    "capability_root",
                    "capability_package_fingerprint",
                    "context_fingerprint",
                    "request_fingerprint",
                ],
                "evidence_derivations": {
                    "type": "object",
                    "keys": "request evidence_ref",
                    "values": (
                        "ranked array of read-only source objects with path, sha256, "
                        "byte_length, preview, and matching_identifiers"
                    ),
                    "copy_mode": "byte_for_byte_only",
                    "shape": {
                        "<evidence_ref>": [
                            {
                                "path": "absolute read-only source file",
                                "sha256": "sha256:<hex>",
                                "byte_length": "positive integer",
                                "preview": "selection-only bounded text",
                                "matching_identifiers": [
                                    "requirement identifier"
                                ],
                            }
                        ]
                    },
                    "selection": (
                        "select an entry for an evidence_ref assigned to the handled "
                        "requirement, read its path as bytes, and write those bytes "
                        "unchanged to exactly one declared fixture path; the path in "
                        "fixtures, fixture_evidence_refs, and service response_fixture "
                        "must exactly match the relative path written below output"
                    ),
                    "preview_usage": "selection metadata only; never fixture content",
                    "forbidden": [
                        "synthesizing fixture payloads",
                        "wrapping or concatenating source bytes",
                        "using previews as fixture bytes",
                    ],
                },
                "reference_algorithm": (
                    "load request JSON; for each requirement, iterate "
                    "requirement['evidence_refs']; obtain sources with "
                    "request['evidence_derivations'].get(evidence_ref, []); "
                    "if no source exists classify only that requirement as unhandled; "
                    "otherwise select one source, copy source['path'] bytes unchanged "
                    "to one declared fixture, then add the requirement_id to "
                    "handled_requirements, add only its refs to evidence_refs, map the "
                    "fixture to the selected evidence_ref in fixture_evidence_refs, "
                    "create one service for the fixture, and map the original "
                    "requirement['identifier'] to that service_id in "
                    "endpoint_replacements; after all requirements, write "
                    "<output-directory>/result.json. Never add evidence_refs for an "
                    "unhandled requirement."
                ),
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
                            "runtime_entrypoint": (
                                "required for skill_runtime; use an exact declared "
                                "manifest runtime_files Python path such as "
                                "replay/runtime.py; dotted replay.runtime or "
                                "replay.runtime:main is normalized to that frozen file"
                            ),
                            "protocol_probes": {
                                "required_for": "skill_runtime",
                                "type": "non-empty array",
                                "items": {
                                    "kind": {
                                        "enum": list(
                                            REPLAY_CAPABILITY_SUPPORTED_PROTOCOL_PROBE_KINDS
                                        )
                                    },
                                    "path": (
                                        "absolute path beginning with /; used by http"
                                    ),
                                    "timeout_seconds": "number in (0, 30]",
                                    "validate_advertised_websockets": (
                                        "optional boolean for http; recursively validate "
                                        "declared ws:// URLs"
                                    ),
                                    "request_text": (
                                        "required bounded UTF-8 request for tcp or websocket"
                                    ),
                                    "response_contains": (
                                        "required literal substring selected from the declared "
                                        "response_fixture for tcp or websocket; include it "
                                        "inside the protocol-valid response envelope. An HTTP "
                                        "discovery probe with validate_advertised_websockets may "
                                        "instead assert a structural protocol field; its paired "
                                        "websocket data-plane probe remains fixture-derived. "
                                        "Required on at least one probe per service"
                                    ),
                                },
                            },
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
            "runtime_service": {
                "purpose": (
                    "skill-owned deterministic protocol behavior when a static "
                    "byte response cannot satisfy a stateful requirement"
                ),
                "transport": "skill_runtime",
                "entrypoint_source": "manifest runtime_files",
                "invocation": [
                    "Python",
                    "-I",
                    "<frozen-runtime-entrypoint>",
                    "--port",
                    "<allocated-loopback-port>",
                    "--fixture",
                    "<frozen-read-only-fixture>",
                    "--scratch",
                    "<private-writable-directory>",
                ],
                "constraints": [
                    "bind IPv4 127.0.0.1 on exactly the supplied port",
                    "create exactly one listening socket per runtime process and "
                    "multiplex all declared HTTP, TCP, and WebSocket interactions "
                    "on that listener; never start multiple servers on the supplied port",
                    "implement every interaction required by each handled stateful "
                    "requirement; a readiness-only or discovery-only response is insufficient",
                    "if HTTP readiness JSON advertises ws:// URLs, serve their valid "
                    "WebSocket upgrade on the same allocated listener",
                    "declare protocol_probes for every externally advertised protocol "
                    "entry point; HTTP JSON discovery probes must enable advertised "
                    "WebSocket validation",
                    "include at least one data-plane probe with expected response content; "
                    "a health-only probe is invalid",
                    "for every runtime_required requirement use skill_runtime, not a static "
                    "fixture transport",
                    "treat fixture bytes as an arbitrary JSON root (object, array, scalar, "
                    "or null) or non-JSON bytes; normalize the decoded value before "
                    "mapping-only operations such as .get instead of assuming an object root",
                    "derive response_contains at compile time as a bounded non-empty literal "
                    "substring of the selected fixture bytes and include that exact substring "
                    "inside a protocol-valid representative runtime response; when the "
                    "response uses serialization, select an encoding-stable fixture token "
                    "matching a conservative form such as [A-Za-z0-9_]{8,32}, use the same "
                    "extraction function in compiler and runtime, and place the exact token in "
                    "a dedicated response field so its bytes remain unchanged; never replace a "
                    "required protocol envelope with raw fixture text",
                    "for each stateful WebSocket entry point, send a representative bounded "
                    "request_text and require fixture-derived response_contains content inside "
                    "the protocol-valid response to that request",
                    "route Upgrade: websocket through the handler's actual GET dispatch "
                    "and continue processing required frames; an unregistered helper or "
                    "handshake-only stub is insufficient",
                    "keep an upgraded WebSocket connection open for multiple frames, "
                    "answer each client ping with a matching pong, then process the "
                    "declared data request without closing after a one-shot unsolicited frame",
                    "preserve opaque request correlation and routing metadata on synchronous "
                    "responses and on follow-up completion events for multiplexed sessions "
                    "or channels; matching only the numeric request id is insufficient when "
                    "the client supplied an additional routing envelope",
                    "write a bounded protocol_trace.jsonl under the supplied scratch "
                    "directory with one JSON object per line for each received request and "
                    "emitted response or event; record direction, sequence, message kind, "
                    "top-level field names, and opaque correlation or routing fields, but "
                    "omit or redact payload bodies and credentials",
                    "if a protocol handler raises, emit a bounded sanitized terminal trace "
                    "or stderr diagnostic before closing the connection; do not silently "
                    "swallow the exception and leave the client with only an incomplete frame",
                    "derive responses only from the supplied recorded fixture; use bounded "
                    "trace context only to infer reusable protocol behavior",
                    "do not hard-code task identifiers, case identifiers, original endpoint "
                    "values, or environment-specific paths",
                    "write only below the supplied scratch directory",
                    "do not make outbound network connections",
                    "be ready according to the declared readiness probe",
                ],
                "validation": {
                    "advertised_websocket_urls": (
                        "all ws:// URLs recursively exposed by HTTP readiness JSON "
                        "must stay on the allocated endpoint and complete an RFC 6455 "
                        "handshake plus ping/pong control-frame exchange"
                    ),
                    "data_plane": (
                        "candidate-declared HTTP, TCP, or WebSocket request/response probes "
                        "must return the expected bounded content before rollout starts"
                    ),
                },
                "selection": (
                    "use a fixture transport for a static response; use "
                    "skill_runtime when the requirement needs a multi-step or "
                    "stateful protocol implemented by candidate-owned runtime code"
                ),
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
                "unused_fixture_policy": (
                    "allowed only when declared, bounded, and evidence-backed; frozen "
                    "skill runtime may read it from the read-only fixture root"
                ),
                "handled_network_requirement_requires_endpoint_replacement": True,
                "single_service_endpoint_replacement_inference": True,
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
            for source_path in (capability.entrypoint, *capability.runtime_files):
                try:
                    ast.parse(
                        source_path.read_text(encoding="utf-8"),
                        filename=source_path.name,
                    )
                except SyntaxError as exc:
                    relative_path = source_path.relative_to(
                        capability.skill_root
                    ).as_posix()
                    field_path = (
                        f"{relative_path}:{exc.lineno}"
                        if exc.lineno is not None
                        else relative_path
                    )
                    return CapabilityValidationResult(
                        capability_type=self.capability_type,
                        passed=False,
                        diagnostics=(
                            CandidateValidationDiagnostic(
                                code="invalid_replay_capability_python",
                                stage="capability_source",
                                failure_class="candidate",
                                repairable=True,
                                field_path=field_path,
                                reason=exc.msg,
                            ),
                        ),
                    )
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
