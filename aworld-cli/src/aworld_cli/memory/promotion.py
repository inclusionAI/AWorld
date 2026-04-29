from __future__ import annotations

import os
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import re

from aworld_cli.memory.durable import INSTRUCTION_MEMORY_TYPES

INSTRUCTIONAL_HINTS = (
    "use ",
    "prefer ",
    "always ",
    "remember ",
    "keep ",
    "do not ",
    "don't ",
    "must ",
    "should ",
)
TEMPORARY_HINTS = (
    "temporary",
    "current task",
    "for now",
    "debug note",
    "this run",
)
WORD_PATTERN = re.compile(r"\b[a-zA-Z][a-zA-Z0-9_-]{2,}\b")
STRONG_INSTRUCTIONAL_HINTS = (
    "always use ",
    "never run ",
    "never use ",
    "must use ",
    "must never ",
    "do not use ",
    "don't use ",
)
WORKSPACE_ANCHOR_HINTS = (
    "workspace",
    "package",
    "pnpm",
    "npm",
    "yarn",
    "test",
    "tests",
    "ci",
    "lint",
    "eslint",
    "prettier",
    "format",
    "repo",
    "repository",
    "code",
    "commit",
    "branch",
    "pull request",
    "pr ",
    "changelog",
    "directory",
    "file",
    "path",
)


@dataclass(frozen=True)
class PromotionDecision:
    memory_type: str
    source: str
    content: str
    confidence: str
    promotion: str
    reason: str
    eligible_for_auto_promotion: bool
    evaluated_at: str

    def to_payload(self) -> dict:
        return asdict(self)


def evaluate_turn_end_candidate(
    content: str,
    *,
    memory_type: str = "workspace",
    source: str = "final_answer",
) -> PromotionDecision:
    normalized = (content or "").strip()
    if not normalized:
        raise ValueError("Turn-end promotion candidate must not be empty")

    if _looks_high_confidence_instructional(normalized):
        confidence = "high"
        reason = "high_confidence_workspace_instruction_candidate"
        eligible = True
    elif _looks_instructional(normalized):
        confidence = "medium"
        reason = "instructional_candidate_auto_promotion_disabled"
        eligible = True
    else:
        confidence = "low"
        reason = "non_instructional_turn_end_observation"
        eligible = False

    return PromotionDecision(
        memory_type=memory_type,
        source=source,
        content=normalized,
        confidence=confidence,
        promotion="session_log_only",
        reason=reason,
        eligible_for_auto_promotion=eligible,
        evaluated_at=datetime.now(timezone.utc).isoformat(),
    )


def auto_promotion_enabled() -> bool:
    raw = os.getenv("AWORLD_CLI_ENABLE_AUTO_PROMOTION", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def should_auto_promote(decision: PromotionDecision) -> bool:
    return (
        auto_promotion_enabled()
        and decision.eligible_for_auto_promotion
        and decision.confidence == "high"
        and decision.memory_type in INSTRUCTION_MEMORY_TYPES
    )


def mark_auto_promoted(decision: PromotionDecision) -> PromotionDecision:
    return replace(
        decision,
        promotion="durable_memory",
        reason="high_confidence_workspace_instruction_auto_promoted",
    )


def candidate_payload(
    decision: PromotionDecision,
    *,
    auto_promoted: bool = False,
) -> dict:
    return {
        **decision.to_payload(),
        "auto_promoted": auto_promoted,
    }


def _looks_high_confidence_instructional(content: str) -> bool:
    lowered = content.lower()
    if any(hint in lowered for hint in TEMPORARY_HINTS):
        return False
    return any(hint in lowered for hint in STRONG_INSTRUCTIONAL_HINTS) and _has_workspace_anchor(lowered)


def _looks_instructional(content: str) -> bool:
    lowered = content.lower()
    if any(hint in lowered for hint in TEMPORARY_HINTS):
        return False
    if _looks_high_confidence_instructional(content):
        return True
    if any(hint in lowered for hint in INSTRUCTIONAL_HINTS):
        return True

    tokens = WORD_PATTERN.findall(lowered)
    return len(tokens) >= 6 and ("workspace" in lowered or "tests" in lowered)


def _has_workspace_anchor(lowered: str) -> bool:
    return any(hint in lowered for hint in WORKSPACE_ANCHOR_HINTS)
