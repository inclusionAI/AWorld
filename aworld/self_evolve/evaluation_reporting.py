"""Evaluation evidence aggregation and reporting helpers."""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from aworld.self_evolve.gates import EvidenceQualityGate
from aworld.self_evolve.replay import ReplayVariantResult
from aworld.self_evolve.types import EvaluationSummary, GateResult


def _metric_number(metrics: Mapping[str, Any], key: str) -> float | None:
    value = metrics.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def _accumulate_score_evidence(
    initial: EvaluationSummary,
    additional: EvaluationSummary,
) -> EvaluationSummary:
    """Pool compatible ordered judge samples without discarding prior evidence."""

    initial_metrics = dict(initial.metrics)
    additional_metrics = dict(additional.metrics)
    initial_plan = initial_metrics.get("comparison_plan_fingerprint")
    additional_plan = additional_metrics.get("comparison_plan_fingerprint")
    compatible = (
        initial.variant_id == additional.variant_id
        and initial.dataset_split == additional.dataset_split
        and isinstance(initial_plan, str)
        and bool(initial_plan)
        and initial_plan == additional_plan
    )
    initial_samples = _finite_score_samples(initial_metrics.get("score_samples"))
    additional_samples = _finite_score_samples(additional_metrics.get("score_samples"))
    if not compatible or not initial_samples or not additional_samples:
        additional_metrics["score_evidence_accumulation"] = {
            "status": "incompatible",
            "initial_sample_count": len(initial_samples),
            "additional_sample_count": len(additional_samples),
        }
        return replace(additional, metrics=additional_metrics)

    samples = (*initial_samples, *additional_samples)
    additional_metrics.update(
        {
            "score": statistics.mean(samples),
            "score_samples": list(samples),
            "score_sample_count": len(samples),
            "score_std": statistics.stdev(samples) if len(samples) >= 2 else 0.0,
            "score_evidence_round_count": (
                _positive_metric_count(
                    initial_metrics.get("score_evidence_round_count")
                )
                or 1
            )
            + 1,
            "score_evidence_accumulation": {
                "status": "pooled",
                "initial_sample_count": len(initial_samples),
                "additional_sample_count": len(additional_samples),
                "pooled_sample_count": len(samples),
                "execution_ids": list(
                    dict.fromkeys(
                        str(value)
                        for value in (
                            initial_metrics.get("evaluation_execution_id"),
                            additional_metrics.get("evaluation_execution_id"),
                        )
                        if isinstance(value, str) and value
                    )
                ),
            },
        }
    )
    for key in (
        "judge_attempt_count",
        "judge_success_count",
        "judge_failure_count",
        "judge_timeout_count",
    ):
        initial_count = _nonnegative_numeric_count(initial_metrics.get(key))
        additional_count = _nonnegative_numeric_count(additional_metrics.get(key))
        if initial_count is not None or additional_count is not None:
            additional_metrics[key] = (initial_count or 0) + (additional_count or 0)
    return replace(additional, metrics=additional_metrics)


def _finite_score_samples(value: object) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    samples: list[float] = []
    for item in value:
        if (
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(float(item))
        ):
            return ()
        samples.append(float(item))
    return tuple(samples)


def _nonnegative_numeric_count(value: object) -> int | None:
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and value >= 0
    ):
        return int(value)
    return None


def _positive_metric_count(value: object) -> int:
    count = _nonnegative_numeric_count(value)
    return count if count is not None and count > 0 else 0


def _evidence_quality_gate(
    summary: EvaluationSummary,
    *,
    baseline: EvaluationSummary | None = None,
) -> GateResult | None:
    metrics = summary.metrics
    requires_evidence_quality = (
        metrics.get("evaluator_mode") == "aworld_trajectory_evaluator"
        or metrics.get("evaluator_source_kind") == "trajectory"
        or any(
            key in metrics
            for key in (
                "has_evidence",
                "evidence_block_count",
                "evidence_compacted",
                "evidence_incomplete",
            )
        )
    )
    if not requires_evidence_quality:
        return None
    return EvidenceQualityGate().evaluate(summary, baseline=baseline)


