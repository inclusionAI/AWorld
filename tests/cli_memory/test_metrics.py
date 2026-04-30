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
