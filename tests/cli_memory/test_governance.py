import pytest

from aworld_cli.memory.durable import append_durable_memory_record
from aworld_cli.memory.governance import (
    GovernedDecision,
    append_governed_decision,
    append_governed_review,
    evaluate_governed_candidate,
    governance_mode,
    list_governed_decisions,
)


def test_governance_mode_defaults_to_shadow(monkeypatch):
    monkeypatch.delenv("AWORLD_CLI_PROMOTION_MODE", raising=False)

    assert governance_mode() == "shadow"


@pytest.mark.parametrize(
    ("mode", "expected_decision", "expected_reason"),
    [
        ("off", "session_log_only", "governance_mode_off"),
        ("shadow", "session_log_only", "shadow_mode_no_auto_promotion"),
        ("governed", "durable_memory", "governed_policy_pass"),
    ],
)
def test_evaluate_governed_candidate_honors_explicit_policy_modes(
    tmp_path,
    mode,
    expected_decision,
    expected_reason,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    decision = evaluate_governed_candidate(
        workspace_path=workspace,
        candidate={
            "candidate_id": "cand-1",
            "content": "Use pnpm for workspace package management.",
            "memory_type": "workspace",
            "confidence": "high",
            "source_ref": {
                "session_id": "s1",
                "task_id": "t1",
                "candidate_id": "cand-1",
            },
        },
        mode=mode,
    )

    assert decision.decision == expected_decision
    assert decision.reason == expected_reason
    assert decision.policy_mode == mode


@pytest.mark.parametrize(
    "candidate",
    [
        {
            "candidate_id": "cand-1",
            "content": "",
            "memory_type": "workspace",
            "confidence": "high",
            "source_ref": {
                "session_id": "s1",
                "task_id": "t1",
                "candidate_id": "cand-1",
            },
        },
        {
            "candidate_id": "cand-1",
            "content": "Use pnpm for workspace package management.",
            "memory_type": "workspace",
            "confidence": "high",
            "source_ref": {},
        },
    ],
)
def test_evaluate_governed_candidate_requires_stable_identity_and_content_for_durable_memory(
    tmp_path,
    candidate,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    decision = evaluate_governed_candidate(
        workspace_path=workspace,
        candidate=candidate,
        mode="governed",
    )

    assert decision.decision == "rejected"
    assert decision.reason in {"missing_content", "missing_source_ref"}


def test_evaluate_governed_candidate_blocks_duplicate_active_durable_memory(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    append_durable_memory_record(
        workspace,
        memory_type="workspace",
        text="Use pnpm for workspace package management.",
        source="remember_command",
    )

    decision = evaluate_governed_candidate(
        workspace_path=workspace,
        candidate={
            "candidate_id": "cand-1",
            "content": "Use pnpm for workspace package management.",
            "memory_type": "workspace",
            "confidence": "high",
            "source_ref": {
                "session_id": "s1",
                "task_id": "t1",
                "candidate_id": "cand-1",
            },
        },
        mode="governed",
    )

    assert decision.decision == "rejected"
    assert "duplicate_active_durable_memory" in decision.blockers


def test_evaluate_governed_candidate_blocks_temporary_content(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    decision = evaluate_governed_candidate(
        workspace_path=workspace,
        candidate={
            "candidate_id": "cand-1",
            "content": "Temporary debug note for the current task only.",
            "memory_type": "workspace",
            "confidence": "low",
            "source_ref": {
                "session_id": "s1",
                "task_id": "t1",
                "candidate_id": "cand-1",
            },
        },
        mode="governed",
    )

    assert decision.decision == "rejected"
    assert "temporary_candidate" in decision.blockers


def test_evaluate_governed_candidate_rejects_ineligible_extraction_candidates(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    decision = evaluate_governed_candidate(
        workspace_path=workspace,
        candidate={
            "candidate_id": "cand-1",
            "content": "I updated the workspace and ran the tests successfully.",
            "memory_type": "workspace",
            "confidence": "low",
            "eligible_for_auto_promotion": False,
            "source_ref": {
                "session_id": "s1",
                "task_id": "t1",
                "candidate_id": "cand-1",
            },
        },
        mode="governed",
    )

    assert decision.decision == "rejected"
    assert "ineligible_extraction_candidate" in decision.blockers


def test_governed_decision_payload_exposes_inspectable_contract():
    decision = GovernedDecision(
        decision_id="dec-1",
        candidate_id="cand-1",
        decision="session_log_only",
        policy_mode="shadow",
        policy_version="2026-05-07",
        reason="shadow_mode_no_auto_promotion",
        blockers=("temporary_candidate",),
        confidence="low",
        memory_type="workspace",
        content="Temporary debug note for the current task only.",
        source_ref={
            "session_id": "s1",
            "task_id": "t1",
            "candidate_id": "cand-1",
        },
        evaluated_at="2026-05-07T00:00:00+00:00",
    )

    assert decision.to_payload() == {
        "decision_id": "dec-1",
        "candidate_id": "cand-1",
        "decision": "session_log_only",
        "policy_mode": "shadow",
        "policy_version": "2026-05-07",
        "reason": "shadow_mode_no_auto_promotion",
        "blockers": ("temporary_candidate",),
        "confidence": "low",
        "memory_type": "workspace",
        "content": "Temporary debug note for the current task only.",
        "source_ref": {
            "session_id": "s1",
            "task_id": "t1",
            "candidate_id": "cand-1",
        },
        "evaluated_at": "2026-05-07T00:00:00+00:00",
    }


def test_append_governed_decision_requires_inspectable_decision_fields(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(ValueError, match="Missing required decision fields"):
        append_governed_decision(
            workspace,
            {
                "decision_id": "dec-1",
                "decision": "session_log_only",
                "policy_mode": "shadow",
            },
        )


def test_append_governed_decision_rejects_missing_policy_mode_without_env_fallback(
    tmp_path,
    monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AWORLD_CLI_PROMOTION_MODE", "governed")

    with pytest.raises(ValueError, match="Missing required decision fields: policy_mode"):
        append_governed_decision(
            workspace,
            {
                "decision_id": "dec-2",
                "candidate_id": "cand-2",
                "decision": "session_log_only",
                "policy_version": "2026-05-07",
                "reason": "shadow_mode_no_auto_promotion",
                "blockers": [],
                "confidence": "low",
                "memory_type": "workspace",
                "content": "Legacy note.",
                "source_ref": {
                    "session_id": "s1",
                    "task_id": "t1",
                    "candidate_id": "cand-2",
                },
                "evaluated_at": "2026-05-07T00:00:00+00:00",
            },
        )


def test_append_governed_decision_and_review_are_append_only(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    append_governed_decision(
        workspace,
        {
            "decision_id": "dec-1",
            "candidate_id": "cand-1",
            "decision": "session_log_only",
            "policy_mode": "shadow",
            "policy_version": "2026-05-07",
            "reason": "shadow_mode_no_auto_promotion",
            "blockers": ["temporary_candidate"],
            "confidence": "low",
            "memory_type": "workspace",
            "content": "Temporary debug note for the current task only.",
            "source_ref": {
                "session_id": "s1",
                "task_id": "t1",
                "candidate_id": "cand-1",
            },
            "evaluated_at": "2026-05-07T00:00:00+00:00",
        },
    )
    append_governed_review(
        workspace,
        {
            "decision_id": "dec-1",
            "review_action": "confirmed",
        },
    )

    decisions = list_governed_decisions(workspace)
    assert decisions == [
        {
            "decision_id": "dec-1",
            "candidate_id": "cand-1",
            "decision": "session_log_only",
            "policy_mode": "shadow",
            "policy_version": "2026-05-07",
            "reason": "shadow_mode_no_auto_promotion",
            "blockers": ["temporary_candidate"],
            "confidence": "low",
            "memory_type": "workspace",
            "content": "Temporary debug note for the current task only.",
            "source_ref": {
                "session_id": "s1",
                "task_id": "t1",
                "candidate_id": "cand-1",
            },
            "evaluated_at": "2026-05-07T00:00:00+00:00",
            "reviews": [
                {
                    "decision_id": "dec-1",
                    "review_action": "confirmed",
                }
            ],
        }
    ]


def test_list_governed_decisions_preserves_legacy_partial_rows(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    decisions_path = workspace / ".aworld" / "memory" / "metrics" / "promotion_decisions.jsonl"
    decisions_path.parent.mkdir(parents=True, exist_ok=True)
    decisions_path.write_text(
        '{"decision_id":"legacy-1","decision":"session_log_only","reason":"legacy_row"}\n',
        encoding="utf-8",
    )

    decisions = list_governed_decisions(workspace)

    assert decisions == [
        {
            "decision_id": "legacy-1",
            "decision": "session_log_only",
            "reason": "legacy_row",
            "policy_mode": "",
            "policy_version": "",
            "confidence": "",
            "source_ref": {},
            "blockers": [],
            "reviews": [],
            "legacy_incomplete": True,
        }
    ]


def test_list_governed_decisions_preserves_legacy_rows_without_reason(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    decisions_path = workspace / ".aworld" / "memory" / "metrics" / "promotion_decisions.jsonl"
    decisions_path.parent.mkdir(parents=True, exist_ok=True)
    decisions_path.write_text(
        '{"decision_id":"legacy-2","decision":"session_log_only"}\n',
        encoding="utf-8",
    )

    decisions = list_governed_decisions(workspace)

    assert decisions == [
        {
            "decision_id": "legacy-2",
            "decision": "session_log_only",
            "reason": "",
            "policy_mode": "",
            "policy_version": "",
            "confidence": "",
            "source_ref": {},
            "blockers": [],
            "reviews": [],
            "legacy_incomplete": True,
        }
    ]
