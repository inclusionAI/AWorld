from __future__ import annotations

import json
from dataclasses import replace

import pytest

from aworld.self_evolve.datasets import (
    SelfEvolveEvalSourceConfig,
    build_dataset_from_source,
)
from aworld.self_evolve.campaign import (
    SelfImprovementCampaignController,
    run_self_improvement_campaign,
)
from aworld.self_evolve.ingestion import (
    IngestionRegistry,
    builtin_extractors,
    extractor_fingerprint,
)
from aworld.self_evolve.ingestion.types import (
    AssetSelector,
    CaseFieldMappings,
    CaseSourceProvenance,
    DatasetMappingSpec,
    FieldMapping,
    FrozenIngestionSnapshot,
    IngestionQualityReport,
    IngestorTrustLevel,
    NormalizedCaseRecord,
    RecordFramingSpec,
    SourceAsset,
    SourceInventory,
    SourceKind,
    fingerprint_bytes,
    fingerprint_json,
    IngestionContractError,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.runner import (
    _source_config_from_stored_dataset_recipe,
    _validate_agentic_rerun_ingestion_ref,
    optimize_from_cli_request,
    prepare_ingestion_from_cli_request,
)


def _snapshot(
    *,
    source_kind: SourceKind = SourceKind.FILE,
    with_trajectory: bool = False,
    ingestor_name: str = "auto",
    ingestor_trust_level: IngestorTrustLevel = (
        IngestorTrustLevel.FRAMEWORK_BUILTIN
    ),
) -> FrozenIngestionSnapshot:
    content_fingerprint = fingerprint_bytes(b'{"id":"case-1"}\n')
    asset = SourceAsset(
        asset_id=SourceAsset.identity_for("cases.jsonl", content_fingerprint),
        relative_path="cases.jsonl",
        media_type="application/x-ndjson",
        size_bytes=16,
        content_fingerprint=content_fingerprint,
        extractor_name="jsonl",
        extractor_version="1",
    )
    inventory = SourceInventory.create(source_kind=source_kind, assets=(asset,))
    extractor_fingerprints = tuple(
        extractor_fingerprint(extractor)
        for extractor in builtin_extractors()
        if extractor.name == "jsonl"
    )
    mapping = DatasetMappingSpec(
        mapping_id="mapping-demo",
        asset_selectors=(
            AssetSelector(
                name="cases",
                include=("cases.jsonl",),
                media_types=("application/x-ndjson",),
            ),
        ),
        record_framing=RecordFramingSpec(
            kind="jsonl_rows",
            asset_selector="cases",
        ),
        fields=CaseFieldMappings(
            case_id=FieldMapping(source="id", required=True),
            input=FieldMapping(source="input", required=True),
            expected_output=FieldMapping(source="answer"),
        ),
    )
    ingestion_id = FrozenIngestionSnapshot.identity_for(
        inventory_fingerprint=inventory.source_root_fingerprint,
        mapping_fingerprint=mapping.fingerprint,
        manifest_fingerprint=None,
        extractor_fingerprints=extractor_fingerprints,
        ingestor_name=ingestor_name,
        ingestor_version="1",
        trust_level=ingestor_trust_level,
    )
    records = tuple(
        NormalizedCaseRecord(
            case_id=f"case-{index}",
            input={"content": f"question {index}"},
            expected_output={"answer": f"answer {index}"},
            metadata={"domain": "demo"},
            trajectory=(
                {
                    "task_id": f"task-{index}",
                    "steps": [
                        {
                            "id": "step-1",
                            "meta": {"task_id": f"task-{index}", "step": 1},
                            "state": {
                                "input": {"content": f"question {index}"}
                            },
                            "action": {"content": f"answer {index}"},
                            "reward": {"status": "succeeded"},
                        }
                    ],
                }
                if with_trajectory
                else None
            ),
            trace_replayability="replayable" if with_trajectory else "absent",
            source=CaseSourceProvenance(
                ingestion_id=ingestion_id,
                asset_ids=(asset.asset_id,),
                record_locators=(f"row:{index}",),
                mapping_fingerprint=mapping.fingerprint,
            ),
        )
        for index in (1, 2)
    )
    normalized_fingerprint = fingerprint_json(
        [record.to_dict() for record in sorted(records, key=lambda item: item.case_id)]
    )
    quality = IngestionQualityReport(
        discovered_asset_count=1,
        supported_asset_count=1,
        ignored_asset_count=0,
        rejected_asset_count=0,
        total_source_bytes=asset.size_bytes,
        mapping_candidate_count=1,
        valid_mapping_candidate_count=1,
        selected_mapping_fingerprint=mapping.fingerprint,
        eligible_record_count=2,
        normalized_case_count=2,
        rejected_record_count=0,
        record_coverage_rate=1.0,
        required_asset_coverage_rate=1.0,
        input_present_rate=1.0,
        expected_output_present_rate=1.0,
        verification_present_rate=0.0,
        trace_present_rate=1.0 if with_trajectory else 0.0,
        trace_replayable_rate=1.0 if with_trajectory else 0.0,
        duplicate_case_id_count=0,
        case_id_stability=True,
        source_fingerprint=inventory.source_root_fingerprint,
        normalized_dataset_fingerprint=normalized_fingerprint,
        deterministic_replay_match=True,
        mapping_execution_count=2,
    )
    return FrozenIngestionSnapshot(
        ingestion_id=ingestion_id,
        inventory=inventory,
        selected_mapping=mapping,
        normalized_cases=records,
        rejected_records=(),
        quality_report=quality,
        extractor_fingerprints=extractor_fingerprints,
        ingestor_name=ingestor_name,
        ingestor_version="1",
        ingestor_trust_level=ingestor_trust_level,
    )


def test_agentic_source_builds_stable_dataset_from_frozen_snapshot() -> None:
    snapshot = _snapshot()
    config = SelfEvolveEvalSourceConfig(
        kind="agentic_source",
        ingestion_snapshot=snapshot,
    )

    first = build_dataset_from_source(config)
    second = build_dataset_from_source(config)

    assert [case.case_id for case in first.cases] == ["case-1", "case-2"]
    assert first.recipe == second.recipe
    assert first.recipe.source["kind"] == "agentic_source"
    assert first.recipe.source["ingestion_id"] == snapshot.ingestion_id
    assert (
        first.recipe.source["normalized_dataset_fingerprint"]
        == snapshot.normalized_dataset_fingerprint
    )
    assert first.recipe.source["split_fingerprint"].startswith("sha256:")


def test_agentic_source_converts_normalized_trajectory_to_existing_trace_pack() -> None:
    snapshot = _snapshot(with_trajectory=True)

    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(
            kind="agentic_source",
            ingestion_snapshot=snapshot,
        )
    )

    first = dataset.cases[0]
    assert first.trace_pack is not None
    assert first.trace_pack.source_kind == "agentic_source"
    assert first.trace_pack.task_id == "task-1"
    assert first.context_snapshot is not None
    assert first.context_snapshot.source_kind == "agentic_source"
    assert first.metadata["trace_replayability"] == "replayable"


