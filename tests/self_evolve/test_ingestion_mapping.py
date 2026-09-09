from __future__ import annotations

from pathlib import Path

import pytest

from aworld.self_evolve.ingestion import (
    AssetSelector,
    CaseFieldMappings,
    DatasetMappingSpec,
    DeclaredExclusion,
    FieldMapping,
    IngestionContractError,
    JoinSpec,
    RecordFramingSpec,
    SourceManifest,
    TrajectoryMappingSpec,
    materialize_mapping,
    parse_source_manifest,
    scan_source,
)


def _spec(
    *,
    framing: str,
    input_source: str = "record.input",
    case_id_source: str | None = "record.id",
    expected_source: str | None = "record.expected_output",
) -> DatasetMappingSpec:
    return DatasetMappingSpec(
        mapping_id="mapping-generic",
        asset_selectors=(AssetSelector(name="source"),),
        record_framing=RecordFramingSpec(kind=framing),
        fields=CaseFieldMappings(
            case_id=(
                FieldMapping(source=case_id_source) if case_id_source else None
            ),
            input=FieldMapping(source=input_source),
            expected_output=(
                FieldMapping(source=expected_source) if expected_source else None
            ),
        ),
    )


@pytest.mark.parametrize(
    ("filename", "content", "framing"),
    [
        (
            "records.json",
            '[{"id":"c1","input":"a","expected_output":"b"}]',
            "json_array",
        ),
        (
            "records.jsonl",
            '{"id":"c1","input":"a","expected_output":"b"}\n',
            "jsonl_rows",
        ),
        (
            "records.csv",
            "id,input,expected_output\nc1,a,b\n",
            "csv_rows",
        ),
        (
            "records.yaml",
            "- id: c1\n  input: a\n  expected_output: b\n",
            "yaml_array",
        ),
    ],
)
def test_structured_formats_materialize_the_same_case(
    tmp_path: Path,
    filename: str,
    content: str,
    framing: str,
) -> None:
    path = tmp_path / filename
    path.write_text(content, encoding="utf-8")
    inventory = scan_source(path)

    result = materialize_mapping(
        path,
        inventory=inventory,
        mapping_spec=_spec(framing=framing),
    )

    assert [(case.case_id, case.input, case.expected_output) for case in result.normalized_cases] == [
        ("c1", "a", "b")
    ]
    assert not result.rejected_records


def test_one_file_per_case_and_stable_generated_identity(tmp_path: Path) -> None:
    path = tmp_path / "request.txt"
    path.write_text("generic request", encoding="utf-8")
    inventory = scan_source(path)
    spec = _spec(
        framing="one_file_per_case",
        input_source="record",
        case_id_source=None,
        expected_source=None,
    )

    first = materialize_mapping(path, inventory=inventory, mapping_spec=spec)
    second = materialize_mapping(path, inventory=inventory, mapping_spec=spec)

    assert first.normalized_cases[0].input == "generic request"
    assert first.normalized_cases[0].case_id.startswith("case-")
    assert first.materialization_fingerprint == second.materialization_fingerprint


def test_cross_file_join_is_deterministic_and_records_provenance(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "requests.json").write_text(
        '[{"id":"c1","input":"question"}]',
        encoding="utf-8",
    )
    (root / "results.json").write_text(
        '[{"id":"c1","answer":"response"}]',
        encoding="utf-8",
    )
    inventory = scan_source(root)
    spec = DatasetMappingSpec(
        mapping_id="mapping-join",
        asset_selectors=(
            AssetSelector(name="requests", include=("requests.json",)),
            AssetSelector(name="results", include=("results.json",)),
        ),
        record_framing=RecordFramingSpec(
            kind="json_array",
            asset_selector="requests",
        ),
        joins=(
            JoinSpec(
                left_asset="requests",
                left_key="id",
                right_asset="results",
                right_key="id",
                cardinality="one_to_one",
            ),
        ),
        fields=CaseFieldMappings(
            case_id=FieldMapping(source="requests.id"),
            input=FieldMapping(source="requests.input"),
            expected_output=FieldMapping(source="results.answer"),
        ),
    )

    result = materialize_mapping(root, inventory=inventory, mapping_spec=spec)

    assert result.normalized_cases[0].expected_output == "response"
    assert len(result.normalized_cases[0].source.asset_ids) == 2
    assert result.unmatched_required_join_count == 0


