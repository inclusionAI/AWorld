from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from aworld.self_evolve.feedback import normalize_feedback_summary
from aworld.self_evolve.sanitization import sanitize_metric_value, sanitize_path_ref, sanitize_text
from aworld.self_evolve.trace_pack import TracePack
from aworld.self_evolve.types import EvaluationSummary


_MAX_SUMMARY_CHARS = 240


@dataclass(frozen=True)
class LessonRecord:
    lesson_id: str
    lesson_type: str
    title: str
    summary: str
    evidence_refs: tuple[str, ...] = ()
    target_scope: Mapping[str, Any] = field(default_factory=dict)
    generality: str = "target"
    confidence: str = "medium"
    source_run_ids: tuple[str, ...] = ()
    source_task_ids: tuple[str, ...] = ()
    metrics: Mapping[str, Any] = field(default_factory=dict)


def extract_lesson_records(
    feedback_items: Sequence[EvaluationSummary],
    *,
    target_scope: Mapping[str, Any],
    trace_packs: Sequence[TracePack] = (),
) -> tuple[LessonRecord, ...]:
    records: list[LessonRecord] = []
    for feedback in feedback_items:
        summary = normalize_feedback_summary(feedback)
        failed_gates = tuple(str(item) for item in summary.get("failed_gates", ()) if item)
        required_behaviors = tuple(
            str(item) for item in summary.get("required_behaviors", ()) if item
        )
        metrics = _lesson_metrics(summary)
        source_run_ids = _source_ids(feedback.metrics.get("run_id"))
        source_task_ids = _source_ids(feedback.metrics.get("task_id"))
        evidence_refs = _evidence_refs(feedback)

        if failed_gates:
            records.append(
                _record(
                    lesson_type="failure_memory",
                    title=f"Prevent {', '.join(failed_gates[:3])}",
                    summary=_failure_summary(failed_gates, summary),
                    evidence_refs=evidence_refs,
                    target_scope=target_scope,
                    confidence="medium",
                    source_run_ids=source_run_ids,
                    source_task_ids=source_task_ids,
                    metrics=metrics,
                )
            )
        if required_behaviors:
            records.append(
                _record(
                    lesson_type="required_runtime_behavior",
                    title="Preserve required runtime behavior",
                    summary=(
                        "Future candidates should preserve: "
                        + ", ".join(required_behaviors[:6])
                    ),
                    evidence_refs=evidence_refs,
                    target_scope=target_scope,
                    confidence="medium",
                    source_run_ids=source_run_ids,
                    source_task_ids=source_task_ids,
                    metrics={**metrics, "required_behaviors": list(required_behaviors)},
                )
            )
        if not failed_gates and _is_success(summary):
            records.append(
                _record(
                    lesson_type="success_memory",
                    title="Preserve high-scoring behavior",
                    summary="Candidate passed feedback checks with high score; preserve its lean behavior path.",
                    evidence_refs=evidence_refs,
                    target_scope=target_scope,
                    confidence="high",
                    source_run_ids=source_run_ids,
                    source_task_ids=source_task_ids,
                    metrics=metrics,
                )
            )
    records.extend(_trace_lesson_records(trace_packs, target_scope=target_scope))
    return tuple(records)


