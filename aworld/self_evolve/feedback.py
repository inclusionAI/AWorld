from __future__ import annotations

from typing import Any, Mapping

from aworld.self_evolve.sanitization import sanitize_text
from aworld.self_evolve.types import EvaluationSummary

_MAX_TEXT_CHARS = 240
_MAX_LIST_ITEMS = 3

_SCALAR_METRIC_KEYS = {
    "lesson_id",
    "lesson_type",
    "lesson_title",
    "lesson_summary",
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
    "baseline_score",
    "candidate_score",
    "score_delta",
    "baseline_A1_groundedness",
    "candidate_A1_groundedness",
    "A1_groundedness_delta",
    "baseline_A2_completeness",
    "candidate_A2_completeness",
    "A2_completeness_delta",
    "baseline_A3_relevance",
    "candidate_A3_relevance",
    "A3_relevance_delta",
    "baseline_A4_readability",
    "candidate_A4_readability",
    "A4_readability_delta",
    "baseline_B1_tool_use",
    "candidate_B1_tool_use",
    "B1_tool_use_delta",
    "baseline_B2_efficiency",
    "candidate_B2_efficiency",
    "B2_efficiency_delta",
    "baseline_B3_compliance",
    "candidate_B3_compliance",
    "B3_compliance_delta",
    "baseline_B4_robustness",
    "candidate_B4_robustness",
    "B4_robustness_delta",
    "baseline_evidence_block_count",
    "candidate_evidence_block_count",
    "evidence_block_count_delta",
    "baseline_evidence_incomplete",
    "candidate_evidence_incomplete",
    "evidence_incomplete_delta",
    "baseline_latency_ms",
    "candidate_latency_ms",
    "latency_ms_delta",
    "evidence_manifest_invalid_entry_count",
    "evidence_manifest_entry_count",
    "failed_repetition_count",
    "replay_failed_repetition_count",
    "replay_evidence_manifest_invalid_entry_count",
}

_EVIDENCE_METRIC_KEYS = {
    "A1_groundedness",
    "evidence_block_count",
    "baseline_evidence_block_count",
    "candidate_evidence_block_count",
    "evidence_block_count_delta",
    "evidence_bundle_entry_count",
    "evidence_bundle_valid",
    "evidence_compacted",
    "evidence_incomplete",
    "baseline_evidence_incomplete",
    "candidate_evidence_incomplete",
    "evidence_incomplete_delta",
    "evidence_manifest_invalid_entry_count",
    "evidence_manifest_entry_count",
    "replay_evidence_manifest_invalid_entry_count",
    "veto_triggered",
}

_LOW_EFFICIENCY_THRESHOLD = 3.0
_MIN_VERIFIED_SCORE = 70.0
_HIGH_BASELINE_SCORE = 85.0
_MIN_GROUNDEDNESS = 3.0


