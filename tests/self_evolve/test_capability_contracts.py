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
    }