def _record(
    *,
    lesson_type: str,
    title: str,
    summary: str,
    evidence_refs: tuple[str, ...],
    target_scope: Mapping[str, Any],
    confidence: str,
    source_run_ids: tuple[str, ...],
    source_task_ids: tuple[str, ...],
    metrics: Mapping[str, Any],
) -> LessonRecord:
    clean_summary = sanitize_text(summary, max_chars=_MAX_SUMMARY_CHARS)
    clean_metrics = {
        str(key): sanitize_metric_value(value)
        for key, value in metrics.items()
    }
    payload = {
        "lesson_type": lesson_type,
        "title": title,
        "summary": clean_summary,
        "target_scope": dict(target_scope),
        "source_run_ids": source_run_ids,
        "source_task_ids": source_task_ids,
        "metrics": clean_metrics,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return LessonRecord(
        lesson_id=f"{lesson_type}-{digest}",
        lesson_type=lesson_type,
        title=title,
        summary=clean_summary,
        evidence_refs=evidence_refs,
        target_scope=dict(target_scope),
        confidence=confidence,
        source_run_ids=source_run_ids,
        source_task_ids=source_task_ids,
        metrics=clean_metrics,
    )


def _lesson_metrics(summary: Mapping[str, Any]) -> dict[str, Any]:
    metrics = summary.get("metrics")
    evidence = summary.get("evidence")
    payload: dict[str, Any] = {}
    if isinstance(metrics, Mapping):
        for key in (
            "score",
            "baseline_score",
            "candidate_score",
            "score_delta",
            "A1_groundedness",
            "B2_efficiency",
        ):
            if key in metrics:
                payload[key] = sanitize_metric_value(metrics[key])
    if isinstance(evidence, Mapping):
        for key in (
            "evidence_compacted",
            "evidence_incomplete",
            "evidence_manifest_invalid_entry_count",
            "invalid_entry_count",
            "veto_triggered",
        ):
            if key in evidence:
                payload[key] = sanitize_metric_value(evidence[key])
        issues = evidence.get("issues")
        if isinstance(issues, list):
            payload["evidence_issues"] = [
                sanitize_text(issue, max_chars=160)
                for issue in issues[:3]
                if str(issue).strip()
            ]
    failed_gates = summary.get("failed_gates")
    if isinstance(failed_gates, list):
        payload["failed_gates"] = [
            sanitize_text(item, max_chars=80) for item in failed_gates[:8]
        ]
    return payload


def _failure_summary(
    failed_gates: tuple[str, ...],
    summary: Mapping[str, Any],
) -> str:
    evidence = summary.get("evidence")
    evidence_bits: list[str] = []
    if isinstance(evidence, Mapping):
        for key in ("evidence_compacted", "evidence_incomplete", "veto_triggered"):
            if evidence.get(key) is True:
                evidence_bits.append(key)
    suffix = f"; evidence issues: {', '.join(evidence_bits)}" if evidence_bits else ""
    return f"Candidate failed gates: {', '.join(failed_gates[:6])}{suffix}."


def _is_success(summary: Mapping[str, Any]) -> bool:
    metrics = summary.get("metrics")
    if not isinstance(metrics, Mapping):
        return False
    score = metrics.get("score", metrics.get("candidate_score"))
    return isinstance(score, (int, float)) and score >= 85.0


def _trace_lesson_records(
    trace_packs: Sequence[TracePack],
    *,
    target_scope: Mapping[str, Any],
) -> list[LessonRecord]:
    records: list[LessonRecord] = []
    for pack in trace_packs:
        if not pack.steps:
            continue
        source_task_ids = _source_ids(pack.task_id)
        evidence_refs = tuple(step.evidence_id for step in pack.steps[:8])
        metrics = _trace_metrics(pack)
        if _trace_failed(pack):
            records.append(
                _record(
                    lesson_type="trajectory_failure_memory",
                    title="Avoid repeated trajectory failure pattern",
                    summary=_trace_summary(
                        pack,
                        prefix="Trajectory ended in a failed or incomplete state",
                    ),
                    evidence_refs=evidence_refs,
                    target_scope=target_scope,
                    confidence="medium",
                    source_run_ids=(),
                    source_task_ids=source_task_ids,
                    metrics=metrics,
                )
            )
        elif _trace_succeeded(pack):
            records.append(
                _record(
                    lesson_type="trajectory_success_memory",
                    title="Preserve successful trajectory pattern",
                    summary=_trace_summary(
                        pack,
                        prefix="Trajectory completed successfully with this bounded behavior path",
                    ),
                    evidence_refs=evidence_refs,
                    target_scope=target_scope,
                    confidence="medium",
                    source_run_ids=(),
                    source_task_ids=source_task_ids,
                    metrics=metrics,
                )
            )
            records.append(
                _record(
                    lesson_type="lean_solution_path",
                    title="Preserve lean successful path",
                    summary=_trace_summary(
                        pack,
                        prefix="Successful trajectory used a lean bounded path worth preserving",
                    ),
                    evidence_refs=evidence_refs,
                    target_scope=target_scope,
                    confidence="high",
                    source_run_ids=(),
                    source_task_ids=source_task_ids,
                    metrics=metrics,
                )
            )
    return records


def _trace_metrics(pack: TracePack) -> dict[str, Any]:
    statuses = [
        str(step.reward.get("status"))
        for step in pack.steps
        if step.reward.get("status") is not None
    ]
    tool_names = tuple(
        dict.fromkeys(
            tool_name
            for step in pack.steps
            for tool_name in step.tool_names
            if tool_name
        )
    )
    return {
        "trace_pack_id": sanitize_text(pack.pack_id, max_chars=160),
        "source_kind": sanitize_text(pack.source_kind, max_chars=80),
        "step_count": len(pack.steps),
        "omitted_step_count": pack.omitted_step_count,
        "statuses": [sanitize_text(status, max_chars=40) for status in statuses[:8]],
        "tool_names": [sanitize_text(tool_name, max_chars=80) for tool_name in tool_names[:8]],
    }


def _trace_failed(pack: TracePack) -> bool:
    if not pack.steps:
        return False
    final_status = _step_status(pack.steps[-1])
    if final_status in {"failed", "error", "timeout", "cancelled", "rejected"}:
        return True
    return any(_step_status(step) in {"failed", "error", "timeout"} for step in pack.steps)


def _trace_succeeded(pack: TracePack) -> bool:
    if not pack.steps:
        return False
    return _step_status(pack.steps[-1]) in {
        "success",
        "succeeded",
        "completed",
        "finished",
        "pass",
        "passed",
    }


def _step_status(step: Any) -> str:
    status = step.reward.get("status") if isinstance(step.reward, Mapping) else None
    return str(status).strip().lower() if status is not None else ""


def _trace_summary(pack: TracePack, *, prefix: str) -> str:
    tool_names = [
        tool_name
        for step in pack.steps
        for tool_name in step.tool_names
        if tool_name
    ]
    tool_phrase = (
        "tools=" + ", ".join(tuple(dict.fromkeys(tool_names))[:4])
        if tool_names
        else "tools=none"
    )
    excerpt = sanitize_text(pack.final_action_excerpt or "", max_chars=120)
    excerpt_phrase = f"; final_excerpt={excerpt}" if excerpt else ""
    return (
        f"{prefix}; task_id={sanitize_text(pack.task_id, max_chars=80)}; "
        f"steps={len(pack.steps)}; {tool_phrase}{excerpt_phrase}."
    )


def _source_ids(value: Any) -> tuple[str, ...]:
    if isinstance(value, str) and value:
        return (value,)
    if isinstance(value, list):
        return tuple(str(item) for item in value if isinstance(item, str) and item)
    return tuple()


def _evidence_refs(feedback: EvaluationSummary) -> tuple[str, ...]:
    refs: list[str] = []
    for key in ("evidence_ref", "evidence_refs", "report_path"):
        value = feedback.metrics.get(key)
        if isinstance(value, str) and value:
            refs.append(sanitize_path_ref(value))
        elif isinstance(value, list):
            refs.extend(sanitize_path_ref(item) for item in value if isinstance(item, str) and item)
    return tuple(refs[:8])
