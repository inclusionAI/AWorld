from __future__ import annotations

import hashlib
import json
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest
import aworld.self_evolve.campaign as campaign_module

from aworld.self_evolve.campaign import (
    CampaignMeasurementLedgerV2,
    CampaignUsage,
    SelfImprovementCampaignController,
    SelfImprovementCampaignStatus,
    SelfImprovementDisposition,
    SelfImprovementDispositionKind,
    derive_self_improvement_disposition,
    run_self_improvement_campaign,
    self_improvement_progress,
)
from aworld.self_evolve.candidate_package import candidate_package_fingerprint
from aworld.self_evolve.sanitization import public_diagnostic_projection
from aworld.self_evolve.types import CandidateVariant, SelfEvolveTargetRef


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


def _write_paired_replay_timeout_artifacts(
    controller: SelfImprovementCampaignController,
    *,
    run_id: str,
    candidate: CandidateVariant,
) -> str:
    fingerprint = candidate_package_fingerprint(candidate)
    controller.store.write_candidate(run_id, candidate)
    replay_dir = (
        controller.store.run_path(run_id) / "replay" / candidate.candidate_id
    )
    members_dir = replay_dir / "members"
    members_dir.mkdir(parents=True)
    (replay_dir / "request.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "candidate_id": candidate.candidate_id,
                "verified_candidate_package_fingerprint": fingerprint,
                "measurement_plan": None,
                "repetition_semantics": "per_member_v3",
                "replay_adaptation": {
                    "cases": [
                        {"case_id": "case-complete"},
                        {"case_id": "case-pending"},
                    ]
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (members_dir / "paired_replay_checkpoint.json").write_text(
        json.dumps(
            {
                "schema_version": (
                    "aworld.self_evolve.paired_replay_checkpoint.v1"
                ),
                "schedule": "progressive_paired",
                "resume_safe": True,
                "pending_case_ids": ["case-pending"],
                "comparable_pair_case_ids": ["case-complete"],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return fingerprint


@pytest.mark.parametrize("member_count", [1, 3])
def test_disposition_is_cardinality_neutral(member_count: int) -> None:
    disposition = derive_self_improvement_disposition(
        _report(*(_event() for _ in range(member_count)))
    )

    assert disposition.kind is SelfImprovementDispositionKind.CONTINUE_CANDIDATE
    assert disposition.owner == "candidate"
    assert len(disposition.progress_delta_ids) == 2


def test_safe_paired_replay_timeout_is_collect_more_evidence() -> None:
    report = {
        "status": "rejected",
        "rejection_attribution": {
            "code": "replay_total_timeout",
            "failure_class": "measurement",
            "failure_owner": "framework",
            "failure_scope": "shared_run",
            "failure_stage": "evaluation",
            "repairable": True,
            "resume_safe": True,
            "next_action": "continue_measurement",
            "resume_candidate_id": "candidate-paired",
            "resume_candidate_package_fingerprint": "sha256:" + "a" * 64,
        },
    }

    disposition = derive_self_improvement_disposition(report)

    assert disposition.kind is SelfImprovementDispositionKind.COLLECT_MORE_EVIDENCE
    assert disposition.reason_code == "replay_total_timeout"
    assert disposition.continuable is True


def test_persisted_framework_member_timeout_is_measurement_retry() -> None:
    report = {
        "status": "rejected",
        "candidate_ids": ["candidate-paired"],
        "paired_replay_resume_checkpoint": {"stage": "paired_replay"},
        "rejection_attribution": {
            "code": "replay_member_phase_timeout",
            "failure_class": "framework",
            "failure_owner": "framework",
            "failure_scope": "member",
            "repairable": True,
        },
    }

    disposition = derive_self_improvement_disposition(report)

    assert disposition.kind is SelfImprovementDispositionKind.REPAIR_MEASUREMENT
    assert disposition.reason_code == "replay_member_phase_timeout"
    assert disposition.scope == "shared_run"


def test_resume_migrates_exhausted_safe_paired_replay_timeout(
    tmp_path: Path,
) -> None:
    controller = SelfImprovementCampaignController(workspace_root=tmp_path)
    campaign = controller.create(
        request={
            "task": "resume paired replay",
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
        },
        max_cycles=1,
    )
    run_id = f"{campaign.campaign_id}-cycle-001"
    candidate = CandidateVariant(
        candidate_id="candidate-paired-replay",
        target=SelfEvolveTargetRef("skill", "demo", "/skills/demo/SKILL.md"),
        content="# Demo\n\nImproved.\n",
        rationale="resume paired replay",
    )
    fingerprint = _write_paired_replay_timeout_artifacts(
        controller,
        run_id=run_id,
        candidate=candidate,
    )
    report = {
        "run_id": run_id,
        "status": "rejected",
        "budget": _budget(),
        "candidate_ids": [candidate.candidate_id],
        "selected_candidate_id": candidate.candidate_id,
        "rejection_attribution": {
            "code": "replay_total_timeout",
            "failure_class": "measurement",
            "failure_owner": "framework",
            "failure_scope": "shared_run",
            "failure_stage": "evaluation",
            "repairable": True,
            "resume_safe": True,
            "next_action": "continue_measurement",
            "resume_candidate_id": candidate.candidate_id,
            "resume_candidate_package_fingerprint": fingerprint,
        },
        "campaign": {},
    }
    report_path = controller.store.write_report(run_id, report)
    exhausted = campaign_module.replace(
        campaign,
        status=SelfImprovementCampaignStatus.EXHAUSTED,
        cycle_index=1,
        run_ids=(run_id,),
        latest_disposition=SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.EXHAUSTED,
            reason_code="measurement_authority_checkpoint_missing_or_invalid",
            owner="evaluation_harness",
            stage="evaluation",
            scope="shared_run",
        ),
        latest_report_path=str(report_path),
    )
    controller.store.write_campaign(exhausted)

    migrated = campaign_module._migrate_paired_replay_timeout_for_resume(
        controller,
        exhausted,
    )

    assert migrated.status is SelfImprovementCampaignStatus.ACTIVE
    assert migrated.measurement_pending_run_id == run_id
    assert migrated.measurement_pending_candidate_id == candidate.candidate_id
    assert migrated.measurement_continuation_count == 1
    assert migrated.latest_disposition is not None
    assert migrated.latest_disposition.kind is (
        SelfImprovementDispositionKind.COLLECT_MORE_EVIDENCE
    )
    migrated_report = controller.store.read_report(run_id)
    assert migrated_report["paired_replay_resume_checkpoint"]["stage"] == (
        "paired_replay"
    )
    assert migrated_report["campaign_causal_migration"]["action"] == (
        "restore_paired_replay_timeout_checkpoint"
    )


def test_resume_migrates_paired_replay_member_timeout_handoff(
    tmp_path: Path,
) -> None:
    controller = SelfImprovementCampaignController(workspace_root=tmp_path)
    campaign = controller.create(
        request={
            "task": "resume member timeout",
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
        },
        max_cycles=1,
    )
    run_id = f"{campaign.campaign_id}-cycle-001"
    candidate = CandidateVariant(
        candidate_id="candidate-member-timeout",
        target=SelfEvolveTargetRef("skill", "demo", "/skills/demo/SKILL.md"),
        content="# Demo\n\nImproved.\n",
        rationale="resume member timeout",
    )
    _write_paired_replay_timeout_artifacts(
        controller,
        run_id=run_id,
        candidate=candidate,
    )
    report = {
        "run_id": run_id,
        "status": "rejected",
        "budget": _budget(),
        "candidate_ids": [candidate.candidate_id],
        "verification_funnel": {
            "authoritative_candidate_count": 1,
            "authoritative_candidate_ids": [candidate.candidate_id],
        },
        "rejection_attribution": {
            "code": "replay_member_phase_timeout",
            "failure_class": "framework",
            "failure_owner": "framework",
            "failure_scope": "member",
            "failure_stage": "evaluation",
            "repairable": True,
        },
        "campaign": {},
    }
    report_path = controller.store.write_report(run_id, report)
    paused = campaign_module.replace(
        campaign,
        status=SelfImprovementCampaignStatus.PAUSED,
        cycle_index=1,
        run_ids=(run_id,),
        cumulative_authoritative_candidates=1,
        latest_disposition=SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.HANDOFF_GOAL,
            reason_code="typed_framework_or_shared_blocker",
            owner="framework",
            stage="evaluation",
            scope="member",
            repairable=True,
        ),
        latest_report_path=str(report_path),
    )
    controller.store.write_campaign(paused)

    migrated = (
        campaign_module._migrate_paired_replay_member_timeout_for_resume(
            controller,
            paused,
        )
    )

    assert migrated.status is SelfImprovementCampaignStatus.ACTIVE
    assert migrated.measurement_pending_run_id == run_id
    assert migrated.measurement_pending_candidate_id == candidate.candidate_id
    assert migrated.measurement_retry_count == 1
    assert migrated.cumulative_authoritative_candidates == 1
    assert campaign_module._campaign_pending_candidate_was_authoritative(
        controller.store,
        campaign=migrated,
    )
    assert migrated.latest_disposition is not None
    assert migrated.latest_disposition.kind is (
        SelfImprovementDispositionKind.REPAIR_MEASUREMENT
    )
    migrated_report = controller.store.read_report(run_id)
    assert migrated_report["paired_replay_resume_checkpoint"]["stage"] == (
        "paired_replay"
    )
    assert migrated_report["campaign_causal_migration"]["action"] == (
        "restore_paired_replay_member_timeout_checkpoint"
    )


def test_screening_intervention_failure_does_not_request_measurement_checkpoint() -> None:
    event = {
        "code": "candidate_intervention_unobserved",
        "owner": "framework",
        "stage": "adaptation",
        "scope": "shared_run",
        "repairable": True,
    }
    report = {
        "run_id": "screening-intervention-unobserved",
        "status": "rejected",
        "budget": _budget(),
        "gate_results": [
            {
                "gate_name": "candidate_replay",
                "passed": False,
                "details": {
                    "code": event["code"],
                    "failure_class": "framework",
                    "failure_owner": "framework",
                    "failure_scope": "shared_run",
                    "failure_stage": "adaptation",
                    "repairable": True,
                    "checkpoint_stage": "screening",
                    "causal_failure_events": [event],
                },
            },
            {
                "gate_name": "trusted_improvement_measurement",
                "passed": False,
                "details": {"failure_class": "measurement"},
            },
        ],
        "measurement": {
            "mode": "required",
            "status": "invalid",
            "validity_status": "invalid",
            "effect_direction": "unmeasured",
            "promotion_eligible": False,
            "next_action": "repair_measurement",
        },
        "campaign_failure_attribution": {
            "primary_gate": "candidate_replay",
            "code": event["code"],
            "failure_class": "framework",
            "failure_owner": "framework",
            "failure_scope": "shared_run",
            "failure_stage": "adaptation",
            "repairable": True,
        },
    }

    disposition = derive_self_improvement_disposition(report)

    assert disposition.kind is SelfImprovementDispositionKind.HANDOFF_GOAL
    assert disposition.reason_code == "typed_framework_or_shared_blocker"
    assert disposition.owner == "framework"


def test_candidate_prerequisite_failure_precedes_derived_measurement_gate() -> None:
    event = _event(constraint="services[*].readiness.kind")
    report = _report(event)
    report["measurement"] = {
        "mode": "required",
        "promotion_eligible": False,
        "next_action": "repair_measurement",
        "status": "invalid",
        "validity_status": "invalid",
    }
    report["gate_results"].append(
        {
            "gate_name": "trusted_improvement_measurement",
            "passed": False,
            "details": {
                "failure_class": "measurement",
                "next_action": "repair_measurement",
            },
        }
    )

    disposition = derive_self_improvement_disposition(report)

    assert disposition.kind is SelfImprovementDispositionKind.CONTINUE_CANDIDATE
    assert disposition.owner == "candidate"
    assert disposition.stage == "capability_compile"
    assert disposition.reason_code == "candidate_repair_frontier_progressed"


def test_focused_budget_denial_takes_precedence_over_candidate_stall() -> None:
    report = _report(_event())
    report["rejection_attribution"] = {
        "failure_class": "candidate",
        "scheduler_reason_code": "focused_budget_denied",
    }

    disposition = derive_self_improvement_disposition(report)

    assert disposition.kind is SelfImprovementDispositionKind.CONTINUE_CAMPAIGN
    assert disposition.reason_code == "cycle_focused_budget_denied"
    assert campaign_module._status_for_disposition(disposition) is (
        SelfImprovementCampaignStatus.ACTIVE
    )


def test_terminal_scheduler_stall_does_not_override_substantive_candidate_gate() -> None:
    report = _report(_event())
    report["rejection_attribution"] = {
        "failure_class": "candidate",
        "primary_gate": "target_behavior_delta",
        "scheduler_reason_code": "repair_frontier_stalled",
        "scheduler_stop": True,
    }

    disposition = derive_self_improvement_disposition(report)

    assert disposition.kind is SelfImprovementDispositionKind.CONTINUE_CANDIDATE
    assert disposition.reason_code == "candidate_repair_frontier_progressed"
    assert disposition.owner == "candidate"
    assert disposition.stage == "capability_compile"
    assert disposition.repairable is True


def test_evaluation_support_prerequisite_requests_composition_followup() -> None:
    event = _event(code="evaluation_support_bootstrap_only")
    event["stage"] = "candidate_generation"
    report = _report(event)
    report["rejection_attribution"] = {
        "failure_class": "candidate",
        "primary_gate": "target_behavior_delta",
        "code": "evaluation_support_bootstrap_only",
        "scheduler_reason_code": "repair_frontier_stalled",
        "scheduler_stop": True,
    }

    disposition = derive_self_improvement_disposition(report)

    assert disposition.kind is SelfImprovementDispositionKind.CONTINUE_CANDIDATE
    assert disposition.reason_code == "evaluation_support_composition_required"
    assert disposition.stage == "candidate_generation"


def test_framework_score_uncertainty_precedes_candidate_scheduler_stall() -> None:
    event = _event(
        code="score_improvement_inconclusive",
        owner="framework",
        scope="shared_run",
        repairable=False,
    )
    event["stage"] = "score_improvement"
    report = _report(event)
    report["rejection_attribution"] = {
        "code": "score_improvement_inconclusive",
        "failure_class": "framework",
        "primary_gate": "score_improvement",
        "scheduler_reason_code": "repair_frontier_stalled",
        "scheduler_stop": True,
    }

    disposition = derive_self_improvement_disposition(report)

    assert disposition.kind is SelfImprovementDispositionKind.HANDOFF_GOAL
    assert disposition.reason_code == "typed_framework_or_shared_blocker"
    assert disposition.owner == "framework"
    assert disposition.stage == "score_improvement"


def test_framework_handoff_preserves_campaign_candidate_cycle(
    tmp_path: Path,
) -> None:
    def run_once(**request):
        run_id = f"{request['campaign_id']}-cycle-{request['campaign_cycle']:03d}"
        event = _event(
            code="repair_contract_owner_inconsistent",
            owner="framework",
            scope="shared_run",
            repairable=False,
        )
        report = _report(event)
        report["run_id"] = run_id
        report_path = (
            tmp_path / ".aworld" / "self_evolve" / run_id / "report.json"
        )
        report_path.parent.mkdir(parents=True)
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return {
            "run_id": run_id,
            "status": "rejected",
            "report_path": str(report_path),
        }

    controller = SelfImprovementCampaignController(
        workspace_root=tmp_path,
        run_once=run_once,
    )
    campaign = controller.create(
        {
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
        },
        max_cycles=1,
    )

    advanced, summary = controller.advance_once(campaign)

    assert advanced.status is SelfImprovementCampaignStatus.PAUSED
    assert advanced.framework_blocked_count == 1
    assert advanced.measurement_ledger.control_plane_run_count == 1
    assert summary["campaign_candidate_cycle_count"] == 0
    assert summary["campaign_max_cycles"] == 2


def test_candidate_evidence_repair_precedes_simultaneous_score_uncertainty() -> None:
    score_event = _event(
        code="score_improvement_inconclusive",
        owner="framework",
        scope="shared_run",
        repairable=False,
    )
    score_event["stage"] = "evaluation"
    evidence_event = _event(
        code="evidence_quality",
        owner="candidate",
        scope="candidate",
        repairable=True,
    )
    evidence_event["stage"] = "evaluation"
    report = _report(score_event)
    report["gate_results"].append(
        {
            "gate_name": "evidence_quality",
            "passed": False,
            "details": {
                "failure_event": evidence_event,
                "causal_failure_events": [evidence_event],
            },
        }
    )
    report["rejection_attribution"] = {
        "code": "score_improvement_inconclusive",
        "failure_class": "framework",
        "primary_gate": "score_improvement",
    }

    disposition = derive_self_improvement_disposition(report)

    assert disposition.kind is SelfImprovementDispositionKind.CONTINUE_CANDIDATE
    assert disposition.reason_code == "candidate_repair_frontier_progressed"
    assert disposition.owner == "candidate"


def test_zero_generation_scheduler_stall_is_not_a_legacy_pause() -> None:
    report = {
        "status": "rejected",
        "gate_results": [
            {
                "gate_name": "candidate_generation",
                "passed": False,
                "details": {
                    "generated_candidate_count": 0,
                    "iterations": 0,
                },
            }
        ],
        "rejection_attribution": {
            "failure_class": "candidate",
            "primary_gate": "candidate_generation",
            "scheduler_reason_code": "repair_frontier_stalled",
            "scheduler_stop": True,
        },
    }

    disposition = derive_self_improvement_disposition(report)

    assert disposition.kind is SelfImprovementDispositionKind.EXHAUSTED
    assert disposition.reason_code == "candidate_repair_frontier_stalled"
    assert disposition.stage == "candidate_generation"


def test_shared_scheduler_block_is_a_framework_goal_handoff() -> None:
    report = {
        "status": "rejected",
        "gate_results": [
            {
                "gate_name": "candidate_generation",
                "passed": False,
                "details": {
                    "generated_candidate_count": 0,
                    "iterations": 0,
                },
            }
        ],
        # Compatibility shape emitted before scheduler attribution overrode
        # the candidate-generation gate's default owner.
        "rejection_attribution": {
            "failure_class": "candidate",
            "primary_gate": "candidate_generation",
            "scheduler_reason_code": "shared_run_blocked",
            "scheduler_stop": True,
        },
    }

    disposition = derive_self_improvement_disposition(report)

    assert disposition.kind is SelfImprovementDispositionKind.HANDOFF_GOAL
    assert disposition.reason_code == "typed_framework_or_shared_blocker"
    assert disposition.owner == "framework"
    assert disposition.scope == "shared_run"


def test_campaign_continues_after_per_cycle_focused_budget_denial(
    tmp_path: Path,
) -> None:
    calls: list[dict] = []

    def run_once(**request):
        calls.append(request)
        run_id = f"{request['campaign_id']}-cycle-{request['campaign_cycle']:03d}"
        if request["campaign_cycle"] == 1:
            report = _report(_event(), tokens=100)
            report["rejection_attribution"] = {
                "failure_class": "candidate",
                "scheduler_reason_code": "focused_budget_denied",
            }
        else:
            report = {
                "run_id": run_id,
                "status": "succeeded",
                "budget": _budget(100),
                "gate_results": [{"gate_name": "post_apply", "passed": True}],
            }
        report["run_id"] = run_id
        report_path = tmp_path / ".aworld" / "self_evolve" / run_id / "report.json"
        report_path.parent.mkdir(parents=True)
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return {"run_id": run_id, "status": report["status"], "report_path": str(report_path)}

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
        },
        max_improvement_cycles=3,
        run_once=run_once,
    )

    assert result["status"] == "succeeded"
    assert len(calls) == 2


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


def test_new_repair_contract_continues_after_return_to_source_stage() -> None:
    prior_event = _event(constraint="services[*].protocol_probes[*].path")
    prior_event["stage"] = "task_rollout"
    previous = self_improvement_progress(_report(prior_event))
    current_report = _report(
        _event(
            constraint="environment.AWORLD_REPLAY_RESPONSE_INDEX.consumer"
        )
    )

    disposition = derive_self_improvement_disposition(
        current_report,
        previous_progress=previous,
    )

    assert disposition.kind is SelfImprovementDispositionKind.CONTINUE_CANDIDATE
    assert disposition.reason_code == "candidate_repair_frontier_progressed"
    assert any(
        item.startswith("constraint-")
        for item in disposition.progress_delta_ids
    )


def test_required_measurement_progress_is_separate_from_candidate_quality() -> None:
    report = _report(_event())
    report["candidate_metrics"] = {"score": 99.0}
    report["measurement"] = {
        "mode": "required",
        "measurement_readiness_stage": "first_comparable_pair",
        "independent_case_count": 1,
        "comparable_pair_count": 3,
        "validity_status": "valid_limited",
        "effect_direction": "inconclusive",
        "confidence_lower_bound": -0.1,
        "promotion_eligible": False,
        "next_action": "collect_more_evidence",
        "attribution_report_path": (
            "experiments/experiment-abc/attribution_report.json"
        ),
    }

    progress = self_improvement_progress(report)

    assert progress.candidate_quality is not None
    assert progress.measurement is not None
    assert progress.measurement.authoritative is True
    assert progress.measurement.readiness_rank == 8
    assert progress.measurement.comparable_pair_count == 3


def test_required_measurement_routes_more_evidence_before_candidate_repair() -> None:
    report = _report(_event(), status="rejected")
    report["measurement"] = {
        "mode": "required",
        "measurement_readiness_stage": "first_comparable_pair",
        "independent_case_count": 1,
        "comparable_pair_count": 1,
        "validity_status": "valid_limited",
        "effect_direction": "inconclusive",
        "confidence_lower_bound": None,
        "promotion_eligible": False,
        "next_action": "collect_more_evidence",
        "attribution_report_path": (
            "experiments/experiment-abc/attribution_report.json"
        ),
    }

    disposition = derive_self_improvement_disposition(report)

    assert disposition.kind is (
        SelfImprovementDispositionKind.COLLECT_MORE_EVIDENCE
    )
    assert disposition.owner == "evaluation_harness"
    assert disposition.continuable is True


def test_required_negative_effect_stops_even_when_raw_score_is_high() -> None:
    report = _report(_event(), status="rejected")
    report["candidate_metrics"] = {"score": 100.0}
    report["measurement"] = {
        "mode": "required",
        "measurement_readiness_stage": "minimum_independent_evidence",
        "independent_case_count": 30,
        "comparable_pair_count": 30,
        "validity_status": "valid",
        "effect_direction": "negative",
        "confidence_lower_bound": -0.2,
        "promotion_eligible": False,
        "next_action": "stop_negative_effect",
        "attribution_report_path": (
            "experiments/experiment-abc/attribution_report.json"
        ),
    }

    disposition = derive_self_improvement_disposition(report)

    assert disposition.kind is SelfImprovementDispositionKind.STOP_NEGATIVE_EFFECT
    assert disposition.continuable is False


def test_advisory_measurement_preserves_legacy_candidate_disposition() -> None:
    report = _report(_event(), status="rejected")
    report["measurement"] = {
        "mode": "advisory",
        "measurement_readiness_stage": "task_rollout",
        "independent_case_count": 0,
        "comparable_pair_count": 0,
        "validity_status": "invalid",
        "effect_direction": "unmeasured",
        "confidence_lower_bound": None,
        "promotion_eligible": False,
        "next_action": "repair_measurement",
    }

    disposition = derive_self_improvement_disposition(report)

    assert disposition.kind is SelfImprovementDispositionKind.CONTINUE_CANDIDATE
    assert disposition.owner == "candidate"


def test_advisory_measurement_cannot_override_success() -> None:
    report = _report(_event(), status="succeeded")
    report["measurement"] = {
        "mode": "advisory",
        "measurement_readiness_stage": "task_rollout",
        "independent_case_count": 0,
        "comparable_pair_count": 0,
        "validity_status": "invalid",
        "effect_direction": "unmeasured",
        "confidence_lower_bound": None,
        "promotion_eligible": False,
        "next_action": "repair_measurement",
    }

    disposition = derive_self_improvement_disposition(report)

    assert disposition.kind is SelfImprovementDispositionKind.COMPLETE


def test_shadow_measurement_does_not_override_candidate_disposition() -> None:
    report = _report(_event(), status="rejected")
    report["measurement"] = {
        "mode": "shadow",
        "measurement_readiness_stage": "task_rollout",
        "independent_case_count": 0,
        "comparable_pair_count": 0,
        "validity_status": "invalid",
        "effect_direction": "unmeasured",
        "confidence_lower_bound": None,
        "promotion_eligible": False,
        "next_action": "repair_measurement",
    }

    disposition = derive_self_improvement_disposition(report)

    assert disposition.kind is SelfImprovementDispositionKind.CONTINUE_CANDIDATE
    assert disposition.owner == "candidate"


def test_agent_browser_shaped_cycles_record_readiness_not_candidate_effect() -> None:
    first = _report(
        _event(code="capability_compile_incomplete"),
        status="rejected",
        tokens=200_000,
    )
    first["target_selection"] = {
        "selection_origin": "operator_explicit",
        "confidence": 1.0,
        "inference_bypassed": True,
        "causal_confidence": None,
    }
    first["measurement"] = {
        "mode": "advisory",
        "measurement_readiness_stage": "capability_compile",
        "independent_case_count": 0,
        "comparable_pair_count": 0,
        "validity_status": "invalid",
        "effect_direction": "unmeasured",
        "effect_estimate": None,
        "confidence_lower_bound": None,
        "promotion_eligible": False,
        "next_action": "repair_measurement",
    }
    second_event = _event(code="two_arm_task_timeout", owner="infrastructure")
    second_event["stage"] = "task_rollout"
    second = _report(second_event, status="rejected", tokens=265_993)
    second["target_selection"] = dict(first["target_selection"])
    second["measurement"] = {
        **first["measurement"],
        "measurement_readiness_stage": "task_rollout",
    }

    previous = self_improvement_progress(first)
    disposition = derive_self_improvement_disposition(
        second,
        previous_progress=previous,
    )

    assert disposition.kind is SelfImprovementDispositionKind.RETRY_INFRASTRUCTURE
    assert "measurement-readiness:6" in disposition.progress_delta_ids
    assert not any(
        item.startswith(("quality-score", "measurement-effect"))
        for item in disposition.progress_delta_ids
    )
    assert second["measurement"]["effect_estimate"] is None
    assert second["measurement"]["promotion_eligible"] is False


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


def test_new_failure_identity_alone_does_not_advance_campaign() -> None:
    first_event = {
        "code": "candidate_protocol_invalid",
        "owner": "candidate",
        "stage": "candidate_generation",
        "scope": "candidate",
        "repairable": True,
        "category": "candidate_generation",
    }
    second_event = {
        **first_event,
        "code": "candidate_materialization_invalid",
    }

    disposition = derive_self_improvement_disposition(
        _report(second_event),
        previous_progress=self_improvement_progress(_report(first_event)),
    )

    assert disposition.kind is SelfImprovementDispositionKind.EXHAUSTED
    assert disposition.progress_delta_ids == ()


def test_lost_passing_gate_blocks_apparent_constraint_progress() -> None:
    first = _report(_event(constraint="payload.items[*].kind"))
    first["gate_results"].append(
        {"gate_name": "global_regression_benchmark", "passed": True}
    )
    second = _report(_event(constraint="payload.items[*].transport"))

    disposition = derive_self_improvement_disposition(
        second,
        previous_progress=self_improvement_progress(first),
    )

    assert disposition.kind is SelfImprovementDispositionKind.EXHAUSTED
    assert disposition.progress_delta_ids == ()


def test_meaningful_quality_improvement_advances_campaign() -> None:
    first = _report(_event())
    first["candidate_metrics"] = {
        "score": 76.8,
        "A1_groundedness": 3.0,
        "command_pass_rate": 0.0,
        "evidence_incomplete": True,
        "deterministic_signal": False,
        "global_regression_passed": False,
        "failed_repetition_count": 0,
    }
    second = _report(_event())
    second["candidate_metrics"] = {
        "score": 82.066,
        "A1_groundedness": 3.333,
        "command_pass_rate": 0.0,
        "evidence_incomplete": True,
        "deterministic_signal": False,
        "global_regression_passed": False,
        "failed_repetition_count": 0,
    }

    disposition = derive_self_improvement_disposition(
        second,
        previous_progress=self_improvement_progress(first),
    )

    assert disposition.kind is SelfImprovementDispositionKind.CONTINUE_CANDIDATE
    assert "quality-score-points:82" in disposition.progress_delta_ids
    assert "quality-groundedness-tenths:33" in disposition.progress_delta_ids


def test_sub_bucket_judge_score_noise_does_not_advance_campaign() -> None:
    first = _report(_event())
    first["candidate_metrics"] = {
        "score": 82.1,
        "A1_groundedness": 3.3,
        "command_pass_rate": 0.0,
    }
    second = _report(_event())
    second["candidate_metrics"] = {
        "score": 82.9,
        "A1_groundedness": 3.3,
        "command_pass_rate": 0.0,
    }

    disposition = derive_self_improvement_disposition(
        second,
        previous_progress=self_improvement_progress(first),
    )

    assert disposition.kind is SelfImprovementDispositionKind.EXHAUSTED
    assert disposition.progress_delta_ids == ()


def test_score_gain_does_not_mask_verification_quality_regression() -> None:
    first = _report(_event())
    first["candidate_metrics"] = {
        "score": 80.0,
        "A1_groundedness": 3.5,
        "command_pass_rate": 0.5,
        "failed_repetition_count": 0,
    }
    second = _report(_event())
    second["candidate_metrics"] = {
        "score": 90.0,
        "A1_groundedness": 3.5,
        "command_pass_rate": 0.0,
        "failed_repetition_count": 0,
    }

    disposition = derive_self_improvement_disposition(
        second,
        previous_progress=self_improvement_progress(first),
    )

    assert disposition.kind is SelfImprovementDispositionKind.EXHAUSTED
    assert disposition.progress_delta_ids == ()


def test_quality_progress_round_trip_is_backward_compatible() -> None:
    progress = self_improvement_progress(
        {
            **_report(_event()),
            "candidate_metrics": {
                "score": 82.066,
                "A1_groundedness": 3.333,
                "command_pass_rate": 1.0,
                "evidence_incomplete": False,
                "deterministic_signal": True,
                "global_regression_passed": True,
                "failed_repetition_count": 0,
            },
        }
    )

    assert type(progress).from_dict(progress.to_dict()) == progress
    legacy = progress.to_dict()
    legacy.pop("candidate_quality")
    assert type(progress).from_dict(legacy).candidate_quality is None


def test_campaign_quality_uses_typed_regression_evidence_not_legacy_metric() -> None:
    report = {
        **_report(_event()),
        "candidate_metrics": {
            "score": 80.0,
            "global_regression_passed": True,
        },
        "regression_evidence": {"passed": False},
    }

    progress = self_improvement_progress(report)

    assert progress.candidate_quality is not None
    assert progress.candidate_quality.global_regression_passed is False
    report.pop("regression_evidence")
    legacy_progress = self_improvement_progress(report)
    assert legacy_progress.candidate_quality is not None
    assert legacy_progress.candidate_quality.global_regression_passed is None


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


def test_terminal_infrastructure_attribution_precedes_historical_candidate_event() -> None:
    report = _report(
        _event(owner="candidate", constraint="payload.items[*].transport"),
        _event(
            code="evaluation_runtime_unhealthy",
            owner="infrastructure",
            scope="shared_run",
            repairable=True,
        ),
    )
    report["rejection_attribution"] = {
        "code": "evaluation_runtime_unhealthy",
        "failure_class": "infrastructure",
        "primary_gate": "evaluation_runtime_health",
    }

    disposition = derive_self_improvement_disposition(report)

    assert disposition.kind is SelfImprovementDispositionKind.RETRY_INFRASTRUCTURE
    assert disposition.owner == "infrastructure"
    assert disposition.reason_code == "typed_infrastructure_failure"


def test_shared_measurement_attribution_precedes_historical_candidate_event() -> None:
    report = _report(
        _event(owner="candidate", constraint="payload.items[*].transport"),
    )
    report["campaign_failure_attribution"] = {
        "primary_gate": "candidate_replay",
        "code": "control_not_comparable",
        "failure_class": "measurement",
        "failure_owner": "framework",
        "failure_scope": "shared_run",
        "next_action": "repair_measurement",
        "diagnostic_refs": ["/tmp/replay/request.json"],
    }

    disposition = derive_self_improvement_disposition(report)

    assert disposition.kind is SelfImprovementDispositionKind.REPAIR_MEASUREMENT
    assert disposition.reason_code == "shared_measurement_control_invalid"
    assert disposition.owner == "evaluation_harness"
    assert disposition.scope == "shared_run"
    assert disposition.diagnostic_refs == ("/tmp/replay/request.json",)


def test_terminal_nonretryable_infrastructure_attribution_pauses_operator() -> None:
    report = _report(
        _event(owner="candidate", constraint="payload.items[*].transport"),
        _event(
            code="evaluation_runtime_unhealthy",
            owner="infrastructure",
            scope="shared_run",
            repairable=False,
        ),
    )
    report["rejection_attribution"] = {
        "code": "evaluation_runtime_unhealthy",
        "failure_class": "infrastructure",
        "primary_gate": "evaluation_runtime_health",
    }

    disposition = derive_self_improvement_disposition(report)

    assert disposition.kind is SelfImprovementDispositionKind.PAUSE_OPERATOR
    assert disposition.owner == "infrastructure"
    assert disposition.reason_code == (
        "typed_infrastructure_failure_not_retryable"
    )


def test_non_repairable_candidate_failure_exhausts() -> None:
    disposition = derive_self_improvement_disposition(
        _report(_event(owner="candidate", repairable=False))
    )

    assert disposition.kind is SelfImprovementDispositionKind.EXHAUSTED
    assert disposition.reason_code == "candidate_failure_not_repairable"


def test_candidate_materialization_failure_continues_typed_campaign() -> None:
    event = {
        "code": "candidate_materialization_invalid",
        "owner": "candidate",
        "stage": "candidate_generation",
        "scope": "candidate",
        "repairable": True,
        "category": "candidate_generation",
    }

    disposition = derive_self_improvement_disposition(_report(event))

    assert disposition.kind is SelfImprovementDispositionKind.CONTINUE_CANDIDATE
    assert disposition.reason_code == "candidate_repair_frontier_progressed"
    assert disposition.stage == "candidate_generation"


def test_generation_policy_frontier_stall_exhausts_without_legacy_pause() -> None:
    event = {
        "code": "candidate_generation_policy_frontier_stalled",
        "owner": "candidate",
        "stage": "candidate_generation",
        "scope": "candidate",
        "repairable": False,
        "category": "candidate_generation_policy",
    }

    disposition = derive_self_improvement_disposition(_report(event))

    assert disposition.kind is SelfImprovementDispositionKind.EXHAUSTED
    assert disposition.reason_code == (
        "candidate_generation_policy_frontier_stalled"
    )
    assert disposition.owner == "candidate"
    assert disposition.stage == "candidate_generation"


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


def test_campaign_accepts_verified_only_without_publishing(tmp_path: Path) -> None:
    controller = SelfImprovementCampaignController(workspace_root=tmp_path)

    campaign = controller.create(
        {
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
        },
        max_cycles=3,
    )

    assert campaign.request["apply_policy"] == "verified_only"
    assert campaign.max_cycles == 3


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


def test_campaign_bounds_authoritative_candidates_across_cycles(
    tmp_path: Path,
) -> None:
    calls: list[dict] = []

    def run_once(**request):
        calls.append(request)
        run_id = f"{request['campaign_id']}-cycle-{request['campaign_cycle']:03d}"
        report = _report(
            _event(
                constraint=(
                    "payload.items[*].kind"
                    if request["campaign_cycle"] == 1
                    else "payload.items[*].value"
                )
            )
        )
        report.update(
            {
                "run_id": run_id,
                "verification_funnel": {
                    "authoritative_candidate_count": (
                        2 if request["campaign_cycle"] == 1 else 1
                    )
                },
            }
        )
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
            "apply_policy": "verified_only",
            "infer_target": True,
            "max_full_evaluation_candidates": 3,
        },
        max_improvement_cycles=3,
        run_once=run_once,
    )

    assert [call["max_full_evaluation_candidates"] for call in calls] == [3, 1]
    assert len(calls) == 2
    assert result["campaign_status"] == "exhausted"
    assert result["campaign_authoritative_candidate_count"] == 3
    assert result["campaign_max_authoritative_candidates"] == 3
    assert result["self_improvement_disposition"]["reason_code"] == (
        "campaign_authoritative_frontier_exhausted"
    )


