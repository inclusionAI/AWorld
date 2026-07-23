from __future__ import annotations

import json
from pathlib import Path

import pytest

from aworld.self_evolve.campaign import (
    CampaignUsage,
    SelfImprovementCampaignController,
    SelfImprovementCampaignStatus,
    SelfImprovementDispositionKind,
    derive_self_improvement_disposition,
    run_self_improvement_campaign,
    self_improvement_progress,
)
from aworld.self_evolve.sanitization import public_diagnostic_projection


def _budget(tokens: int = 10) -> dict:
    return {
        "ledger": {
            "spent_by_stage": {
                "candidate_generation": {
                    "tokens": tokens,
                    "cost_usd": "0.01",
                    "wall_seconds": "1",
                }
            }
        }
    }


def _event(
    *,
    code: str = "schema_field_validation_failed",
    owner: str = "candidate",
    scope: str = "candidate",
    repairable: bool = True,
    constraint: str = "payload.items[*].kind",
) -> dict:
    return {
        "code": code,
        "owner": owner,
        "stage": "capability_compile",
        "scope": scope,
        "repairable": repairable,
        "category": "schema",
        "schema_field_constraints": [
            {
                "schema_layer": "compile_result",
                "field_path": constraint,
                "rule": "required",
                "expected": True,
            }
        ],
    }


def _report(*events: dict, status: str = "rejected", tokens: int = 10) -> dict:
    return {
        "run_id": "synthetic",
        "status": status,
        "budget": _budget(tokens),
        "gate_results": [
            {
                "gate_name": "candidate_repair_conformance",
                "passed": False,
                "details": {"causal_failure_events": list(events)},
            }
        ],
    }


@pytest.mark.parametrize("member_count", [1, 3])
def test_disposition_is_cardinality_neutral(member_count: int) -> None:
    disposition = derive_self_improvement_disposition(
        _report(*(_event() for _ in range(member_count)))
    )

    assert disposition.kind is SelfImprovementDispositionKind.CONTINUE_CANDIDATE
    assert disposition.owner == "candidate"
    assert len(disposition.progress_delta_ids) == 2


def test_disposition_keeps_distinct_member_constraints() -> None:
    disposition = derive_self_improvement_disposition(
        _report(
            _event(constraint="payload.items[*].kind"),
            _event(constraint="payload.items[*].transport"),
        )
    )

    assert disposition.kind is SelfImprovementDispositionKind.CONTINUE_CANDIDATE
    constraint_deltas = [
        item for item in disposition.progress_delta_ids if item.startswith("constraint-")
    ]
    assert len(constraint_deltas) == 2


def test_progress_ranks_typed_lifecycle_stages() -> None:
    compile_progress = self_improvement_progress(_report(_event()))
    replay_event = _event()
    replay_event["stage"] = "task_rollout"
    replay_progress = self_improvement_progress(_report(replay_event))

    assert compile_progress.deepest_stage_rank == 3
    assert replay_progress.deepest_stage_rank == 5


def test_recovery_trace_advances_campaign_frontier_without_new_failure_code() -> None:
    identity = "sha256:" + "a" * 64
    first_report = _report(_event())
    first_report["gate_results"][0]["details"]["recovery_trace"] = {
        "schema_version": "aworld.self_evolve.recovery_trace.public.v1",
        "member_count": 2,
        "candidate_success_rate": 1 / 6,
        "recovered_member_count": 1,
        "members": [
            {
                "member_identity": identity,
                "classification": "partial_recovery",
                "candidate_repetition_count": 3,
                "candidate_success_rate": 1 / 3,
            }
        ],
    }
    second_report = _report(_event())
    second_report["gate_results"][0]["details"]["recovery_trace"] = {
        "schema_version": "aworld.self_evolve.recovery_trace.public.v1",
        "member_count": 2,
        "candidate_success_rate": 1 / 3,
        "recovered_member_count": 1,
        "members": [
            {
                "member_identity": identity,
                "classification": "partial_recovery",
                "candidate_repetition_count": 3,
                "candidate_success_rate": 2 / 3,
            }
        ],
    }

    previous = self_improvement_progress(first_report)
    disposition = derive_self_improvement_disposition(
        second_report,
        previous_progress=previous,
    )

    assert disposition.kind is SelfImprovementDispositionKind.CONTINUE_CANDIDATE
    assert any(
        item.endswith("success-2") for item in disposition.progress_delta_ids
    )


