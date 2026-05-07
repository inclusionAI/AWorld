from aworld_cli.memory.governance import append_governed_decision, append_governed_review
from aworld_cli.memory.metrics import (
    append_promotion_metric,
    summarize_promotion_metrics,
)
from aworld_cli.memory.promotion import evaluate_turn_end_candidate


def test_promotion_metrics_summary_counts_decisions_by_outcome(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    append_promotion_metric(
        workspace_path=workspace,
        session_id="session-1",
        task_id="task-1",
        decision=evaluate_turn_end_candidate("Use pnpm and keep tests fast."),
    )
    append_promotion_metric(
        workspace_path=workspace,
        session_id="session-1",
        task_id="task-2",
        decision=evaluate_turn_end_candidate("Temporary debug note for the current task only."),
    )

    summary = summarize_promotion_metrics(workspace)

    assert summary.metrics_path == workspace / ".aworld" / "memory" / "metrics" / "promotion.jsonl"
    assert summary.total_evaluations == 2
    assert summary.eligible_for_auto_promotion == 1
    assert summary.by_confidence == {"low": 1, "medium": 1}
    assert summary.by_promotion == {"session_log_only": 2}
    assert summary.by_reason == {
        "instructional_candidate_auto_promotion_disabled": 1,
        "non_instructional_turn_end_observation": 1,
    }


def test_promotion_metrics_summary_reports_quality_and_threshold_readiness(
    tmp_path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    append_governed_decision(
        workspace,
        {
            "decision_id": "gdec_1",
            "policy_mode": "shadow",
            "policy_version": "2026-05-07",
            "decision": "session_log_only",
            "reason": "shadow_mode_no_auto_promotion",
            "confidence": "medium",
            "source_ref": {
                "session_id": "session-1",
                "task_id": "task-1",
                "candidate_id": "cand-1",
            },
            "blockers": [],
        },
    )
    append_governed_review(
        workspace,
        {"decision_id": "gdec_1", "review_action": "confirmed"},
    )

    append_governed_decision(
        workspace,
        {
            "decision_id": "gdec_2",
            "policy_mode": "governed",
            "policy_version": "2026-05-07",
            "decision": "durable_memory",
            "reason": "governed_policy_pass",
            "confidence": "high",
            "source_ref": {
                "session_id": "session-1",
                "task_id": "task-2",
                "candidate_id": "cand-2",
            },
            "blockers": [],
        },
    )
    append_governed_review(
        workspace,
        {"decision_id": "gdec_2", "review_action": "confirmed"},
    )
    append_governed_review(
        workspace,
        {"decision_id": "gdec_2", "review_action": "reverted"},
    )

    append_governed_decision(
        workspace,
        {
            "decision_id": "gdec_3",
            "policy_mode": "shadow",
            "policy_version": "2026-05-07",
            "decision": "session_log_only",
            "reason": "shadow_mode_no_auto_promotion",
            "confidence": "medium",
            "source_ref": {
                "session_id": "session-1",
                "task_id": "task-3",
                "candidate_id": "cand-3",
            },
            "blockers": [],
        },
    )

    summary = summarize_promotion_metrics(workspace)

    assert summary.reviewed_promotions == 2
    assert summary.confirmed_promotions == 1
    assert summary.reverted_promotions == 1
    assert summary.pending_review == 1
    assert summary.precision_proxy == 0.5
    assert summary.pollution_proxy == 0.5
    assert summary.default_rollout_ready is False