def test_campaign_grants_one_bounded_repair_for_new_terminal_counterexample(
    tmp_path: Path,
) -> None:
    calls: list[dict] = []

    def run_once(**request):
        calls.append(request)
        run_id = f"{request['campaign_id']}-cycle-{request['campaign_cycle']:03d}"
        if request["campaign_cycle"] == 1:
            report = _report(_event())
            report["gate_results"][0]["details"]["replay_counterexamples"] = [
                {
                    "schema_version": "aworld.replay.counterexample.v1",
                    "sequence": 1,
                    "failure_code": "tool_call_after_evidence_ready",
                    "owner": "candidate",
                    "stage": "task_rollout",
                    "state_before": "evidence_ready",
                    "trigger": "tool_call",
                    "required_transition": "finalize_task_response",
                }
            ]
            report["verification_funnel"] = {
                "authoritative_candidate_count": 1,
            }
        else:
            report = {
                "run_id": run_id,
                "status": "succeeded",
                "budget": _budget(10),
                "gate_results": [{"gate_name": "post_apply", "passed": True}],
                "verification_funnel": {
                    "authoritative_candidate_count": 1,
                },
            }
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
            "apply_policy": "verified_only",
            "infer_target": True,
            "max_full_evaluation_candidates": 1,
        },
        max_improvement_cycles=1,
        run_once=run_once,
    )

    assert len(calls) == 2
    assert [call["campaign_cycle"] for call in calls] == [1, 2]
    assert [call["max_full_evaluation_candidates"] for call in calls] == [1, 1]
    assert result["campaign_status"] == "complete"
    assert result["campaign_repair_continuation_used"] is True
    assert result["campaign_configured_max_cycles"] == 1
    assert result["campaign_max_cycles"] == 2
    assert result["campaign_authoritative_candidate_count"] == 2
    assert result["campaign_exhaustion_axes"] == []


