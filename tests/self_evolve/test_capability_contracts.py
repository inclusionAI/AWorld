from __future__ import annotations

import json
from pathlib import Path

from aworld.self_evolve.capability_contracts import (
    ReplayCapabilityContractProvider,
    capability_contract_factory,
    discover_applicable_capability_contracts,
    validate_applicable_capabilities,
)
from aworld.self_evolve.replay_adaptation import ReplayCapabilityRequirement
from aworld.self_evolve.replay_capability import (
    REPLAY_CAPABILITY_PROTOCOL_VERSION,
    REPLAY_CAPABILITY_SCHEMA_VERSION,
)
from aworld.self_evolve.types import CandidateVariant, SelfEvolveTargetRef


def _requirement(kind: str = "stateful_tool") -> ReplayCapabilityRequirement:
    return ReplayCapabilityRequirement(
        requirement_id="requirement-1",
        kind=kind,
        identifier="tool:recorded-state",
        case_ids=("case-1",),
        evidence_refs=("event:1",),
        status="unbound",
        detail="requires a deterministic skill-owned binding",
    )


def test_replay_authoring_contract_is_derived_from_public_protocol_constants() -> None:
    provider = ReplayCapabilityContractProvider()

    contract = provider.authoring_contract((_requirement(),))

    assert contract["capability_type"] == "replay"
    assert contract["manifest"]["path"] == "replay/capability.json"
    assert contract["manifest"]["schema_version"] == REPLAY_CAPABILITY_SCHEMA_VERSION
    assert contract["compiler"]["protocol_version"] == (
        REPLAY_CAPABILITY_PROTOCOL_VERSION
    )
    assert contract["compiler"]["arguments"] == [
        "--request",
        "<request-json>",
        "--output",
        "<output-directory>",
    ]
    assert contract["manifest"]["field_constraints"]["entrypoint"] == {
        "type": "relative_file_path",
        "suffix": ".py",
        "must_exist": True,
        "command_prefix_allowed": False,
        "relative_to": "skill_root",
        "example": "replay/compiler.py",
    }
    assert contract["manifest"]["field_constraints"]["runtime_files"][
        "items"
    ]["file_only"] is True
    assert contract["manifest"]["field_constraints"]["concurrency_mode"][
        "enum"
    ] == ["exclusive", "isolated", "shared_read_only"]
    result_shape = contract["compiler"]["result_shape"]
    assert contract["compiler"]["result_delivery"] == {
        "path": "<output-directory>/result.json",
        "stdout_is_result": False,
    }
    assert contract["compiler"]["evidence_derivations"]["copy_mode"] == (
        "byte_for_byte_only"
    )
    assert contract["compiler"]["evidence_derivations"]["shape"] == {
        "<evidence_ref>": [
            {
                "path": "absolute read-only source file",
                "sha256": "sha256:<hex>",
                "byte_length": "positive integer",
                "preview": "selection-only bounded text",
                "matching_identifiers": ["requirement identifier"],
            }
        ]
    }
    algorithm = contract["compiler"]["reference_algorithm"]
    assert "request['evidence_derivations'].get(evidence_ref, [])" in algorithm
    assert "copy source['path'] bytes unchanged" in algorithm
    assert "write <output-directory>/result.json" in algorithm
    assert contract["compiler"]["evidence_derivations"]["forbidden"] == [
        "synthesizing fixture payloads",
        "wrapping or concatenating source bytes",
        "using previews as fixture bytes",
    ]
    assert result_shape["handled_requirements"]["items"] == "requirement_id"
    assert result_shape["evidence_refs"]["type"] == "object"
    assert result_shape["fixture_evidence_refs"]["keys"] == "fixture_path"
    assert result_shape["endpoint_replacements"]["values"] == "service_id"
    assert result_shape["services"]["items"]["transport"]["enum"] == [
        "http_fixture",
        "skill_runtime",
        "tcp_fixture",
    ]
    assert result_shape["services"]["items"]["runtime_entrypoint"] == (
        "required for skill_runtime; use an exact declared manifest runtime_files "
        "Python path such as replay/runtime.py; dotted replay.runtime or "
        "replay.runtime:main is normalized to that frozen file"
    )
    assert result_shape["services"]["items"]["protocol_probes"] == {
        "required_for": "skill_runtime",
        "type": "non-empty array",
        "max_items": 16,
        "items": {
            "kind": {"enum": ["http", "tcp", "websocket"]},
            "path": "absolute path beginning with /; used by http",
            "timeout_seconds": "number in (0, 30]",
            "validate_advertised_websockets": (
                "optional boolean for http; recursively validate declared ws:// URLs"
            ),
            "request_text": (
                "required bounded UTF-8 request for tcp or websocket"
            ),
            "response_contains": (
                "required recorded value or semantically decoded JSON/JSONL container "
                "selected from the declared response_fixture for tcp or websocket; "
                "include it inside the protocol-valid response envelope. JSON "
                "reserialization may differ in whitespace or escaping but must decode "
                "to a fixture descendant. An HTTP discovery probe with "
                "validate_advertised_websockets may instead assert a structural "
                "protocol field; its paired websocket data-plane probe remains "
                "fixture-derived. Required on at least one probe per service"
            ),
        },
    }
    assert contract["runtime_service"]["invocation"] == [
        "Python",
        "-I",
        "<frozen-runtime-entrypoint>",
        "--port",
        "<allocated-loopback-port>",
        "--fixture",
        "<frozen-read-only-fixture>",
        "--scratch",
        "<private-writable-directory>",
    ]
    assert contract["runtime_service"]["constraints"] == [
        "bind IPv4 127.0.0.1 on exactly the supplied port",
        "create exactly one listening socket per runtime process and multiplex all declared HTTP, TCP, and WebSocket interactions on that listener; never start multiple servers on the supplied port",
        "implement every interaction required by each handled stateful requirement; a readiness-only or discovery-only response is insufficient",
        "if HTTP readiness JSON advertises ws:// URLs, serve their valid WebSocket upgrade on the same allocated listener",
        "declare protocol_probes for every externally advertised protocol entry point; HTTP JSON discovery probes must enable advertised WebSocket validation",
        "include at least one data-plane probe with expected response content; a health-only probe is invalid",
        "for every runtime_required requirement use skill_runtime, not a static fixture transport",
        "treat fixture bytes as an arbitrary JSON root (object, array, scalar, or null) or non-JSON bytes; normalize the decoded value before mapping-only operations such as .get instead of assuming an object root",
        "for observed task-plane operations, recursively traverse nested fixture objects and arrays, preserve protocol-required scalar types, and select non-empty recorded descendants for response fields instead of returning placeholders, empty arrays, or empty schemas",
        "derive response_contains at compile time from a deterministic non-empty recorded response value without regex, length, digest, or placeholder filtering; for trajectory envelopes discover action_result or tool_outputs gateways before traversing content, response, result, output, body, or data payloads, recursively decode nested JSON, and use the same bounded selector in compiler and runtime; include the selected value and its surrounding recorded container inside the protocol-valid response so multiple recorded values remain available; never fall back to request or metadata branches after a response gateway is found and never replace a required protocol envelope with raw fixture text",
        "for each stateful WebSocket entry point, send at least one representative bounded request_text and require fixture-derived response_contains content inside the protocol-valid response to that request; implement other observed operations without declaring redundant assertion probes unless each exact response truthfully contains its declared expectation",
        "route Upgrade: websocket through the handler's actual GET dispatch and continue processing required frames; an unregistered helper or handshake-only stub is insufficient",
        "keep an upgraded WebSocket connection open for multiple frames, answer each client ping with a matching pong, then process the declared data request without closing after a one-shot unsolicited frame",
        "preserve opaque request correlation and routing metadata on synchronous responses and on follow-up completion events for multiplexed sessions or channels; matching only the numeric request id is insufficient when the client supplied an additional routing envelope",
        "write a bounded protocol_trace.jsonl under the supplied scratch directory with one JSON object per line for each received request and emitted response or event; record direction, sequence, message kind, top-level field names, and opaque correlation or routing fields, but omit or redact payload bodies and credentials",
        "if a protocol handler raises, emit a bounded sanitized terminal trace or stderr diagnostic before closing the connection; do not silently swallow the exception and leave the client with only an incomplete frame",
        "derive responses only from the supplied recorded fixture; use bounded trace context only to infer reusable protocol behavior",
        "do not hard-code task identifiers, case identifiers, original endpoint values, or environment-specific paths",
        "write only below the supplied scratch directory",
        "do not make outbound network connections",
        "be ready according to the declared readiness probe",
    ]
    assert contract["runtime_service"]["validation"][
        "advertised_websocket_urls"
    ] == (
        "all ws:// URLs recursively exposed by HTTP readiness JSON must stay on "
        "the allocated endpoint and complete an RFC 6455 handshake plus ping/pong "
        "control-frame exchange"
    )
    assert contract["runtime_service"]["validation"]["data_plane"] == (
        "candidate-declared HTTP, TCP, or WebSocket request/response probes "
        "must return the expected bounded content before rollout starts"
    )
    assert contract["validation"]["fixture_bytes"] == (
        "byte-for-byte recorded evidence derivation"
    )
    lowered = json.dumps(contract, ensure_ascii=False, sort_keys=True).lower()
    assert "browser" not in lowered
    assert "cdp" not in lowered


