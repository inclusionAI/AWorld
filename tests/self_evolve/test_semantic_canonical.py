from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from aworld.self_evolve.constitution import (
    default_self_evolve_constitution,
)
from aworld.self_evolve.evaluation_plan import (
    EvaluationDisposition,
    ManifestOrigin,
    SemanticQualificationRegistryV1,
    default_semantic_ingestion_profile,
)
from aworld.self_evolve.evidence import (
    ClaimVerificationOrigin,
    EvidenceProducerKind,
)
from aworld.self_evolve.ingestion.chunking import build_source_bundle
from aworld.self_evolve.ingestion.agent import AgenticDatasetIngestor
from aworld.self_evolve.ingestion.scanner import scan_source
from aworld.self_evolve.ingestion.semantic_canonical import (
    CANONICAL_SEMANTIC_SOURCE_SCHEMA_VERSION,
    decode_canonical_semantic_source,
    recognize_canonical_semantic_source,
)
from aworld.self_evolve.ingestion.semantic_compiler import (
    TraceExtractionOrigin,
)
from aworld.self_evolve.ingestion.semantic_verifier import (
    SemanticQualificationEvidenceV1,
)
from aworld.self_evolve.ingestion.semantic_workflow import (
    validate_evidence_graph_against_source_bundle,
)
from aworld.self_evolve.ingestion.types import (
    DatasetIngestionRequest,
    IngestionContractError,
    IngestionManifestOrigin,
    IngestionMode,
    fingerprint_json,
)
from aworld.self_evolve.ingestion.semantic_snapshot import (
    FrozenSemanticIngestionSnapshotV2,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore


def _fingerprint(value: str) -> str:
    return fingerprint_json({"value": value})


def _empty_part(*, part_key: str = "main") -> dict:
    return {
        "schema_version": CANONICAL_SEMANTIC_SOURCE_SCHEMA_VERSION,
        "bundle_key": "recovery-benchmark",
        "part_key": part_key,
        "entities": [],
        "claims": [],
        "conflicts": [],
        "cases": [],
        "signals": [],
        "plans": [],
        "traces": [],
    }


def _canonical_payload() -> dict:
    payload = _empty_part()
    payload["entities"] = [
        {
            "entity_key": "task",
            "kind": "task",
            "canonical_name": "Recover from a failed tool call",
        },
        {
            "entity_key": "execution-a",
            "kind": "execution",
            "canonical_name": "Execution A",
        },
        {
            "entity_key": "execution-b",
            "kind": "execution",
            "canonical_name": "Execution B",
        },
        {
            "entity_key": "human-reviewer",
            "kind": "reviewer",
            "canonical_name": "Human reviewer",
        },
    ]
    payload["traces"] = [
        {
            "trace_key": "trace-a",
            "trajectory": {
                "steps": [{"id": "a", "action": {"content": "retry"}}]
            },
        },
        {
            "trace_key": "trace-b",
            "trajectory": {
                "steps": [{"id": "b", "action": {"content": "recover"}}]
            },
        },
    ]
    payload["claims"] = [
        {
            "claim_key": "input",
            "kind": "task_input",
            "subject_entity_keys": ["task"],
            "payload": {"input": "Recover from the failed tool call"},
            "citation_part_keys": ["main"],
        },
        {
            "claim_key": "trajectory-a",
            "kind": "execution_trajectory",
            "subject_entity_keys": ["execution-a"],
            "payload": {"trace_key": "trace-a"},
            "citation_part_keys": ["main"],
        },
        {
            "claim_key": "trajectory-b",
            "kind": "execution_trajectory",
            "subject_entity_keys": ["execution-b"],
            "payload": {"trace_key": "trace-b"},
            "citation_part_keys": ["main"],
        },
        {
            "claim_key": "result-a",
            "kind": "execution_result",
            "subject_entity_keys": ["execution-a"],
            "payload": {"result": {"success": False}},
            "citation_part_keys": ["main"],
        },
        {
            "claim_key": "result-b",
            "kind": "execution_result",
            "subject_entity_keys": ["execution-b"],
            "payload": {"result": {"success": True}},
            "citation_part_keys": ["main"],
        },
        {
            "claim_key": "human-ranking",
            "kind": "human_comparison",
            "subject_entity_keys": ["human-reviewer"],
            "object_entity_keys": ["execution-a", "execution-b"],
            "payload": {
                "relation": "preferred_over",
                "preferred_entity_id": "execution-b",
                "scope": "task",
            },
            "citation_part_keys": ["main"],
        },
    ]
    payload["cases"] = [
        {
            "case_key": "recovery-case",
            "task_entity_key": "task",
            "execution_entity_keys": ["execution-a", "execution-b"],
            "input_claim_keys": ["input"],
            "trajectory_claim_keys": ["trajectory-a", "trajectory-b"],
            "result_claim_keys": ["result-a", "result-b"],
            "comparison_claim_keys": ["human-ranking"],
        }
    ]
    payload["signals"] = [
        {
            "signal_key": "recovery-signal",
            "case_key": "recovery-case",
            "kind": "preference_delta",
            "compared_execution_keys": ["execution-a", "execution-b"],
            "preferred_execution_keys": ["execution-b"],
            "supporting_claim_keys": [
                "trajectory-a",
                "trajectory-b",
                "result-a",
                "result-b",
                "human-ranking",
            ],
            "behavior_delta": {
                "preferred_observations": ["B diagnosed the failure"],
                "non_preferred_observations": ["A repeated the failed call"],
                "result_difference": ["B succeeded and A failed"],
                "source_claim_keys": [
                    "trajectory-a",
                    "trajectory-b",
                    "result-a",
                    "result-b",
                ],
            },
            "metric_delta": {"task_success": 1.0},
            "desired_behavior": ["Diagnose before retrying"],
            "avoid_behavior": ["Do not repeat an identical failed call"],
            "capability_requirement": ["Recover from tool failures"],
        }
    ]
    payload["plans"] = [
        {
            "plan_key": "recovery-plan",
            "case_key": "recovery-case",
            "training_signal_keys": ["recovery-signal"],
            "replay_seed_execution_key": "execution-a",
        }
    ]
    return payload


def _bundle(path: Path):
    return build_source_bundle(path, inventory=scan_source(path))


def _qualification() -> SemanticQualificationEvidenceV1:
    constitution = default_self_evolve_constitution()
    return SemanticQualificationEvidenceV1(
        registry=SemanticQualificationRegistryV1(
            trusted_report_fingerprints=()
        ),
        report=None,
        model_profile_fingerprint=_fingerprint("model"),
        provider_fingerprint=_fingerprint("provider"),
        semantic_protocol_fingerprint=_fingerprint("protocol"),
        constitution_fingerprint=constitution.fingerprint,
        corpus_fingerprint=_fingerprint("corpus"),
        threshold_set_fingerprint=_fingerprint("thresholds"),
    )


def _decode(path: Path):
    bundle = _bundle(path)
    source_set = recognize_canonical_semantic_source(bundle)
    assert source_set is not None
    result = decode_canonical_semantic_source(
        source_set,
        bundle,
        default_semantic_ingestion_profile(),
        ManifestOrigin.ABSENT,
        None,
        _qualification(),
    )
    return bundle, source_set, result


def test_canonical_source_decodes_with_zero_model_calls_and_authority(
    tmp_path: Path,
) -> None:
    source = tmp_path / "canonical.json"
    source.write_text(
        json.dumps(_canonical_payload()),
        encoding="utf-8",
    )

    bundle, _, result = _decode(source)

    assert result.model_call_count == 0
    assert validate_evidence_graph_against_source_bundle(
        bundle,
        result.evidence_graph,
    ).valid
    assert {
        item.producer_kind for item in result.evidence_graph.claims
    } == {EvidenceProducerKind.DETERMINISTIC_DECODER}
    assert {
        item.verification_origin
        for item in result.evidence_graph.claim_verifications
    } == {ClaimVerificationOrigin.DETERMINISTIC_DECODER}
    assert set(
        result.evidence_authority_context.deterministic_verification_ids
    ) == {
        item.verification_id
        for item in result.evidence_graph.claim_verifications
    }
    assert result.improvement_signal_set.signals[0].actionability.value == (
        "actionable"
    )
    assert result.evaluation_plans[0].disposition is (
        EvaluationDisposition.PROPOSAL_ONLY
    )
    assert "semantic_model_not_qualified" in (
        result.evaluation_plans[0].reason_codes
    )
    assert len(result.resolved_traces) == 2
    assert all(
        item.extraction_attestation is not None
        and item.extraction_attestation.extraction_origin
        is TraceExtractionOrigin.DETERMINISTIC_DECODER
        and item.extraction_attestation.candidate_attestations == ()
        for item in result.resolved_traces
    )


async def test_auto_ingestor_routes_canonical_source_without_model_and_reloads(
    tmp_path: Path,
) -> None:
    source = tmp_path / "canonical.json"
    source.write_text(
        json.dumps(_canonical_payload()),
        encoding="utf-8",
    )
    snapshot = await AgenticDatasetIngestor().prepare(
        DatasetIngestionRequest(
            source_path=source,
            mode=IngestionMode.AUTO_VERIFIED,
        )
    )

    assert isinstance(snapshot, FrozenSemanticIngestionSnapshotV2)
    assert snapshot.quality_gate.allowed is True
    assert snapshot.ingestion_model_call_count == 0
    assert snapshot.quality_report.semantic_extraction_origin == (
        "deterministic_canonical"
    )
    assert snapshot.quality_report.verified_eligible_plan_count == 1

    store = FilesystemSelfEvolveStore(tmp_path)
    store.write_ingestion(snapshot)
    source.unlink()
    restored = store.read_ingestion(snapshot.ingestion_id)
    assert isinstance(restored, FrozenSemanticIngestionSnapshotV2)
    assert restored.to_dict(public=False) == snapshot.to_dict(public=False)


def test_noncanonical_source_is_not_claimed(tmp_path: Path) -> None:
    source = tmp_path / "notes.json"
    source.write_text('{"ranking": "B > A"}', encoding="utf-8")

    assert recognize_canonical_semantic_source(_bundle(source)) is None


def test_yaml_canonical_part_uses_the_same_decoder_contract(
    tmp_path: Path,
) -> None:
    source = tmp_path / "canonical.yaml"
    source.write_text(
        yaml.safe_dump(
            _canonical_payload(),
            allow_unicode=True,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    _, source_set, result = _decode(source)

    assert source_set.parts[0].relative_path == "canonical.yaml"
    assert result.model_call_count == 0
    assert result.evidence_graph.claims


def test_canonical_marker_rejects_mixed_directory(tmp_path: Path) -> None:
    canonical = tmp_path / "canonical.json"
    canonical.write_text(
        json.dumps(_canonical_payload()),
        encoding="utf-8",
    )
    (tmp_path / "notes.md").write_text(
        "This document is not a canonical part.",
        encoding="utf-8",
    )

    with pytest.raises(
        IngestionContractError,
        match="cannot mix",
    ):
        recognize_canonical_semantic_source(_bundle(tmp_path))


def test_source_cannot_self_declare_authority_or_final_ids(
    tmp_path: Path,
) -> None:
    payload = _canonical_payload()
    payload["claims"][0]["verification_origin"] = "deterministic_decoder"
    source = tmp_path / "forged.json"
    source.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        IngestionContractError,
        match="invalid fields|cannot declare",
    ):
        recognize_canonical_semantic_source(_bundle(source))


def test_dangling_duplicate_and_bundle_mismatch_fail_closed(
    tmp_path: Path,
) -> None:
    dangling = _canonical_payload()
    dangling["claims"][0]["subject_entity_keys"] = ["missing"]
    source = tmp_path / "dangling.json"
    source.write_text(json.dumps(dangling), encoding="utf-8")
    with pytest.raises(IngestionContractError, match="unknown key"):
        recognize_canonical_semantic_source(_bundle(source))

    duplicate_dir = tmp_path / "duplicates"
    duplicate_dir.mkdir()
    first = _canonical_payload()
    second = _empty_part(part_key="second")
    second["bundle_key"] = "another-bundle"
    (duplicate_dir / "first.json").write_text(
        json.dumps(first),
        encoding="utf-8",
    )
    (duplicate_dir / "second.json").write_text(
        json.dumps(second),
        encoding="utf-8",
    )
    with pytest.raises(IngestionContractError, match="same bundle_key"):
        recognize_canonical_semantic_source(_bundle(duplicate_dir))


def test_single_and_multipart_sources_share_logical_identity(
    tmp_path: Path,
) -> None:
    single = tmp_path / "single.json"
    payload = _canonical_payload()
    single.write_text(json.dumps(payload), encoding="utf-8")

    multi = tmp_path / "multi"
    multi.mkdir()
    first = _empty_part(part_key="main")
    second = _empty_part(part_key="review")
    for field in ("entities", "traces"):
        first[field] = payload[field]
    first["claims"] = payload["claims"][:-1]
    second["claims"] = [payload["claims"][-1]]
    second["claims"][0]["citation_part_keys"] = ["review"]
    for field in ("cases", "signals", "plans"):
        second[field] = payload[field]
    (multi / "evidence.json").write_text(
        json.dumps(first),
        encoding="utf-8",
    )
    (multi / "review.json").write_text(
        json.dumps(second),
        encoding="utf-8",
    )

    _, _, single_result = _decode(single)
    _, _, multi_result = _decode(multi)

    assert (
        single_result.evidence_graph.logical_fingerprint
        == multi_result.evidence_graph.logical_fingerprint
    )
    assert (
        single_result.evidence_graph.provenance_fingerprint
        != multi_result.evidence_graph.provenance_fingerprint
    )
    assert (
        single_result.improvement_signal_set.fingerprint
        == multi_result.improvement_signal_set.fingerprint
    )
    assert [
        item.plan_fingerprint for item in single_result.evaluation_plans
    ] == [
        item.plan_fingerprint for item in multi_result.evaluation_plans
    ]


async def test_undeclared_same_slot_contradiction_fails_closed(
    tmp_path: Path,
) -> None:
    payload = _canonical_payload()
    payload["claims"].append(
        {
            "claim_key": "result-b-contradiction",
            "kind": "execution_result",
            "subject_entity_keys": ["execution-b"],
            "payload": {"result": {"success": False}},
            "citation_part_keys": ["main"],
        }
    )
    payload["cases"][0]["result_claim_keys"].append(
        "result-b-contradiction"
    )
    payload["signals"][0]["supporting_claim_keys"].append(
        "result-b-contradiction"
    )
    payload["signals"][0]["behavior_delta"]["source_claim_keys"].append(
        "result-b-contradiction"
    )
    source = tmp_path / "contradictory.json"
    source.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        IngestionContractError,
        match="without an explicit conflict",
    ) as error:
        await AgenticDatasetIngestor().prepare(
            DatasetIngestionRequest(
                source_path=source,
                mode=IngestionMode.AUTO_VERIFIED,
            )
        )

    assert error.value.reason_code == "canonical_conflict_undeclared"


async def test_custom_manifest_asset_is_frozen_for_canonical_redecode(
    tmp_path: Path,
) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    source = nested / "policy.json"
    source.write_text(
        json.dumps(_canonical_payload()),
        encoding="utf-8",
    )
    manifest = tmp_path / "policy.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": (
                    "aworld.self_evolve.source_manifest.v1"
                ),
                "semantics": (
                    default_semantic_ingestion_profile().to_dict()
                ),
            }
        ),
        encoding="utf-8",
    )
    snapshot = await AgenticDatasetIngestor().prepare(
        DatasetIngestionRequest(
            source_path=tmp_path,
            manifest_path=manifest,
            manifest_origin=(
                IngestionManifestOrigin.OPERATOR_EXPLICIT
            ),
            mode=IngestionMode.AUTO_VERIFIED,
        )
    )

    assert isinstance(snapshot, FrozenSemanticIngestionSnapshotV2)
    assert snapshot.canonical_manifest_asset_id is not None
    store = FilesystemSelfEvolveStore(tmp_path)
    store.write_ingestion(snapshot)
    restored = store.read_ingestion(snapshot.ingestion_id)
    assert restored.to_dict(public=False) == snapshot.to_dict(public=False)