def test_disposition_ignores_bounded_projection_placeholders_as_progress() -> None:
    event = _event()
    event["schema_field_constraints"] = [
        {
            "kind": "bounded_public_summary",
            "constraint_count": 3,
        }
    ]

    disposition = derive_self_improvement_disposition(_report(event))

    assert len(disposition.progress_delta_ids) == 1
    assert not any(
        item.startswith("constraint-")
        for item in disposition.progress_delta_ids
    )


def test_disposition_observes_constraint_in_nested_public_repair_contract() -> None:
    event = _event()
    event.pop("schema_field_constraints")
    event["details"] = {
        "repair_conformance": {
            "projection_schema_version": (
                "aworld.self_evolve.repair_conformance.public.v1"
            ),
            "focus_candidate_id": "candidate-parent",
            "schema_field_constraints": [
                {
                    "schema_layer": "compile_result",
                    "field_path": "services[*].transport",
                    "rule": "enum",
                    "expected": ["http_fixture", "skill_runtime"],
                }
            ],
        }
    }
    projected = public_diagnostic_projection(_report(event))

    disposition = derive_self_improvement_disposition(projected)

    assert any(
        item.startswith("constraint-")
        for item in disposition.progress_delta_ids
    )


def test_disposition_routes_framework_and_infrastructure_separately() -> None:
    framework = derive_self_improvement_disposition(
        _report(_event(owner="framework", repairable=False))
    )
    infrastructure = derive_self_improvement_disposition(
        _report(_event(owner="infrastructure", scope="shared_run", repairable=True))
    )

    assert framework.kind is SelfImprovementDispositionKind.HANDOFF_GOAL
    assert infrastructure.kind is SelfImprovementDispositionKind.RETRY_INFRASTRUCTURE


def test_candidate_progress_precedes_concurrent_infrastructure_retry() -> None:
    disposition = derive_self_improvement_disposition(
        _report(
            _event(owner="infrastructure", scope="shared_run"),
            _event(owner="candidate", constraint="payload.items[*].transport"),
        )
    )

    assert disposition.kind is SelfImprovementDispositionKind.CONTINUE_CANDIDATE


def test_non_repairable_candidate_failure_exhausts() -> None:
    disposition = derive_self_improvement_disposition(
        _report(_event(owner="candidate", repairable=False))
    )

    assert disposition.kind is SelfImprovementDispositionKind.EXHAUSTED
    assert disposition.reason_code == "candidate_failure_not_repairable"


def test_campaign_store_round_trip_and_rejects_invalid_cycle(tmp_path: Path) -> None:
    controller = SelfImprovementCampaignController(workspace_root=tmp_path)
    campaign = controller.create(
        {
            "from_trajectory": "trajectory.log",
            "apply_policy": "auto_verified",
            "infer_target": True,
        },
        max_cycles=3,
    )

    assert controller.load(campaign.campaign_id) == campaign
    payload = campaign.to_dict()
    payload["cycle_index"] = 4
    path = controller.store.campaign_path(campaign.campaign_id) / "campaign.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="cycle index"):
        controller.load(campaign.campaign_id)


def test_campaign_store_rejects_missing_referenced_run(tmp_path: Path) -> None:
    controller = SelfImprovementCampaignController(workspace_root=tmp_path)
    campaign = controller.create(
        {
            "from_trajectory": "trajectory.log",
            "apply_policy": "auto_verified",
            "infer_target": True,
        }
    )
    payload = campaign.to_dict()
    payload.update(
        {
            "cycle_index": 1,
            "run_ids": [f"{campaign.campaign_id}-cycle-001"],
        }
    )
    path = controller.store.campaign_path(campaign.campaign_id) / "campaign.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="references missing run"):
        controller.load(campaign.campaign_id)