def test_unmatched_join_and_duplicate_ids_are_explicit(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "requests.json").write_text(
        '[{"id":"same","input":"a"},{"id":"same","input":"b"}]',
        encoding="utf-8",
    )
    (root / "results.json").write_text("[]", encoding="utf-8")
    inventory = scan_source(root)
    spec = DatasetMappingSpec(
        mapping_id="mapping-join",
        asset_selectors=(
            AssetSelector(name="requests", include=("requests.json",)),
            AssetSelector(name="results", include=("results.json",)),
        ),
        record_framing=RecordFramingSpec(
            kind="json_array",
            asset_selector="requests",
        ),
        joins=(
            JoinSpec(
                left_asset="requests",
                left_key="id",
                right_asset="results",
                right_key="id",
            ),
        ),
        fields=CaseFieldMappings(
            case_id=FieldMapping(source="requests.id"),
            input=FieldMapping(source="requests.input"),
        ),
    )

    unmatched = materialize_mapping(root, inventory=inventory, mapping_spec=spec)
    assert unmatched.unmatched_required_join_count == 2
    assert {item.reason_code for item in unmatched.rejected_records} == {
        "required_join_unmatched"
    }

    no_join = DatasetMappingSpec(
        mapping_id="mapping-duplicates",
        asset_selectors=(spec.asset_selectors[0],),
        record_framing=RecordFramingSpec(kind="json_array"),
        fields=CaseFieldMappings(
            case_id=FieldMapping(source="record.id"),
            input=FieldMapping(source="record.input"),
        ),
    )
    duplicates = materialize_mapping(
        root,
        inventory=inventory,
        mapping_spec=no_join,
    )
    assert [case.case_id for case in duplicates.normalized_cases] == ["same", "same"]


def test_mixed_eval_and_trajectory_normalizes_statuses(tmp_path: Path) -> None:
    path = tmp_path / "records.json"
    path.write_text(
        """[
          {"id":"c1","input":"a","expected_output":"b","trajectory":[
            {"state":{"input":"a"},"action":{"content":"try"},"reward":{"status":"custom-ok"}}
          ]},
          {"id":"c2","input":"c","expected_output":"d","trajectory":[
            {"state":{"input":"c"},"action":{"content":"try"},"reward":{"status":"mystery"}}
          ]}
        ]""",
        encoding="utf-8",
    )
    inventory = scan_source(path)
    base = _spec(framing="json_array")
    spec = DatasetMappingSpec(
        mapping_id="mapping-trajectory",
        asset_selectors=base.asset_selectors,
        record_framing=base.record_framing,
        fields=base.fields,
        trajectory=TrajectoryMappingSpec(
            steps=FieldMapping(source="record.trajectory"),
            status_map={"custom-ok": "succeeded"},
        ),
    )

    result = materialize_mapping(path, inventory=inventory, mapping_spec=spec)

    statuses = [
        case.trajectory["steps"][0]["reward"]["status"]  # type: ignore[index]
        for case in result.normalized_cases
    ]
    assert statuses == ["succeeded", "unknown"]
    assert result.normalized_cases[0].trace_replayability == "replayable"


def test_null_input_and_invalid_jsonl_line_are_not_silently_lost(
    tmp_path: Path,
) -> None:
    path = tmp_path / "records.jsonl"
    path.write_text(
        '{"id":"c1","input":null}\nnot-json\n{"id":"c2","input":"ok"}\n',
        encoding="utf-8",
    )
    inventory = scan_source(path)

    result = materialize_mapping(
        path,
        inventory=inventory,
        mapping_spec=_spec(
            framing="jsonl_rows",
            expected_source=None,
        ),
    )

    assert [case.case_id for case in result.normalized_cases] == ["c2"]
    assert {item.reason_code for item in result.rejected_records} == {
        "input_missing",
        "record_parse_failed",
    }


def test_every_invalid_jsonl_line_is_preserved_as_a_rejected_record(
    tmp_path: Path,
) -> None:
    path = tmp_path / "records.jsonl"
    path.write_text(
        "\n".join(["not-json"] * 65 + ['{"id":"ok","input":"value"}'])
        + "\n",
        encoding="utf-8",
    )
    inventory = scan_source(path)

    result = materialize_mapping(
        path,
        inventory=inventory,
        mapping_spec=_spec(
            framing="jsonl_rows",
            expected_source=None,
        ),
    )

    assert len(result.rejected_records) == 65
    assert result.eligible_record_count == 66
    assert result.rejected_records[-1].record_locator == "line:65"