def test_campaign_grants_bounded_repair_at_authoritative_frontier(
    tmp_path: Path,
) -> None:
    calls: list[dict] = []

    def run_once(**request):
        calls.append(request)
        run_id = f"{request['campaign_id']}-cycle-{request['campaign_cycle']:03d}"
        if request["campaign_cycle"] == 1:
            event = _event(code="candidate_evidence_incomplete")
            report = _report(event)
            report["campaign_measurement_outcome"] = {
                "schema_version": "aworld.self_evolve.campaign_measurement_outcome.v2",
                "execution_status": "completed",
                "improvement_outcome": "no_effect",
                "release_gates_passed": False,
                "continuation_available": True,
                "reason_code": "no_effect_candidate_repair_available",
                "projection": "candidate_rejected",
            }
            report["gate_results"][0]["details"]["replay_counterexamples"] = [
                {
                    "schema_version": "aworld.replay.counterexample.v1",
                    "sequence": 1,
                    "failure_code": "candidate_evidence_incomplete",
                    "owner": "candidate",
                    "stage": "evaluation",
                    "state_before": "answer_ready",
                    "trigger": "required_verification",
                    "required_transition": "capture_grounded_evidence",
                }
            ]
            report["verification_funnel"] = {
                "authoritative_candidate_count": 1,
            }
        else:
            report = {
                "status": "succeeded",
                "budget": _budget(10),
                "gate_results": [{"gate_name": "post_apply", "passed": True}],
                "verification_funnel": {"authoritative_candidate_count": 1},
            }
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
            "apply_policy": "verified_only",
            "infer_target": True,
            "max_full_evaluation_candidates": 1,
        },
        max_improvement_cycles=2,
        run_once=run_once,
    )

    assert len(calls) == 2
    assert [call["max_full_evaluation_candidates"] for call in calls] == [1, 1]
    assert result["campaign_status"] == "complete"
    assert result["campaign_repair_continuation_used"] is True
    assert result["campaign_configured_max_cycles"] == 2
    assert result["campaign_max_authoritative_candidates"] == 2


def test_conformance_counterexample_counts_as_new_repair_evidence() -> None:
    report = {
        "gate_results": [
            {
                "gate_name": "candidate_repair_conformance",
                "passed": False,
                "details": {
                    "counterexample_contracts": [
                        {
                            "schema_version": (
                                "aworld.self_evolve.fixture_probe_counterexample.v1"
                            ),
                            "counterexample_id": (
                                "fixture-probe-counterexample-abc"
                            ),
                            "selector_policy": (
                                "framework_recorded_response_v1"
                            ),
                        }
                    ]
                },
            }
        ]
    }

    fingerprints = campaign_module._report_candidate_counterexample_fingerprints(
        report
    )

    assert len(fingerprints) == 1
    assert campaign_module._report_has_new_candidate_counterexample(
        report,
        prior_reports=(),
    )
    assert not campaign_module._report_has_new_candidate_counterexample(
        report,
        prior_reports=(report,),
    )
    assert (
        "constraint-fixture-probe-counterexample-abc"
        in campaign_module._constraint_identities(report)
    )


def test_schema_parse_counterexample_counts_as_new_repair_evidence() -> None:
    report = {
        "gate_results": [
            {
                "gate_name": "candidate_repair_conformance",
                "passed": False,
                "details": {
                    "counterexample_contracts": [
                        {
                            "schema_version": (
                                "aworld.self_evolve.schema_counterexample.v1"
                            ),
                            "counterexample_id": "schema-counterexample-abc",
                            "constraint": {
                                "field_path": (
                                    "services[*@transport:http_fixture]"
                                    ".runtime_entrypoint"
                                ),
                                "rule": "type",
                            },
                        }
                    ]
                },
            }
        ]
    }

    assert campaign_module._report_has_new_candidate_counterexample(
        report,
        prior_reports=(),
    )
    assert "constraint-schema-counterexample-abc" in (
        campaign_module._constraint_identities(report)
    )


def test_typed_release_constraint_counts_as_new_candidate_repair_evidence() -> None:
    report = _report(_event(code="candidate_evidence_incomplete"))
    report["gate_results"][0]["details"]["evidence_repair_constraints"] = [
        {
            "schema_version": "aworld.self_evolve.evidence_repair_constraint.v1",
            "constraint_identity_digest": "sha256:candidate-evidence-abc",
            "owner": "candidate",
            "required_action": "support_or_omit",
        }
    ]

    assert campaign_module._report_has_new_candidate_repair_evidence(
        report,
        prior_reports=(),
    )
    assert not campaign_module._report_has_new_candidate_repair_evidence(
        report,
        prior_reports=(report,),
    )


def test_shared_measurement_invalid_attempt_is_not_authoritative_consumption() -> None:
    report = {
        "verification_funnel": {"authoritative_candidate_count": 1},
        "campaign_failure_attribution": {
            "failure_class": "measurement",
            "failure_owner": "framework",
            "failure_scope": "shared_run",
            "repairable": True,
        },
    }

    assert campaign_module._report_authoritative_candidate_count(report) == 0


def test_candidate_prerequisite_is_not_authoritative_consumption() -> None:
    report = _report(_event(constraint="services[*].readiness.kind"))
    report["verification_funnel"] = {
        "authoritative_candidate_attempt_count": 1,
        "authoritative_candidate_count": 1,
    }
    report["measurement"] = {
        "mode": "required",
        "status": "invalid",
        "validity_status": "invalid",
    }

    assert campaign_module._report_authoritative_candidate_count(report) == 0


def test_screening_candidate_blocker_precedes_derived_measurement_gate() -> None:
    event = {
        "owner": "candidate",
        "scope": "candidate",
        "repairable": True,
        "stage": "task_rollout",
        "code": "candidate_runtime_policy_regressed",
    }
    report = {
        "status": "rejected",
        "gate_results": [
            {
                "gate_name": "candidate_replay",
                "passed": False,
                "details": {
                    "failure_class": "candidate",
                    "failure_owner": "candidate",
                    "failure_scope": "candidate",
                    "repairable": True,
                    "evaluator_skipped": True,
                    "checkpoint_stage": "screening",
                    "failure_event": event,
                    "causal_failure_events": [event],
                },
            },
            {
                "gate_name": "trusted_improvement_measurement",
                "passed": False,
                "details": {
                    "failure_class": "measurement",
                    "next_action": "repair_measurement",
                },
            },
        ],
        "measurement": {
            "mode": "required",
            "status": "invalid",
            "validity_status": "invalid",
            "promotion_eligible": False,
            "next_action": "repair_measurement",
        },
        "verification_funnel": {
            "authoritative_candidate_attempt_count": 1,
            "authoritative_candidate_count": 1,
            "authoritative_candidate_ids": ["candidate-earlier"],
        },
    }

    disposition = derive_self_improvement_disposition(report)

    assert disposition.kind is SelfImprovementDispositionKind.CONTINUE_CANDIDATE
    assert disposition.owner == "candidate"
    assert disposition.scope == "candidate"
    assert disposition.reason_code == "candidate_repair_frontier_progressed"
    assert campaign_module._report_authoritative_candidate_count(report) == 1


def test_measurement_policy_routes_framework_repair_to_goal_handoff() -> None:
    report = {
        "status": "rejected",
        "measurement": {
            "mode": "required",
            "status": "not_started",
            "validity_status": "prerequisite_blocked",
            "promotion_eligible": False,
            "next_action": "repair_framework",
        },
        "gate_results": [
            {
                "gate_name": "candidate_screening_preflight",
                "passed": False,
                "details": {
                    "failure_class": "framework",
                    "failure_owner": "framework",
                    "failure_scope": "shared_run",
                    "repairable": True,
                },
            }
        ],
    }

    disposition = derive_self_improvement_disposition(report)

    assert disposition.kind is SelfImprovementDispositionKind.HANDOFF_GOAL
    assert disposition.owner == "framework"
    assert disposition.reason_code == "typed_framework_or_shared_blocker"


def test_screening_candidate_blocker_continues_repair_without_checkpoint(
    tmp_path: Path,
) -> None:
    calls: list[dict] = []

    def run_once(**request):
        calls.append(request)
        run_id = f"{request['campaign_id']}-cycle-{request['campaign_cycle']:03d}"
        if request["campaign_cycle"] == 1:
            event = {
                "owner": "candidate",
                "scope": "candidate",
                "repairable": True,
                "stage": "task_rollout",
                "code": "candidate_runtime_policy_regressed",
            }
            report = {
                "run_id": run_id,
                "status": "rejected",
                "candidate_ids": ["candidate-repair-focus"],
                "selected_candidate_id": None,
                "repair_focus_candidate_id": "candidate-repair-focus",
                "budget": _budget(10),
                "gate_results": [
                    {
                        "gate_name": "candidate_replay",
                        "passed": False,
                        "details": {
                            "failure_class": "candidate",
                            "failure_owner": "candidate",
                            "failure_scope": "candidate",
                            "repairable": True,
                            "evaluator_skipped": True,
                            "checkpoint_stage": "screening",
                            "failure_event": event,
                            "causal_failure_events": [event],
                        },
                    },
                    {
                        "gate_name": "trusted_improvement_measurement",
                        "passed": False,
                        "details": {"failure_class": "measurement"},
                    },
                ],
                "measurement": {
                    "mode": "required",
                    "status": "not_started",
                    "validity_status": "prerequisite_blocked",
                    "effect_direction": "unmeasured",
                    "promotion_eligible": False,
                    "next_action": "continue_candidate_repair",
                },
                "verification_funnel": {
                    "authoritative_candidate_attempt_count": 1,
                    "authoritative_candidate_count": 1,
                    "authoritative_candidate_ids": ["candidate-earlier"],
                },
            }
        else:
            report = {
                "run_id": run_id,
                "status": "succeeded",
                "budget": _budget(10),
                "gate_results": [{"gate_name": "post_apply", "passed": True}],
                "verification_funnel": {
                    "authoritative_candidate_attempt_count": 1,
                    "authoritative_candidate_count": 1,
                    "authoritative_candidate_ids": ["candidate-fixed"],
                },
            }
        report_path = tmp_path / ".aworld" / "self_evolve" / run_id / "report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
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
            "apply_policy": "verified_only",
            "infer_target": True,
        },
        max_improvement_cycles=2,
        run_once=run_once,
    )

    assert len(calls) == 2
    assert calls[1].get("campaign_measurement_pending_run_id") is None
    assert calls[1].get("campaign_measurement_pending_candidate_id") is None
    assert result["campaign_status"] == "complete"
    assert result["campaign_measurement_retry_count"] == 0
    assert result["campaign_authoritative_candidate_count"] == 2


def test_resume_migrates_misattributed_candidate_blocker_campaign(
    tmp_path: Path,
) -> None:
    calls: list[dict] = []
    controller = SelfImprovementCampaignController(
        workspace_root=tmp_path,
        run_once=lambda **request: _write_successful_campaign_fixture(
            tmp_path,
            calls,
            request,
        ),
    )
    campaign = controller.create(
        {
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
        },
        max_cycles=2,
    )
    run_id = f"{campaign.campaign_id}-cycle-001"
    event = {
        "owner": "candidate",
        "scope": "candidate",
        "repairable": True,
        "stage": "task_rollout",
        "code": "candidate_runtime_policy_regressed",
    }
    report = {
        "run_id": run_id,
        "status": "rejected",
        "gate_results": [
            {
                "gate_name": "candidate_replay",
                "passed": False,
                "details": {
                    "failure_class": "candidate",
                    "failure_owner": "candidate",
                    "failure_scope": "candidate",
                    "repairable": True,
                    "evaluator_skipped": True,
                    "checkpoint_stage": "screening",
                    "failure_event": event,
                    "causal_failure_events": [event],
                },
            }
        ],
        "measurement": {
            "mode": "required",
            "status": "invalid",
            "validity_status": "invalid",
            "promotion_eligible": False,
            "next_action": "repair_measurement",
        },
        "budget": _budget(10),
        "verification_funnel": {
            "authoritative_candidate_count": 1,
            "authoritative_candidate_attempt_count": 1,
            "authoritative_candidate_ids": ["candidate-earlier"],
        },
    }
    report_path = controller.store.write_report(run_id, report)
    exhausted = campaign_module.replace(
        campaign,
        status=SelfImprovementCampaignStatus.EXHAUSTED,
        cycle_index=1,
        run_ids=(run_id,),
        cumulative_authoritative_candidates=1,
        latest_progress=self_improvement_progress(report),
        latest_disposition=SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.EXHAUSTED,
            reason_code="measurement_authority_checkpoint_missing_or_invalid",
            owner="evaluation_harness",
            stage="measurement",
            scope="shared_run",
        ),
        latest_report_path=str(report_path),
    )
    controller.store.write_campaign(exhausted)

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={},
        resume_campaign=campaign.campaign_id,
        run_once=controller.run_once,
    )

    assert len(calls) == 1
    assert result["campaign_status"] == "complete"
    assert result["campaign_authoritative_candidate_count"] == 2
    migrated_report = controller.store.read_report(run_id)
    assert migrated_report["campaign_causal_migration"]["action"] == (
        "restore_candidate_repair_continuation"
    )


def test_resume_restores_deeper_abandoned_repair_champion(
    tmp_path: Path,
) -> None:
    calls: list[dict] = []
    controller = SelfImprovementCampaignController(
        workspace_root=tmp_path,
        run_once=lambda **request: _write_successful_campaign_fixture(
            tmp_path,
            calls,
            request,
        ),
    )
    campaign = controller.create(
        {
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
        },
        max_cycles=3,
    )
    reports: list[dict] = []
    run_ids: list[str] = []
    for cycle, stage in ((1, "capability_compile"), (2, "capability_preflight"), (3, "capability_compile")):
        run_id = f"{campaign.campaign_id}-cycle-{cycle:03d}"
        event = _event(constraint="services[*].readiness.kind")
        event["stage"] = stage
        report = _report(event)
        focus_id = f"candidate-focus-{cycle}"
        report.update(
            {
                "run_id": run_id,
                "repair_focus_candidate_id": focus_id,
            }
        )
        controller.store.write_report(run_id, report)
        candidate_path = (
            controller.store.run_path(run_id)
            / "candidates"
            / f"{focus_id}.json"
        )
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_path.write_text("{}", encoding="utf-8")
        reports.append(report)
        run_ids.append(run_id)
    exhausted = campaign_module.replace(
        campaign,
        status=SelfImprovementCampaignStatus.EXHAUSTED,
        cycle_index=3,
        run_ids=tuple(run_ids),
        latest_progress=self_improvement_progress(reports[-1]),
        latest_disposition=SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.EXHAUSTED,
            reason_code="candidate_repair_frontier_stalled",
            owner="candidate",
            stage="candidate_repair_conformance",
            scope="candidate",
        ),
        latest_report_path=str(
            controller.store.run_path(run_ids[-1]) / "report.json"
        ),
    )
    controller.store.write_campaign(exhausted)

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={},
        resume_campaign=campaign.campaign_id,
        run_once=controller.run_once,
    )

    assert len(calls) == 1
    assert calls[0]["campaign_cycle"] == 4
    assert calls[0]["campaign_prior_run_ids"][-1] == run_ids[1]
    assert result["campaign_status"] == "complete"
    assert result["campaign_repair_continuation_used"] is True
    migrated_report = controller.store.read_report(run_ids[-1])
    assert migrated_report["campaign_causal_migration"]["action"] == (
        "restore_deepest_repair_champion"
    )


def test_resume_restores_legacy_repairable_neutral_measurement(
    tmp_path: Path,
) -> None:
    calls: list[dict] = []
    controller = SelfImprovementCampaignController(
        workspace_root=tmp_path,
        run_once=lambda **request: _write_successful_campaign_fixture(
            tmp_path,
            calls,
            request,
        ),
    )
    campaign = controller.create(
        {
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
            "max_full_evaluation_candidates": 1,
        },
        max_cycles=2,
    )
    run_id = f"{campaign.campaign_id}-cycle-001"
    event = _event(code="candidate_evidence_incomplete")
    report = _report(event)
    report.update(
        {
            "run_id": run_id,
            "campaign_measurement_outcome": {
                "schema_version": "aworld.self_evolve.campaign_measurement_outcome.v2",
                "execution_status": "completed",
                "improvement_outcome": "no_effect",
                "release_gates_passed": False,
                "continuation_available": False,
                "reason_code": "inconclusive_effect",
                "projection": "candidate_rejected",
            },
            "verification_funnel": {"authoritative_candidate_count": 1},
        }
    )
    report["gate_results"][0]["details"]["evidence_repair_constraints"] = [
        {
            "schema_version": "aworld.self_evolve.evidence_repair_constraint.v1",
            "constraint_identity_digest": "sha256:candidate-evidence-abc",
            "owner": "candidate",
            "required_action": "support_or_omit",
        }
    ]
    report_path = controller.store.write_report(run_id, report)
    paused = campaign_module.replace(
        campaign,
        status=SelfImprovementCampaignStatus.PAUSED,
        cycle_index=1,
        run_ids=(run_id,),
        cumulative_authoritative_candidates=1,
        latest_progress=self_improvement_progress(report),
        latest_disposition=SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.STOP_NO_EFFECT,
            reason_code="inconclusive_effect",
            owner="candidate",
            stage="measurement",
            scope="candidate",
        ),
        latest_measurement_outcome=(
            campaign_module.CampaignMeasurementOutcomeV2.from_dict(
                report["campaign_measurement_outcome"]
            )
        ),
        latest_report_path=str(report_path),
    )
    controller.store.write_campaign(paused)

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={},
        resume_campaign=campaign.campaign_id,
        run_once=controller.run_once,
    )

    assert len(calls) == 1
    assert calls[0]["campaign_cycle"] == 2
    assert calls[0]["max_full_evaluation_candidates"] == 1
    assert result["campaign_status"] == "complete"
    assert result["campaign_repair_continuation_used"] is True
    migrated_report = controller.store.read_report(run_id)
    assert migrated_report["campaign_causal_migration"]["action"] == (
        "restore_repairable_neutral_candidate"
    )
    assert migrated_report["campaign_measurement_outcome"][
        "continuation_available"
    ] is True


def test_resume_restores_unattempted_task_behavior_repair_family(
    tmp_path: Path,
) -> None:
    calls: list[dict] = []
    controller = SelfImprovementCampaignController(
        workspace_root=tmp_path,
        run_once=lambda **request: _write_successful_campaign_fixture(
            tmp_path,
            calls,
            request,
        ),
    )
    campaign = controller.create(
        {
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
            "max_full_evaluation_candidates": 1,
        },
        max_cycles=2,
    )
    run_id = f"{campaign.campaign_id}-cycle-001"
    event = {
        "semantic_key": "replay-failure-candidate-recovery",
        "code": "candidate_recovery_incomplete",
        "owner": "candidate",
        "stage": "task_rollout",
        "scope": "candidate",
        "repairable": True,
        "category": "recovery_trace",
    }
    report = _report(event)
    report.update(
        {
            "run_id": run_id,
            "repair_frontier_state": {
                "scheduler_state": {
                    "frontier_mutation_families": {
                        event["semantic_key"]: []
                    }
                }
            },
            "verification_funnel": {"authoritative_candidate_count": 0},
        }
    )
    report_path = controller.store.write_report(run_id, report)
    exhausted = campaign_module.replace(
        campaign,
        status=SelfImprovementCampaignStatus.EXHAUSTED,
        cycle_index=1,
        run_ids=(run_id,),
        repair_continuation_used=True,
        latest_progress=self_improvement_progress(report),
        latest_disposition=SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.EXHAUSTED,
            reason_code="candidate_repair_frontier_stalled",
            owner="candidate",
            stage="candidate_generation",
            scope="candidate",
        ),
        latest_report_path=str(report_path),
    )
    controller.store.write_campaign(exhausted)

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={},
        resume_campaign=campaign.campaign_id,
        run_once=controller.run_once,
    )

    assert len(calls) == 1
    assert calls[0]["campaign_cycle"] == 2
    assert result["campaign_status"] == "complete"
    migrated_report = controller.store.read_report(run_id)
    assert migrated_report["campaign_causal_migration"]["action"] == (
        "restore_unattempted_task_behavior_repair"
    )


