from aworld_cli.memory.promotion import (
    evaluate_turn_end_candidate,
    extract_turn_end_candidate_content,
)


def test_extract_turn_end_candidate_content_prefers_instructional_sentence():
    content = (
        "I updated the workspace and ran the tests successfully. "
        "Always use pnpm for workspace package management and never run npm install here. "
        "Everything else looked good."
    )

    extracted = extract_turn_end_candidate_content(content)

    assert extracted == "Always use pnpm for workspace package management and never run npm install here."


def test_evaluate_turn_end_candidate_uses_extracted_instructional_sentence():
    decision = evaluate_turn_end_candidate(
        "I updated the workspace and ran the tests successfully. "
        "Use pnpm for workspace package management. "
        "Everything passed.",
    )

    assert decision.content == "Use pnpm for workspace package management."
    assert decision.confidence == "medium"