def test_store_freezes_ingestion_and_writes_verified_run_reference(
    tmp_path,
) -> None:
    snapshot = _snapshot()
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(
            kind="agentic_source",
            ingestion_snapshot=snapshot,
        )
    )
    store = FilesystemSelfEvolveStore(tmp_path)

    ingestion_path = store.write_ingestion(
        snapshot,
        dataset_recipe=dataset.recipe,
    )
    recipe_path = store.write_dataset_recipe("run-1", dataset.recipe)

    assert store.read_ingestion(snapshot.ingestion_id) == snapshot
    assert recipe_path.is_file()
    reference = store.read_ingestion_ref("run-1")
    assert reference["ingestion_id"] == snapshot.ingestion_id
    assert reference["split_fingerprint"] == dataset.recipe.source[
        "split_fingerprint"
    ]
    trainable = [
        json.loads(line)
        for line in (ingestion_path / "trainable_cases.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    held_out = [
        json.loads(line)
        for line in (ingestion_path / "held_out_cases.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert {item["case_id"] for item in trainable} == set(
        dataset.recipe.trainable_case_ids
    )
    assert {item["case_id"] for item in held_out} == set(
        dataset.recipe.held_out_case_ids
    )
    assert (ingestion_path / "ingestion.json").stat().st_mode & 0o777 == 0o600


def test_store_fails_closed_when_immutable_ingestion_content_conflicts(
    tmp_path,
) -> None:
    snapshot = _snapshot()
    store = FilesystemSelfEvolveStore(tmp_path)
    store.write_ingestion(snapshot)
    conflicting = replace(
        snapshot,
        split_fingerprint=fingerprint_json({"changed": True}),
    )

    with pytest.raises(ValueError, match="immutable ingestion id"):
        store.write_ingestion(conflicting)


def test_campaign_source_identity_uses_frozen_ingestion_not_mutable_raw_source(
    tmp_path,
) -> None:
    source = tmp_path / "domain-data"
    source.mkdir()
    (source / "cases.jsonl").write_text('{"input":"before"}\n', encoding="utf-8")
    snapshot = _snapshot(source_kind=SourceKind.DIRECTORY)
    store = FilesystemSelfEvolveStore(tmp_path)
    store.write_ingestion(snapshot)
    controller = SelfImprovementCampaignController(workspace_root=tmp_path)

    campaign = controller.create(
        {
            "apply_policy": "auto_verified",
            "from_source": str(source),
            "source_ingestor": "auto",
            "frozen_ingestion_id": snapshot.ingestion_id,
            "target": "skill:demo",
            "infer_target": False,
        },
        max_cycles=2,
    )
    (source / "cases.jsonl").write_text('{"input":"after"}\n', encoding="utf-8")

    reloaded = controller.load(campaign.campaign_id)

    assert reloaded.source_snapshot["ingestion_id"] == snapshot.ingestion_id
    assert (
        reloaded.source_snapshot["normalized_dataset_fingerprint"]
        == snapshot.normalized_dataset_fingerprint
    )


def test_framework_ingestion_only_freezes_default_auto_source_before_targeting(
    tmp_path,
) -> None:
    source = tmp_path / "cases.jsonl"
    source.write_text(
        "\n".join(
            (
                '{"case_id":"case-1","input":"question 1","expected_output":"answer 1"}',
                '{"case_id":"case-2","input":"question 2","expected_output":"answer 2"}',
            )
        )
        + "\n",
        encoding="utf-8",
    )

    summary = optimize_from_cli_request(
        workspace_root=tmp_path,
        from_source=str(source),
        ingestion_only=True,
        infer_target=True,
    )

    assert summary["status"] == "ingested"
    assert summary["ingestion_id"].startswith("ingestion-")
    assert summary["gate_results"][0]["gate_name"] == "dataset_ingestion"
    assert summary["gate_results"][0]["passed"] is True
    ingestion_path = (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / "ingestions"
        / summary["ingestion_id"]
    )
    assert ingestion_path.is_dir()
    assert not list(
        path
        for path in (tmp_path / ".aworld" / "self_evolve").iterdir()
        if path.name.startswith("cli-")
    )


def test_framework_agentic_source_report_references_same_frozen_snapshot(
    tmp_path,
) -> None:
    skill_path = tmp_path / "aworld-skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\nname: demo\n---\n# Demo\n\nExisting guidance.\n",
        encoding="utf-8",
    )
    source = tmp_path / "cases.jsonl"
    source.write_text(
        '{"case_id":"case-1","input":"question","expected_output":"answer"}\n',
        encoding="utf-8",
    )

    summary = optimize_from_cli_request(
        workspace_root=tmp_path,
        from_source=str(source),
        target="skill:demo",
        infer_target=False,
        apply_policy="proposal",
    )

    store = FilesystemSelfEvolveStore(tmp_path)
    report = store.read_report(summary["run_id"])
    reference = store.read_ingestion_ref(summary["run_id"])
    assert report["ingestion"]["ingestion_id"] == reference["ingestion_id"]
    assert report["gate_results"][0]["gate_name"] == "dataset_ingestion"
    assert report["gate_results"][0]["passed"] is True


def test_campaign_prepares_agentic_source_once_before_persisting_contract(
    tmp_path,
    monkeypatch,
) -> None:
    source = tmp_path / "cases.jsonl"
    source.write_text(
        '{"case_id":"case-1","input":"question","expected_output":"answer"}\n',
        encoding="utf-8",
    )

    def fake_run_bounded(self, campaign, *, runtime_request=None):
        return {
            "campaign_id": campaign.campaign_id,
            "frozen_ingestion_id": campaign.request["frozen_ingestion_id"],
            "runtime_frozen_ingestion_id": runtime_request[
                "frozen_ingestion_id"
            ],
        }

    monkeypatch.setattr(
        SelfImprovementCampaignController,
        "run_bounded",
        fake_run_bounded,
    )

    summary = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={
            "apply_policy": "auto_verified",
            "from_source": str(source),
            "source_ingestor": "auto",
            "target": "skill:demo",
            "infer_target": False,
        },
        max_improvement_cycles=2,
    )

    assert summary["frozen_ingestion_id"].startswith("ingestion-")
    assert (
        summary["runtime_frozen_ingestion_id"]
        == summary["frozen_ingestion_id"]
    )