def test_campaign_resume_rejects_changed_source_contents(tmp_path: Path) -> None:
    trajectory = tmp_path / "trajectory.log"
    trajectory.write_text("original trajectory\n", encoding="utf-8")
    controller = SelfImprovementCampaignController(workspace_root=tmp_path)
    campaign = controller.create(
        {
            "from_trajectory": str(trajectory),
            "apply_policy": "auto_verified",
            "infer_target": True,
        }
    )

    trajectory.write_text("changed trajectory\n", encoding="utf-8")

    with pytest.raises(ValueError, match="source changed"):
        controller.load(campaign.campaign_id)


def test_campaign_repairs_then_completes_without_operator_relaunch(tmp_path: Path) -> None:
    calls: list[dict] = []

    def run_once(**request):
        calls.append(request)
        run_id = f"{request['campaign_id']}-cycle-{request['campaign_cycle']:03d}"
        report = (
            _report(_event())
            if request["campaign_cycle"] == 1
            else {
                "run_id": run_id,
                "status": "succeeded",
                "budget": _budget(),
                "gate_results": [{"gate_name": "post_apply", "passed": True}],
            }
        )
        report["run_id"] = run_id
        report["target"] = {
            "target_type": "skill",
            "target_id": "generic",
            "path": f"draft/{request['campaign_cycle']}/SKILL.md",
        }
        report_path = tmp_path / ".aworld" / "self_evolve" / run_id / "report.json"
        report_path.parent.mkdir(parents=True)
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return {"run_id": run_id, "status": report["status"], "report_path": str(report_path)}

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={
            "from_trajectory": "trajectory.log",
            "apply_policy": "auto_verified",
            "infer_target": True,
            "max_run_tokens": 1000,
        },
        max_improvement_cycles=3,
        run_once=run_once,
    )

    assert result["campaign_status"] == "complete"
    assert result["status"] == "succeeded"
    assert len(calls) == 2
    assert calls[1]["campaign_prior_run_ids"] == (calls[0]["campaign_id"] + "-cycle-001",)
    assert calls[1]["campaign_expected_target"] == {
        "target_type": "skill",
        "target_id": "generic",
    }
    assert calls[1]["total_run_token_budget"] == 990


def test_campaign_implicit_default_budget_is_available_per_cycle(
    tmp_path: Path,
) -> None:
    calls: list[dict] = []

    def run_once(**request):
        calls.append(request)
        run_id = f"{request['campaign_id']}-cycle-{request['campaign_cycle']:03d}"
        report = (
            _report(_event(), tokens=10)
            if request["campaign_cycle"] == 1
            else {
                "run_id": run_id,
                "status": "succeeded",
                "budget": _budget(10),
                "gate_results": [{"gate_name": "post_apply", "passed": True}],
            }
        )
        report["run_id"] = run_id
        report_path = tmp_path / ".aworld" / "self_evolve" / run_id / "report.json"
        report_path.parent.mkdir(parents=True)
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return {
            "run_id": run_id,
            "status": report["status"],
            "report_path": str(report_path),
        }

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={
            "from_trajectory": "trajectory.log",
            "apply_policy": "auto_verified",
            "infer_target": True,
        },
        max_improvement_cycles=3,
        run_once=run_once,
    )

    assert result["status"] == "succeeded"
    assert [call["total_run_token_budget"] for call in calls] == [500_000, 500_000]


