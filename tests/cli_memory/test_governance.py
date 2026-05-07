from aworld_cli.memory.governance import (
    append_governed_decision,
    append_governed_review,
    evaluate_governed_candidate,
    governance_mode,
    list_governed_decisions,
)


def test_governance_mode_defaults_to_shadow(monkeypatch):
    monkeypatch.delenv("AWORLD_CLI_PROMOTION_MODE", raising=False)

    assert governance_mode() == "shadow"


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


def test_append_governed_decision_and_review_are_append_only(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    append_governed_decision(
        workspace,
        {
            "decision_id": "dec-1",
            "decision": "session_log_only",
            "policy_mode": "shadow",
            "source_ref": {
                "session_id": "s1",
                "task_id": "t1",
                "candidate_id": "cand-1",
            },
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
    assert decisions[0]["decision_id"] == "dec-1"
    assert decisions[0]["reviews"][-1]["review_action"] == "confirmed"
