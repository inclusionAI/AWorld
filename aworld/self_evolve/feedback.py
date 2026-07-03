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
    "evidence_manifest_invalid_entry_count",
    "evidence_manifest_entry_count",
}

_EVIDENCE_METRIC_KEYS = {
    "A1_groundedness",
    "evidence_block_count",
    "evidence_compacted",
    "evidence_incomplete",
    "evidence_manifest_invalid_entry_count",
    "evidence_manifest_entry_count",
    "veto_triggered",
}

_LOW_EFFICIENCY_THRESHOLD = 3.0
_MIN_VERIFIED_SCORE = 70.0
_MIN_GROUNDEDNESS = 3.0


def normalize_feedback_summary(feedback: EvaluationSummary) -> dict[str, Any]:
    """Compress evaluator feedback into a stable optimizer-facing schema."""
    metrics = feedback.metrics
    evidence = _evidence_summary(metrics)
    failed_gates = _string_list(metrics.get("failed_gates"))
    required_behaviors = _required_behaviors(
        failed_gates=failed_gates,
        evidence=evidence,
        metrics=metrics,
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
    invalid_reasons = _string_list(metrics.get("evidence_manifest_invalid_reasons"))
    if invalid_reasons:
        summary["invalid_reasons"] = invalid_reasons
    invalid_count = metrics.get("evidence_manifest_invalid_entry_count")
    if isinstance(invalid_count, (int, float)):
        summary["invalid_entry_count"] = invalid_count
    return summary


def _required_behaviors(
    *,
    failed_gates: list[str],
    evidence: Mapping[str, Any],
    metrics: Mapping[str, Any],
) -> list[str]:
    behaviors: list[str] = []
    has_evidence_failure = "evidence_quality" in set(failed_gates)
    has_evidence_compaction = evidence.get("evidence_compacted") is True
    has_incomplete_evidence = evidence.get("evidence_incomplete") is True
    has_manifest_errors = _positive_number(evidence.get("invalid_entry_count")) or _positive_number(
        evidence.get("evidence_manifest_invalid_entry_count")
    )
    has_veto = evidence.get("veto_triggered") is True or metrics.get("veto_triggered") is True
    groundedness = _metric_float(evidence.get("A1_groundedness"))
    if groundedness is None:
        groundedness = _metric_float(metrics.get("A1_groundedness"))
    score = _metric_float(metrics.get("score"))
    if has_evidence_failure or has_evidence_compaction or has_incomplete_evidence:
        behaviors.extend(
            [
                "artifact_first",
                "bounded_structured_summary",
                "non_compacted_evidence",
                "claim_evidence_ledger",
                "claim_by_claim_verification",
            ]
        )
    if has_manifest_errors:
        behaviors.extend(
            [
                "manifest_schema_compliance",
                "artifact_reference_integrity",
                "validate_manifest_before_final",
            ]
        )
    if has_veto:
        behaviors.extend(
            [
                "pre_final_veto_check",
                "support_every_claim_with_artifact_reference",
                "remove_or_qualify_unsupported_claims",
            ]
        )
    if groundedness is not None and groundedness < _MIN_GROUNDEDNESS:
        behaviors.extend(
            [
                "raise_groundedness_before_breadth",
                "support_every_claim_with_artifact_reference",
                "quote_or_reference_minimal_source_spans",
            ]
        )
    if score is not None and score < _MIN_VERIFIED_SCORE:
        behaviors.extend(
            [
                "prioritize_gate_thresholds",
                "improve_score_before_expanding_scope",
            ]
        )

    if _has_efficiency_improvement_issue(failed_gates=failed_gates, metrics=metrics):
        behaviors.extend(
            [
                "plan_before_tools",
                "prefer_direct_structured_extraction",
                "minimize_failed_attempts",
                "avoid_repeated_paths",
                "stop_after_sufficient_evidence",
            ]
        )
    return list(dict.fromkeys(behaviors))


def _has_efficiency_improvement_issue(
    *,
    failed_gates: list[str],
    metrics: Mapping[str, Any],
) -> bool:
    if "score_improvement" in set(failed_gates):
        return True
    efficiency = metrics.get("B2_efficiency")
    if isinstance(efficiency, (int, float)) and efficiency < _LOW_EFFICIENCY_THRESHOLD:
        return True
    score_delta = metrics.get("score_delta")
    if isinstance(score_delta, (int, float)) and score_delta <= 0:
        return True
    baseline_score = metrics.get("baseline_score")
    candidate_score = metrics.get("candidate_score")
    return (
        isinstance(baseline_score, (int, float))
        and isinstance(candidate_score, (int, float))
        and candidate_score <= baseline_score
    )


def _metric_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    return float(value) if isinstance(value, (int, float)) else None


def _positive_number(value: Any) -> bool:
    numeric = _metric_float(value)
    return numeric is not None and numeric > 0


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
