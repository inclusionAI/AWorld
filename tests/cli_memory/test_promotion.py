from aworld_cli.memory.promotion import evaluate_turn_end_candidate


def test_evaluate_turn_end_candidate_marks_instructional_content_as_eligible_but_not_promoted():
    decision = evaluate_turn_end_candidate(
        "Use pnpm for workspace package management and keep tests fast.",
    )

    assert decision.memory_type == "workspace"
    assert decision.source == "final_answer"
    assert decision.content == "Use pnpm for workspace package management and keep tests fast."
    assert decision.confidence == "medium"
    assert decision.promotion == "session_log_only"
    assert decision.reason == "instructional_candidate_auto_promotion_disabled"
    assert decision.eligible_for_auto_promotion is True
    assert decision.evaluated_at


def test_evaluate_turn_end_candidate_marks_strong_instructional_content_as_high_confidence():
    decision = evaluate_turn_end_candidate(
        "Always use pnpm for workspace package management and never run npm install here.",
    )

    assert decision.confidence == "high"
    assert decision.promotion == "session_log_only"
    assert decision.reason == "high_confidence_workspace_instruction_candidate"
    assert decision.eligible_for_auto_promotion is True


def test_evaluate_turn_end_candidate_does_not_mark_non_workspace_instruction_as_high_confidence():
    decision = evaluate_turn_end_candidate(
        "Must never ship broken onboarding copy to customers.",
    )

    assert decision.confidence == "medium"
    assert decision.promotion == "session_log_only"
    assert decision.reason == "instructional_candidate_auto_promotion_disabled"
    assert decision.eligible_for_auto_promotion is True


def test_evaluate_turn_end_candidate_keeps_non_instructional_content_as_low_confidence():
    decision = evaluate_turn_end_candidate(
        "Temporary debug note for the current task only.",
    )

    assert decision.confidence == "low"
    assert decision.promotion == "session_log_only"
    assert decision.reason == "non_instructional_turn_end_observation"
    assert decision.eligible_for_auto_promotion is False
