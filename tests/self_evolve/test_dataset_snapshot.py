from __future__ import annotations

import json
from pathlib import Path

import pytest

from aworld.self_evolve.cli_ingestion import _load_or_build_campaign_dataset
from aworld.self_evolve.campaign import (
    SelfImprovementCampaignController,
    run_self_improvement_campaign,
)
from aworld.self_evolve.dataset_snapshot import (
    load_campaign_dataset_snapshot,
    load_campaign_dataset_snapshot_manifest,
    write_campaign_dataset_snapshot,
)
from aworld.self_evolve.datasets import (
    SelfEvolveEvalSourceConfig,
    build_dataset_from_source,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore


def _trajectory_fixture() -> Path:
    return (
        Path(__file__).parent
        / "fixtures"
        / "credit_assignment_cases"
        / "sample_trajectory.log"
    )


def test_campaign_dataset_snapshot_round_trips_as_streamed_cases(
    tmp_path: Path,
) -> None:
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(
            kind="trajectory_log",
            path=str(_trajectory_fixture()),
            max_cases=2,
        )
    )
    snapshot_path = tmp_path / "dataset_snapshot"
    source_fingerprint = "sha256:" + "a" * 64

    manifest = write_campaign_dataset_snapshot(
        snapshot_path,
        dataset,
        campaign_id="campaign-streamed",
        campaign_source_fingerprint=source_fingerprint,
    )
    restored = load_campaign_dataset_snapshot(
        snapshot_path,
        expected_campaign_id="campaign-streamed",
        expected_campaign_source_fingerprint=source_fingerprint,
    )

    assert restored == dataset
    assert manifest["storage_layout"] == "jsonl_case_stream"
    assert "cases" not in manifest
    assert manifest["case_count"] == len(dataset.cases)
    assert len(
        (snapshot_path / "cases.jsonl").read_text(encoding="utf-8").splitlines()
    ) == len(dataset.cases)


def test_campaign_dataset_snapshot_fails_closed_after_case_stream_change(
    tmp_path: Path,
) -> None:
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(
            kind="trajectory_log",
            path=str(_trajectory_fixture()),
            max_cases=1,
        )
    )
    snapshot_path = tmp_path / "dataset_snapshot"
    source_fingerprint = "sha256:" + "b" * 64
    write_campaign_dataset_snapshot(
        snapshot_path,
        dataset,
        campaign_id="campaign-corrupt",
        campaign_source_fingerprint=source_fingerprint,
    )
    with (snapshot_path / "cases.jsonl").open("ab") as handle:
        handle.write(b"{}\n")

    with pytest.raises(ValueError, match="campaign dataset snapshot"):
        load_campaign_dataset_snapshot(
            snapshot_path,
            expected_campaign_id="campaign-corrupt",
            expected_campaign_source_fingerprint=source_fingerprint,
        )


def test_campaign_cycle_reuses_frozen_dataset_without_reparsing_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_path = _trajectory_fixture()
    controller = SelfImprovementCampaignController(workspace_root=tmp_path)
    campaign = controller.create(
        {
            "apply_policy": "verified_only",
            "from_trajectory": str(source_path),
            "target": "skill:demo",
        }
    )
    source_config = SelfEvolveEvalSourceConfig(
        kind="trajectory_log",
        path=str(source_path),
    )
    original_builder = build_dataset_from_source
    build_count = 0

    def counted_builder(*args, **kwargs):
        nonlocal build_count
        build_count += 1
        return original_builder(*args, **kwargs)

    events: list[str] = []
    store = FilesystemSelfEvolveStore(tmp_path)

    first, snapshot_path, first_reused = (
        _load_or_build_campaign_dataset(
            store=store,
            campaign_id=campaign.campaign_id,
            campaign_cycle=1,
            source_config=source_config,
            current_trajectory=None,
            task_id=None,
            progress_callback=lambda _stage, message: events.append(message),
            dataset_builder=counted_builder,
        )
    )
    second, second_path, second_reused = (
        _load_or_build_campaign_dataset(
            store=store,
            campaign_id=campaign.campaign_id,
            campaign_cycle=1,
            source_config=source_config,
            current_trajectory=None,
            task_id=None,
            progress_callback=lambda _stage, message: events.append(message),
            dataset_builder=counted_builder,
        )
    )

    assert first == second
    assert snapshot_path == second_path
    assert first_reused is False
    assert second_reused is True
    assert build_count == 1
    assert events.count("Loading self-evolve trajectory source") == 1
    assert events.count("Loading frozen campaign dataset snapshot") == 1
    assert snapshot_path is not None
    manifest = load_campaign_dataset_snapshot_manifest(
        snapshot_path,
        expected_campaign_id=campaign.campaign_id,
        expected_campaign_source_fingerprint=campaign.source_fingerprint,
    )
    assert manifest["case_count"] == len(first.cases)
    recipe_ref = first.recipe.source["campaign_dataset_snapshot"]
    assert recipe_ref["snapshot_fingerprint"] == manifest["snapshot_fingerprint"]


