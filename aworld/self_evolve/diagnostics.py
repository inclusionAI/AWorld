from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from aworld.self_evolve.replay import CandidateReplayResult
from aworld.self_evolve.failure_events import (
    AggregatedReplayFailure,
    FailureOwner,
    FailureScope,
    FailureStage,
    ReplayFailureEvent,
    ReplayFailureObservation,
    aggregate_replay_failure_observations,
    aggregate_replay_failures,
)
from aworld.self_evolve.sanitization import sanitize_path_ref
from aworld.self_evolve.types import EvaluationSummary, GateResult


class HarnessDiagnosticKind(str, Enum):
    CONTEXT = "context"
    WORKFLOW = "workflow"
    TOOL_PROTOCOL = "tool_protocol"
    EVALUATION = "evaluation"
    MEMORY = "memory"
    PERMISSION_BOUNDARY = "permission_boundary"
    ARTIFACT_LIFECYCLE = "artifact_lifecycle"


class LessonPromotionStatus(str, Enum):
    ADVISORY = "advisory"
    PROMOTED_TO_STRATEGY_HINT = "promoted_to_strategy_hint"
    PROMOTED_TO_RUNTIME_BEHAVIOR = "promoted_to_runtime_behavior"


@dataclass(frozen=True)
class HarnessDiagnostic:
    diagnostic_id: str
    kind: HarnessDiagnosticKind
    title: str
    summary: str
    source_refs: tuple[str, ...] = ()
    affected_gates: tuple[str, ...] = ()
    promotion_status: LessonPromotionStatus = LessonPromotionStatus.ADVISORY
    metrics: Mapping[str, Any] = field(default_factory=dict)


def extract_harness_diagnostics(
    *,
    gate_results: Sequence[GateResult],
    summaries: Sequence[EvaluationSummary | None] = (),
    replay_result: CandidateReplayResult | None = None,
    causal_events: Sequence[ReplayFailureEvent | AggregatedReplayFailure] = (),
) -> tuple[HarnessDiagnostic, ...]:
    """Extract framework-level diagnostics without copying raw evidence text."""
    diagnostics: list[HarnessDiagnostic] = []
    failed_gate_names = tuple(gate.gate_name for gate in gate_results if not gate.passed)
    causal_aggregates = _causal_aggregates(
        causal_events=causal_events,
        replay_result=replay_result,
    )
    diagnostics.extend(
        _causal_diagnostic(event, affected_gates=failed_gate_names)
        for event in causal_aggregates
    )
    if "evidence_quality" in failed_gate_names:
        diagnostics.append(
            _diagnostic(
                kind=HarnessDiagnosticKind.ARTIFACT_LIFECYCLE,
                title="Evidence quality blocked verified apply",
                summary="Replay or evaluation evidence was missing, incomplete, compacted, or not artifact-backed enough for verified apply.",
                affected_gates=("evidence_quality",),
                metrics=_evidence_metrics(summaries),
                source_refs=_summary_refs(summaries),
                promotion_status=LessonPromotionStatus.ADVISORY,
            )
        )
    if (
        not causal_aggregates
        and ("candidate_replay" in failed_gate_names or "replay_confidence" in failed_gate_names)
    ):
        diagnostics.append(
            _diagnostic(
                kind=HarnessDiagnosticKind.WORKFLOW,
                title="Replay did not provide stable comparison evidence",
                summary="Candidate replay did not produce enough successful paired trajectories for the configured apply policy.",
                affected_gates=tuple(
                    gate
                    for gate in ("candidate_replay", "replay_confidence")
                    if gate in failed_gate_names
                ),
                metrics=_replay_metrics(replay_result),
                source_refs=_replay_refs(replay_result),
                promotion_status=LessonPromotionStatus.ADVISORY,
            )
        )
    if "evaluation" in failed_gate_names and not _causal_explains_evaluation_noise(
        causal_aggregates
    ):
        diagnostics.append(
            _diagnostic(
                kind=HarnessDiagnosticKind.EVALUATION,
                title="Evaluation backend failed",
                summary="Evaluator execution failed before producing a trusted candidate decision.",
                affected_gates=("evaluation",),
                metrics=_gate_failure_details(gate_results, "evaluation"),
                source_refs=_summary_refs(summaries),
                promotion_status=LessonPromotionStatus.ADVISORY,
            )
        )
    if _has_context_diagnostic(summaries):
        diagnostics.append(
            _diagnostic(
                kind=HarnessDiagnosticKind.CONTEXT,
                title="Evaluation context was incomplete or over budget",
                summary="Trajectory or evidence context exceeded bounded evaluation limits or was missing from the evaluator input.",
                affected_gates=failed_gate_names,
                metrics=_context_metrics(summaries),
                source_refs=_summary_refs(summaries),
                promotion_status=LessonPromotionStatus.ADVISORY,
            )
        )
    if _has_tool_protocol_diagnostic(
        summaries, replay_result=replay_result
    ) and not _causal_explains_tool_protocol_noise(causal_aggregates):
        diagnostics.append(
            _diagnostic(
                kind=HarnessDiagnosticKind.TOOL_PROTOCOL,
                title="Tool protocol signals require candidate strategy repair",
                summary="Replay or evaluation reported invalid, compacted, or schema-incompatible tool interaction signals.",
                affected_gates=failed_gate_names,
                metrics=_tool_protocol_metrics(summaries, replay_result=replay_result),
                source_refs=_summary_refs(summaries),
                promotion_status=LessonPromotionStatus.PROMOTED_TO_STRATEGY_HINT,
            )
        )
    if _has_evaluator_inconsistency(
        summaries
    ) and not _causal_explains_evaluation_noise(causal_aggregates):
        diagnostics.append(
            _diagnostic(
                kind=HarnessDiagnosticKind.EVALUATION,
                title="Judge evaluation was inconsistent or incomplete",
                summary="Judge attempts failed or produced too few successful structured evaluations for a stable decision.",
                affected_gates=failed_gate_names,
                metrics=_evaluation_consistency_metrics(summaries),
                source_refs=_summary_refs(summaries),
                promotion_status=LessonPromotionStatus.ADVISORY,
            )
        )
    if "duplicate_rejected_candidate" in failed_gate_names:
        diagnostics.append(
            _diagnostic(
                kind=HarnessDiagnosticKind.MEMORY,
                title="Candidate repeated a rejected variant",
                summary="Candidate generation reproduced a previously rejected content fingerprint for the same target.",
                affected_gates=("duplicate_rejected_candidate",),
                metrics=_gate_failure_details(gate_results, "duplicate_rejected_candidate"),
                source_refs=_summary_refs(summaries),
                promotion_status=LessonPromotionStatus.ADVISORY,
            )
        )
    if "protected_path" in failed_gate_names:
        diagnostics.append(
            _diagnostic(
                kind=HarnessDiagnosticKind.PERMISSION_BOUNDARY,
                title="Candidate targeted a protected path",
                summary="Candidate proposal attempted to modify a path outside the allowed self-evolve target boundary.",
                affected_gates=("protected_path",),
                metrics=_gate_failure_details(gate_results, "protected_path"),
                promotion_status=LessonPromotionStatus.ADVISORY,
            )
        )
    return tuple(_dedupe_diagnostics(diagnostics))


