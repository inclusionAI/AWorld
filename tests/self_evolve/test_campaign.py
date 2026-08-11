from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
import aworld.self_evolve.campaign as campaign_module

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


def test_advisory_measurement_routes_measurement_repair() -> None:
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

    assert disposition.kind is SelfImprovementDispositionKind.REPAIR_MEASUREMENT
    assert disposition.owner == "evaluation_harness"


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

    assert disposition.kind is SelfImprovementDispositionKind.REPAIR_MEASUREMENT
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