def test_evaluator_rerun_recipe_loads_frozen_cases_without_raw_source(
    tmp_path,
) -> None:
    snapshot = _snapshot()
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(
            kind="agentic_source",
            ingestion_snapshot=snapshot,
        )
    )
    store = FilesystemSelfEvolveStore(tmp_path)
    store.write_ingestion(snapshot, dataset_recipe=dataset.recipe)
    recipe_path = store.write_dataset_recipe("source-run", dataset.recipe)

    source_config, split_seed = _source_config_from_stored_dataset_recipe(
        recipe_path
    )
    rebuilt = build_dataset_from_source(
        source_config,
        split_seed=split_seed,
    )

    assert source_config.kind == "agentic_source"
    assert rebuilt.recipe == dataset.recipe
    assert [case.case_id for case in rebuilt.cases] == ["case-1", "case-2"]


def test_agentic_evaluator_rerun_requires_matching_ingestion_reference(
    tmp_path,
) -> None:
    snapshot = _snapshot()
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(
            kind="agentic_source",
            ingestion_snapshot=snapshot,
        )
    )
    store = FilesystemSelfEvolveStore(tmp_path)
    store.write_ingestion(snapshot, dataset_recipe=dataset.recipe)
    store.write_dataset_recipe("source-run", dataset.recipe)
    run_path = store.run_path("source-run")

    _validate_agentic_rerun_ingestion_ref(run_path)
    (run_path / "ingestion_ref.json").unlink()

    with pytest.raises(FileNotFoundError, match="ingestion reference"):
        _validate_agentic_rerun_ingestion_ref(run_path)