def test_campaign_dataset_snapshot_manifest_does_not_embed_case_payloads(
    tmp_path: Path,
) -> None:
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(
            kind="trajectory_log",
            path=str(_trajectory_fixture()),
            max_cases=1,
        )
    )
    snapshot_path = tmp_path / "dataset_snapshot"
    write_campaign_dataset_snapshot(
        snapshot_path,
        dataset,
        campaign_id="campaign-manifest",
        campaign_source_fingerprint="sha256:" + "c" * 64,
    )

    manifest_payload = json.loads(
        (snapshot_path / "manifest.json").read_text(encoding="utf-8")
    )

    assert "cases" not in manifest_payload
    assert manifest_payload["storage_layout"] == "jsonl_case_stream"


def test_bounded_campaign_reuses_snapshot_across_real_cycle_checkpoints(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_path = _trajectory_fixture()
    original_builder = build_dataset_from_source
    build_count = 0
    reused: list[bool] = []

    def counted_builder(*args, **kwargs):
        nonlocal build_count
        build_count += 1
        return original_builder(*args, **kwargs)


    def run_once(**request):
        cycle = request["campaign_cycle"]
        run_id = f"{request['campaign_id']}-cycle-{cycle:03d}"
        _, _, snapshot_reused = _load_or_build_campaign_dataset(
            store=FilesystemSelfEvolveStore(tmp_path),
            campaign_id=request["campaign_id"],
            campaign_cycle=cycle,
            source_config=SelfEvolveEvalSourceConfig(
                kind="trajectory_log",
                path=request["from_trajectory"],
            ),
            current_trajectory=None,
            task_id=None,
            progress_callback=None,
            dataset_builder=counted_builder,
        )
        reused.append(snapshot_reused)
        report = {
            "run_id": run_id,
            "status": "rejected",
            "target": {
                "target_type": "skill",
                "target_id": "demo",
                "path": "/tmp/demo/SKILL.md",
            },
            "budget": {
                "ledger": {
                    "spent_by_stage": {
                        "candidate_generation": {
                            "tokens": 1,
                            "cost_usd": "0",
                            "wall_seconds": "0",
                        }
                    }
                }
            },
            "gate_results": [
                {
                    "gate_name": "candidate_repair_conformance",
                    "passed": False,
                    "details": {
                        "causal_failure_events": [
                            {
                                "code": "schema_field_validation_failed",
                                "owner": "candidate",
                                "stage": "capability_compile",
                                "scope": "candidate",
                                "repairable": True,
                                "category": "schema",
                                "schema_field_constraints": [
                                    {
                                        "schema_layer": "compile_result",
                                        "field_path": f"services[*].cycle_{cycle}",
                                        "rule": "required",
                                        "expected": True,
                                    }
                                ],
                            }
                        ]
                    },
                }
            ],
        }
        report_path = (
            tmp_path
            / ".aworld"
            / "self_evolve"
            / run_id
            / "report.json"
        )
        report_path.parent.mkdir(parents=True)
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return {
            "run_id": run_id,
            "status": "rejected",
            "report_path": str(report_path),
        }

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={
            "apply_policy": "verified_only",
            "from_trajectory": str(source_path),
            "target": "skill:demo",
        },
        max_improvement_cycles=2,
        run_once=run_once,
    )

    assert reused == [False, True]
    assert build_count == 1
    assert result["campaign_cycle"] == 2


def test_frozen_snapshot_decouples_later_cycles_from_raw_source_changes(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "trajectory.log"
    source_path.write_text(
        _trajectory_fixture().read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    controller = SelfImprovementCampaignController(workspace_root=tmp_path)
    campaign = controller.create(
        {
            "apply_policy": "verified_only",
            "from_trajectory": str(source_path),
            "target": "skill:demo",
        }
    )
    store = FilesystemSelfEvolveStore(tmp_path)
    first, _, _ = _load_or_build_campaign_dataset(
        store=store,
        campaign_id=campaign.campaign_id,
        campaign_cycle=1,
        source_config=SelfEvolveEvalSourceConfig(
            kind="trajectory_log",
            path=str(source_path),
        ),
        current_trajectory=None,
        task_id=None,
        progress_callback=None,
    )
    source_path.write_text("changed after snapshot publication\n", encoding="utf-8")

    reloaded_campaign = controller.load(campaign.campaign_id)
    second, _, reused = _load_or_build_campaign_dataset(
        store=store,
        campaign_id=reloaded_campaign.campaign_id,
        campaign_cycle=1,
        source_config=SelfEvolveEvalSourceConfig(
            kind="trajectory_log",
            path=str(source_path),
        ),
        current_trajectory=None,
        task_id=None,
        progress_callback=None,
    )

    assert reused is True
    assert second == first