def test_shared_measurement_timeout_does_not_exhaust_authoritative_frontier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    def run_once(**request):
        calls.append(request)
        run_id = f"{request['campaign_id']}-cycle-{request['campaign_cycle']:03d}"
        if request["campaign_cycle"] == 1:
            report = _report(_event())
            candidate_id = "candidate-measurement-pending"
            report["candidate_ids"] = [candidate_id]
            report["selected_candidate_id"] = candidate_id
            report["campaign_failure_attribution"] = {
                "primary_gate": "candidate_replay",
                "code": "replay_total_timeout",
                "failure_class": "measurement",
                "failure_owner": "framework",
                "failure_scope": "shared_run",
                "failure_stage": "evaluation",
                "repairable": True,
                "next_action": "continue_measurement",
                "diagnostic_refs": ["/tmp/replay/checkpoint.json"],
            }
            report["verification_funnel"] = {
                "authoritative_candidate_attempt_count": 1,
                "authoritative_candidate_count": 0,
            }
            candidate_path = (
                tmp_path
                / ".aworld"
                / "self_evolve"
                / run_id
                / "candidates"
                / f"{candidate_id}.json"
            )
            candidate_path.parent.mkdir(parents=True, exist_ok=True)
            candidate_path.write_text("{}", encoding="utf-8")
        else:
            report = {
                "run_id": run_id,
                "status": "succeeded",
                "budget": _budget(10),
                "gate_results": [{"gate_name": "post_apply", "passed": True}],
                "verification_funnel": {
                    "authoritative_candidate_attempt_count": 1,
                    "authoritative_candidate_count": 1,
                },
            }
        report["run_id"] = run_id
        report_path = tmp_path / ".aworld" / "self_evolve" / run_id / "report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return {
            "run_id": run_id,
            "status": report["status"],
            "report_path": str(report_path),
        }

    monkeypatch.setattr(
        campaign_module,
        "_measurement_resume_checkpoint",
        lambda store, *, run_id, report: (
            SimpleNamespace(candidate_id="candidate-measurement-pending")
            if (
                store.run_path(run_id)
                / "candidates"
                / "candidate-measurement-pending.json"
            ).is_file()
            else None
        ),
    )

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
            "max_full_evaluation_candidates": 1,
        },
        max_improvement_cycles=3,
        run_once=run_once,
    )

    assert len(calls) == 2
    assert [call["max_full_evaluation_candidates"] for call in calls] == [1, 1]
    assert calls[1]["campaign_measurement_pending_run_id"] == (
        calls[0]["campaign_id"] + "-cycle-001"
    )
    assert calls[1]["campaign_measurement_pending_candidate_id"] == (
        "candidate-measurement-pending"
    )
    assert result["campaign_status"] == "complete"
    assert result["campaign_authoritative_candidate_count"] == 1
    assert result["campaign_measurement_retry_count"] == 1
    assert result["campaign_candidate_cycle_count"] == 1


def test_infrastructure_retry_preserves_measurement_candidate_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    def run_once(**request):
        calls.append(request)
        run_id = f"{request['campaign_id']}-cycle-{request['campaign_cycle']:03d}"
        candidate_id = "candidate-measurement-pending"
        if request["campaign_cycle"] == 1:
            report = _report(_event())
            report.update(
                {
                    "campaign_failure_attribution": {
                        "primary_gate": "candidate_replay",
                        "code": "control_not_comparable",
                        "failure_class": "measurement",
                        "failure_owner": "framework",
                        "failure_scope": "shared_run",
                        "failure_stage": "evaluation",
                        "repairable": True,
                    },
                    "candidate_ids": [candidate_id],
                    "selected_candidate_id": candidate_id,
                }
            )
            candidate_path = (
                tmp_path
                / ".aworld"
                / "self_evolve"
                / run_id
                / "candidates"
                / f"{candidate_id}.json"
            )
            candidate_path.parent.mkdir(parents=True, exist_ok=True)
            candidate_path.write_text("{}", encoding="utf-8")
        elif request["campaign_cycle"] == 2:
            report = _report(
                _event(
                    code="evaluation_backend_timeout",
                    owner="infrastructure",
                    scope="shared_run",
                )
            )
        else:
            report = {
                "status": "succeeded",
                "budget": _budget(10),
                "gate_results": [{"gate_name": "post_apply", "passed": True}],
            }
        report["run_id"] = run_id
        report_path = tmp_path / ".aworld" / "self_evolve" / run_id / "report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return {
            "run_id": run_id,
            "status": report["status"],
            "report_path": str(report_path),
        }

    monkeypatch.setattr(
        campaign_module,
        "_measurement_resume_checkpoint",
        lambda store, *, run_id, report: (
            SimpleNamespace(candidate_id="candidate-measurement-pending")
            if (
                store.run_path(run_id)
                / "candidates"
                / "candidate-measurement-pending.json"
            ).is_file()
            else None
        ),
    )

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
        },
        max_improvement_cycles=2,
        run_once=run_once,
    )

    assert len(calls) == 3
    assert calls[1]["campaign_measurement_pending_candidate_id"] == (
        "candidate-measurement-pending"
    )
    assert calls[2]["campaign_measurement_pending_candidate_id"] == (
        "candidate-measurement-pending"
    )
    assert calls[2]["campaign_measurement_pending_run_id"] == (
        calls[0]["campaign_id"] + "-cycle-001"
    )
    assert result["campaign_status"] == "complete"


def test_resume_migrates_legacy_unobserved_intervention_campaign(
    tmp_path: Path,
) -> None:
    calls: list[dict] = []
    controller = SelfImprovementCampaignController(
        workspace_root=tmp_path,
        run_once=lambda **request: _write_successful_campaign_fixture(
            tmp_path,
            calls,
            request,
        ),
    )
    campaign = controller.create(
        {
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
        },
        max_cycles=2,
    )
    run_id = f"{campaign.campaign_id}-cycle-001"
    legacy_details = {
        "code": "candidate_intervention_unobserved",
        "failure_class": "framework",
        "failure_owner": "framework",
        "failure_scope": "shared_run",
        "failure_stage": "adaptation",
        "repairable": False,
    }
    report = {
        "run_id": run_id,
        "status": "rejected",
        "budget": _budget(),
        "gate_results": [
            {
                "gate_name": "candidate_replay",
                "passed": False,
                "details": dict(legacy_details),
            }
        ],
        "measurement": {
            "mode": "required",
            "status": "invalid",
            "validity_status": "invalid",
            "effect_direction": "unmeasured",
            "promotion_eligible": False,
            "next_action": "repair_measurement",
        },
        "campaign_failure_attribution": {
            "primary_gate": "candidate_replay",
            **legacy_details,
        },
        "population": {
            "screening": {
                "attempts": [
                    {
                        "candidate_id": "candidate-replay-only",
                        "passed": True,
                        "details": dict(legacy_details),
                    }
                ]
            }
        },
        "verification_funnel": {
            "authoritative_candidate_count": 0,
            "authoritative_candidate_attempt_count": 0,
            "authoritative_candidate_ids": [],
        },
    }
    report_path = controller.store.write_report(run_id, report)
    exhausted = campaign_module.replace(
        campaign,
        status=SelfImprovementCampaignStatus.EXHAUSTED,
        cycle_index=1,
        run_ids=(run_id,),
        latest_progress=self_improvement_progress(report),
        latest_disposition=SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.EXHAUSTED,
            reason_code="measurement_authority_checkpoint_missing_or_invalid",
            owner="evaluation_harness",
            stage="measurement",
            scope="shared_run",
        ),
        latest_report_path=str(report_path),
    )
    controller.store.write_campaign(exhausted)

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={},
        resume_campaign=campaign.campaign_id,
        run_once=controller.run_once,
    )

    assert len(calls) == 1
    assert result["campaign_status"] == "complete"
    migrated_report = controller.store.read_report(run_id)
    assert migrated_report["campaign_causal_migration"]["action"] == (
        "restore_framework_control_selection_continuation"
    )
    assert migrated_report["gate_results"][0]["details"][
        "checkpoint_stage"
    ] == "screening"


def test_shared_measurement_retry_fails_closed_without_candidate_checkpoint(
    tmp_path: Path,
) -> None:
    def run_once(**request):
        run_id = f"{request['campaign_id']}-cycle-{request['campaign_cycle']:03d}"
        report = _report(_event())
        report.update(
            {
                "run_id": run_id,
                "campaign_failure_attribution": {
                    "primary_gate": "candidate_replay",
                    "code": "replay_total_timeout",
                    "failure_class": "measurement",
                    "failure_owner": "framework",
                    "failure_scope": "shared_run",
                    "failure_stage": "evaluation",
                    "repairable": True,
                    "next_action": "continue_measurement",
                },
            }
        )
        report_path = tmp_path / ".aworld" / "self_evolve" / run_id / "report.json"
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
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
        },
        max_improvement_cycles=3,
        run_once=run_once,
    )

    assert result["campaign_status"] == "exhausted"
    assert result["campaign_measurement_retry_count"] == 0
    assert result["self_improvement_disposition"]["reason_code"] == (
        "measurement_authority_checkpoint_missing_or_invalid"
    )


def test_resume_reopens_repairable_framework_screening_admission_failure(
    tmp_path: Path,
) -> None:
    calls: list[dict] = []
    controller = SelfImprovementCampaignController(
        workspace_root=tmp_path,
        run_once=lambda **request: _write_successful_campaign_fixture(
            tmp_path,
            calls,
            request,
        ),
    )
    campaign = controller.create(
        {
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
        },
        max_cycles=3,
    )
    run_id = f"{campaign.campaign_id}-cycle-001"
    failure_details = {
        "code": "measurement_plan_admission_failed",
        "failure_class": "measurement",
        "failure_owner": "framework",
        "failure_scope": "shared_run",
        "failure_stage": "evaluation",
        "repairable": True,
        "checkpoint_stage": "screening",
        "next_action": "repair_measurement",
        "resume_safe": False,
    }
    report = {
        "run_id": run_id,
        "status": "rejected",
        "budget": _budget(),
        "gate_results": [
            {
                "gate_name": "candidate_replay",
                "passed": False,
                "reason": "invalid replay artifact namespace",
                "details": dict(failure_details),
            }
        ],
        "campaign_failure_attribution": {
            "primary_gate": "candidate_replay",
            **failure_details,
        },
        "measurement": {
            "mode": "required",
            "status": "not_started",
            "validity_status": "prerequisite_blocked",
            "comparable_pair_count": 0,
            "independent_case_count": 0,
            "effect_direction": "unmeasured",
            "promotion_eligible": False,
            "next_action": "repair_measurement",
            "decision_reason": "measurement_plan_admission_failed",
        },
        "verification_funnel": {
            "authoritative_candidate_attempt_count": 1,
            "authoritative_candidate_count": 1,
            "authoritative_candidate_ids": ["candidate-screened"],
            "authoritative_case_observations": {
                "user-task": {"attempt_count": 1, "passed_count": 1}
            },
            "authoritative_case_observations_advisory_only": True,
        },
    }
    report_path = controller.store.write_report(run_id, report)
    exhausted = campaign_module.replace(
        campaign,
        status=SelfImprovementCampaignStatus.EXHAUSTED,
        cycle_index=1,
        run_ids=(run_id,),
        latest_progress=self_improvement_progress(report),
        latest_disposition=SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.EXHAUSTED,
            reason_code="measurement_authority_checkpoint_missing_or_invalid",
            owner="evaluation_harness",
            stage="evaluation",
            scope="shared_run",
        ),
        latest_report_path=str(report_path),
    )
    controller.store.write_campaign(exhausted)

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={},
        resume_campaign=campaign.campaign_id,
        run_once=controller.run_once,
    )

    assert len(calls) == 1
    assert calls[0]["campaign_cycle"] == 2
    assert calls[0].get("campaign_measurement_pending_run_id") is None
    assert calls[0].get("campaign_measurement_pending_candidate_id") is None
    assert result["campaign_status"] == "complete"
    assert result["campaign_measurement_retry_count"] == 0
    assert result["campaign_repair_continuation_used"] is False
    assert result["campaign_authoritative_candidate_count"] == 1
    migrated_report = controller.store.read_report(run_id)
    assert migrated_report["campaign_causal_migration"]["action"] == (
        "restore_framework_screening_admission_continuation"
    )
    assert migrated_report["self_improvement_disposition"]["kind"] == (
        "continue_campaign"
    )


def test_resume_adds_unattempted_websocket_http_version_repair_constraint(
    tmp_path: Path,
) -> None:
    calls: list[dict] = []
    controller = SelfImprovementCampaignController(
        workspace_root=tmp_path,
        run_once=lambda **request: _write_successful_campaign_fixture(
            tmp_path,
            calls,
            request,
        ),
    )
    campaign = controller.create(
        {
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
        },
        max_cycles=3,
    )
    run_id = f"{campaign.campaign_id}-cycle-003"
    report = {
        "run_id": run_id,
        "status": "rejected",
        "budget": _budget(),
        "repair_focus_candidate_id": "candidate-http-1-0",
        "gate_results": [
            {
                "gate_name": "candidate_repair_conformance",
                "passed": False,
                "reason": "candidate declared repair probe failed before task rollout",
                "details": {
                    "code": "repair_probe_execution_failed",
                    "failure_class": "candidate",
                    "repairable": True,
                    "diagnostics": [
                        {
                            "code": "protocol_trace_contract_failed",
                            "error_type": "ReplayServiceProtocolError",
                            "reason": (
                                "advertised WebSocket handshake requires HTTP/1.1; "
                                "service stderr: bounded"
                            ),
                        }
                    ],
                    "repair_conformance": {
                        "projection_schema_version": (
                            "aworld.self_evolve.repair_conformance.public.v1"
                        ),
                        "failure_codes": ["protocol_trace_contract_failed"],
                        "schema_field_constraints": [],
                    },
                },
            }
        ],
        "verification_funnel": {
            "authoritative_candidate_attempt_count": 0,
            "authoritative_candidate_count": 0,
            "authoritative_candidate_ids": [],
        },
    }
    report_path = controller.store.write_report(run_id, report)
    prior_run_ids = tuple(
        f"{campaign.campaign_id}-cycle-{cycle:03d}" for cycle in (1, 2)
    )
    for prior_run_id in prior_run_ids:
        controller.store.write_report(
            prior_run_id,
            {
                "run_id": prior_run_id,
                "status": "rejected",
                "budget": _budget(),
                "gate_results": [],
            },
        )
    candidate_path = (
        controller.store.run_path(run_id)
        / "candidates"
        / "candidate-http-1-0.json"
    )
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text("{}", encoding="utf-8")
    exhausted = campaign_module.replace(
        campaign,
        status=SelfImprovementCampaignStatus.EXHAUSTED,
        cycle_index=3,
        run_ids=(*prior_run_ids, run_id),
        latest_progress=self_improvement_progress(report),
        latest_disposition=SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.EXHAUSTED,
            reason_code="candidate_repair_frontier_stalled",
            owner="candidate",
            stage="capability_preflight",
            scope="candidate",
            repairable=True,
        ),
        latest_report_path=str(report_path),
    )
    controller.store.write_campaign(exhausted)

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={},
        resume_campaign=campaign.campaign_id,
        run_once=controller.run_once,
    )

    assert len(calls) == 1
    assert calls[0]["campaign_cycle"] == 4
    assert result["campaign_status"] == "complete"
    assert result["campaign_repair_continuation_used"] is True
    assert result["campaign_measurement_retry_count"] == 0
    migrated_report = controller.store.read_report(run_id)
    assert migrated_report["campaign_causal_migration"]["action"] == (
        "restore_unattempted_websocket_http_version_repair"
    )
    constraint = migrated_report["gate_results"][0]["details"][
        "schema_field_constraints"
    ][0]
    assert constraint["field_path"] == "websocket_handshake.http_version"
    assert constraint["expected"] == ["HTTP/1.1"]
    assert migrated_report["self_improvement_disposition"]["kind"] == (
        "continue_candidate"
    )


def test_resume_recovers_no_work_cycle_after_constraint_frontier_migration(
    tmp_path: Path,
) -> None:
    calls: list[dict] = []
    controller = SelfImprovementCampaignController(
        workspace_root=tmp_path,
        run_once=lambda **request: _write_successful_campaign_fixture(
            tmp_path,
            calls,
            request,
        ),
    )
    campaign = controller.create(
        {
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
        },
        max_cycles=3,
    )
    frontier_key = "replay-failure-http-version"
    run_ids = tuple(
        f"{campaign.campaign_id}-cycle-{cycle:03d}" for cycle in range(1, 5)
    )
    for run_id in run_ids[:2]:
        controller.store.write_report(
            run_id,
            {
                "run_id": run_id,
                "status": "rejected",
                "budget": _budget(),
                "gate_results": [],
            },
        )
    repaired_report = {
        "run_id": run_ids[2],
        "status": "rejected",
        "budget": _budget(),
        "gate_results": [
            {
                "gate_name": "candidate_repair_conformance",
                "passed": False,
                "details": {
                    "code": "repair_probe_execution_failed",
                    "failure_class": "candidate",
                    "repairable": True,
                    "diagnostics": [
                        {
                            "code": "websocket_handshake_http_version_invalid",
                            "reason": (
                                "advertised WebSocket handshake requires HTTP/1.1"
                            ),
                        }
                    ],
                    "causal_failure_events": [
                        {
                            "semantic_key": frontier_key,
                            "code": "protocol_trace_contract_failed",
                            "owner": "candidate",
                            "scope": "candidate",
                            "stage": "capability_preflight",
                            "repairable": True,
                        }
                    ],
                },
            }
        ],
        "campaign_causal_migration": {
            "schema_version": (
                "aworld.self_evolve.websocket_http_version_repair_migration.v1"
            ),
            "action": "restore_unattempted_websocket_http_version_repair",
        },
    }
    controller.store.write_report(run_ids[2], repaired_report)
    no_work_report = {
        "run_id": run_ids[3],
        "status": "rejected",
        "budget": _budget(),
        "gate_results": [
            {
                "gate_name": "candidate_generation",
                "passed": False,
                "reason": "optimizer did not produce a replayable candidate",
                "details": {"generated_candidate_count": 0, "iterations": 0},
            }
        ],
        "verification_funnel": {
            "authoritative_candidate_attempt_count": 0,
            "authoritative_candidate_count": 0,
            "authoritative_candidate_ids": [],
        },
        "repair_frontier_state": {
            "schema_version": "aworld.self_evolve.repair_frontier_state.v1",
            "records": [
                {
                    "semantic_key": frontier_key,
                    "status": "dormant",
                    "mutation_families": [
                        "minimal_behavior_delta",
                        "missing_capability_completion",
                    ],
                }
            ],
            "active_count": 0,
            "dormant_count": 1,
            "resolved_count": 0,
            "regressed_count": 0,
            "scheduler_state": {
                "initial_exploration_scheduled": True,
                "untyped_frontier_exploration_scheduled": False,
                "frontier_progress": {frontier_key: 4},
                "frontier_stalls": {frontier_key: 2},
                "frontier_mutation_families": {
                    frontier_key: [
                        "minimal_behavior_delta",
                        "missing_capability_completion",
                    ]
                },
                "last_focused_frontier": frontier_key,
            },
        },
    }
    report_path = controller.store.write_report(run_ids[3], no_work_report)
    exhausted = campaign_module.replace(
        campaign,
        status=SelfImprovementCampaignStatus.EXHAUSTED,
        cycle_index=4,
        run_ids=run_ids,
        repair_continuation_used=True,
        latest_progress=self_improvement_progress(no_work_report),
        latest_disposition=SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.EXHAUSTED,
            reason_code="candidate_repair_frontier_stalled",
            owner="candidate",
            stage="candidate_generation",
            scope="candidate",
            repairable=False,
        ),
        latest_report_path=str(report_path),
    )
    controller.store.write_campaign(exhausted)

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={},
        resume_campaign=campaign.campaign_id,
        run_once=controller.run_once,
    )

    assert len(calls) == 1
    assert calls[0]["campaign_cycle"] == 5
    assert result["campaign_status"] == "complete"
    assert result["campaign_framework_blocked_count"] == 1
    assert result["campaign_repair_continuation_used"] is True
    migrated_report = controller.store.read_report(run_ids[3])
    assert migrated_report["campaign_causal_migration"]["action"] == (
        "reactivate_migrated_constraint_after_no_work_cycle"
    )
    scheduler = migrated_report["repair_frontier_state"]["scheduler_state"]
    assert scheduler["frontier_stalls"][frontier_key] == 0
    assert scheduler["frontier_mutation_families"][frontier_key] == []
    assert scheduler["last_focused_frontier"] is None