def test_agentic_eval_only_source_does_not_guess_target_without_trace(
    tmp_path,
) -> None:
    source = tmp_path / "cases.jsonl"
    source.write_text(
        '{"case_id":"case-1","input":"question","expected_output":"answer"}\n',
        encoding="utf-8",
    )

    summary = optimize_from_cli_request(
        workspace_root=tmp_path,
        from_source=str(source),
        infer_target=True,
        apply_policy="proposal",
    )

    report = FilesystemSelfEvolveStore(tmp_path).read_report(summary["run_id"])
    selection = report["target_selection"]
    assert summary["status"] == "rejected"
    assert selection["selected_target"] is None
    assert "target_evidence_missing" in selection["signals"]
    assert selection["diagnostics"]["reason_code"] == "target_evidence_missing"


def test_auto_verified_rejects_untrusted_registered_ingestor_before_candidate(
    tmp_path,
) -> None:
    snapshot = _snapshot(
        ingestor_name="external",
        ingestor_trust_level=IngestorTrustLevel.EXTERNAL_UNTRUSTED,
    )

    class ExternalIngestor:
        name = "external"
        version = "1"
        trust_level = IngestorTrustLevel.EXTERNAL_UNTRUSTED

        async def prepare(self, request):
            return snapshot

    registry = IngestionRegistry(
        ingestors=(ExternalIngestor(),),
        extractors=builtin_extractors(),
    )
    source = tmp_path / "opaque.domain"
    source.write_text("custom source\n", encoding="utf-8")

    summary = optimize_from_cli_request(
        workspace_root=tmp_path,
        from_source=str(source),
        source_ingestor="external",
        ingestion_registry=registry,
        target="skill:demo",
        infer_target=False,
        apply_policy="auto_verified",
    )

    report = FilesystemSelfEvolveStore(tmp_path).read_report(summary["run_id"])
    ingestion_gate = report["gate_results"][0]
    assert summary["status"] == "rejected"
    assert report["candidate_ids"] == []
    assert ingestion_gate["gate_name"] == "dataset_ingestion"
    assert ingestion_gate["passed"] is False
    assert (
        ingestion_gate["reason_code"]
        == "ingestor_not_trusted_for_auto_verified"
    )


