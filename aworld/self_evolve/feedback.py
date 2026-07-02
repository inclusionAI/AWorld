from __future__ import annotations

from typing import Any, Mapping

from aworld.self_evolve.types import EvaluationSummary

_MAX_TEXT_CHARS = 240
_MAX_LIST_ITEMS = 3

_SCALAR_METRIC_KEYS = {
    "score",
    "cost",
    "latency_ms",
    "A1_groundedness",
    "A2_completeness",
    "A3_relevance",
    "A4_readability",
    "B1_tool_use",
    "B2_efficiency",
    "B3_compliance",
    "B4_robustness",
    "veto_triggered",
    "has_evidence",
    "agent_finished",
}

_EVIDENCE_METRIC_KEYS = {
    "evidence_block_count",
    "evidence_compacted",
    "evidence_incomplete",
}


def normalize_feedback_summary(feedback: EvaluationSummary) -> dict[str, Any]:
    """Compress evaluator feedback into a stable optimizer-facing schema."""
    metrics = feedback.metrics
    evidence = _evidence_summary(metrics)
    failed_gates = _string_list(metrics.get("failed_gates"))
    required_behaviors = _required_behaviors(
        failed_gates=failed_gates,
        evidence=evidence,
    )
    return {
        "variant_id": feedback.variant_id,
        "dataset_split": feedback.dataset_split,
        "metrics": _metric_summary(metrics),
        "failed_gates": failed_gates,
        "evidence": evidence,
        "required_behaviors": required_behaviors,
    }


def _metric_summary(metrics: Mapping[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in sorted(_SCALAR_METRIC_KEYS):
        value = metrics.get(key)
        if isinstance(value, bool) or isinstance(value, (int, float, str)):
            summary[key] = _compact_value(value)
    return summary


def _evidence_summary(metrics: Mapping[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in sorted(_EVIDENCE_METRIC_KEYS):
        value = metrics.get(key)
        if isinstance(value, bool) or isinstance(value, (int, float, str)):
            summary[key] = _compact_value(value)
    issues = _string_list(metrics.get("evidence_issues"))
    if issues:
        summary["issues"] = issues
    return summary


def _required_behaviors(
    *,
    failed_gates: list[str],
    evidence: Mapping[str, Any],
) -> list[str]:
    has_evidence_failure = "evidence_quality" in set(failed_gates)
    has_evidence_compaction = evidence.get("evidence_compacted") is True
    has_incomplete_evidence = evidence.get("evidence_incomplete") is True
    if not (has_evidence_failure or has_evidence_compaction or has_incomplete_evidence):
        return []
    return [
        "artifact_first",
        "bounded_structured_summary",
        "non_compacted_evidence",
        "claim_evidence_ledger",
        "claim_by_claim_verification",
    ]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value[:_MAX_LIST_ITEMS]:
        text = str(item).strip()
        if text:
            result.append(_clip_text(text))
    return result


def _compact_value(value: Any) -> Any:
    if isinstance(value, str):
        return _clip_text(value)
    return value


def _clip_text(value: str) -> str:
    if len(value) <= _MAX_TEXT_CHARS:
        return value
    return value[: _MAX_TEXT_CHARS - 1].rstrip() + "…"