def test_replay_provider_applies_only_to_supported_generic_requirements() -> None:
    provider = ReplayCapabilityContractProvider()

    assert provider.applies_to((_requirement("local_file"),)) is True
    assert provider.applies_to((_requirement("unsupported-domain-kind"),)) is False


def test_applicable_contract_discovery_uses_aworld_factory_registration() -> None:
    registration_name = "test-contract-provider"

    @capability_contract_factory.register(registration_name)
    class _TestContractProvider:
        capability_type = "test"

        def applies_to(self, requirements):
            return bool(requirements)

        def authoring_contract(self, requirements):
            return {
                "capability_type": self.capability_type,
                "requirement_count": len(requirements),
            }

        def validate_candidate(self, candidate):
            raise AssertionError("generation-time discovery must not validate candidates")

    try:
        contracts = discover_applicable_capability_contracts((_requirement(),))
    finally:
        capability_contract_factory.unregister(registration_name)

    assert capability_contract_factory.get_class("replay") is (
        ReplayCapabilityContractProvider
    )
    assert [item["capability_type"] for item in contracts] == ["replay", "test"]
    assert contracts[1]["requirement_count"] == 1


def _candidate() -> CandidateVariant:
    return CandidateVariant(
        candidate_id="candidate-1",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        content="# Demo\n",
        rationale="publish a replay capability",
        target_fingerprint="sha256:current",
    )