def test_resume_recovers_scheduler_checkpoint_lineage_regression(
    tmp_path: Path,
) -> None:
    calls: list[dict] = []
    controller = SelfImprovementCampaignController(
        workspace_root=tmp_path,
        run_once=lambda **request: _write_successful_campaign_fixture(
            tmp_path,
            calls,
            request,
        ),
    )
    campaign = controller.create(
        {
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
        },
        max_cycles=3,
    )
    frontier_key = "replay-failure-http-version"
    run_ids = tuple(
        f"{campaign.campaign_id}-cycle-{cycle:03d}" for cycle in range(1, 6)
    )
    for run_id in run_ids[:2]:
        controller.store.write_report(
            run_id,
            {
                "run_id": run_id,
                "status": "rejected",
                "budget": _budget(),
                "gate_results": [],
            },
        )
    controller.store.write_report(
        run_ids[2],
        {
            "run_id": run_ids[2],
            "status": "rejected",
            "budget": _budget(),
            "gate_results": [
                {
                    "gate_name": "candidate_repair_conformance",
                    "passed": False,
                    "details": {
                        "diagnostics": [
                            {
                                "code": (
                                    "websocket_handshake_http_version_invalid"
                                )
                            }
                        ],
                        "causal_failure_events": [
                            {
                                "semantic_key": frontier_key,
                                "code": "protocol_trace_contract_failed",
                                "owner": "candidate",
                                "scope": "candidate",
                                "stage": "capability_preflight",
                                "repairable": True,
                            }
                        ],
                    },
                }
            ],
            "campaign_causal_migration": {
                "action": "restore_unattempted_websocket_http_version_repair"
            },
        },
    )
    reset_scheduler = {
        "initial_exploration_scheduled": True,
        "untyped_frontier_exploration_scheduled": False,
        "frontier_progress": {frontier_key: 4},
        "frontier_stalls": {frontier_key: 0},
        "frontier_mutation_families": {frontier_key: []},
        "last_focused_frontier": None,
    }
    controller.store.write_report(
        run_ids[3],
        {
            "run_id": run_ids[3],
            "status": "rejected",
            "budget": _budget(),
            "gate_results": [
                {
                    "gate_name": "candidate_generation",
                    "passed": False,
                    "details": {
                        "generated_candidate_count": 0,
                        "iterations": 0,
                    },
                }
            ],
            "repair_frontier_state": {
                "records": [],
                "scheduler_state": reset_scheduler,
            },
            "campaign_causal_migration": {
                "action": "reactivate_migrated_constraint_after_no_work_cycle"
            },
        },
    )
    reverted_scheduler = {
        **reset_scheduler,
        "frontier_stalls": {frontier_key: 2},
        "frontier_mutation_families": {
            frontier_key: ["minimal_behavior_delta"]
        },
        "last_focused_frontier": frontier_key,
    }
    latest_report = {
        "run_id": run_ids[4],
        "status": "rejected",
        "budget": _budget(),
        "gate_results": [
            {
                "gate_name": "candidate_generation",
                "passed": False,
                "details": {
                    "generated_candidate_count": 0,
                    "iterations": 0,
                },
            }
        ],
        "verification_funnel": {
            "authoritative_candidate_count": 0,
        },
        "repair_frontier_state": {
            "records": [],
            "scheduler_state": reverted_scheduler,
        },
    }
    report_path = controller.store.write_report(run_ids[4], latest_report)
    exhausted = campaign_module.replace(
        campaign,
        status=SelfImprovementCampaignStatus.EXHAUSTED,
        cycle_index=5,
        run_ids=run_ids,
        repair_continuation_used=True,
        measurement_ledger=(
            campaign.measurement_ledger.charge_framework_blocked(run_ids[3])
        ),
        latest_progress=self_improvement_progress(latest_report),
        latest_disposition=SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.EXHAUSTED,
            reason_code="candidate_repair_frontier_stalled",
            owner="candidate",
            stage="candidate_generation",
            scope="candidate",
            repairable=False,
        ),
        latest_report_path=str(report_path),
    )
    controller.store.write_campaign(exhausted)

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={},
        resume_campaign=campaign.campaign_id,
        run_once=controller.run_once,
    )

    assert len(calls) == 1
    assert calls[0]["campaign_cycle"] == 6
    assert calls[0]["campaign_scheduler_checkpoint_run_ids"] == run_ids
    assert result["campaign_status"] == "complete"
    assert result["campaign_framework_blocked_count"] == 2
    migrated = controller.store.read_report(run_ids[4])
    assert migrated["campaign_causal_migration"]["action"] == (
        "reactivate_migrated_constraint_after_checkpoint_lineage_regression"
    )
    scheduler = migrated["repair_frontier_state"]["scheduler_state"]
    assert scheduler["frontier_stalls"][frontier_key] == 0
    assert scheduler["frontier_mutation_families"][frontier_key] == []
    assert scheduler["last_focused_frontier"] is None


def test_resume_recovers_discarded_screening_baseline_cache(
    tmp_path: Path,
) -> None:
    calls: list[dict] = []
    controller = SelfImprovementCampaignController(
        workspace_root=tmp_path,
        run_once=lambda **request: _write_successful_campaign_fixture(
            tmp_path,
            calls,
            request,
        ),
    )
    campaign = controller.create(
        {
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
        },
        max_cycles=1,
    )
    run_id = f"{campaign.campaign_id}-cycle-001"
    report = {
        "run_id": run_id,
        "status": "failed",
        "budget": _budget(),
        "gate_results": [
            {
                "gate_name": "candidate_replay",
                "passed": False,
                "details": {
                    "code": "screening_control_infeasible",
                    "checkpoint_stage": "screening",
                    "failure_class": "framework",
                    "failure_owner": "framework",
                    "failure_scope": "shared_run",
                    "repairable": True,
                },
            }
        ],
        "measurement": {
            "status": "not_started",
            "decision_reason": "screening_control_infeasible",
        },
        "population": {
            "screening": {
                "attempts": [
                    {
                        "candidate_id": "candidate-a",
                        "control_case_attempts": [
                            {
                                "case_ids": ["task-a"],
                                "baseline_cache_offered": False,
                                "baseline_status": "succeeded",
                                "invalid_control": False,
                            }
                        ],
                    },
                    {
                        "candidate_id": "candidate-b",
                        "control_case_attempts": [
                            {
                                "case_ids": ["task-a"],
                                "baseline_cache_offered": False,
                                "baseline_status": "failed",
                                "invalid_control": True,
                            }
                        ],
                    },
                ]
            }
        },
    }
    report_path = controller.store.write_report(run_id, report)
    exhausted = campaign_module.replace(
        campaign,
        status=SelfImprovementCampaignStatus.EXHAUSTED,
        cycle_index=1,
        run_ids=(run_id,),
        cumulative_authoritative_candidates=1,
        latest_progress=self_improvement_progress(report),
        latest_disposition=SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.EXHAUSTED,
            reason_code="campaign_infrastructure_retry_limit_reached",
            owner="infrastructure",
            stage="candidate_generation",
            scope="shared_run",
            repairable=False,
        ),
        latest_report_path=str(report_path),
    )
    controller.store.write_campaign(exhausted)

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={},
        resume_campaign=campaign.campaign_id,
        run_once=controller.run_once,
    )

    assert len(calls) == 1
    assert calls[0]["campaign_cycle"] == 2
    assert calls[0]["max_full_evaluation_candidates"] == 2
    assert result["campaign_status"] == "complete"
    assert result["campaign_framework_blocked_count"] == 1
    assert result["campaign_authoritative_candidate_count"] == 2
    migrated = controller.store.read_report(run_id)
    assert migrated["campaign_causal_migration"]["action"] == (
        "restore_discarded_screening_baseline_cache"
    )
    assert migrated["campaign_causal_migration"]["affected_case_ids"] == [
        "task-a"
    ]


def test_resume_recovers_suppressed_task_behavior_materialization(
    tmp_path: Path,
) -> None:
    calls: list[dict] = []
    controller = SelfImprovementCampaignController(
        workspace_root=tmp_path,
        run_once=lambda **request: _write_successful_campaign_fixture(
            tmp_path,
            calls,
            request,
        ),
    )
    campaign = controller.create(
        {
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
        },
        max_cycles=1,
    )
    run_id = f"{campaign.campaign_id}-cycle-001"
    materialization_failure = {
        "code": "repair_target_behavior_unchanged",
        "stage": "candidate_semantic_validation",
        "representation": "candidate_package",
        "repairable": True,
        "contract_identity_digest": "task-contract",
        "details": {
            "repair_conformance": {
                "reason": (
                    "task-rollout repair must materially change SKILL.md; "
                    "support file edits alone cannot repair the observed agent behavior"
                )
            }
        },
    }
    report = {
        "run_id": run_id,
        "status": "rejected",
        "budget": _budget(),
        "gate_results": [
            {
                "gate_name": "candidate_replay",
                "passed": False,
                "details": {
                    "code": "candidate_screening_deadline_exceeded",
                    "failure_owner": "candidate",
                    "failure_scope": "candidate",
                    "repairable": True,
                },
            }
        ],
        "verification_funnel": {
            "authoritative_candidate_count": 0,
            "generation_materialization_frontier_exhausted": True,
            "generation_stop_reason": "materialization_frontier_repeated",
        },
        "optimizer_diagnostics": {
            "iterations": [
                {
                    "diagnostics": {
                        "candidate_materialization_failures": [
                            materialization_failure
                        ]
                    }
                },
                {
                    "diagnostics": {
                        "candidate_materialization_failures": [
                            materialization_failure
                        ]
                    }
                },
            ]
        },
    }
    report_path = controller.store.write_report(run_id, report)
    exhausted = campaign_module.replace(
        campaign,
        status=SelfImprovementCampaignStatus.EXHAUSTED,
        cycle_index=1,
        run_ids=(run_id,),
        latest_progress=self_improvement_progress(report),
        latest_disposition=SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.EXHAUSTED,
            reason_code="candidate_repair_frontier_stalled",
            owner="candidate",
            stage="candidate_generation",
            scope="candidate",
            repairable=False,
        ),
        latest_report_path=str(report_path),
    )
    controller.store.write_campaign(exhausted)

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={},
        resume_campaign=campaign.campaign_id,
        run_once=controller.run_once,
    )

    assert len(calls) == 1
    assert calls[0]["campaign_cycle"] == 2
    assert result["campaign_status"] == "complete"
    assert result["campaign_framework_blocked_count"] == 1
    migrated = controller.store.read_report(run_id)
    assert migrated["campaign_causal_migration"]["action"] == (
        "restore_suppressed_task_behavior_materialization"
    )
    assert migrated["campaign_causal_migration"][
        "suppressed_candidate_count"
    ] == 2


def test_resume_discards_regressing_checkpoint_when_positive_candidate_was_missed(
    tmp_path: Path,
) -> None:
    calls: list[dict] = []
    controller = SelfImprovementCampaignController(
        workspace_root=tmp_path,
        run_once=lambda **request: _write_successful_campaign_fixture(
            tmp_path,
            calls,
            request,
        ),
    )
    campaign = controller.create(
        {
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
        },
        max_cycles=1,
    )
    run_id = f"{campaign.campaign_id}-cycle-001"
    selected_candidate_id = "candidate-regressed-high-score"
    positive_candidate_id = "candidate-positive-effect"
    projection_constraint = {
        "schema_version": "aworld.self_evolve.evidence_repair_constraint.v1",
        "subject_kind": "artifact",
        "failure_mode": "projection_compacted",
        "source_layer": "artifact_projection",
        "required_action": "expand_bounded_projection",
        "owner": "framework",
        "occurrence_count": 1,
    }
    candidate_constraint = {
        "schema_version": "aworld.self_evolve.evidence_repair_constraint.v1",
        "subject_kind": "general_claim",
        "failure_mode": "support_incomplete",
        "source_layer": "candidate_output",
        "required_action": "support_or_omit",
        "owner": "candidate",
        "occurrence_count": 1,
    }
    report = {
        "run_id": run_id,
        "status": "rejected",
        "budget": _budget(),
        "selected_candidate_id": selected_candidate_id,
        "candidate_ids": [selected_candidate_id, positive_candidate_id],
        "iterations": [
            {
                "candidate_id": selected_candidate_id,
                "baseline_metrics": {"score": 86.0},
                "candidate_metrics": {
                    "score": 84.2,
                    "judge_artifact_read_budget_exhausted": False,
                    "judge_artifact_projection_incomplete": False,
                },
            },
            {
                "candidate_id": positive_candidate_id,
                "baseline_metrics": {"score": 79.425},
                "candidate_metrics": {"score": 82.825},
            },
        ],
        "gate_results": [
            {
                "gate_name": "score_improvement",
                "passed": False,
                "details": {
                    "code": "score_improvement_below_minimum",
                    "delta": -1.8,
                    "failure_owner": "candidate",
                    "failure_scope": "candidate",
                    "repairable": True,
                },
            },
            {
                "gate_name": "evidence_quality",
                "passed": False,
                "details": {
                    "failure_class": "framework",
                    "failure_owner": "framework",
                    "failure_scope": "shared_run",
                    "repairable": True,
                    "evidence_repair_constraints": [
                        projection_constraint,
                        candidate_constraint,
                    ],
                },
            },
        ],
    }
    report_path = controller.store.write_report(run_id, report)
    paused = campaign_module.replace(
        campaign,
        status=SelfImprovementCampaignStatus.PAUSED,
        cycle_index=1,
        run_ids=(run_id,),
        cumulative_authoritative_candidates=1,
        measurement_ledger=campaign.measurement_ledger.charge_framework_blocked(
            run_id
        ),
        latest_progress=self_improvement_progress(report),
        latest_disposition=SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.HANDOFF_GOAL,
            reason_code="baseline_evidence_policy_infeasible",
            owner="framework",
            stage="measurement",
            scope="shared_run",
            repairable=True,
        ),
        latest_report_path=str(report_path),
        measurement_pending_run_id=run_id,
        measurement_pending_candidate_id=selected_candidate_id,
    )
    controller.store.write_campaign(paused)

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={},
        resume_campaign=campaign.campaign_id,
        run_once=controller.run_once,
    )

    assert len(calls) == 1
    assert calls[0]["campaign_cycle"] == 2
    assert "campaign_measurement_pending_run_id" not in calls[0]
    assert "campaign_measurement_pending_candidate_id" not in calls[0]
    assert result["campaign_status"] == "complete"
    migrated = controller.store.read_report(run_id)
    assert migrated["campaign_causal_migration"] == {
        "schema_version": (
            "aworld.self_evolve.paired_candidate_checkpoint_migration.v1"
        ),
        "action": "discard_regressing_measurement_checkpoint",
        "source_run_id": run_id,
        "discarded_candidate_id": selected_candidate_id,
        "discarded_score_delta": pytest.approx(-1.8),
        "preferred_candidate_id": positive_candidate_id,
        "preferred_score_delta": pytest.approx(3.4),
        "candidate_reserve_granted": False,
        "measurement_retry_granted": False,
    }


def test_legacy_candidate_only_pending_marker_is_cleared_before_next_cycle(
    tmp_path: Path,
) -> None:
    calls: list[dict] = []
    controller = SelfImprovementCampaignController(
        workspace_root=tmp_path,
        run_once=lambda **request: _write_successful_campaign_fixture(
            tmp_path,
            calls,
            request,
        ),
    )
    campaign = controller.create(
        {
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
        },
        max_cycles=3,
    )
    source_run_id = f"{campaign.campaign_id}-cycle-001"
    source_path = controller.store.run_path(source_run_id)
    candidate_id = "candidate-screening-only"
    (source_path / "candidates").mkdir(parents=True)
    (source_path / "candidates" / f"{candidate_id}.json").write_text(
        "{}", encoding="utf-8"
    )
    controller.store.write_report(
        source_run_id,
        {
            "run_id": source_run_id,
            "status": "rejected",
            "measurement_pending_candidate_id": candidate_id,
            "campaign_failure_attribution": {
                "failure_class": "measurement",
                "failure_owner": "framework",
                "failure_scope": "shared_run",
                "code": "control_not_comparable",
            },
        },
    )
    legacy = campaign_module.replace(
        campaign,
        cycle_index=1,
        run_ids=(source_run_id,),
        measurement_pending_run_id=source_run_id,
        measurement_pending_candidate_id=candidate_id,
    )
    controller.store.write_campaign(legacy)

    advanced, summary = controller.advance_once(legacy)

    assert len(calls) == 1
    assert calls[0].get("campaign_measurement_pending_run_id") is None
    assert calls[0].get("campaign_measurement_pending_candidate_id") is None
    assert advanced.measurement_pending_run_id is None
    assert advanced.measurement_pending_candidate_id is None
    assert summary["campaign_checkpoint_migration"]["reason_code"] == (
        "authoritative_checkpoint_missing_or_invalid"
    )