def test_file_and_directory_sources_produce_equivalent_logical_cases(
    tmp_path,
) -> None:
    file_root = tmp_path / "file-source"
    directory_root = tmp_path / "directory-source"
    file_root.mkdir()
    directory_root.mkdir()
    payload = (
        '{"case_id":"case-1","input":"question 1","expected_output":"answer 1"}\n'
        '{"case_id":"case-2","input":"question 2","expected_output":"answer 2"}\n'
    )
    file_path = file_root / "records.jsonl"
    file_path.write_text(payload, encoding="utf-8")
    (directory_root / "records.jsonl").write_text(payload, encoding="utf-8")

    file_snapshot = prepare_ingestion_from_cli_request(
        workspace_root=tmp_path,
        from_source=str(file_path),
    )
    directory_snapshot = prepare_ingestion_from_cli_request(
        workspace_root=tmp_path,
        from_source=str(directory_root),
    )

    def logical_cases(snapshot):
        return [
            {
                "case_id": case.case_id,
                "input": case.input,
                "expected_output": case.expected_output,
                "metadata": case.metadata,
                "trajectory": case.trajectory,
            }
            for case in snapshot.normalized_cases
        ]

    assert logical_cases(file_snapshot) == logical_cases(directory_snapshot)


def test_manifest_policy_cannot_be_dropped_by_final_auto_verified_gate(
    tmp_path,
) -> None:
    source = tmp_path / "records.jsonl"
    source.write_text(
        '{"case_id":"case-1","input":"question"}\n',
        encoding="utf-8",
    )
    mapping = DatasetMappingSpec(
        mapping_id="manifest-mapping",
        asset_selectors=(AssetSelector(name="source"),),
        record_framing=RecordFramingSpec(kind="jsonl_rows"),
        fields=CaseFieldMappings(
            case_id=FieldMapping(source="record.case_id"),
            input=FieldMapping(source="record.input"),
        ),
    )
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": (
                    "aworld.self_evolve.source_manifest.v1"
                ),
                "mapping": mapping.to_dict(),
                "policy": {"expected_output_required": True},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(IngestionContractError) as error:
        prepare_ingestion_from_cli_request(
            workspace_root=tmp_path,
            from_source=str(source),
            source_manifest=str(manifest),
            apply_policy="auto_verified",
        )

    assert error.value.reason_code == "expected_output_required"


def test_registered_ingestor_cannot_self_promote_snapshot_trust(
    tmp_path,
) -> None:
    spoofed = _snapshot(
        ingestor_name="external",
        ingestor_trust_level=IngestorTrustLevel.FRAMEWORK_BUILTIN,
    )

    class ExternalIngestor:
        name = "external"
        version = "1"
        trust_level = IngestorTrustLevel.EXTERNAL_UNTRUSTED

        async def prepare(self, request):
            return spoofed

    source = tmp_path / "opaque.domain"
    source.write_text("custom source\n", encoding="utf-8")
    registry = IngestionRegistry(
        ingestors=(ExternalIngestor(),),
        extractors=builtin_extractors(),
    )

    with pytest.raises(
        IngestionContractError,
        match="registered strategy",
    ):
        prepare_ingestion_from_cli_request(
            workspace_root=tmp_path,
            from_source=str(source),
            source_ingestor="external",
            ingestion_registry=registry,
        )


