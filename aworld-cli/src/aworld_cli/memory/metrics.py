from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from aworld_cli.memory.governance import list_governed_decisions
from aworld_cli.memory.promotion import PromotionDecision


@dataclass(frozen=True)
class PromotionMetricsSummary:
    metrics_path: Path
    total_evaluations: int
    eligible_for_auto_promotion: int
    by_confidence: dict[str, int]
    by_promotion: dict[str, int]
    by_reason: dict[str, int]
    latest_decision: dict | None = None
    last_auto_promoted: dict | None = None
    last_eligible_blocked: dict | None = None
    reviewed_promotions: int = 0
    confirmed_promotions: int = 0
    reverted_promotions: int = 0
    pending_review: int = 0
    precision_proxy: float = 0.0
    pollution_proxy: float = 0.0
    default_rollout_ready: bool = False


def promotion_metrics_file(workspace_path: str | os.PathLike[str]) -> Path:
    workspace = Path(workspace_path).expanduser().resolve()
    return workspace / ".aworld" / "memory" / "metrics" / "promotion.jsonl"


def append_promotion_metric(
    workspace_path: str | os.PathLike[str],
    *,
    session_id: str,
    task_id: str | None,
    decision: PromotionDecision,
) -> Path:
    target = promotion_metrics_file(workspace_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "recorded_at": decision.evaluated_at,
        "session_id": session_id,
        "task_id": task_id,
        **decision.to_payload(),
    }
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False))
        handle.write("\n")
    return target


def _review_actions(decision: dict) -> set[str]:
    reviews = decision.get("reviews")
    if not isinstance(reviews, list):
        return set()
    actions: set[str] = set()
    for review in reviews:
        if not isinstance(review, dict):
            continue
        action = review.get("review_action")
        if isinstance(action, str) and action.strip():
            actions.add(action.strip().lower())
    return actions


def _promoted_decision_has_complete_explainability(decision: dict) -> bool:
    if str(decision.get("decision") or "").strip().lower() != "durable_memory":
        return True
    if decision.get("legacy_incomplete") is True:
        return False
    required_text_fields = (
        "decision_id",
        "policy_mode",
        "policy_version",
        "decision",
        "reason",
        "confidence",
        "memory_type",
        "evaluated_at",
    )
    for field_name in required_text_fields:
        value = decision.get(field_name)
        if not isinstance(value, str) or not value.strip():
            return False
    blockers = decision.get("blockers")
    if not isinstance(blockers, list):
        return False
    source_ref = decision.get("source_ref")
    if not isinstance(source_ref, dict):
        return False
    required_keys = ("session_id", "task_id", "candidate_id")
    return all(
        isinstance(source_ref.get(key), str) and source_ref[key].strip()
        for key in required_keys
    )


def summarize_promotion_metrics(
    workspace_path: str | os.PathLike[str],
    *,
    max_records: int = 500,
) -> PromotionMetricsSummary:
    target = promotion_metrics_file(workspace_path)
    decisions = list_governed_decisions(workspace_path)[-max_records:]

    reviewed_promotions = 0
    confirmed_promotions = 0
    reverted_promotions = 0
    pending_review = 0
    promoted_records_have_complete_explainability = True
    for decision in decisions:
        review_actions = _review_actions(decision)
        if not review_actions:
            pending_review += 1
        else:
            reviewed_promotions += 1
        if "confirmed" in review_actions:
            confirmed_promotions += 1
        if "reverted" in review_actions:
            reverted_promotions += 1
        if not _promoted_decision_has_complete_explainability(decision):
            promoted_records_have_complete_explainability = False

    precision_proxy = (
        confirmed_promotions / reviewed_promotions if reviewed_promotions else 0.0
    )
    pollution_proxy = (
        reverted_promotions / reviewed_promotions if reviewed_promotions else 0.0
    )
    default_rollout_ready = (
        reviewed_promotions >= 100
        and precision_proxy >= 0.90
        and pollution_proxy <= 0.05
        and promoted_records_have_complete_explainability
    )

    if not target.exists():
        return PromotionMetricsSummary(
            metrics_path=target,
            total_evaluations=0,
            eligible_for_auto_promotion=0,
            by_confidence={},
            by_promotion={},
            by_reason={},
            latest_decision=None,
            last_auto_promoted=None,
            last_eligible_blocked=None,
            reviewed_promotions=reviewed_promotions,
            confirmed_promotions=confirmed_promotions,
            reverted_promotions=reverted_promotions,
            pending_review=pending_review,
            precision_proxy=precision_proxy,
            pollution_proxy=pollution_proxy,
            default_rollout_ready=default_rollout_ready,
        )

    by_confidence: Counter[str] = Counter()
    by_promotion: Counter[str] = Counter()
    by_reason: Counter[str] = Counter()
    total_evaluations = 0
    eligible_for_auto_promotion = 0
    latest_decision: dict | None = None
    last_auto_promoted: dict | None = None
    last_eligible_blocked: dict | None = None

    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []

    for line in lines[-max_records:]:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        total_evaluations += 1
        latest_decision = payload
        confidence = payload.get("confidence")
        promotion = payload.get("promotion")
        reason = payload.get("reason")
        if isinstance(confidence, str) and confidence:
            by_confidence[confidence] += 1
        if isinstance(promotion, str) and promotion:
            by_promotion[promotion] += 1
        if isinstance(reason, str) and reason:
            by_reason[reason] += 1
        if payload.get("eligible_for_auto_promotion") is True:
            eligible_for_auto_promotion += 1
            if promotion == "session_log_only":
                last_eligible_blocked = payload
        if promotion == "durable_memory":
            last_auto_promoted = payload

    return PromotionMetricsSummary(
        metrics_path=target,
        total_evaluations=total_evaluations,
        eligible_for_auto_promotion=eligible_for_auto_promotion,
        by_confidence=dict(sorted(by_confidence.items())),
        by_promotion=dict(sorted(by_promotion.items())),
        by_reason=dict(sorted(by_reason.items())),
        latest_decision=latest_decision,
        last_auto_promoted=last_auto_promoted,
        last_eligible_blocked=last_eligible_blocked,
        reviewed_promotions=reviewed_promotions,
        confirmed_promotions=confirmed_promotions,
        reverted_promotions=reverted_promotions,
        pending_review=pending_review,
        precision_proxy=precision_proxy,
        pollution_proxy=pollution_proxy,
        default_rollout_ready=default_rollout_ready,
    )