def test_resume_reopens_unobserved_support_timeout_as_framework_control_cycle(
    tmp_path: Path,
) -> None:
    calls: list[dict] = []
    controller = SelfImprovementCampaignController(
        workspace_root=tmp_path,
        run_once=lambda **request: _write_successful_campaign_fixture(
            tmp_path,
            calls,
            request,
        ),
    )
    campaign = controller.create(
        {
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
        },
        max_cycles=1,
    )
    run_id = f"{campaign.campaign_id}-cycle-001"
    timeout = {
        "code": "replay_member_phase_timeout",
        "failure_owner": "framework",
    }

    def attempt(candidate_id: str) -> dict:
        return {
            "candidate_id": candidate_id,
            "details": {
                "code": "candidate_replay_support_baseline_incompatible",
                "failure_owner": "candidate",
                "baseline_failure": timeout,
                "candidate_execution_observed": False,
                "candidate_intervention_required": True,
                "candidate_intervention_observed": None,
                "failed_members": [
                    {
                        "case_id": "task-unstable",
                        "baseline_failure": timeout,
                        "candidate_status": "blocked",
                    }
                ],
            },
        }

    report = {
        "run_id": run_id,
        "status": "rejected",
        "budget": _budget(),
        "campaign_failure_attribution": {
            "code": "candidate_replay_support_baseline_incompatible",
            "failure_owner": "candidate",
        },
        "verification_funnel": {"authoritative_candidate_count": 0},
        "population": {
            "screening_batches": [
                {
                    "screening_strategy": (
                        "adaptive_qualification_then_authoritative"
                    ),
                    "attempts": [attempt("candidate-a")],
                },
                {
                    "screening_strategy": (
                        "adaptive_qualification_then_authoritative"
                    ),
                    "attempts": [attempt("candidate-b")],
                },
            ]
        },
    }
    report_path = controller.store.write_report(run_id, report)
    exhausted = campaign_module.replace(
        campaign,
        status=SelfImprovementCampaignStatus.EXHAUSTED,
        cycle_index=1,
        run_ids=(run_id,),
        latest_progress=self_improvement_progress(report),
        latest_disposition=SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.EXHAUSTED,
            reason_code="candidate_repair_frontier_stalled",
            owner="candidate",
            stage="adaptation",
            scope="candidate",
            repairable=True,
        ),
        latest_report_path=str(report_path),
    )
    controller.store.write_campaign(exhausted)

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={},
        resume_campaign=campaign.campaign_id,
        run_once=controller.run_once,
    )

    assert len(calls) == 1
    assert calls[0]["campaign_cycle"] == 2
    assert result["campaign_status"] == "complete"
    assert result["campaign_framework_blocked_count"] == 1
    migrated = controller.store.read_report(run_id)
    migration = migrated["campaign_causal_migration"]
    assert migration["action"] == (
        "restore_unobserved_support_timeout_control_cycle"
    )
    assert migration["affected_case_ids"] == ["task-unstable"]
    assert migration["affected_candidate_ids"] == [
        "candidate-a",
        "candidate-b",
    ]
    assert migration["candidate_reserve_granted"] is False
    assert migration["measurement_retry_granted"] is False


def test_resume_reopens_repeated_source_behavior_materialization_frontier(
    tmp_path: Path,
) -> None:
    calls: list[dict] = []
    controller = SelfImprovementCampaignController(
        workspace_root=tmp_path,
        run_once=lambda **request: _write_successful_campaign_fixture(
            tmp_path,
            calls,
            request,
        ),
    )
    campaign = controller.create(
        {
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
        },
        max_cycles=1,
    )
    run_id = f"{campaign.campaign_id}-cycle-001"
    contract_digest = "sha256:source-behavior-contract"

    def iteration(index: int) -> dict:
        return {
            "iteration": index,
            "diagnostics": {
                "candidate_materialization_failures": [
                    {
                        "code": "source_behavior_proof_failed",
                        "stage": "candidate_semantic_validation",
                        "representation": "candidate_package",
                        "repairable": True,
                        "contract_identity_digest": contract_digest,
                    }
                ]
            },
        }

    report = {
        "run_id": run_id,
        "status": "rejected",
        "budget": _budget(),
        "verification_funnel": {
            "authoritative_candidate_count": 0,
            "generation_materialization_frontier_exhausted": True,
            "generation_stop_reason": "materialization_frontier_repeated",
        },
        "optimizer_diagnostics": {
            "iterations": [iteration(1), iteration(2)],
        },
    }
    report_path = controller.store.write_report(run_id, report)
    exhausted = campaign_module.replace(
        campaign,
        status=SelfImprovementCampaignStatus.EXHAUSTED,
        cycle_index=1,
        run_ids=(run_id,),
        latest_progress=self_improvement_progress(report),
        latest_disposition=SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.EXHAUSTED,
            reason_code="candidate_repair_frontier_stalled",
            owner="candidate",
            stage="candidate_generation",
            scope="candidate",
            repairable=True,
        ),
        latest_report_path=str(report_path),
    )
    controller.store.write_campaign(exhausted)

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={},
        resume_campaign=campaign.campaign_id,
        run_once=controller.run_once,
    )

    assert len(calls) == 1
    assert calls[0]["campaign_cycle"] == 2
    assert result["campaign_status"] == "complete"
    assert result["campaign_framework_blocked_count"] == 1
    migrated = controller.store.read_report(run_id)
    assert migrated["campaign_causal_migration"] == {
        "schema_version": (
            "aworld.self_evolve.source_behavior_materialization_migration.v1"
        ),
        "action": "restore_source_behavior_materialization_frontier",
        "source_run_id": run_id,
        "failed_generation_attempt_count": 2,
        "contract_identity_digests": [contract_digest],
        "candidate_reserve_granted": False,
        "measurement_retry_granted": False,
    }


def test_resume_reopens_repeated_fixture_source_selection_frontier(
    tmp_path: Path,
) -> None:
    calls: list[dict] = []
    controller = SelfImprovementCampaignController(
        workspace_root=tmp_path,
        run_once=lambda **request: _write_successful_campaign_fixture(
            tmp_path,
            calls,
            request,
        ),
    )
    campaign = controller.create(
        {
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
            "max_full_evaluation_candidates": 3,
        },
        max_cycles=1,
    )
    run_id = f"{campaign.campaign_id}-cycle-001"
    report = {
        "run_id": run_id,
        "status": "rejected",
        "budget": _budget(),
        "campaign_failure_attribution": {
            "code": "repair_capability_compile_failed",
            "primary_gate": "candidate_repair_conformance",
            "repairable": True,
            "occurrence_count": 3,
            "affected_candidate_count": 3,
        },
        "rejection_attribution": {
            "code": "repair_capability_compile_failed",
            "primary_gate": "candidate_repair_conformance",
            "capability_error_code": "protocol_probe_not_fixture_derived",
            "repairable": True,
        },
        "verification_funnel": {
            "authoritative_candidate_count": 0,
            "authoritative_candidate_attempt_count": 0,
            "conformance_same_slot_repair_count": 3,
        },
    }
    report_path = controller.store.write_report(run_id, report)
    exhausted = campaign_module.replace(
        campaign,
        status=SelfImprovementCampaignStatus.EXHAUSTED,
        cycle_index=1,
        cumulative_authoritative_candidates=2,
        run_ids=(run_id,),
        latest_progress=self_improvement_progress(report),
        latest_disposition=SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.EXHAUSTED,
            reason_code="campaign_cycle_limit_reached",
            owner="candidate",
            stage="capability_compile",
            scope="candidate",
            repairable=False,
        ),
        latest_report_path=str(report_path),
    )
    controller.store.write_campaign(exhausted)

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={},
        resume_campaign=campaign.campaign_id,
        run_once=controller.run_once,
    )

    assert len(calls) == 1
    assert calls[0]["campaign_cycle"] == 2
    assert result["campaign_status"] == "complete"
    assert result["campaign_framework_blocked_count"] == 1
    migrated = controller.store.read_report(run_id)
    assert migrated["campaign_causal_migration"] == {
        "schema_version": (
            "aworld.self_evolve.fixture_source_selection_migration.v1"
        ),
        "action": "restore_fixture_source_selection_frontier",
        "source_run_id": run_id,
        "capability_error_code": "protocol_probe_not_fixture_derived",
        "repeated_same_slot_failure_count": 3,
        "candidate_reserve_granted": False,
        "measurement_retry_granted": False,
    }


@pytest.mark.parametrize(
    ("report_patch", "campaign_candidates"),
    [
        (
            {"rejection_attribution.capability_error_code": "other_failure"},
            2,
        ),
        (
            {"verification_funnel.conformance_same_slot_repair_count": 1},
            2,
        ),
        ({}, 3),
    ],
)
def test_fixture_source_selection_resume_migration_fails_closed(
    tmp_path: Path,
    report_patch: dict[str, object],
    campaign_candidates: int,
) -> None:
    calls: list[dict] = []
    controller = SelfImprovementCampaignController(
        workspace_root=tmp_path,
        run_once=lambda **request: _write_successful_campaign_fixture(
            tmp_path,
            calls,
            request,
        ),
    )
    campaign = controller.create(
        {
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
            "max_full_evaluation_candidates": 3,
        },
        max_cycles=1,
    )
    run_id = f"{campaign.campaign_id}-cycle-001"
    report = {
        "run_id": run_id,
        "status": "rejected",
        "budget": _budget(),
        "campaign_failure_attribution": {
            "code": "repair_capability_compile_failed",
            "primary_gate": "candidate_repair_conformance",
            "repairable": True,
            "occurrence_count": 3,
            "affected_candidate_count": 3,
        },
        "rejection_attribution": {
            "code": "repair_capability_compile_failed",
            "primary_gate": "candidate_repair_conformance",
            "capability_error_code": "protocol_probe_not_fixture_derived",
            "repairable": True,
        },
        "verification_funnel": {
            "authoritative_candidate_count": 0,
            "authoritative_candidate_attempt_count": 0,
            "conformance_same_slot_repair_count": 3,
        },
    }
    for dotted_path, value in report_patch.items():
        section, key = dotted_path.split(".", 1)
        report[section][key] = value
    report_path = controller.store.write_report(run_id, report)
    exhausted = campaign_module.replace(
        campaign,
        status=SelfImprovementCampaignStatus.EXHAUSTED,
        cycle_index=1,
        cumulative_authoritative_candidates=campaign_candidates,
        run_ids=(run_id,),
        latest_progress=self_improvement_progress(report),
        latest_disposition=SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.EXHAUSTED,
            reason_code="campaign_cycle_limit_reached",
            owner="candidate",
            stage="capability_compile",
            scope="candidate",
            repairable=False,
        ),
        latest_report_path=str(report_path),
    )
    controller.store.write_campaign(exhausted)

    with pytest.raises(ValueError, match="is terminal"):
        run_self_improvement_campaign(
            workspace_root=tmp_path,
            request={},
            resume_campaign=campaign.campaign_id,
            run_once=controller.run_once,
        )

    assert calls == []
    assert controller.store.read_campaign(campaign.campaign_id).status is (
        SelfImprovementCampaignStatus.EXHAUSTED
    )
    assert "campaign_causal_migration" not in controller.store.read_report(run_id)


def test_resume_reactivates_fixture_source_frontier_after_no_work_cycle(
    tmp_path: Path,
) -> None:
    calls: list[dict] = []
    controller = SelfImprovementCampaignController(
        workspace_root=tmp_path,
        run_once=lambda **request: _write_successful_campaign_fixture(
            tmp_path,
            calls,
            request,
        ),
    )
    campaign = controller.create(
        {
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
            "max_full_evaluation_candidates": 3,
        },
        max_cycles=1,
    )
    source_run_id = f"{campaign.campaign_id}-cycle-001"
    no_work_run_id = f"{campaign.campaign_id}-cycle-002"
    frontier_keys = ("replay-failure-compile-a", "replay-failure-compile-b")
    controller.store.write_report(
        source_run_id,
        {
            "run_id": source_run_id,
            "status": "rejected",
            "budget": _budget(),
            "campaign_causal_migration": {
                "action": "restore_fixture_source_selection_frontier",
                "candidate_reserve_granted": False,
                "measurement_retry_granted": False,
            },
            "optimizer_diagnostics": {
                "iterations": [
                    {"diagnostics": {"active_frontier_key": frontier_keys[0]}},
                    {"diagnostics": {"active_frontier_key": frontier_keys[1]}},
                ]
            },
        },
    )
    stalled_scheduler = {
        "initial_exploration_scheduled": True,
        "untyped_frontier_exploration_scheduled": False,
        "frontier_progress": {key: 1 for key in frontier_keys},
        "frontier_stalls": {key: 2 for key in frontier_keys},
        "frontier_mutation_families": {
            key: ["minimal_behavior_delta", "target_behavior_composition"]
            for key in frontier_keys
        },
        "last_focused_frontier": frontier_keys[-1],
    }
    no_work_report = {
        "run_id": no_work_run_id,
        "status": "rejected",
        "budget": _budget(),
        "gate_results": [
            {
                "gate_name": "candidate_generation",
                "passed": False,
                "details": {"generated_candidate_count": 0, "iterations": 0},
            }
        ],
        "verification_funnel": {
            "authoritative_candidate_count": 0,
            "authoritative_candidate_attempt_count": 0,
        },
        "repair_frontier_state": {
            "active_count": 0,
            "dormant_count": 2,
            "records": [
                {
                    "semantic_key": key,
                    "status": "dormant",
                    "mutation_families": stalled_scheduler[
                        "frontier_mutation_families"
                    ][key],
                }
                for key in frontier_keys
            ],
            "scheduler_state": stalled_scheduler,
        },
    }
    report_path = controller.store.write_report(no_work_run_id, no_work_report)
    exhausted = campaign_module.replace(
        campaign,
        status=SelfImprovementCampaignStatus.EXHAUSTED,
        cycle_index=2,
        cumulative_authoritative_candidates=2,
        run_ids=(source_run_id, no_work_run_id),
        measurement_ledger=(
            campaign.measurement_ledger.charge_framework_blocked(source_run_id)
        ),
        latest_progress=self_improvement_progress(no_work_report),
        latest_disposition=SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.EXHAUSTED,
            reason_code="candidate_repair_frontier_stalled",
            owner="candidate",
            stage="candidate_generation",
            scope="candidate",
            repairable=False,
        ),
        latest_report_path=str(report_path),
    )
    controller.store.write_campaign(exhausted)

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={},
        resume_campaign=campaign.campaign_id,
        run_once=controller.run_once,
    )

    assert len(calls) == 1
    assert calls[0]["campaign_cycle"] == 3
    assert result["campaign_status"] == "complete"
    assert result["campaign_framework_blocked_count"] == 2
    migrated = controller.store.read_report(no_work_run_id)
    migration = migrated["campaign_causal_migration"]
    assert migration["action"] == (
        "reactivate_fixture_source_frontier_after_no_work_cycle"
    )
    assert migration["reactivated_frontier_keys"] == list(frontier_keys)
    assert migration["candidate_reserve_granted"] is False
    assert migration["measurement_retry_granted"] is False
    scheduler = migrated["repair_frontier_state"]["scheduler_state"]
    assert scheduler["frontier_stalls"] == {key: 0 for key in frontier_keys}
    assert scheduler["frontier_mutation_families"] == {
        key: [] for key in frontier_keys
    }
    assert scheduler["last_focused_frontier"] is None


def test_resume_replaces_implicit_single_turn_authoritative_replay_budget(
    tmp_path: Path,
) -> None:
    calls: list[dict] = []
    controller = SelfImprovementCampaignController(
        workspace_root=tmp_path,
        run_once=lambda **request: _write_successful_campaign_fixture(
            tmp_path,
            calls,
            request,
        ),
    )
    campaign = controller.create(
        {
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
        },
        max_cycles=1,
    )
    run_id = f"{campaign.campaign_id}-cycle-001"
    completion_failure = {
        "code": "replay_task_completion_not_established",
        "failure_stage": "task_rollout",
        "outcome": "task_failure",
        "repairable": False,
    }
    report = {
        "run_id": run_id,
        "status": "rejected",
        "budget": _budget(),
        "gate_results": [
            {
                "gate_name": "candidate_replay",
                "passed": False,
                "details": {
                    "code": "candidate_replay_support_baseline_incompatible",
                    "control_identity": {"max_steps": 1},
                    "failed_members": [
                        {
                            "case_id": "task-browser",
                            "baseline_failure": completion_failure,
                            "candidate_failure": {
                                **completion_failure,
                                "outcome": "candidate_failure",
                                "repairable": True,
                            },
                        }
                    ],
                },
            }
        ],
        "verification_funnel": {"authoritative_candidate_count": 1},
    }
    report_path = controller.store.write_report(run_id, report)
    exhausted = campaign_module.replace(
        campaign,
        status=SelfImprovementCampaignStatus.EXHAUSTED,
        cycle_index=1,
        run_ids=(run_id,),
        cumulative_authoritative_candidates=1,
        latest_progress=self_improvement_progress(report),
        latest_disposition=SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.EXHAUSTED,
            reason_code="campaign_cycle_limit_reached",
            owner="candidate",
            stage="measurement",
            scope="candidate",
            repairable=False,
        ),
        latest_report_path=str(report_path),
    )
    controller.store.write_campaign(exhausted)

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={},
        resume_campaign=campaign.campaign_id,
        run_once=controller.run_once,
    )

    assert len(calls) == 1
    assert calls[0]["campaign_cycle"] == 2
    assert calls[0]["replay_max_steps"] == 24
    assert result["campaign_status"] == "complete"
    assert result["campaign_framework_blocked_count"] == 1
    migrated = controller.store.read_report(run_id)
    assert migrated["campaign_causal_migration"] == {
        "schema_version": (
            "aworld.self_evolve.single_turn_replay_budget_migration.v1"
        ),
        "action": "replace_implicit_single_turn_replay_budget",
        "source_run_id": run_id,
        "affected_case_ids": ["task-browser"],
        "previous_replay_max_steps": 1,
        "replacement_replay_max_steps": 24,
        "operator_budget_overridden": False,
        "candidate_reserve_granted": False,
        "measurement_retry_granted": False,
    }


def test_resume_reopens_path_sensitive_stored_candidate_repair_frontier(
    tmp_path: Path,
) -> None:
    controller = SelfImprovementCampaignController(workspace_root=tmp_path)
    campaign = controller.create(
        {
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
        },
        max_cycles=1,
    )
    run_id = f"{campaign.campaign_id}-cycle-001"
    report = {
        "run_id": run_id,
        "status": "rejected",
        "budget": _budget(),
        "candidate_source_dispositions": {
            "candidate": {
                "kind": "stored_evidence_rerun",
                "source_run_id": "stored-source-run",
                "requires_fresh_evaluation": True,
            }
        },
        "gate_results": [
            {
                "gate_name": "candidate_replay",
                "passed": False,
                "details": {
                    "code": "candidate_replay_support_baseline_incompatible",
                    "failure_owner": "candidate",
                    "repairable": True,
                },
            }
        ],
        "verification_funnel": {"authoritative_candidate_count": 1},
    }
    report_path = controller.store.write_report(run_id, report)
    exhausted = campaign_module.replace(
        campaign,
        status=SelfImprovementCampaignStatus.EXHAUSTED,
        cycle_index=1,
        run_ids=(run_id,),
        cumulative_authoritative_candidates=1,
        latest_progress=self_improvement_progress(report),
        latest_disposition=SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.EXHAUSTED,
            reason_code="candidate_repair_frontier_stalled",
            owner="candidate",
            stage="task_rollout",
            scope="candidate",
            repairable=True,
        ),
        latest_report_path=str(report_path),
    )
    controller.store.write_campaign(exhausted)

    migrated = campaign_module._migrate_path_sensitive_support_identity_for_resume(
        controller,
        exhausted,
    )

    assert migrated.status is SelfImprovementCampaignStatus.ACTIVE
    assert migrated.repair_continuation_used is True
    assert migrated.latest_disposition is not None
    assert migrated.latest_disposition.kind is (
        SelfImprovementDispositionKind.CONTINUE_CANDIDATE
    )
    assert migrated.latest_disposition.reason_code == (
        "replay_support_identity_repaired"
    )
    rewritten = controller.store.read_report(run_id)
    assert rewritten["campaign_causal_migration"]["action"] == (
        "restore_path_independent_support_repair_frontier"
    )