def test_campaign_stops_when_semantic_frontier_does_not_change(tmp_path: Path) -> None:
    calls: list[dict] = []

    def run_once(**request):
        calls.append(request)
        run_id = f"{request['campaign_id']}-cycle-{request['campaign_cycle']:03d}"
        report = _report(_event())
        report["run_id"] = run_id
        report_path = tmp_path / ".aworld" / "self_evolve" / run_id / "report.json"
        report_path.parent.mkdir(parents=True)
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return {"run_id": run_id, "status": "rejected", "report_path": str(report_path)}

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={
            "from_trajectory": "trajectory.log",
            "apply_policy": "auto_verified",
            "infer_target": True,
        },
        max_improvement_cycles=5,
        run_once=run_once,
    )

    assert result["campaign_status"] == "exhausted"
    assert result["self_improvement_disposition"]["reason_code"] == (
        "candidate_repair_frontier_stalled"
    )
    assert len(calls) == 2


def test_campaign_missing_usage_telemetry_stops_before_second_run(tmp_path: Path) -> None:
    calls: list[dict] = []

    def run_once(**request):
        calls.append(request)
        run_id = f"{request['campaign_id']}-cycle-{request['campaign_cycle']:03d}"
        report = _report(_event())
        report["run_id"] = run_id
        report.pop("budget")
        report_path = tmp_path / ".aworld" / "self_evolve" / run_id / "report.json"
        report_path.parent.mkdir(parents=True)
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return {"run_id": run_id, "status": "rejected", "report_path": str(report_path)}

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={
            "from_trajectory": "trajectory.log",
            "apply_policy": "auto_verified",
            "infer_target": True,
        },
        max_improvement_cycles=3,
        run_once=run_once,
    )

    assert len(calls) == 1
    assert result["campaign_status"] == "budget_limited"
    assert result["self_improvement_disposition"]["reason_code"] == (
        "campaign_usage_telemetry_missing"
    )


def test_campaign_recovers_completed_run_after_checkpoint_interruption(
    tmp_path: Path,
) -> None:
    calls = 0

    def interrupted_run(**request):
        nonlocal calls
        calls += 1
        run_id = f"{request['campaign_id']}-cycle-{request['campaign_cycle']:03d}"
        report = _report(_event())
        report["run_id"] = run_id
        report_path = tmp_path / ".aworld" / "self_evolve" / run_id / "report.json"
        report_path.parent.mkdir(parents=True)
        report_path.write_text(json.dumps(report), encoding="utf-8")
        raise RuntimeError("simulated interruption after durable run report")

    controller = SelfImprovementCampaignController(
        workspace_root=tmp_path,
        run_once=interrupted_run,
    )
    campaign = controller.create(
        {
            "from_trajectory": "trajectory.log",
            "apply_policy": "auto_verified",
            "infer_target": True,
        }
    )
    with pytest.raises(RuntimeError, match="simulated interruption"):
        controller.advance_once(campaign)

    recovered, summary = controller.advance_once(controller.load(campaign.campaign_id))

    assert calls == 1
    assert recovered.cycle_index == 1
    assert summary["run_id"].endswith("cycle-001")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("from_trajectory", "replacement.log"),
        ("target", "skill:replacement"),
        ("from_run", "old-run"),
        ("rerun_evaluator", True),
    ],
)
def test_campaign_resume_rejects_contract_replacement(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    controller = SelfImprovementCampaignController(workspace_root=tmp_path)
    campaign = controller.create(
        {
            "from_trajectory": "trajectory.log",
            "apply_policy": "auto_verified",
            "infer_target": True,
        }
    )

    with pytest.raises(ValueError, match="persisted source/target contract"):
        run_self_improvement_campaign(
            workspace_root=tmp_path,
            request={field: value},
            resume_campaign=campaign.campaign_id,
        )


def test_campaign_usage_is_typed_and_additive() -> None:
    usage = CampaignUsage(tokens=3, cost_usd="0.1", wall_seconds="2")
    combined = usage + CampaignUsage(tokens=4, cost_usd="0.2", wall_seconds="3")

    assert combined.to_dict() == {
        "tokens": 7,
        "cost_usd": "0.3",
        "wall_seconds": "5",
    }