def _write_replay_package(root: Path, *, schema_version: str) -> None:
    replay_root = root / "replay"
    replay_root.mkdir(parents=True)
    (replay_root / "compiler.py").write_text("print('compiler')\n", encoding="utf-8")
    (replay_root / "capability.json").write_text(
        json.dumps(
            {
                "schema_version": schema_version,
                "capability_id": "recorded-state",
                "protocol": REPLAY_CAPABILITY_PROTOCOL_VERSION,
                "entrypoint": "replay/compiler.py",
                "handles": ["stateful_tool"],
            }
        ),
        encoding="utf-8",
    )


def test_replay_provider_validates_the_materialized_candidate_package(
    tmp_path: Path,
) -> None:
    _write_replay_package(tmp_path, schema_version=REPLAY_CAPABILITY_SCHEMA_VERSION)

    results = validate_applicable_capabilities(
        requirements=(_requirement(),),
        candidate=_candidate(),
        skill_root=tmp_path,
    )

    assert len(results) == 1
    assert results[0].capability_type == "replay"
    assert results[0].passed is True


def test_replay_provider_rejects_candidate_python_syntax_before_runtime(
    tmp_path: Path,
) -> None:
    _write_replay_package(tmp_path, schema_version=REPLAY_CAPABILITY_SCHEMA_VERSION)
    (tmp_path / "replay" / "compiler.py").write_text(
        "cclass Compiler:\n    pass\n",
        encoding="utf-8",
    )

    results = validate_applicable_capabilities(
        requirements=(_requirement(),),
        candidate=_candidate(),
        skill_root=tmp_path,
    )

    assert results[0].passed is False
    assert results[0].diagnostics[0].to_dict() == {
        "code": "invalid_replay_capability_python",
        "stage": "capability_source",
        "failure_class": "candidate",
        "repairable": True,
        "field_path": "replay/compiler.py:1",
        "reason": "invalid syntax",
    }


def test_replay_provider_returns_typed_candidate_diagnostic_for_invalid_manifest(
    tmp_path: Path,
) -> None:
    _write_replay_package(tmp_path, schema_version="unsupported")

    results = validate_applicable_capabilities(
        requirements=(_requirement(),),
        candidate=_candidate(),
        skill_root=tmp_path,
    )

    assert results[0].passed is False
    assert results[0].diagnostics[0].to_dict() == {
        "code": "invalid_replay_capability_manifest",
        "stage": "capability_manifest",
        "failure_class": "candidate",
        "repairable": True,
        "field_path": "replay/capability.json",
        "reason": "unsupported replay capability schema: unsupported",
    }