def test_resume_restores_typed_runtime_python_syntax_repair(
    tmp_path: Path,
) -> None:
    controller = SelfImprovementCampaignController(workspace_root=tmp_path)
    campaign = controller.create(
        {
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
        },
        max_cycles=1,
    )
    run_id = f"{campaign.campaign_id}-cycle-001"
    candidate_id = "candidate-invalid-runtime"
    runtime_path = (
        controller.store.run_path(run_id)
        / "candidates"
        / candidate_id
        / "replay"
        / "runtime.py"
    )
    runtime_path.parent.mkdir(parents=True)
    runtime_path.write_text(
        "value = 0\n"
        "def handle():\n"
        "    global value\n"
        "    value += 1\n"
        "    global value\n",
        encoding="utf-8",
    )
    report = {
        "run_id": run_id,
        "status": "rejected",
        "budget": _budget(),
        "repair_focus_candidate_id": candidate_id,
        "gate_results": [
            {
                "gate_name": "candidate_repair_conformance",
                "passed": False,
                "reason": "candidate declared repair probe failed",
                "details": {
                    "code": "repair_probe_execution_failed",
                    "failure_class": "candidate",
                    "repairable": True,
                    "diagnostics": [
                        {
                            "code": "repair_probe_execution_failed",
                            "error_type": "ReplayServiceProcessExitedError",
                            "reason": (
                                "service exited; SyntaxError: name 'value' is "
                                "used prior to global declaration"
                            ),
                        }
                    ],
                },
            }
        ],
        "verification_funnel": {
            "authoritative_candidate_count": 0,
            "authoritative_candidate_attempt_count": 0,
            "authoritative_candidate_ids": [],
        },
        "campaign": {},
    }
    report_path = controller.store.write_report(run_id, report)
    exhausted = campaign_module.replace(
        campaign,
        status=SelfImprovementCampaignStatus.EXHAUSTED,
        cycle_index=1,
        run_ids=(run_id,),
        latest_progress=self_improvement_progress(report),
        latest_disposition=SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.EXHAUSTED,
            reason_code="campaign_cycle_limit_reached",
            owner="candidate",
            stage="capability_preflight",
            scope="candidate",
            repairable=False,
        ),
        latest_report_path=str(report_path),
    )
    controller.store.write_campaign(exhausted)

    migrated = campaign_module._migrate_untyped_runtime_python_syntax_for_resume(
        controller,
        exhausted,
    )

    assert migrated.status is SelfImprovementCampaignStatus.ACTIVE
    assert migrated.repair_continuation_used is True
    assert migrated.latest_disposition is not None
    assert migrated.latest_disposition.reason_code == (
        "candidate_runtime_python_syntax_repair_available"
    )
    rewritten = controller.store.read_report(run_id)
    assert rewritten["campaign_causal_migration"]["action"] == (
        "restore_typed_runtime_python_syntax_repair"
    )
    details = rewritten["gate_results"][0]["details"]
    assert details["capability_error_code"] == "runtime_python_syntax_invalid"
    assert details["counterexample_contracts"][0]["syntax_kind"] == (
        "global_declaration_after_use"
    )


def test_resume_refunds_unbound_recorded_response_fixture_cycle(
    tmp_path: Path,
) -> None:
    controller = SelfImprovementCampaignController(workspace_root=tmp_path)
    campaign = controller.create(
        {
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
        },
        max_cycles=1,
    )
    prior_run_id = f"{campaign.campaign_id}-cycle-001"
    run_id = f"{campaign.campaign_id}-cycle-002"
    candidate_id = "candidate-unbound-sidecar"
    candidate_replay = (
        controller.store.run_path(run_id)
        / "candidates"
        / candidate_id
        / "replay"
    )
    candidate_replay.mkdir(parents=True)
    candidate_replay.joinpath("compiler.py").write_text(
        "def _select_source(evidence_ref, derivations):\n"
        "    sources = derivations.get(evidence_ref, [])\n"
        "    ranked = sorted(sources, key=lambda s: s.get('byte_length', 0))\n"
        "    return ranked[0]\n",
        encoding="utf-8",
    )
    runtime_source = (
        "import json\n"
        "import os\n"
        "def respond():\n"
        "    path = os.getenv('AWORLD_REPLAY_RESPONSE_INDEX')\n"
        "    with open(path, encoding='utf-8') as stream:\n"
        "        index = json.load(stream)\n"
        "    records = index['records']\n"
        "    return records[0]['value']\n"
    )
    runtime_path = candidate_replay / "runtime.py"
    runtime_path.write_text(runtime_source, encoding="utf-8")
    runtime_digest = hashlib.sha256(runtime_source.encode("utf-8")).hexdigest()

    capability_root = (
        controller.store.run_path(run_id)
        / "replay_adaptation"
        / "dataset"
        / "candidate"
        / "skill_replay_capability"
    )
    frozen_root = capability_root / "frozen"
    frozen_runtime = frozen_root / "runtime" / "replay" / "runtime.py"
    frozen_runtime.parent.mkdir(parents=True)
    frozen_runtime.write_text(runtime_source, encoding="utf-8")
    frozen_fixture = frozen_root / "fixtures" / "fixtures" / "fixture.bin"
    frozen_fixture.parent.mkdir(parents=True)
    frozen_fixture.write_text('{"request": "metadata only"}', encoding="utf-8")
    frozen_root.joinpath("frozen_manifest.json").write_text(
        json.dumps(
            {
                "runtime_files": [
                    {
                        "path": "replay/runtime.py",
                        "sha256": f"sha256:{runtime_digest}",
                    }
                ],
                "services": [
                    {
                        "service_id": "service-1",
                        "requirement_id": "requirement-1",
                        "transport": "skill_runtime",
                        "response_fixture": "fixtures/fixture.bin",
                        "protocol_probes": [
                            {
                                "kind": "http",
                                "path": "/data",
                                "response_record_id": None,
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    compile_root = capability_root / "compile-a"
    compile_root.mkdir()
    compile_root.joinpath("request.json").write_text(
        json.dumps(
            {
                "requirements": [
                    {
                        "requirement_id": "requirement-1",
                        "evidence_refs": ["evidence-1"],
                    }
                ],
                "evidence_derivations": {
                    "evidence-1": [
                        {
                            "path": "recorded.bin",
                            "response_index_path": "recorded.responses.json",
                            "response_record_count": 3,
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    report = {
        "run_id": run_id,
        "status": "rejected",
        "budget": _budget(),
        "repair_focus_candidate_id": candidate_id,
        "gate_results": [
            {
                "gate_name": "candidate_repair_conformance",
                "passed": False,
                "details": {
                    "code": "repair_probe_execution_failed",
                    "failure_class": "candidate",
                    "repairable": True,
                    "diagnostics": [
                        {
                            "code": "replay_service_readiness_failed",
                            "error_type": "ReplayServiceReadinessTimeout",
                            "reason": (
                                "protocol probe timed out; TypeError: expected "
                                "str, bytes or os.PathLike object, not NoneType"
                            ),
                        }
                    ],
                },
            }
        ],
        "verification_funnel": {
            "authoritative_candidate_count": 0,
            "authoritative_candidate_attempt_count": 0,
        },
        "campaign": {},
    }
    controller.store.write_report(prior_run_id, {"run_id": prior_run_id})
    report_path = controller.store.write_report(run_id, report)
    exhausted = campaign_module.replace(
        campaign,
        status=SelfImprovementCampaignStatus.EXHAUSTED,
        cycle_index=2,
        run_ids=(prior_run_id, run_id),
        repair_continuation_used=True,
        latest_progress=self_improvement_progress(report),
        latest_disposition=SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.EXHAUSTED,
            reason_code="campaign_cycle_limit_reached",
            owner="candidate",
            stage="capability_preflight",
            scope="candidate",
            repairable=False,
        ),
        latest_report_path=str(report_path),
    )
    controller.store.write_campaign(exhausted)

    migrated = (
        campaign_module._migrate_unselected_recorded_response_fixture_for_resume(
            controller,
            exhausted,
        )
    )

    assert migrated.status is SelfImprovementCampaignStatus.ACTIVE
    assert migrated.measurement_ledger.framework_blocked_count == 1
    assert migrated.latest_disposition is not None
    assert migrated.latest_disposition.reason_code == (
        "candidate_fixture_source_selection_repair_available"
    )
    rewritten = controller.store.read_report(run_id)
    assert rewritten["campaign_causal_migration"]["action"] == (
        "restore_recorded_response_fixture_selection_repair"
    )
    details = rewritten["gate_results"][0]["details"]
    assert details["capability_error_code"] == (
        "recorded_response_fixture_unselected"
    )
    assert details["schema_field_constraints"][0]["schema_layer"] == (
        "compiler"
    )


def test_resume_repairs_zero_work_after_single_turn_budget_migration(
    tmp_path: Path,
) -> None:
    calls: list[dict] = []
    controller = SelfImprovementCampaignController(
        workspace_root=tmp_path,
        run_once=lambda **request: _write_successful_campaign_fixture(
            tmp_path,
            calls,
            request,
        ),
    )
    campaign = controller.create(
        {
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
            "replay_max_steps": 10,
        },
        max_cycles=1,
    )
    source_run_id = f"{campaign.campaign_id}-cycle-001"
    no_work_run_id = f"{campaign.campaign_id}-cycle-002"
    frontier_key = "replay-failure-single-turn"
    source_report = {
        "run_id": source_run_id,
        "status": "rejected",
        "budget": _budget(),
        "optimizer_diagnostics": {
            "candidate_generation_outcomes": [
                {"active_frontier_key": frontier_key}
            ]
        },
        "campaign_causal_migration": {
            "action": "replace_implicit_single_turn_replay_budget"
        },
        "verification_funnel": {"authoritative_candidate_count": 1},
    }
    no_work_report = {
        "run_id": no_work_run_id,
        "status": "rejected",
        "budget": _budget(),
        "gate_results": [
            {
                "gate_name": "candidate_generation",
                "passed": False,
                "details": {
                    "generated_candidate_count": 0,
                    "iterations": 0,
                },
            }
        ],
        "verification_funnel": {"authoritative_candidate_count": 0},
        "repair_frontier_state": {
            "active_count": 0,
            "dormant_count": 1,
            "records": [
                {
                    "semantic_key": frontier_key,
                    "status": "dormant",
                    "mutation_families": ["missing_capability_completion"],
                }
            ],
            "scheduler_state": {
                "frontier_stalls": {frontier_key: 2},
                "frontier_mutation_families": {
                    frontier_key: ["missing_capability_completion"]
                },
                "initial_exploration_scheduled": True,
                "untyped_frontier_exploration_scheduled": False,
                "last_focused_frontier": frontier_key,
            },
        },
    }
    controller.store.write_report(source_run_id, source_report)
    no_work_path = controller.store.write_report(
        no_work_run_id,
        no_work_report,
    )
    exhausted = campaign_module.replace(
        campaign,
        status=SelfImprovementCampaignStatus.EXHAUSTED,
        cycle_index=2,
        run_ids=(source_run_id, no_work_run_id),
        repair_continuation_used=True,
        measurement_ledger=campaign.measurement_ledger.charge_framework_blocked(
            source_run_id
        ),
        latest_progress=self_improvement_progress(no_work_report),
        latest_disposition=SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.EXHAUSTED,
            reason_code="candidate_repair_frontier_stalled",
            owner="candidate",
            stage="candidate_generation",
            scope="candidate",
            repairable=False,
        ),
        latest_report_path=str(no_work_path),
    )
    controller.store.write_campaign(exhausted)

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={},
        resume_campaign=campaign.campaign_id,
        run_once=controller.run_once,
    )

    assert len(calls) == 1
    assert calls[0]["campaign_cycle"] == 3
    assert result["campaign_status"] == "complete"
    assert result["campaign_framework_blocked_count"] == 2
    migrated = controller.store.read_report(no_work_run_id)
    migration = migrated["campaign_causal_migration"]
    assert migration["action"] == (
        "restore_exploration_after_single_turn_budget_fix"
    )
    assert migration["reactivated_frontier_keys"] == [frontier_key]
    assert migration["candidate_reserve_granted"] is False
    assert migration["measurement_retry_granted"] is False
    scheduler = migrated["repair_frontier_state"]["scheduler_state"]
    assert scheduler["frontier_stalls"][frontier_key] == 0
    assert scheduler["frontier_mutation_families"][frontier_key] == []
    assert scheduler["last_focused_frontier"] is None


def test_resume_replaces_evidence_rollout_budget_and_discards_old_checkpoint(
    tmp_path: Path,
) -> None:
    calls: list[dict] = []
    controller = SelfImprovementCampaignController(
        workspace_root=tmp_path,
        run_once=lambda **request: _write_successful_campaign_fixture(
            tmp_path,
            calls,
            request,
        ),
    )
    campaign = controller.create(
        {
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
            "replay_max_steps": 10,
        },
        max_cycles=1,
    )
    run_id = f"{campaign.campaign_id}-cycle-001"
    report = {
        "run_id": run_id,
        "status": "rejected",
        "budget": _budget(),
        "gate_results": [
            {
                "gate_name": "candidate_replay",
                "passed": False,
                "details": {
                    "code": "evidence_policy_v2_attestation_failed",
                    "baseline_failure": {
                        "code": "replay_task_completion_not_established"
                    },
                    "candidate_failure": {
                        "code": "evidence_policy_v2_attestation_failed",
                        "reason": "framework evidence inventory is empty",
                    },
                    "failed_members": [{"case_id": "task-browser"}],
                },
            }
        ],
        "verification_funnel": {"authoritative_candidate_count": 0},
    }
    report_path = controller.store.write_report(run_id, report)
    paused = campaign_module.replace(
        campaign,
        status=SelfImprovementCampaignStatus.PAUSED,
        cycle_index=1,
        run_ids=(run_id,),
        latest_progress=self_improvement_progress(report),
        latest_disposition=SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.HANDOFF_GOAL,
            reason_code="evidence_policy_v2_attestation_failed",
            owner="framework",
            stage="measurement",
            scope="shared_run",
            repairable=True,
        ),
        latest_report_path=str(report_path),
        measurement_pending_run_id=run_id,
        measurement_pending_candidate_id="candidate-old-budget",
    )
    controller.store.write_campaign(paused)

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={},
        resume_campaign=campaign.campaign_id,
        run_once=controller.run_once,
    )

    assert len(calls) == 1
    assert calls[0]["campaign_cycle"] == 2
    assert calls[0]["replay_max_steps"] == 24
    assert "campaign_measurement_pending_run_id" not in calls[0]
    assert "campaign_measurement_pending_candidate_id" not in calls[0]
    assert result["campaign_status"] == "complete"
    migrated = controller.store.read_report(run_id)
    migration = migrated["campaign_causal_migration"]
    assert migration["action"] == (
        "replace_insufficient_evidence_replay_budget"
    )
    assert migration["affected_case_ids"] == ["task-browser"]
    assert migration["previous_replay_max_steps"] == 10
    assert migration["replacement_replay_max_steps"] == 24
    assert migration["candidate_reserve_granted"] is False
    assert migration["measurement_retry_granted"] is False


def test_resume_repairs_zero_work_checkpoint_after_screening_control_blocker(
    tmp_path: Path,
) -> None:
    calls: list[dict] = []
    controller = SelfImprovementCampaignController(
        workspace_root=tmp_path,
        run_once=lambda **request: _write_successful_campaign_fixture(
            tmp_path,
            calls,
            request,
        ),
    )
    campaign = controller.create(
        {
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
        },
        max_cycles=1,
    )
    source_run_id = f"{campaign.campaign_id}-cycle-001"
    no_work_run_id = f"{campaign.campaign_id}-cycle-002"
    source_report = {
        "run_id": source_run_id,
        "status": "rejected",
        "budget": _budget(),
        "candidate_ids": ["candidate-frozen"],
        "optimizer_diagnostics": {
            "candidate_generation_outcomes": [
                {"active_frontier_key": "legacy-candidate-frontier"}
            ]
        },
        "campaign_failure_attribution": {
            "primary_gate": "candidate_replay",
            "code": "screening_control_infeasible",
            "failure_owner": "framework",
            "failure_scope": "shared_run",
            "repairable": True,
        },
        "verification_funnel": {"authoritative_candidate_count": 0},
    }
    no_work_report = {
        "run_id": no_work_run_id,
        "status": "rejected",
        "budget": _budget(),
        "gate_results": [
            {
                "gate_name": "candidate_generation",
                "passed": False,
                "details": {
                    "generated_candidate_count": 0,
                    "iterations": 0,
                },
            }
        ],
        "verification_funnel": {"authoritative_candidate_count": 0},
        "repair_frontier_state": {
            "active_count": 0,
            "dormant_count": 1,
            "records": [
                {
                    "semantic_key": "legacy-candidate-frontier",
                    "status": "dormant",
                    "mutation_families": ["quality_regression_repair"],
                }
            ],
            "scheduler_state": {
                "frontier_stalls": {"legacy-candidate-frontier": 2},
                "frontier_mutation_families": {
                    "legacy-candidate-frontier": [
                        "quality_regression_repair"
                    ]
                },
                "initial_exploration_scheduled": True,
                "untyped_frontier_exploration_scheduled": False,
                "last_focused_frontier": "legacy-candidate-frontier",
            },
        },
    }
    controller.store.write_report(source_run_id, source_report)
    no_work_path = controller.store.write_report(
        no_work_run_id,
        no_work_report,
    )
    exhausted = campaign_module.replace(
        campaign,
        status=SelfImprovementCampaignStatus.EXHAUSTED,
        cycle_index=2,
        run_ids=(source_run_id, no_work_run_id),
        repair_continuation_used=True,
        measurement_ledger=campaign.measurement_ledger.charge_framework_blocked(
            source_run_id
        ),
        latest_progress=self_improvement_progress(no_work_report),
        latest_disposition=SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.EXHAUSTED,
            reason_code="candidate_repair_frontier_stalled",
            owner="candidate",
            stage="candidate_generation",
            scope="candidate",
            repairable=False,
        ),
        latest_report_path=str(no_work_path),
    )
    controller.store.write_campaign(exhausted)

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={},
        resume_campaign=campaign.campaign_id,
        run_once=controller.run_once,
    )

    assert len(calls) == 1
    assert calls[0]["campaign_cycle"] == 3
    assert result["campaign_status"] == "complete"
    assert result["campaign_framework_blocked_count"] == 2
    migrated = controller.store.read_report(no_work_run_id)
    migration = migrated["campaign_causal_migration"]
    assert migration["action"] == (
        "restore_exploration_after_screening_control_no_work"
    )
    assert migration["candidate_reserve_granted"] is False
    assert migration["measurement_retry_granted"] is False
    scheduler = migrated["repair_frontier_state"]["scheduler_state"]
    assert scheduler["initial_exploration_scheduled"] is False
    assert scheduler["last_focused_frontier"] is None
    assert scheduler["frontier_stalls"]["legacy-candidate-frontier"] == 0
    assert migrated["repair_frontier_state"]["active_count"] == 1


def _write_successful_campaign_fixture(
    tmp_path: Path,
    calls: list[dict],
    request: dict,
) -> dict:
    calls.append(request)
    run_id = f"{request['campaign_id']}-cycle-{request['campaign_cycle']:03d}"
    report = {
        "run_id": run_id,
        "status": "succeeded",
        "budget": _budget(10),
        "gate_results": [{"gate_name": "post_apply", "passed": True}],
        "verification_funnel": {
            "authoritative_candidate_attempt_count": 1,
            "authoritative_candidate_count": 1,
        },
    }
    report_path = tmp_path / ".aworld" / "self_evolve" / run_id / "report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return {
        "run_id": run_id,
        "status": "succeeded",
        "report_path": str(report_path),
    }


def test_candidate_prerequisite_enters_repair_without_measurement_retry(
    tmp_path: Path,
) -> None:
    calls: list[dict] = []

    def run_once(**request):
        calls.append(request)
        run_id = f"{request['campaign_id']}-cycle-{request['campaign_cycle']:03d}"
        if request["campaign_cycle"] == 1:
            event = _event(constraint="services[*].readiness.kind")
            report = _report(event)
            report.update(
                {
                    "run_id": run_id,
                    "candidate_ids": ["candidate-invalid-capability"],
                    "selected_candidate_id": "candidate-invalid-capability",
                    "measurement": {
                        "mode": "required",
                        "status": "invalid",
                        "validity_status": "invalid",
                        "comparable_pair_count": 0,
                        "effect_direction": "unmeasured",
                        "promotion_eligible": False,
                        "next_action": "repair_measurement",
                    },
                    "verification_funnel": {
                        "authoritative_candidate_attempt_count": 1,
                        "authoritative_candidate_count": 1,
                    },
                }
            )
            report["gate_results"].append(
                {
                    "gate_name": "trusted_improvement_measurement",
                    "passed": False,
                    "details": {"failure_class": "measurement"},
                }
            )
        else:
            report = {
                "run_id": run_id,
                "status": "succeeded",
                "budget": _budget(10),
                "gate_results": [{"gate_name": "post_apply", "passed": True}],
                "verification_funnel": {
                    "authoritative_candidate_attempt_count": 1,
                    "authoritative_candidate_count": 1,
                },
            }
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
            "apply_policy": "verified_only",
            "infer_target": True,
        },
        max_improvement_cycles=2,
        run_once=run_once,
    )

    assert len(calls) == 2
    assert calls[1].get("campaign_measurement_pending_run_id") is None
    assert calls[1].get("campaign_measurement_pending_candidate_id") is None
    assert result["campaign_measurement_retry_count"] == 0
    assert result["campaign_authoritative_candidate_count"] == 1
    assert result["campaign_status"] == "complete"


def test_shared_measurement_retries_are_bounded_separately_from_candidate_cycles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    def run_once(**request):
        calls.append(request)
        run_id = f"{request['campaign_id']}-cycle-{request['campaign_cycle']:03d}"
        candidate_id = "candidate-measurement-pending"
        report = _report(_event())
        report.update(
            {
                "run_id": run_id,
                "candidate_ids": [candidate_id],
                "selected_candidate_id": candidate_id,
                "campaign_failure_attribution": {
                    "primary_gate": "candidate_replay",
                    "code": "replay_total_timeout",
                    "failure_class": "measurement",
                    "failure_owner": "framework",
                    "failure_scope": "shared_run",
                    "failure_stage": "evaluation",
                    "repairable": True,
                    "next_action": "continue_measurement",
                },
            }
        )
        run_path = tmp_path / ".aworld" / "self_evolve" / run_id
        candidate_path = run_path / "candidates" / f"{candidate_id}.json"
        candidate_path.parent.mkdir(parents=True)
        candidate_path.write_text("{}", encoding="utf-8")
        report_path = run_path / "report.json"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return {
            "run_id": run_id,
            "status": "rejected",
            "report_path": str(report_path),
        }

    monkeypatch.setattr(
        campaign_module,
        "_measurement_resume_checkpoint",
        lambda store, *, run_id, report: (
            SimpleNamespace(candidate_id="candidate-measurement-pending")
            if (
                store.run_path(run_id)
                / "candidates"
                / "candidate-measurement-pending.json"
            ).is_file()
            else None
        ),
    )

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
        },
        max_improvement_cycles=1,
        run_once=run_once,
    )

    assert len(calls) == 3
    assert result["campaign_status"] == "exhausted"
    assert result["campaign_measurement_retry_count"] == 2
    assert result["campaign_candidate_cycle_count"] == 1
    assert result["self_improvement_disposition"]["reason_code"] == (
        "campaign_measurement_retry_limit_reached"
    )


def test_resume_restores_retryable_member_timeout_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    controller = SelfImprovementCampaignController(
        workspace_root=tmp_path,
        run_once=lambda **request: _write_successful_campaign_fixture(
            tmp_path,
            calls,
            request,
        ),
    )
    campaign = controller.create(
        {
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
        },
        max_cycles=3,
    )
    run_ids = tuple(
        f"{campaign.campaign_id}-cycle-{index:03d}" for index in range(1, 6)
    )
    candidate_id = "candidate-member-timeout"
    for run_id in run_ids[:-1]:
        controller.store.write_report(
            run_id,
            {"run_id": run_id, "status": "rejected", "budget": _budget()},
        )
    latest_report = {
        "run_id": run_ids[-1],
        "status": "rejected",
        "budget": _budget(),
        "candidate_ids": [candidate_id],
        "replay": {
            "candidate": {
                "variant_id": candidate_id,
                "failure": {
                    "code": "replay_member_phase_timeout",
                    "owner": "framework",
                    "scope": "member",
                    "repairable": True,
                },
            }
        },
        "campaign_measurement_outcome": {
            "schema_version": "aworld.self_evolve.campaign_measurement_outcome.v2",
            "execution_status": "completed",
            "improvement_outcome": "no_effect",
            "release_gates_passed": False,
            "continuation_available": True,
            "reason_code": "no_effect_candidate_repair_available",
            "projection": "candidate_rejected",
        },
        "verification_funnel": {
            "authoritative_candidate_attempt_count": 1,
            "authoritative_candidate_count": 1,
            "authoritative_candidate_ids": [candidate_id],
        },
    }
    latest_report_path = controller.store.write_report(
        run_ids[-1], latest_report
    )
    exhausted = campaign_module.replace(
        campaign,
        status=SelfImprovementCampaignStatus.EXHAUSTED,
        cycle_index=5,
        run_ids=run_ids,
        cumulative_authoritative_candidates=3,
        measurement_ledger=(
            campaign.measurement_ledger.charge_continuation(run_ids[1])
            .charge_continuation(run_ids[3])
        ),
        latest_progress=self_improvement_progress(latest_report),
        latest_disposition=SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.EXHAUSTED,
            reason_code="campaign_cycle_and_authoritative_frontier_exhausted",
            owner="candidate",
            stage="measurement",
            scope="candidate",
            repairable=False,
        ),
        latest_measurement_outcome=(
            campaign_module.CampaignMeasurementOutcomeV2.from_dict(
                latest_report["campaign_measurement_outcome"]
            )
        ),
        latest_report_path=str(latest_report_path),
    )
    controller.store.write_campaign(exhausted)
    checkpoint = SimpleNamespace(
        candidate_id=candidate_id,
        source_run_id=run_ids[3],
    )
    monkeypatch.setattr(
        campaign_module,
        "_measurement_resume_checkpoint",
        lambda store, *, run_id, report: (
            checkpoint if run_id == run_ids[3] else None
        ),
    )

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={},
        resume_campaign=campaign.campaign_id,
        run_once=controller.run_once,
    )

    assert len(calls) == 1
    assert calls[0]["campaign_cycle"] == 6
    assert calls[0]["campaign_measurement_pending_run_id"] == run_ids[3]
    assert calls[0]["campaign_measurement_pending_candidate_id"] == candidate_id
    assert calls[0]["max_full_evaluation_candidates"] == 1
    assert result["campaign_status"] == "complete"
    assert result["campaign_measurement_retry_count"] == 1
    assert result["campaign_authoritative_candidate_count"] == 3
    migrated = controller.store.read_report(run_ids[-1])
    assert migrated["campaign_causal_migration"]["action"] == (
        "restore_retryable_member_measurement_checkpoint"
    )
    assert migrated["campaign_causal_migration"]["candidate_reserve_restored"] == 1