def _summary_with_replay_evidence_metrics(
    summary: EvaluationSummary,
    replay_variant: ReplayVariantResult,
) -> EvaluationSummary:
    replay_metrics = replay_variant.metrics or {}
    evidence_metric_names = (
        "evidence_strategy_passed",
        "evidence_manifest_entry_count",
        "evidence_manifest_invalid_entry_count",
        "evidence_manifest_present",
        "evidence_manifest_valid",
        "evidence_compaction_signals",
        "evidence_bundle_path",
        "evidence_bundle_present",
        "evidence_bundle_valid",
        "evidence_bundle_entry_count",
        "evidence_artifact_reference_count",
        "evidence_manifested_artifact_reference_count",
        "evidence_unmanifested_artifact_reference_count",
        "evidence_unmanifested_artifact_reference_identity_digests",
        "evidence_runtime_policy_active",
        "evidence_runtime_policy_passed",
        "evidence_runtime_policy_authoritative_passed",
        "evidence_runtime_policy_authority",
        "evidence_runtime_policy_mode",
        "evidence_runtime_policy_advisory_violation_count",
        "evidence_runtime_policy_violation_count",
        "evidence_runtime_policy_phase",
        "evidence_runtime_policy_tool_call_attempt_count",
        "evidence_runtime_policy_artifact_file_count",
        "evidence_runtime_policy_artifact_bytes",
        "evidence_runtime_policy_consecutive_failed_action_count",
        "evidence_runtime_policy_max_consecutive_failed_actions",
        "evidence_runtime_policy_allowed_loopback_endpoint_count",
        "evidence_runtime_policy_allowed_control_action_count",
        "task_completion_established",
        "timeout_evidence_recovered",
        "replay_counterexamples",
        "failed_repetition_count",
        "repetition_failures",
    )
    merged_metrics = dict(summary.metrics)
    for metric_name in evidence_metric_names:
        if metric_name in replay_metrics:
            merged_metrics.setdefault(metric_name, replay_metrics[metric_name])
            merged_metrics[f"replay_{metric_name}"] = replay_metrics[metric_name]
    _merge_replay_runtime_cost_metrics(merged_metrics, replay_metrics)
    _merge_replay_deterministic_verification(
        merged_metrics,
        replay_variant,
        replay_metrics,
    )
    failure_summary = _replay_failure_summary(replay_metrics.get("repetition_failures"))
    merged_metrics.update(failure_summary)
    return replace(summary, metrics=merged_metrics)


def _merge_replay_runtime_cost_metrics(
    merged_metrics: dict[str, Any],
    replay_metrics: Mapping[str, Any],
) -> None:
    """Keep candidate runtime cost separate from evaluator/judge overhead."""

    for source_key, target_key in (
        ("cost_usd", "replay_cost_usd"),
        ("total_tokens", "replay_total_tokens"),
        ("latency_ms", "replay_latency_ms"),
        ("duration_ms", "replay_duration_ms"),
        ("external_tool_call_count", "replay_external_tool_call_count"),
    ):
        value = replay_metrics.get(source_key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            merged_metrics[target_key] = value


def _merge_replay_deterministic_verification(
    merged_metrics: dict[str, Any],
    replay_variant: ReplayVariantResult,
    replay_metrics: Mapping[str, Any],
) -> None:
    """Project successful paired-replay invariants as independent verification.

    This signal proves the deterministic replay/evidence lifecycle only; judge
    scores remain the authority for answer quality.  It is nevertheless a real
    independent signal for verified confidence and replaces the old judge-gate
    alias that was reported as verification commands.
    """

    status = getattr(replay_variant.status, "value", replay_variant.status)
    repetition_count = replay_metrics.get("repetition_count")
    successful_count = replay_metrics.get("successful_repetition_count")
    count = (
        int(repetition_count)
        if isinstance(repetition_count, (int, float))
        and not isinstance(repetition_count, bool)
        and repetition_count > 0
        else max(1, len(replay_variant.repetition_results))
    )
    successful = (
        int(successful_count)
        if isinstance(successful_count, (int, float))
        and not isinstance(successful_count, bool)
        else count if status == "succeeded" else 0
    )
    invariant_passed = bool(
        status == "succeeded"
        and successful == count
        and replay_metrics.get("blocked_repetition_count", 0) == 0
        and replay_metrics.get("failed_repetition_count", 0) == 0
        and replay_metrics.get("evidence_bundle_valid") is not False
        and replay_metrics.get("evidence_manifest_valid") is not False
        and replay_metrics.get("evidence_runtime_policy_authoritative_passed")
        is not False
    )
    merged_metrics.update(
        {
            "deterministic_signal": invariant_passed,
            "deterministic_verification_source": "paired_replay_invariants",
            "deterministic_verification_case_count": count,
            "deterministic_verification_pass_count": (
                count if invariant_passed else 0
            ),
            "deterministic_verification_failure_count": (
                0 if invariant_passed else count
            ),
            "deterministic_verification_pass_rate": (
                1.0 if invariant_passed else 0.0
            ),
        }
    )


def _replay_failure_summary(value: object) -> dict[str, object]:
    if not isinstance(value, list):
        return {}
    reasons: list[str] = []
    types: list[str] = []
    evidence_manifest_invalid_entry_count = 0
    for item in value:
        if not isinstance(item, Mapping):
            continue
        reason = item.get("reason")
        if isinstance(reason, str) and reason and reason not in reasons:
            reasons.append(reason)
        failure_type = item.get("type") or item.get("reason")
        if isinstance(failure_type, str) and failure_type and failure_type not in types:
            types.append(failure_type)
        invalid_count = item.get("evidence_manifest_invalid_entry_count")
        if isinstance(invalid_count, (int, float)):
            evidence_manifest_invalid_entry_count += int(invalid_count)
    summary: dict[str, object] = {}
    if reasons:
        summary["replay_failure_reasons"] = reasons
    if types:
        summary["replay_failure_types"] = types
    if evidence_manifest_invalid_entry_count:
        summary["replay_evidence_manifest_invalid_entry_count"] = (
            evidence_manifest_invalid_entry_count
        )
    return summary
