from __future__ import annotations

from .self_test_bridge import (
    SELF_TEST_FINAL_ONLY_PROMPT,
    SELF_TEST_SLOW_PROMPT,
    SELF_TEST_TEXT_PROMPT,
    SELF_TEST_TOOL_PROMPT,
    SELF_TEST_TURN_ERROR_PROMPT,
)
from .validation import Phase1ValidationProfile


SELF_TEST_PHASE1_PROFILE = Phase1ValidationProfile(
    visible_text_prompt=SELF_TEST_TEXT_PROMPT,
    visible_text_expected="self-test",
    tool_prompt=SELF_TEST_TOOL_PROMPT,
    turn_error_prompt=SELF_TEST_TURN_ERROR_PROMPT,
    final_text_prompt=SELF_TEST_FINAL_ONLY_PROMPT,
    final_text_expected="final-only",
    slow_prompt=SELF_TEST_SLOW_PROMPT,
)

PHASE1_VALIDATION_PROFILES = {
    "self-test": SELF_TEST_PHASE1_PROFILE,
}


def list_phase1_validation_profiles() -> list[str]:
    return sorted(PHASE1_VALIDATION_PROFILES)


def resolve_phase1_validation_profile(name: str) -> Phase1ValidationProfile:
    try:
        return PHASE1_VALIDATION_PROFILES[name]
    except KeyError as exc:
        available = ", ".join(list_phase1_validation_profiles())
        raise ValueError(f"Unknown ACP validation profile: {name}. Available: {available}") from exc