def normalize_feedback_summary(feedback: EvaluationSummary) -> dict[str, Any]:
    """Compress evaluator feedback into a stable optimizer-facing schema."""
    metrics = feedback.metrics
    evidence = _evidence_summary(metrics)
    failed_gates = _string_list(metrics.get("failed_gates"))
    required_behaviors = _required_behaviors(
        dataset_split=feedback.dataset_split,
        failed_gates=failed_gates,
        evidence=evidence,
        metrics=metrics,
    )
    repair_plan = _repair_plan(
        failed_gates=failed_gates,
        evidence=evidence,
        metrics=metrics,
        required_behaviors=required_behaviors,
    )
    return {
        "variant_id": feedback.variant_id,
        "dataset_split": feedback.dataset_split,
        "metrics": _metric_summary(metrics),
        "failed_gates": failed_gates,
        "evidence": evidence,
        "required_behaviors": required_behaviors,
        "repair_plan": repair_plan,
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
    replay_invalid_count = metrics.get("replay_evidence_manifest_invalid_entry_count")
    if isinstance(replay_invalid_count, (int, float)):
        summary["replay_invalid_entry_count"] = replay_invalid_count
    replay_failure_reasons = _string_list(metrics.get("replay_failure_reasons"))
    if replay_failure_reasons:
        summary["replay_failure_reasons"] = replay_failure_reasons
    replay_failure_types = _string_list(metrics.get("replay_failure_types"))
    if replay_failure_types:
        summary["replay_failure_types"] = replay_failure_types
    return summary


def _required_behaviors(
    *,
    dataset_split: str,
    failed_gates: list[str],
    evidence: Mapping[str, Any],
    metrics: Mapping[str, Any],
) -> list[str]:
    behaviors: list[str] = _string_list(metrics.get("required_behaviors"))
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

    if _has_verifiability_regression(metrics):
        behaviors.extend(
            [
                "reduce_answer_scope_to_verified_claims",
                "prefer_fewer_verified_claims_over_broad_synthesis",
                "optimize_verifiability_per_evidence_block",
                "avoid_collecting_more_evidence_without_verifiability_gain",
            ]
        )
    if _has_cost_or_latency_regression(metrics):
        behaviors.append("cap_evidence_acquisition_and_summarization_cost")

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
    if _has_compacted_tool_argument_issue(evidence=evidence, metrics=metrics):
        behaviors.extend(
            [
                "avoid_compacted_tool_arguments",
                "regenerate_schema_valid_tool_arguments",
                "stop_repeating_invalid_tool_calls",
                "switch_to_artifact_read_after_invalid_tool_argument",
            ]
        )
    if _has_high_scoring_baseline_regression(metrics):
        behaviors.extend(
            [
                "differentiate_from_high_scoring_baseline",
                "preserve_baseline_strengths",
                "define_behavior_delta_before_tools",
                "prefer_targeted_changes_over_broad_rewrites",
            ]
        )
    if _has_high_baseline_without_efficiency_gain(metrics):
        behaviors.extend(
            [
                "use_efficiency_delta_for_high_baseline",
                "preserve_claim_set_and_source_links",
                "do_not_add_verification_steps_without_score_gain",
                "candidate_must_use_no_more_steps_than_baseline",
            ]
        )
    dimension_regressions = _dimension_regressions(metrics)
    if "A1_groundedness" in dimension_regressions:
        behaviors.extend(
            [
                "preserve_groundedness",
                "support_every_claim_with_artifact_reference",
                "do_not_trade_groundedness_for_breadth",
            ]
        )
    if "A2_completeness" in dimension_regressions:
        behaviors.extend(
            [
                "preserve_answer_completeness",
                "do_not_narrow_supported_answer",
                "restore_baseline_supported_claims",
            ]
        )
    if "B2_efficiency" in dimension_regressions:
        behaviors.extend(
            [
                "preserve_or_improve_efficiency",
                "avoid_extra_steps_without_score_gain",
            ]
        )
    if dataset_split == "held_out" and failed_gates:
        behaviors.extend(
            [
                "generalize_runtime_behavior_across_task_variants",
                "preserve_validation_gains_on_held_out",
                "repair_held_out_regression_before_release",
            ]
        )
    return list(dict.fromkeys(behaviors))


def _repair_plan(
    *,
    failed_gates: list[str],
    evidence: Mapping[str, Any],
    metrics: Mapping[str, Any],
    required_behaviors: list[str],
) -> dict[str, Any]:
    issues: list[str] = []
    actions: list[str] = []
    acceptance_criteria: list[str] = []
    has_evidence_problem = (
        "evidence_quality" in set(failed_gates)
        or evidence.get("evidence_compacted") is True
        or evidence.get("evidence_incomplete") is True
    )
    has_manifest_problem = (
        _positive_number(evidence.get("invalid_entry_count"))
        or _positive_number(evidence.get("evidence_manifest_invalid_entry_count"))
        or _positive_number(evidence.get("replay_invalid_entry_count"))
        or _positive_number(evidence.get("replay_evidence_manifest_invalid_entry_count"))
    )
    replay_failure_text = " ".join(
        str(item)
        for item in (
            list(evidence.get("replay_failure_reasons") or [])
            + list(evidence.get("replay_failure_types") or [])
        )
    ).lower()
    has_replay_timeout = "timeout" in replay_failure_text
    has_replay_evidence_failure = "evidence_quality_failed" in replay_failure_text
    has_replay_trajectory_capture_failure = (
        "trajectory_capture_unavailable" in replay_failure_text
    )
    has_compacted_tool_argument_issue = _has_compacted_tool_argument_issue(
        evidence=evidence,
        metrics=metrics,
    )
    if has_evidence_problem:
        issues.append("compacted_or_incomplete_evidence")
        actions.extend(
            [
                "capture_bounded_non_compacted_evidence",
                "limit_final_answer_to_supported_claims",
            ]
        )
        acceptance_criteria.append("all_final_claims_have_non_compacted_support")
    if has_manifest_problem:
        issues.append("invalid_evidence_manifest")
        actions.append("write_valid_bounded_evidence_manifest")
        acceptance_criteria.append("manifest_has_no_invalid_entries")
    if has_replay_timeout:
        issues.append("replay_timeout")
        actions.append("change_strategy_after_failed_replay")
        acceptance_criteria.append("replay_repetitions_complete_without_evidence_failures")
    if has_replay_evidence_failure:
        issues.append("replay_evidence_quality_failure")
        actions.extend(
            [
                "change_strategy_after_failed_replay",
                "do_not_finalize_after_failed_evidence_retry",
            ]
        )
        acceptance_criteria.append("replay_repetitions_complete_without_evidence_failures")
    if has_compacted_tool_argument_issue:
        issues.append("compacted_tool_argument_replay")
        actions.extend(
            [
                "regenerate_compacted_tool_arguments",
                "switch_to_artifact_read_after_invalid_tool_argument",
                "stop_repeating_invalid_tool_calls",
            ]
        )
        acceptance_criteria.append("tool_arguments_are_schema_valid_and_non_compacted")
    if has_replay_trajectory_capture_failure:
        issues.append("replay_trajectory_capture_failure")
        actions.extend(
            [
                "change_strategy_after_failed_replay",
                "ensure_replay_returns_trajectory_evidence",
                "do_not_finalize_without_captured_trajectory",
            ]
        )
        acceptance_criteria.append("replay_repetitions_return_trajectory_evidence")
    if "score_improvement" in set(failed_gates) or _has_efficiency_improvement_issue(
        failed_gates=failed_gates,
        metrics=metrics,
    ):
        issues.append("score_or_efficiency_regression")
        actions.extend(
            [
                "reduce_answer_scope_to_verified_claims",
                "stop_after_sufficient_verified_evidence",
            ]
        )
        acceptance_criteria.append(
            "candidate_score_exceeds_baseline_without_extra_unverified_scope"
        )
    if _has_high_scoring_baseline_regression(metrics):
        actions.extend(
            [
                "preserve_high_scoring_baseline_strengths",
                "define_candidate_behavior_delta",
                "prefer_targeted_change_over_broad_rewrite",
            ]
        )
        acceptance_criteria.append("candidate_score_exceeds_baseline_score")
    if _has_high_baseline_without_efficiency_gain(metrics):
        issues.append("high_baseline_without_efficiency_gain")
        actions.extend(
            [
                "replace_broad_validation_with_efficiency_delta",
                "preserve_claim_set_and_source_links",
                "avoid_extra_verification_steps",
            ]
        )
        acceptance_criteria.extend(
            [
                "candidate_uses_no_more_steps_than_baseline",
                "candidate_groundedness_is_no_worse_than_baseline",
            ]
        )
    dimension_regressions = _dimension_regressions(metrics)
    if dimension_regressions:
        issues.append("dimension_regression")
        actions.extend(
            [
                f"restore_{dimension}" for dimension in dimension_regressions[:_MAX_LIST_ITEMS]
            ]
        )
        acceptance_criteria.append("regressed_dimensions_are_no_worse_than_baseline")
    if "required_verification" in set(failed_gates):
        issues.append("missing_deterministic_verification")
        actions.append("run_pre_final_claim_verification")
        acceptance_criteria.append("deterministic_verification_passes_before_final")

    if not issues:
        return {
            "priority": "maintain_verified_behavior",
            "issues": [],
            "actions": [],
            "acceptance_criteria": [],
        }

    priority = (
        "evidence_verifiability"
        if has_evidence_problem or has_manifest_problem
        else "score_and_efficiency"
    )
    if required_behaviors:
        actions.extend(required_behaviors[:5])
    return {
        "priority": priority,
        "issues": list(dict.fromkeys(issues)),
        "actions": list(dict.fromkeys(actions)),
        "acceptance_criteria": list(dict.fromkeys(acceptance_criteria)),
    }


def _has_compacted_tool_argument_issue(
    *,
    evidence: Mapping[str, Any],
    metrics: Mapping[str, Any],
) -> bool:
    replay_failure_text = " ".join(
        str(item)
        for item in (
            list(evidence.get("replay_failure_reasons") or [])
            + list(evidence.get("replay_failure_types") or [])
            + list(metrics.get("replay_failure_reasons") or [])
            + list(metrics.get("replay_failure_types") or [])
            + list(metrics.get("evidence_issues") or [])
        )
    ).lower()
    return any(
        marker in replay_failure_text
        for marker in (
            "compacted_string_field",
            "compacted tool argument",
            "compacted_tool_argument",
            "invalid tool argument",
            "invalid_tool_argument",
            "schema-valid tool call arguments",
            "input should be a valid string",
        )
    )


def _has_verifiability_regression(metrics: Mapping[str, Any]) -> bool:
    score_delta = _metric_float(metrics.get("score_delta"))
    evidence_block_delta = _metric_float(metrics.get("evidence_block_count_delta"))
    incomplete_delta = _metric_float(metrics.get("evidence_incomplete_delta"))
    baseline_incomplete = _metric_float(metrics.get("baseline_evidence_incomplete"))
    candidate_incomplete = _metric_float(metrics.get("candidate_evidence_incomplete"))
    score_regressed = score_delta is not None and score_delta <= 0
    evidence_expanded = evidence_block_delta is not None and evidence_block_delta > 0
    evidence_became_less_complete = (
        incomplete_delta is not None
        and incomplete_delta > 0
    ) or (
        baseline_incomplete is not None
        and candidate_incomplete is not None
        and candidate_incomplete > baseline_incomplete
    )
    return score_regressed and (evidence_expanded or evidence_became_less_complete)


def _has_cost_or_latency_regression(metrics: Mapping[str, Any]) -> bool:
    latency_delta = _metric_float(metrics.get("latency_ms_delta"))
    if latency_delta is not None and latency_delta > 0:
        return True
    baseline_latency = _metric_float(metrics.get("baseline_latency_ms"))
    candidate_latency = _metric_float(metrics.get("candidate_latency_ms"))
    return (
        baseline_latency is not None
        and candidate_latency is not None
        and candidate_latency > baseline_latency
    )


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


def _has_high_scoring_baseline_regression(metrics: Mapping[str, Any]) -> bool:
    baseline_score = _metric_float(metrics.get("baseline_score"))
    candidate_score = _metric_float(metrics.get("candidate_score"))
    score_delta = _metric_float(metrics.get("score_delta"))
    if baseline_score is None or baseline_score < _HIGH_BASELINE_SCORE:
        return False
    if score_delta is not None:
        return score_delta <= 0
    return candidate_score is not None and candidate_score <= baseline_score


def _has_high_baseline_without_efficiency_gain(metrics: Mapping[str, Any]) -> bool:
    if not _has_high_scoring_baseline_regression(metrics):
        return False
    b2_delta = _metric_float(metrics.get("B2_efficiency_delta"))
    baseline_b2 = _metric_float(metrics.get("baseline_B2_efficiency"))
    candidate_b2 = _metric_float(metrics.get("candidate_B2_efficiency"))
    if b2_delta is not None:
        return b2_delta <= 0
    if baseline_b2 is not None and candidate_b2 is not None:
        return candidate_b2 <= baseline_b2
    return True


def _dimension_regressions(metrics: Mapping[str, Any]) -> list[str]:
    dimensions = (
        "A1_groundedness",
        "A2_completeness",
        "A3_relevance",
        "A4_readability",
        "B1_tool_use",
        "B2_efficiency",
        "B3_compliance",
        "B4_robustness",
    )
    regressions: list[str] = []
    for dimension in dimensions:
        delta = _metric_float(metrics.get(f"{dimension}_delta"))
        if delta is not None and delta < 0:
            regressions.append(dimension)
            continue
        baseline = _metric_float(metrics.get(f"baseline_{dimension}"))
        candidate = _metric_float(metrics.get(f"candidate_{dimension}"))
        if baseline is not None and candidate is not None and candidate < baseline:
            regressions.append(dimension)
    return regressions


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
    return sanitize_text(value, max_chars=_MAX_TEXT_CHARS)