def _causal_explains_evaluation_noise(
    events: Sequence[AggregatedReplayFailure],
) -> bool:
    return any(
        event.stage is FailureStage.EVALUATION
        and event.owner
        in {
            FailureOwner.TASK,
            FailureOwner.INFRASTRUCTURE,
            FailureOwner.FRAMEWORK,
        }
        or event.code.startswith(("judge_", "evaluation_"))
        for event in events
    )


def _causal_explains_tool_protocol_noise(
    events: Sequence[AggregatedReplayFailure],
) -> bool:
    return any(
        event.owner is FailureOwner.CANDIDATE
        and event.stage
        in {
            FailureStage.ADAPTATION,
            FailureStage.CAPABILITY_COMPILE,
            FailureStage.CAPABILITY_PREFLIGHT,
        }
        for event in events
    )


def _diagnostic(
    *,
    kind: HarnessDiagnosticKind,
    title: str,
    summary: str,
    affected_gates: tuple[str, ...],
    metrics: Mapping[str, Any] | None = None,
    source_refs: tuple[str, ...] = (),
    promotion_status: LessonPromotionStatus,
) -> HarnessDiagnostic:
    payload = {
        "kind": kind.value,
        "title": title,
        "summary": summary,
        "affected_gates": affected_gates,
        "source_refs": source_refs,
        "metrics": dict(metrics or {}),
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return HarnessDiagnostic(
        diagnostic_id=f"{kind.value}-{digest}",
        kind=kind,
        title=title,
        summary=summary,
        affected_gates=affected_gates,
        source_refs=source_refs,
        metrics=dict(metrics or {}),
        promotion_status=promotion_status,
    )


def _causal_aggregates(
    *,
    causal_events: Sequence[ReplayFailureEvent | AggregatedReplayFailure],
    replay_result: CandidateReplayResult | None,
) -> tuple[AggregatedReplayFailure, ...]:
    supplied_aggregates = tuple(
        event for event in causal_events if isinstance(event, AggregatedReplayFailure)
    )
    supplied_events = tuple(
        event for event in causal_events if isinstance(event, ReplayFailureEvent)
    )
    if supplied_aggregates:
        return tuple(sorted(supplied_aggregates, key=lambda item: item.semantic_key))
    if supplied_events:
        return aggregate_replay_failure_observations(
            tuple(ReplayFailureObservation(event=event) for event in supplied_events)
        )
    if replay_result is not None:
        return aggregate_replay_failures(replay_result)
    return ()


def _causal_diagnostic(
    event: AggregatedReplayFailure,
    *,
    affected_gates: tuple[str, ...],
) -> HarnessDiagnostic:
    if event.scope is FailureScope.SHARED_RUN or event.owner in {
        FailureOwner.INFRASTRUCTURE,
        FailureOwner.FRAMEWORK,
    }:
        kind = HarnessDiagnosticKind.WORKFLOW
        title = "Replay workflow reported a typed shared or framework failure"
        promotion = LessonPromotionStatus.ADVISORY
    elif event.owner is FailureOwner.TASK:
        kind = HarnessDiagnosticKind.EVALUATION
        title = "Replay reported a typed task-quality failure"
        promotion = LessonPromotionStatus.ADVISORY
    else:
        kind = HarnessDiagnosticKind.TOOL_PROTOCOL
        title = "Replay reported a typed candidate capability failure"
        promotion = (
            LessonPromotionStatus.PROMOTED_TO_STRATEGY_HINT
            if event.repairable
            else LessonPromotionStatus.ADVISORY
        )
    metrics = {
        "semantic_key": event.semantic_key,
        "code": event.code,
        "owner": event.owner.value,
        "stage": event.stage.value,
        "scope": event.scope.value,
        "repairable": event.repairable,
        "category": event.category,
        "capability_id": event.capability_id,
        "requirement_id": event.requirement_id,
        "occurrence_count": event.occurrence_count,
        "affected_member_count": event.affected_member_count,
        "distinct_source_count": event.distinct_source_count,
    }
    return HarnessDiagnostic(
        diagnostic_id=f"causal-{event.semantic_key}",
        kind=kind,
        title=title,
        summary=(
            f"Typed replay cause {event.code} occurred at {event.stage.value}; "
            f"ownership is {event.owner.value}."
        ),
        source_refs=tuple(
            sanitize_path_ref(item) for item in event.artifact_refs[:12]
        ),
        affected_gates=affected_gates,
        promotion_status=promotion,
        metrics=metrics,
    )


def _dedupe_diagnostics(
    diagnostics: Sequence[HarnessDiagnostic],
) -> list[HarnessDiagnostic]:
    deduped: dict[str, HarnessDiagnostic] = {}
    for diagnostic in diagnostics:
        deduped.setdefault(diagnostic.diagnostic_id, diagnostic)
    return list(deduped.values())


def _evidence_metrics(summaries: Sequence[EvaluationSummary | None]) -> dict[str, Any]:
    metric_keys = (
        "evidence_block_count",
        "evidence_bundle_entry_count",
        "evidence_bundle_valid",
        "evidence_compacted",
        "evidence_incomplete",
        "evidence_manifest_invalid_entry_count",
        "replay_evidence_manifest_invalid_entry_count",
    )
    payload: dict[str, Any] = {}
    for summary in summaries:
        if summary is None:
            continue
        for key in metric_keys:
            value = summary.metrics.get(key)
            if isinstance(value, bool) or isinstance(value, (int, float, str)):
                payload.setdefault(key, value)
    return payload


def _replay_metrics(replay_result: CandidateReplayResult | None) -> dict[str, Any]:
    if replay_result is None:
        return {}
    return {
        "baseline_status": replay_result.baseline.status,
        "candidate_status": replay_result.candidate.status,
        "baseline_repetition_count": _metric_int(
            replay_result.baseline.metrics.get("repetition_count")
        ),
        "candidate_repetition_count": _metric_int(
            replay_result.candidate.metrics.get("repetition_count")
        ),
        "baseline_successful_repetition_count": _metric_int(
            replay_result.baseline.metrics.get("successful_repetition_count")
        ),
        "candidate_successful_repetition_count": _metric_int(
            replay_result.candidate.metrics.get("successful_repetition_count")
        ),
    }


def _has_context_diagnostic(summaries: Sequence[EvaluationSummary | None]) -> bool:
    return any(
        _summary_bool(summary, key)
        for summary in summaries
        for key in (
            "context_window_exceeded",
            "trajectory_context_missing",
            "evidence_context_truncated",
            "prompt_context_over_budget",
        )
    )


def _context_metrics(summaries: Sequence[EvaluationSummary | None]) -> dict[str, Any]:
    keys = (
        "context_window_exceeded",
        "trajectory_context_missing",
        "evidence_context_truncated",
        "prompt_context_over_budget",
        "trajectory_token_count",
        "evidence_token_count",
    )
    return _summary_metric_subset(summaries, keys)


def _has_tool_protocol_diagnostic(
    summaries: Sequence[EvaluationSummary | None],
    *,
    replay_result: CandidateReplayResult | None,
) -> bool:
    # Legacy fallback consumes machine-type fields only.  Free-form reasons
    # are audit evidence and must never drive framework classification.
    text = " ".join(
        _summary_string_items(
            summaries,
            keys=(
                "replay_failure_types",
                "tool_failure_types",
            ),
        )
    ).lower()
    if replay_result is not None:
        for variant in (replay_result.baseline, replay_result.candidate):
            text += " " + " ".join(
                str(item)
                for key in ("replay_failure_types",)
                for item in _as_string_list(variant.metrics.get(key))
            ).lower()
    return any(
        marker in text
        for marker in (
            "invalid_tool",
            "tool_argument",
            "schema",
            "compacted_tool",
            "tool output compacted",
        )
    )


def _tool_protocol_metrics(
    summaries: Sequence[EvaluationSummary | None],
    *,
    replay_result: CandidateReplayResult | None,
) -> dict[str, Any]:
    payload = _summary_metric_subset(
        summaries,
        (
            "replay_failure_types",
            "tool_failure_types",
        ),
    )
    if replay_result is not None:
        payload.update(_replay_metrics(replay_result))
    return payload


def _has_evaluator_inconsistency(
    summaries: Sequence[EvaluationSummary | None],
) -> bool:
    for summary in summaries:
        if summary is None:
            continue
        failure_count = _metric_int(summary.metrics.get("judge_failure_count"))
        success_count = _metric_int(summary.metrics.get("judge_success_count"))
        if failure_count is not None and failure_count > 0:
            return True
        if success_count == 0 and summary.metrics.get("judge_failures"):
            return True
        if _summary_bool(summary, "evaluator_inconsistent"):
            return True
    return False


def _evaluation_consistency_metrics(
    summaries: Sequence[EvaluationSummary | None],
) -> dict[str, Any]:
    return _summary_metric_subset(
        summaries,
        (
            "judge_failure_count",
            "judge_success_count",
            "judge_attempt_count",
            "judge_failures",
            "evaluator_inconsistent",
        ),
    )


def _summary_metric_subset(
    summaries: Sequence[EvaluationSummary | None],
    keys: Sequence[str],
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for summary in summaries:
        if summary is None:
            continue
        for key in keys:
            value = summary.metrics.get(key)
            if value is None:
                continue
            if isinstance(value, bool) or isinstance(value, (int, float, str)):
                payload.setdefault(key, _sanitize_diagnostic_value(value))
            elif isinstance(value, list):
                payload.setdefault(
                    key,
                    [_sanitize_diagnostic_value(item) for item in value[:3]],
                )
    return payload


def _summary_bool(summary: EvaluationSummary | None, key: str) -> bool:
    return summary is not None and summary.metrics.get(key) is True


def _summary_string_items(
    summaries: Sequence[EvaluationSummary | None],
    *,
    keys: Sequence[str],
) -> list[str]:
    values: list[str] = []
    for summary in summaries:
        if summary is None:
            continue
        for key in keys:
            values.extend(_as_string_list(summary.metrics.get(key)))
    return values


def _as_string_list(value: Any) -> list[str]:
    if isinstance(value, str) and value:
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return []


def _sanitize_diagnostic_value(value: Any) -> Any:
    if isinstance(value, str):
        from aworld.self_evolve.sanitization import sanitize_text

        return sanitize_text(value, max_chars=160)
    if isinstance(value, bool) or isinstance(value, (int, float)) or value is None:
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize_diagnostic_value(item)
            for key, item in list(value.items())[:8]
        }
    return str(value)[:160]


def _gate_failure_details(
    gate_results: Sequence[GateResult],
    gate_name: str,
) -> dict[str, Any]:
    for gate in gate_results:
        if gate.gate_name == gate_name and not gate.passed:
            return dict(gate.details or {})
    return {}


def _summary_refs(summaries: Sequence[EvaluationSummary | None]) -> tuple[str, ...]:
    refs: list[str] = []
    for summary in summaries:
        if summary is None:
            continue
        for key in ("report_path", "evidence_ref", "evidence_bundle_path"):
            value = summary.metrics.get(key)
            if isinstance(value, str) and value:
                refs.append(sanitize_path_ref(value))
    return tuple(dict.fromkeys(refs[:12]))


def _replay_refs(replay_result: CandidateReplayResult | None) -> tuple[str, ...]:
    if replay_result is None:
        return ()
    refs: list[str] = []
    for variant in (replay_result.baseline, replay_result.candidate):
        value = variant.metrics.get("artifact_dir")
        if isinstance(value, str) and value:
            refs.append(sanitize_path_ref(value))
    return tuple(dict.fromkeys(refs[:12]))


def _metric_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None
