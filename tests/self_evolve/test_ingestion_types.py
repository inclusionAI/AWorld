from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from aworld.self_evolve.ingestion import (
    AssetSelector,
    CaseFieldMappings,
    CaseSourceProvenance,
    DatasetIngestionRequest,
    DatasetMappingSpec,
    FieldMapping,
    FrozenIngestionSnapshot,
    IngestionContractError,
    IngestionLimits,
    IngestionQualityReport,
    NormalizedCaseRecord,
    RecordFramingSpec,
    SourceAsset,
    SourceInventory,
    SourceKind,
    fingerprint_bytes,
    normalized_records_fingerprint,
)


def _asset(path: str = "records.json") -> SourceAsset:
    content_fingerprint = fingerprint_bytes(b"[]")
    return SourceAsset(
        asset_id=SourceAsset.identity_for(path, content_fingerprint),
        relative_path=path,
        media_type="application/json",
        size_bytes=2,
        content_fingerprint=content_fingerprint,
        extractor_name="json",
        extractor_version="1",
        structural_profile={"root_type": "array", "field_names": ["input"]},
    )


def _mapping() -> DatasetMappingSpec:
    return DatasetMappingSpec(
        mapping_id="mapping-generic",
        asset_selectors=(AssetSelector(name="source"),),
        record_framing=RecordFramingSpec(kind="json_array"),
        fields=CaseFieldMappings(input=FieldMapping(source="record.input")),
    )


def _case(asset: SourceAsset, mapping: DatasetMappingSpec) -> NormalizedCaseRecord:
    return NormalizedCaseRecord(
        case_id="case-1",
        input={"request": "private value"},
        source=CaseSourceProvenance(
            asset_ids=(asset.asset_id,),
            record_locators=("$[0]",),
            mapping_fingerprint=mapping.fingerprint,
        ),
    )


def _report(
    inventory: SourceInventory,
    mapping: DatasetMappingSpec,
    case: NormalizedCaseRecord,
) -> IngestionQualityReport:
    return IngestionQualityReport(
        discovered_asset_count=1,
        supported_asset_count=1,
        ignored_asset_count=0,
        rejected_asset_count=0,
        total_source_bytes=2,
        mapping_candidate_count=1,
        valid_mapping_candidate_count=1,
        selected_mapping_fingerprint=mapping.fingerprint,
        eligible_record_count=1,
        normalized_case_count=1,
        rejected_record_count=0,
        record_coverage_rate=1.0,
        required_asset_coverage_rate=1.0,
        input_present_rate=1.0,
        expected_output_present_rate=0.0,
        verification_present_rate=0.0,
        trace_present_rate=0.0,
        trace_replayable_rate=0.0,
        duplicate_case_id_count=0,
        case_id_stability=True,
        source_fingerprint=inventory.source_root_fingerprint,
        normalized_dataset_fingerprint=normalized_records_fingerprint((case,)),
        deterministic_replay_match=True,
        mapping_execution_count=2,
    )


def test_request_defaults_to_auto_and_round_trips() -> None:
    request = DatasetIngestionRequest(source_path=Path("domain-data"))

    assert request.ingestor_name == "auto"
    assert DatasetIngestionRequest.from_dict(request.to_dict()) == request
    assert "source_path" not in request.to_dict(public=True)


def test_inventory_and_mapping_round_trip_with_verified_fingerprints() -> None:
    asset = _asset()
    inventory = SourceInventory.create(
        source_kind=SourceKind.DIRECTORY,
        assets=(asset,),
    )
    mapping = _mapping()

    assert SourceInventory.from_dict(inventory.to_dict()) == inventory
    assert DatasetMappingSpec.from_dict(mapping.to_dict()) == mapping
    with pytest.raises(FrozenInstanceError):
        inventory.assets = ()  # type: ignore[misc]


def test_schema_and_fingerprint_mismatch_fail_closed() -> None:
    inventory = SourceInventory.create(
        source_kind=SourceKind.FILE,
        assets=(_asset(),),
    )
    payload = inventory.to_dict()
    payload["schema_version"] = "other.v1"
    with pytest.raises(IngestionContractError, match="schema_version"):
        SourceInventory.from_dict(payload)

    payload = inventory.to_dict()
    payload["source_root_fingerprint"] = "sha256:" + "0" * 64
    with pytest.raises(IngestionContractError, match="fingerprint mismatch"):
        SourceInventory.from_dict(payload)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_files": True},
        {"max_files": 0},
        {"max_file_bytes": -1},
        {"max_asset_sample_bytes": 20, "max_agent_sample_bytes": 10},
    ],
)
def test_limits_reject_bool_zero_negative_and_inconsistent_values(kwargs: dict) -> None:
    with pytest.raises(IngestionContractError):
        IngestionLimits(**kwargs)


def test_unsafe_case_identity_and_untrusted_verification_are_rejected() -> None:
    asset = _asset()
    mapping = _mapping()
    provenance = CaseSourceProvenance(
        asset_ids=(asset.asset_id,),
        record_locators=("$",),
        mapping_fingerprint=mapping.fingerprint,
    )

    with pytest.raises(IngestionContractError, match="safe stable identity"):
        NormalizedCaseRecord(case_id="../escape", input="x", source=provenance)
    with pytest.raises(IngestionContractError, match="trusted provenance"):
        NormalizedCaseRecord(
            case_id="case-1",
            input="x",
            source=provenance,
            verification_command="echo unsafe",
        )


def test_duplicate_inventory_identity_is_rejected() -> None:
    asset = _asset()
    with pytest.raises(IngestionContractError, match="duplicate"):
        SourceInventory(
            source_kind=SourceKind.DIRECTORY,
            source_root_fingerprint="sha256:" + "0" * 64,
            assets=(asset, asset),
        )


def test_frozen_snapshot_round_trip_and_public_projection() -> None:
    asset = _asset()
    inventory = SourceInventory.create(
        source_kind=SourceKind.DIRECTORY,
        assets=(asset,),
    )
    mapping = _mapping()
    case = _case(asset, mapping)
    snapshot = FrozenIngestionSnapshot(
        ingestion_id="ingestion-generic",
        inventory=inventory,
        selected_mapping=mapping,
        normalized_cases=(case,),
        rejected_records=(),
        quality_report=_report(inventory, mapping, case),
    )

    assert FrozenIngestionSnapshot.from_dict(snapshot.to_dict()) == snapshot
    public = snapshot.public_projection()
    assert "normalized_cases" not in public
    assert "private value" not in str(public)
    assert public["inventory"]["assets"][0]["relative_path"] == "records.json"
    assert "/Users/" not in str(public)
