from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from aworld.self_evolve.ingestion import (
    AssetSelector,
    CaseFieldMappings,
    DatasetMappingSpec,
    FieldMapping,
    IngestionContractError,
    IngestionMode,
    IngestionVerifier,
    IngestorTrustLevel,
    RecordFramingSpec,
    build_quality_report,
    evaluate_ingestion_gate,
    materialize_mapping,
    scan_source,
)


def _spec(
    *,
    input_field: str = "input",
    expected_field: str | None = "expected_output",
    mapping_id: str = "mapping-generic",
) -> DatasetMappingSpec:
    return DatasetMappingSpec(
        mapping_id=mapping_id,
        asset_selectors=(AssetSelector(name="source"),),
        record_framing=RecordFramingSpec(kind="json_array"),
        fields=CaseFieldMappings(
            case_id=FieldMapping(source="record.id"),
            input=FieldMapping(source=f"record.{input_field}"),
            expected_output=(
                FieldMapping(source=f"record.{expected_field}")
                if expected_field
                else None
            ),
        ),
    )


def test_proposal_passes_with_typed_quality_warnings(tmp_path: Path) -> None:
    path = tmp_path / "records.json"
    path.write_text('[{"id":"c1","input":"a"}]', encoding="utf-8")
    inventory = scan_source(path)

    result = IngestionVerifier().verify(
        path,
        inventory=inventory,
        mapping_specs=(_spec(expected_field=None),),
    )

    assert result.gate.passed
    assert result.gate.reason_code == "ingestion_passed_with_warnings"
    assert "expected_output_incomplete" in result.gate.warning_reason_codes
    assert result.quality_report.deterministic_replay_match


def test_auto_verified_requires_trust_coverage_and_frozen_split(
    tmp_path: Path,
) -> None:
    path = tmp_path / "records.json"
    path.write_text(
        '[{"id":"c1","input":"a","expected_output":"b"}]',
        encoding="utf-8",
    )
    inventory = scan_source(path)
    spec = _spec()
    verifier = IngestionVerifier()

    not_frozen = verifier.verify(
        path,
        inventory=inventory,
        mapping_specs=(spec,),
        mode=IngestionMode.AUTO_VERIFIED,
    )
    assert not not_frozen.gate.passed
    assert not_frozen.gate.reason_code == "dataset_split_not_frozen"

    untrusted = verifier.verify(
        path,
        inventory=inventory,
        mapping_specs=(spec,),
        mode=IngestionMode.AUTO_VERIFIED,
        trust_level=IngestorTrustLevel.EXTERNAL_UNTRUSTED,
        snapshot_frozen=True,
        split_frozen=True,
    )
    assert not untrusted.gate.passed
    assert (
        untrusted.gate.reason_code
        == "ingestor_not_trusted_for_auto_verified"
    )

    trusted = verifier.verify(
        path,
        inventory=inventory,
        mapping_specs=(spec,),
        mode=IngestionMode.AUTO_VERIFIED,
        snapshot_frozen=True,
        split_frozen=True,
    )
    assert trusted.gate.passed
    assert trusted.gate.details["agent_confidence_authoritative"] is False


def test_duplicate_identity_fails_hard_gate(tmp_path: Path) -> None:
    path = tmp_path / "records.json"
    path.write_text(
        """[
          {"id":"same","input":"a","expected_output":"b"},
          {"id":"same","input":"c","expected_output":"d"}
        ]""",
        encoding="utf-8",
    )
    inventory = scan_source(path)

    with pytest.raises(IngestionContractError) as error:
        IngestionVerifier().verify(
            path,
            inventory=inventory,
            mapping_specs=(_spec(),),
        )
    assert error.value.reason_code == "duplicate_case_identity"


def test_equally_ranked_different_mappings_are_ambiguous(tmp_path: Path) -> None:
    path = tmp_path / "records.json"
    path.write_text(
        '[{"id":"c1","left":"a","right":"b","expected_output":"c"}]',
        encoding="utf-8",
    )
    inventory = scan_source(path)

    with pytest.raises(IngestionContractError) as error:
        IngestionVerifier().verify(
            path,
            inventory=inventory,
            mapping_specs=(
                _spec(input_field="left", mapping_id="mapping-left"),
                _spec(input_field="right", mapping_id="mapping-right"),
            ),
        )
    assert error.value.reason_code == "mapping_ambiguous"


def test_record_coverage_threshold_is_inclusive_at_point_ninety_five(
    tmp_path: Path,
) -> None:
    path = tmp_path / "records.json"
    records = [
        {"id": f"c{index}", "input": f"input-{index}", "expected_output": "ok"}
        for index in range(19)
    ]
    records.append({"id": "bad", "input": None, "expected_output": "ok"})
    import json

    path.write_text(json.dumps(records), encoding="utf-8")
    inventory = scan_source(path)
    materialization = materialize_mapping(
        path,
        inventory=inventory,
        mapping_spec=_spec(),
    )
    report = build_quality_report(inventory, materialization)

    assert report.record_coverage_rate == 0.95
    gate = evaluate_ingestion_gate(
        report,
        mode=IngestionMode.AUTO_VERIFIED,
        trust_level=IngestorTrustLevel.FRAMEWORK_BUILTIN,
        snapshot_frozen=True,
        split_frozen=True,
    )
    assert gate.passed


def test_held_out_exposure_and_nondeterminism_fail_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "records.json"
    path.write_text(
        '[{"id":"c1","input":"a","expected_output":"b"}]',
        encoding="utf-8",
    )
    inventory = scan_source(path)
    materialization = materialize_mapping(
        path,
        inventory=inventory,
        mapping_spec=_spec(),
    )
    exposed = replace(materialization, held_out_value_exposure_count=1)
    report = build_quality_report(inventory, exposed)
    gate = evaluate_ingestion_gate(
        report,
        mode=IngestionMode.AUTO_VERIFIED,
        trust_level=IngestorTrustLevel.FRAMEWORK_BUILTIN,
        snapshot_frozen=True,
        split_frozen=True,
    )
    assert not gate.passed
    assert gate.reason_code == "held_out_value_exposed"

    nondeterministic = build_quality_report(
        inventory,
        materialization,
        deterministic_replay_match=False,
    )
    assert "mapping_nondeterministic" in nondeterministic.failure_reason_codes


def test_public_projection_contains_no_normalized_values(tmp_path: Path) -> None:
    path = tmp_path / "records.json"
    path.write_text(
        '[{"id":"c1","input":"private-input","expected_output":"private-answer"}]',
        encoding="utf-8",
    )
    inventory = scan_source(path)
    result = IngestionVerifier().verify(
        path,
        inventory=inventory,
        mapping_specs=(_spec(),),
    )

    public = str(result.gate.to_dict())
    assert "private-input" not in public
    assert "private-answer" not in public
    assert str(path) not in public