def test_measurement_retry_reuses_source_checkpoint_after_resumed_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    candidate_id = "candidate-frozen-measurement"

    def run_once(**request):
        calls.append(request)
        run_id = f"{request['campaign_id']}-cycle-{request['campaign_cycle']:03d}"
        if request["campaign_cycle"] < 3:
            report = {
                "run_id": run_id,
                "status": "rejected",
                "budget": _budget(),
                "candidate_ids": [candidate_id],
                "selected_candidate_id": candidate_id,
                "campaign_failure_attribution": {
                    "primary_gate": "candidate_replay",
                    "code": "replay_member_phase_timeout",
                    "failure_class": "measurement",
                    "failure_owner": "framework",
                    "failure_scope": "shared_run",
                    "failure_stage": "evaluation",
                    "repairable": True,
                },
            }
            candidate_path = (
                tmp_path
                / ".aworld"
                / "self_evolve"
                / run_id
                / "candidates"
                / f"{candidate_id}.json"
            )
            candidate_path.parent.mkdir(parents=True, exist_ok=True)
            candidate_path.write_text("{}", encoding="utf-8")
        else:
            report = {
                "run_id": run_id,
                "status": "succeeded",
                "budget": _budget(),
                "gate_results": [{"gate_name": "post_apply", "passed": True}],
                "verification_funnel": {"authoritative_candidate_count": 1},
            }
        report_path = tmp_path / ".aworld" / "self_evolve" / run_id / "report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return {"run_id": run_id, "status": report["status"], "report_path": str(report_path)}

    source_run_id: str | None = None

    def checkpoint_for_source(store, *, run_id, report):
        nonlocal source_run_id
        if source_run_id is None:
            source_run_id = run_id
        if run_id != source_run_id:
            return None
        return SimpleNamespace(candidate_id=candidate_id, source_run_id=source_run_id)

    monkeypatch.setattr(
        campaign_module,
        "_measurement_resume_checkpoint",
        checkpoint_for_source,
    )

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={
            "from_trajectory": "trajectory.log",
            "apply_policy": "verified_only",
            "infer_target": True,
        },
        max_improvement_cycles=1,
        run_once=run_once,
    )

    assert len(calls) == 3
    assert source_run_id is not None
    assert calls[1]["campaign_measurement_pending_run_id"] == source_run_id
    assert calls[2]["campaign_measurement_pending_run_id"] == source_run_id
    assert result["campaign_status"] == "complete"
    assert result["campaign_measurement_retry_count"] == 2


def test_campaign_has_no_implicit_default_budget_per_cycle(
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
    assert all("total_run_token_budget" not in call for call in calls)
    assert all("max_run_tokens" not in call for call in calls)


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


def test_campaign_prioritizes_cross_run_champion_feedback(tmp_path: Path) -> None:
    calls: list[dict] = []

    def run_once(**request):
        calls.append(request)
        cycle = request["campaign_cycle"]
        run_id = f"{request['campaign_id']}-cycle-{cycle:03d}"
        if cycle == 1:
            report = _report(_event(constraint="payload.items[*].kind"))
            report["candidate_metrics"] = {
                "score": 90,
                "command_pass_rate": 1.0,
                "global_regression_passed": True,
                "deterministic_signal": True,
            }
            report["selected_candidate_id"] = "candidate-strong"
        elif cycle == 2:
            report = _report(
                _event(constraint="payload.items[*].kind"),
                _event(constraint="payload.items[*].transport"),
            )
            report["candidate_metrics"] = {
                "score": 70,
                "command_pass_rate": 1.0,
                "global_regression_passed": True,
                "deterministic_signal": True,
            }
            report["selected_candidate_id"] = "candidate-weaker"
        else:
            report = {
                "run_id": run_id,
                "status": "succeeded",
                "budget": _budget(),
                "gate_results": [{"gate_name": "post_apply", "passed": True}],
            }
        report["run_id"] = run_id
        report_path = (
            tmp_path / ".aworld" / "self_evolve" / run_id / "report.json"
        )
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
    assert calls[2]["campaign_prior_run_ids"] == (
        f"{calls[0]['campaign_id']}-cycle-002",
        f"{calls[0]['campaign_id']}-cycle-001",
    )
    assert calls[2]["campaign_scheduler_checkpoint_run_ids"] == (
        f"{calls[0]['campaign_id']}-cycle-001",
        f"{calls[0]['campaign_id']}-cycle-002",
    )


def test_campaign_prioritizes_deepest_repair_focus_champion(tmp_path: Path) -> None:
    controller = SelfImprovementCampaignController(workspace_root=tmp_path)
    compile_run = "campaign-repair-cycle-001"
    preflight_run = "campaign-repair-cycle-002"
    compile_report = _report(_event())
    compile_report.update(
        {
            "run_id": compile_run,
            "repair_focus_candidate_id": "candidate-compile",
        }
    )
    preflight_event = _event(constraint="services[*].readiness.kind")
    preflight_event["stage"] = "capability_preflight"
    preflight_report = _report(preflight_event)
    preflight_report.update(
        {
            "run_id": preflight_run,
            "repair_focus_candidate_id": "candidate-preflight",
        }
    )
    controller.store.write_report(compile_run, compile_report)
    controller.store.write_report(preflight_run, preflight_report)

    ordered = campaign_module._campaign_prior_run_ids_by_champion(
        controller.store,
        (compile_run, preflight_run),
    )

    assert self_improvement_progress(compile_report).deepest_stage_rank == 3
    assert self_improvement_progress(preflight_report).deepest_stage_rank == 4
    assert ordered[-1] == preflight_run


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


def test_retryable_infrastructure_keeps_specific_cycle_limit_reason(
    tmp_path: Path,
) -> None:
    calls: list[dict] = []

    def run_once(**request):
        calls.append(request)
        run_id = f"{request['campaign_id']}-cycle-{request['campaign_cycle']:03d}"
        report = {
            "run_id": run_id,
            "status": "failed",
            "budget": _budget(),
            "terminal_cause": {
                "code": "evaluation_runtime_unhealthy",
                "failure_class": "infrastructure",
                "retryable": True,
                "stage": "evaluation_runtime_health",
            },
            "gate_results": [
                {
                    "gate_name": "evaluation_runtime_health",
                    "passed": False,
                    "details": {
                        "causal_failure_events": [
                            {
                                "code": "evaluation_runtime_unhealthy",
                                "owner": "infrastructure",
                                "stage": "evaluation_runtime_health",
                                "scope": "shared_run",
                                "repairable": True,
                            }
                        ]
                    },
                }
            ],
        }
        report_path = tmp_path / ".aworld" / "self_evolve" / run_id / "report.json"
        report_path.parent.mkdir(parents=True)
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return {"run_id": run_id, "status": "failed", "report_path": str(report_path)}

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={
            "from_trajectory": "trajectory.log",
            "apply_policy": "auto_verified",
            "infer_target": True,
        },
        max_improvement_cycles=1,
        run_once=run_once,
    )

    assert len(calls) == 1
    assert result["campaign_status"] == "exhausted"
    assert result["self_improvement_disposition"]["reason_code"] == (
        "campaign_infrastructure_retry_limit_reached"
    )
    assert result["self_improvement_disposition"]["owner"] == "infrastructure"


def test_campaign_no_target_uses_zero_usage_and_pauses_for_operator(
    tmp_path: Path,
) -> None:
    trajectory = [
        {
            "meta": {"step": 1},
            "state": {"input": {"content": "Summarize the supplied material."}},
            "action": {"content": "Summary complete."},
            "reward": {"status": "success"},
        }
    ]

    result = run_self_improvement_campaign(
        workspace_root=tmp_path,
        request={
            "current_trajectory": trajectory,
            "task": "low-signal-task",
            "apply_policy": "auto_verified",
            "infer_target": True,
            "max_run_tokens": 1_000,
        },
        max_improvement_cycles=3,
    )

    assert result["status"] == "rejected"
    assert result["campaign_status"] == "paused"
    assert result["campaign_cycle"] == 1
    assert result["self_improvement_disposition"] == {
        "schema_version": "aworld.self_evolve.disposition.v1",
        "kind": "pause_operator",
        "reason_code": "target_selection_no_target",
        "owner": "task",
        "stage": "target_selection",
        "scope": "shared_run",
        "repairable": False,
        "continuable": False,
        "progress_delta_ids": [],
        "diagnostic_refs": [],
    }
    report = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
    assert report["budget"]["ledger"]["spent_by_stage"] == {}
    assert report["budget"]["ledger"]["ceilings"]["total_tokens"] == 1_000


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


def test_campaign_archives_dead_incomplete_run_and_retries_same_cycle(
    tmp_path: Path,
) -> None:
    calls: list[dict] = []

    def run_once(**request):
        calls.append(request)
        run_id = f"{request['campaign_id']}-cycle-{request['campaign_cycle']:03d}"
        report = {
            "run_id": run_id,
            "status": "succeeded",
            "budget": _budget(10),
            "gate_results": [{"gate_name": "post_apply", "passed": True}],
        }
        report_path = tmp_path / ".aworld" / "self_evolve" / run_id / "report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return {
            "run_id": run_id,
            "status": "succeeded",
            "report_path": str(report_path),
        }

    controller = SelfImprovementCampaignController(
        workspace_root=tmp_path,
        run_once=run_once,
    )
    campaign = controller.create(
        {
            "from_trajectory": "trajectory.log",
            "apply_policy": "auto_verified",
            "infer_target": True,
        },
        max_cycles=3,
    )
    run_id = f"{campaign.campaign_id}-cycle-001"
    run_dir = tmp_path / ".aworld" / "self_evolve" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps({"run_id": run_id, "status": "running"}),
        encoding="utf-8",
    )
    (run_dir / ".active.json").write_text(
        json.dumps(
            {
                "hostname": socket.gethostname(),
                "pid": 2_147_483_647,
                "started_at": 1.0,
            }
        ),
        encoding="utf-8",
    )

    advanced, summary = controller.advance_once(campaign)

    assert advanced.status is SelfImprovementCampaignStatus.COMPLETE
    assert advanced.cycle_index == 1
    assert advanced.cumulative_usage.tokens == 10
    assert calls[0]["campaign_cycle"] == 1
    assert "total_run_token_budget" not in calls[0]
    archive = Path(summary["interrupted_run_archive_path"])
    assert archive.name == f"{run_id}-attempt-001"
    assert (archive / "interruption.json").is_file()


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


def test_framework_blocked_run_does_not_spend_candidate_cycle_budget() -> None:
    ledger = CampaignMeasurementLedgerV2().charge_framework_blocked(
        "campaign-demo-cycle-001"
    )

    assert ledger.framework_blocked_count == 1
    assert ledger.control_plane_run_count == 1
    assert CampaignMeasurementLedgerV2.from_dict(ledger.to_dict()) == ledger

    with pytest.raises(ValueError, match="two ledgers"):
        ledger.charge_invalid_retry("campaign-demo-cycle-001")