def test_manifest_command_has_explicit_user_provenance(tmp_path: Path) -> None:
    path = tmp_path / "records.json"
    path.write_text('[{"id":"c1","input":"a"}]', encoding="utf-8")
    inventory = scan_source(path)
    manifest = SourceManifest(
        mapping_spec=_spec(
            framing="json_array",
            expected_source=None,
        ),
        verification_command="python -m pytest tests/domain.py -q",
    )

    result = materialize_mapping(
        path,
        inventory=inventory,
        mapping_spec=manifest.mapping_spec,
        manifest=manifest,
    )

    case = result.normalized_cases[0]
    assert case.verification_command == manifest.verification_command
    assert case.source.verification_origin == "user_manifest"


@pytest.mark.parametrize(
    "payload",
    [
        {
            "schema_version": "aworld.self_evolve.dataset_mapping.v1",
            "mapping_id": "bad",
            "asset_selectors": [],
            "record_framing": {"kind": "json_array"},
            "fields": {"input": {"from": "record.input"}},
            "shell": "echo unsafe",
        },
        {
            "schema_version": "aworld.self_evolve.dataset_mapping.v1",
            "mapping_id": "bad",
            "asset_selectors": [],
            "record_framing": {"kind": "json_array"},
            "fields": {
                "input": {"from": "record.input"},
                "verification_command": "echo unsafe",
            },
        },
    ],
)
def test_mapping_never_accepts_code_or_command(payload: dict) -> None:
    with pytest.raises(IngestionContractError):
        DatasetMappingSpec.from_dict(payload)


def test_manifest_rejects_source_escape_and_outcome_exclusions() -> None:
    with pytest.raises(IngestionContractError) as error:
        parse_source_manifest(
            {
                "schema_version": "aworld.self_evolve.source_manifest.v1",
                "assets": {"include": ["../outside/*.json"]},
                "case": {
                    "framing": "json_array",
                    "input": {"from": "record.input"},
                },
            }
        )
    assert error.value.reason_code == "source_escape"

    with pytest.raises(IngestionContractError) as exclusion_error:
        DeclaredExclusion(
            asset_selector="source",
            structural_reason="drop incorrect expected_output",
        )
    assert exclusion_error.value.reason_code == "outcome_based_exclusion_not_allowed"


def test_manifest_sections_build_named_asset_selectors() -> None:
    manifest = parse_source_manifest(
        {
            "schema_version": "aworld.self_evolve.source_manifest.v1",
            "assets": {
                "include": [
                    "requests/**/*.json",
                    "results/**/*.json",
                ]
            },
            "case": {
                "framing": "one_request_per_file",
                "id": {"from": "requests.request_id"},
                "input": {"from": "requests.payload"},
                "expected_output": {"from": "results.answer"},
            },
            "joins": [
                {
                    "left": "requests.request_id",
                    "right": "results.request_id",
                    "required": True,
                }
            ],
        }
    )

    assert [item.name for item in manifest.mapping_spec.asset_selectors] == [
        "requests",
        "results",
    ]
    assert manifest.mapping_spec.record_framing.kind == "one_file_per_case"
    assert manifest.mapping_spec.record_framing.asset_selector == "requests"


def test_manifest_constant_cannot_be_used_without_manifest(tmp_path: Path) -> None:
    path = tmp_path / "records.json"
    path.write_text('[{"id":"c1","input":"a"}]', encoding="utf-8")
    inventory = scan_source(path)
    spec = DatasetMappingSpec(
        mapping_id="mapping-constant",
        asset_selectors=(AssetSelector(name="source"),),
        record_framing=RecordFramingSpec(kind="json_array"),
        fields=CaseFieldMappings(
            input=FieldMapping(
                transform="constant_from_manifest",
                constant="manifest-owned",
            )
        ),
    )

    with pytest.raises(IngestionContractError) as error:
        materialize_mapping(path, inventory=inventory, mapping_spec=spec)
    assert error.value.reason_code == "mapping_protocol_invalid"
