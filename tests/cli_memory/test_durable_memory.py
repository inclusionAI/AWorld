from pathlib import Path

import pytest

from aworld_cli.memory import provider as provider_module
from aworld_cli.memory.durable import (
    append_durable_memory_record,
    normalize_memory_kind,
    read_all_durable_memory_records,
)
from aworld_cli.memory.governance import append_governed_decision, evaluate_governed_candidate
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


def test_append_durable_memory_record_persists_memory_kind(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    result = append_durable_memory_record(
        workspace,
        memory_type="workspace",
        memory_kind="workflow",
        text="Use pnpm for workspace package management",
        source="remember_command",
    )

    records = read_all_durable_memory_records(workspace)

    assert result.record_created is True
    assert records[0].memory_type == "workspace"
    assert records[0].memory_kind == "workflow"


def test_read_all_durable_memory_records_preserves_legacy_untyped_entries(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    durable_file = workspace / ".aworld" / "memory" / "durable.jsonl"
    durable_file.parent.mkdir(parents=True, exist_ok=True)
    durable_file.write_text(
        '{"recorded_at":"2026-05-08T00:00:00+00:00","memory_type":"workspace","content":"Use pnpm","source":"remember_command"}\n',
        encoding="utf-8",
    )

    records = read_all_durable_memory_records(workspace)

    assert len(records) == 1
    assert records[0].memory_type == "workspace"
    assert records[0].memory_kind is None


def test_read_all_durable_memory_records_degrades_invalid_memory_kind_to_none(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    durable_file = workspace / ".aworld" / "memory" / "durable.jsonl"
    durable_file.parent.mkdir(parents=True, exist_ok=True)
    durable_file.write_text(
        '{"recorded_at":"2026-05-08T00:00:00+00:00","memory_type":"workspace","memory_kind":"opinionated","content":"Use pnpm","source":"remember_command"}\n',
        encoding="utf-8",
    )

    records = read_all_durable_memory_records(workspace)

    assert len(records) == 1
    assert records[0].content == "Use pnpm"
    assert records[0].memory_kind is None


def test_read_all_durable_memory_records_degrades_non_string_memory_kind_to_none(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    durable_file = workspace / ".aworld" / "memory" / "durable.jsonl"
    durable_file.parent.mkdir(parents=True, exist_ok=True)
    durable_file.write_text(
        '{"recorded_at":"2026-05-08T00:00:00+00:00","memory_type":"workspace","memory_kind":123,"content":"Use pnpm","source":"remember_command"}\n',
        encoding="utf-8",
    )

    records = read_all_durable_memory_records(workspace)

    assert len(records) == 1
    assert records[0].content == "Use pnpm"
    assert records[0].memory_kind is None


def test_normalize_memory_kind_rejects_unknown_values() -> None:
    with pytest.raises(ValueError, match="Invalid durable memory kind"):
        normalize_memory_kind("opinionated")


def test_provider_append_durable_memory_record_threads_memory_kind(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    provider = CliDurableMemoryProvider()

    result = provider.append_durable_memory_record(
        workspace_path=workspace,
        text="Use pnpm for workspace package management",
        memory_type="workspace",
        memory_kind="workflow",
        source="remember_command",
    )

    records = provider.get_durable_memory_records(workspace)

    assert result.record_created is True
    assert len(records) == 1
    assert records[0].memory_kind == "workflow"
    assert records[0].memory_type == "workspace"


def test_typed_fact_durable_write_does_not_mirror_into_workspace_instructions(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    provider = CliDurableMemoryProvider()

    result = provider.append_durable_memory_record(
        workspace_path=workspace,
        text="The release branch is cut from main every Thursday.",
        memory_type="workspace",
        memory_kind="fact",
        source="remember_command",
    )

    records = provider.get_durable_memory_records(workspace)

    assert result.record_created is True
    assert result.instruction_target is None
    assert result.instruction_updated is False
    assert records[0].memory_kind == "fact"
    assert not (workspace / ".aworld" / "AWORLD.md").exists()


def test_typed_instructional_write_mirrors_using_normalized_memory_kind(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    provider = CliDurableMemoryProvider()

    result = provider.append_durable_memory_record(
        workspace_path=workspace,
        text="Use pnpm for workspace package management.",
        memory_type="workspace",
        memory_kind=" Workflow ",
        source="remember_command",
    )

    records = provider.get_durable_memory_records(workspace)

    assert result.record_created is True
    assert result.instruction_target == workspace / ".aworld" / "AWORLD.md"
    assert result.instruction_updated is True
    assert records[0].memory_kind == "workflow"
    assert "Use pnpm for workspace package management." in result.instruction_target.read_text(
        encoding="utf-8"
    )


def test_provider_delegates_instruction_eligibility_to_durable_helper(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    provider = CliDurableMemoryProvider()
    captured: list[tuple[str, str | None]] = []

    def _fake_instruction_eligibility(*, memory_type: str, memory_kind: str | None) -> bool:
        captured.append((memory_type, memory_kind))
        return False

    monkeypatch.setattr(
        provider_module,
        "is_instruction_eligible_memory",
        _fake_instruction_eligibility,
        raising=False,
    )

    result = provider.append_durable_memory_record(
        workspace_path=workspace,
        text="Use pnpm for workspace package management.",
        memory_type="workspace",
        memory_kind="workflow",
        source="remember_command",
    )

    assert captured == [("workspace", "workflow")]
    assert result.instruction_target is None
    assert result.instruction_updated is False
    assert not (workspace / ".aworld" / "AWORLD.md").exists()


def test_append_durable_memory_record_allows_typed_write_after_legacy_untyped_duplicate(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    first_result = append_durable_memory_record(
        workspace,
        memory_type="workspace",
        text="Use pnpm for workspace package management",
        source="remember_command",
    )
    second_result = append_durable_memory_record(
        workspace,
        memory_type="workspace",
        memory_kind="workflow",
        text="Use pnpm for workspace package management",
        source="remember_command",
    )

    records = read_all_durable_memory_records(workspace)

    assert first_result.record_created is True
    assert second_result.record_created is True
    assert len(records) == 2
    assert [record.memory_kind for record in records] == [None, "workflow"]


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


def test_reverted_governed_memory_no_longer_blocks_repromotion(tmp_path) -> None:
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

    decision = evaluate_governed_candidate(
        workspace_path=workspace,
        candidate={
            "candidate_id": "cand-456",
            "content": "Use pnpm for workspace package management",
            "memory_type": "workspace",
            "confidence": "high",
            "source_ref": {
                "session_id": "session-2",
                "task_id": "task-2",
                "candidate_id": "cand-456",
            },
        },
        mode="governed",
    )
    write_result = provider.append_durable_memory_record(
        workspace_path=workspace,
        text="Use pnpm for workspace package management",
        memory_type="workspace",
        source="remember_command",
    )

    assert decision.decision == "durable_memory"
    assert "duplicate_active_durable_memory" not in decision.blockers
    assert write_result.record_created is True


def test_latest_review_state_controls_activity_and_duplicate_blocking(tmp_path) -> None:
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
    provider.record_governed_review(
        workspace,
        decision_id="gdec_123",
        review_action="confirmed",
    )

    active_records = provider.get_active_durable_memory_records(workspace)
    decision = evaluate_governed_candidate(
        workspace_path=workspace,
        candidate={
            "candidate_id": "cand-456",
            "content": "Use pnpm for workspace package management",
            "memory_type": "workspace",
            "confidence": "high",
            "source_ref": {
                "session_id": "session-2",
                "task_id": "task-2",
                "candidate_id": "cand-456",
            },
        },
        mode="governed",
    )
    write_result = provider.append_durable_memory_record(
        workspace_path=workspace,
        text="Use pnpm for workspace package management",
        memory_type="workspace",
        source="remember_command",
    )

    assert len(active_records) == 1
    assert active_records[0].decision_id == "gdec_123"
    assert decision.decision == "rejected"
    assert "duplicate_active_durable_memory" in decision.blockers
    assert write_result.record_created is False
