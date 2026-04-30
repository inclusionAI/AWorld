from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

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


def summarize_promotion_metrics(
    workspace_path: str | os.PathLike[str],
    *,
    max_records: int = 500,
) -> PromotionMetricsSummary:
    target = promotion_metrics_file(workspace_path)
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
    )
