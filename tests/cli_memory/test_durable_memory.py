from pathlib import Path

from aworld_cli.memory.governance import append_governed_decision
from aworld_cli.memory.provider import CliDurableMemoryProvider


def test_provider_lists_explicit_durable_records_by_type(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    provider = CliDurableMemoryProvider()

    result = provider.append_durable_memory_record(
        workspace_path=workspace,
        text="Use pnpm for workspace package management",
        memory_type="workspace",
        source="remember_command",
    )

    assert result.record_created is True
    assert result.record_path == workspace / ".aworld" / "memory" / "durable.jsonl"
    assert result.instruction_target == workspace / ".aworld" / "AWORLD.md"

    records = provider.get_durable_memory_records(workspace)
    assert len(records) == 1
    assert records[0].memory_type == "workspace"
    assert records[0].content == "Use pnpm for workspace package management"
    assert records[0].source == "remember_command"

    workspace_records = provider.get_durable_memory_records(
        workspace,
        memory_type="workspace",
    )
    assert workspace_records == records

    assert provider.get_durable_memory_records(workspace, memory_type="reference") == ()


def test_provider_lists_governed_decisions_and_records_reviews(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    provider = CliDurableMemoryProvider()

    append_governed_decision(
        workspace,
        {
            "decision_id": "gdec_123",
            "candidate_id": "cand-123",
            "decision": "session_log_only",
            "policy_mode": "shadow",
            "policy_version": "2026-05-07",
            "reason": "shadow_mode_no_auto_promotion",
            "blockers": [],
            "confidence": "high",
            "memory_type": "workspace",
            "content": "Use pnpm for workspace package management",
            "source_ref": {
                "session_id": "session-1",
                "task_id": "task-1",
                "candidate_id": "cand-123",
            },
            "evaluated_at": "2026-05-07T00:00:00+00:00",
        },
    )

    review_path = provider.record_governed_review(
        workspace,
        decision_id="gdec_123",
        review_action="confirmed",
    )

    decisions = provider.list_governed_decisions(workspace)

    assert review_path == workspace / ".aworld" / "memory" / "metrics" / "promotion_reviews.jsonl"
    assert decisions == (
        {
            "decision_id": "gdec_123",
            "candidate_id": "cand-123",
            "decision": "session_log_only",
            "policy_mode": "shadow",
            "policy_version": "2026-05-07",
            "reason": "shadow_mode_no_auto_promotion",
            "blockers": [],
            "confidence": "high",
            "memory_type": "workspace",
            "content": "Use pnpm for workspace package management",
            "source_ref": {
                "session_id": "session-1",
                "task_id": "task-1",
                "candidate_id": "cand-123",
            },
            "evaluated_at": "2026-05-07T00:00:00+00:00",
            "reviews": [
                {
                    "decision_id": "gdec_123",
                    "review_action": "confirmed",
                }
            ],
        },
    )


def test_provider_active_durable_records_exclude_reverted_governed_promotions(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    provider = CliDurableMemoryProvider()

    provider.append_durable_memory_record(
        workspace_path=workspace,
        text="Use pnpm for workspace package management",
        memory_type="workspace",
        source="governed_auto_promotion",
        decision_id="gdec_123",
        source_ref={
            "session_id": "session-1",
            "task_id": "task-1",
            "candidate_id": "cand-123",
        },
    )
    provider.append_durable_memory_record(
        workspace_path=workspace,
        text="Keep test runs fast",
        memory_type="workspace",
        source="remember_command",
    )

    append_governed_decision(
        workspace,
        {
            "decision_id": "gdec_123",
            "candidate_id": "cand-123",
            "decision": "durable_memory",
            "policy_mode": "governed",
            "policy_version": "2026-05-07",
            "reason": "governed_policy_pass",
            "blockers": [],
            "confidence": "high",
            "memory_type": "workspace",
            "content": "Use pnpm for workspace package management",
            "source_ref": {
                "session_id": "session-1",
                "task_id": "task-1",
                "candidate_id": "cand-123",
            },
            "evaluated_at": "2026-05-07T00:00:00+00:00",
        },
    )
    provider.record_governed_review(
        workspace,
        decision_id="gdec_123",
        review_action="reverted",
    )

    active_records = provider.get_active_durable_memory_records(workspace)

    assert len(active_records) == 1
    assert active_records[0].content == "Keep test runs fast"
    assert active_records[0].source == "remember_command"


def test_provider_active_durable_records_revert_by_decision_id_not_content(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    provider = CliDurableMemoryProvider()

    provider.append_durable_memory_record(
        workspace_path=workspace,
        text="Use pnpm for workspace package management",
        memory_type="workspace",
        source="governed_auto_promotion",
        decision_id="gdec_active",
        source_ref={
            "session_id": "session-1",
            "task_id": "task-1",
            "candidate_id": "cand-active",
        },
    )

    append_governed_decision(
        workspace,
        {
            "decision_id": "gdec_active",
            "candidate_id": "cand-active",
            "decision": "durable_memory",
            "policy_mode": "governed",
            "policy_version": "2026-05-07",
            "reason": "governed_policy_pass",
            "blockers": [],
            "confidence": "high",
            "memory_type": "workspace",
            "content": "Use pnpm for workspace package management",
            "source_ref": {
                "session_id": "session-1",
                "task_id": "task-1",
                "candidate_id": "cand-active",
            },
            "evaluated_at": "2026-05-07T00:00:00+00:00",
        },
    )
    append_governed_decision(
        workspace,
        {
            "decision_id": "gdec_reverted",
            "candidate_id": "cand-reverted",
            "decision": "durable_memory",
            "policy_mode": "governed",
            "policy_version": "2026-05-07",
            "reason": "governed_policy_pass",
            "blockers": [],
            "confidence": "high",
            "memory_type": "workspace",
            "content": "Use pnpm for workspace package management",
            "source_ref": {
                "session_id": "session-2",
                "task_id": "task-2",
                "candidate_id": "cand-reverted",
            },
            "evaluated_at": "2026-05-07T00:00:00+00:00",
        },
    )
    provider.record_governed_review(
        workspace,
        decision_id="gdec_reverted",
        review_action="reverted",
    )

    active_records = provider.get_active_durable_memory_records(workspace)

    assert len(active_records) == 1
    assert active_records[0].decision_id == "gdec_active"
    assert active_records[0].content == "Use pnpm for workspace package management"