def test_workspace_ingestor_requires_explicit_configuration_allowlist() -> None:
    configuration_fingerprint = fingerprint_json(
        {"ingestor": "workspace", "version": "1"}
    )

    class WorkspaceIngestor:
        name = "workspace"
        version = "1"
        trust_level = IngestorTrustLevel.WORKSPACE_ALLOWLISTED

        async def prepare(self, request):
            raise AssertionError("registry validation happens before prepare")

    WorkspaceIngestor.configuration_fingerprint = configuration_fingerprint

    with pytest.raises(
        IngestionContractError,
        match="not present in the registry allowlist",
    ):
        IngestionRegistry(ingestors=(WorkspaceIngestor(),))

    registry = IngestionRegistry(
        ingestors=(WorkspaceIngestor(),),
        allowlisted_ingestor_fingerprints=(configuration_fingerprint,),
    )
    assert registry.get_ingestor("workspace").version == "1"


def test_custom_ingestor_cannot_register_as_framework_builtin() -> None:
    class ForgedBuiltinIngestor:
        name = "forged"
        version = "1"
        trust_level = IngestorTrustLevel.FRAMEWORK_BUILTIN

        async def prepare(self, request):
            raise AssertionError("registry validation happens first")

    with pytest.raises(
        IngestionContractError,
        match="reserved for framework implementations",
    ):
        IngestionRegistry(ingestors=(ForgedBuiltinIngestor(),))


def test_store_rejects_self_inconsistent_snapshot_quality(tmp_path) -> None:
    snapshot = _snapshot()
    forged = replace(
        snapshot,
        quality_report=replace(
            snapshot.quality_report,
            normalized_case_count=0,
        ),
    )

    with pytest.raises(IngestionContractError) as error:
        FilesystemSelfEvolveStore(tmp_path).write_ingestion(forged)

    assert error.value.reason_code == "quality_report_mismatch"


def test_ingestion_model_calls_are_debited_from_run_budget(
    tmp_path,
) -> None:
    snapshot = replace(
        _snapshot(
            ingestor_name="external",
            ingestor_trust_level=IngestorTrustLevel.EXTERNAL_UNTRUSTED,
        ),
        ingestion_model_call_count=2,
    )

    class ExternalIngestor:
        name = "external"
        version = "1"
        trust_level = IngestorTrustLevel.EXTERNAL_UNTRUSTED

        async def prepare(self, request):
            return snapshot

    registry = IngestionRegistry(
        ingestors=(ExternalIngestor(),),
        extractors=builtin_extractors(),
    )
    source = tmp_path / "opaque.domain"
    source.write_text("custom source\n", encoding="utf-8")
    skill = tmp_path / "aworld-skills" / "demo" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: demo\n---\n# Demo\n\nExisting guidance.\n",
        encoding="utf-8",
    )

    summary = optimize_from_cli_request(
        workspace_root=tmp_path,
        from_source=str(source),
        source_ingestor="external",
        ingestion_registry=registry,
        target="skill:demo",
        infer_target=False,
        total_run_token_budget=20_000,
    )

    report = FilesystemSelfEvolveStore(tmp_path).read_report(summary["run_id"])
    ingestion_debit = next(
        item
        for item in report["budget"]["debits"]
        if item["item_id"] == "frozen-dataset-ingestion"
    )
    assert ingestion_debit["stage"] == "candidate_generation"
    assert ingestion_debit["actual"]["tokens"] == 8_192


def test_aworld_trajectory_log_is_recognized_as_agentic_trace_source(
    tmp_path,
) -> None:
    source = tmp_path / "trajectory.log"
    source.write_text(
        "INFO "
        + repr(
            {
                "task_id": "task-1",
                "trajectory": [
                    {
                        "id": "step-1",
                        "state": {"input": {"content": "question"}},
                        "action": {"content": "answer"},
                        "reward": {"status": "succeeded"},
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    snapshot = prepare_ingestion_from_cli_request(
        workspace_root=tmp_path,
        from_source=str(source),
    )
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(
            kind="agentic_source",
            ingestion_snapshot=snapshot,
        )
    )

    assert snapshot.normalized_cases[0].case_id == "task-1"
    assert snapshot.normalized_cases[0].trace_replayability == "replayable"
    assert dataset.cases[0].trace_pack is not None
