from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from aworld.self_evolve.feedback import normalize_feedback_summary
from aworld.self_evolve.failure_events import (
    AGGREGATED_FAILURE_SCHEMA_VERSION,
    AggregatedReplayFailure,
    ReplayFailureEvent,
    ReplayFailureObservation,
    aggregate_replay_failure_observations,
)
from aworld.self_evolve.sanitization import sanitize_metric_value, sanitize_path_ref, sanitize_text
from aworld.self_evolve.trace_pack import TracePack
from aworld.self_evolve.types import EvaluationSummary


_MAX_SUMMARY_CHARS = 240
_MAX_LESSON_SOURCE_IDS = 32
_MAX_LESSON_OCCURRENCE_IDS = 64
_MAX_LESSON_EVIDENCE_REFS = 16


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
    occurrence_count: int = 1
    occurrence_ids: tuple[str, ...] = ()
    source_candidate_ids: tuple[str, ...] = ()
    affected_case_ids: tuple[str, ...] = ()
    affected_case_count: int = 0
    distinct_source_count: int = 0
    emission_ids: tuple[str, ...] = ()
    batch_ids: tuple[str, ...] = ()
    aggregate_digests: tuple[str, ...] = ()
    emission_stats: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)


def extract_lesson_records(
    feedback_items: Sequence[EvaluationSummary],
    *,
    target_scope: Mapping[str, Any],
    trace_packs: Sequence[TracePack] = (),
) -> tuple[LessonRecord, ...]:
    records: list[LessonRecord] = []
    for feedback in feedback_items:
        causal_records = _causal_lesson_records(
            feedback,
            target_scope=target_scope,
        )
        records.extend(causal_records)
        summary = normalize_feedback_summary(feedback)
        failed_gates = tuple(str(item) for item in summary.get("failed_gates", ()) if item)
        required_behaviors = tuple(
            str(item) for item in summary.get("required_behaviors", ()) if item
        )
        metrics = _lesson_metrics(summary)
        source_run_ids = _source_ids(feedback.metrics.get("run_id"))
        source_task_ids = _source_ids(feedback.metrics.get("task_id"))
        evidence_refs = _evidence_refs(feedback)

        if failed_gates and not causal_records:
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
    return aggregate_lesson_records(tuple(records))


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
    occurrence_count: int = 1,
    occurrence_ids: tuple[str, ...] = (),
    source_candidate_ids: tuple[str, ...] = (),
    affected_case_ids: tuple[str, ...] = (),
    affected_case_count: int | None = None,
    distinct_source_count: int | None = None,
    emission_id: str | None = None,
    batch_id: str | None = None,
    aggregate_digest: str | None = None,
    semantic_identity: str | None = None,
) -> LessonRecord:
    clean_summary = sanitize_text(summary, max_chars=_MAX_SUMMARY_CHARS)
    clean_source_run_ids = _bounded_unique_ids(
        source_run_ids, limit=_MAX_LESSON_SOURCE_IDS
    )
    clean_source_task_ids = _bounded_unique_ids(
        source_task_ids, limit=_MAX_LESSON_SOURCE_IDS
    )
    clean_source_candidate_ids = _bounded_unique_ids(
        source_candidate_ids, limit=_MAX_LESSON_SOURCE_IDS
    )
    clean_metrics = {
        str(key): sanitize_metric_value(value)
        for key, value in metrics.items()
    }
    semantic_metrics = {
        key: clean_metrics[key]
        for key in (
            "causal_semantic_key",
            "causal_code",
            "causal_owner",
            "causal_stage",
            "causal_scope",
            "causal_category",
            "capability_id",
            "requirement_id",
            "contract_fingerprint",
            "capability_identity_digest",
            "requirement_identity_digest",
            "contract_identity_digest",
            "required_behaviors",
        )
        if key in clean_metrics
    }
    payload = {
        "lesson_type": lesson_type,
        "title": title,
        "summary": clean_summary,
        "target_scope": dict(target_scope),
        "semantic_identity": semantic_identity,
        "semantic_metrics": semantic_metrics,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    clean_affected_case_count = max(
        len(set(affected_case_ids)),
        affected_case_count or 0,
    )
    clean_distinct_source_count = max(
        _distinct_source_count(
            clean_source_run_ids,
            clean_source_task_ids,
            clean_source_candidate_ids,
        ),
        distinct_source_count or 0,
    )
    clean_occurrence_count = max(1, int(occurrence_count))
    emission_stats: dict[str, Mapping[str, Any]] = {}
    if emission_id:
        emission_stats[emission_id] = {
            "occurrence_count": clean_occurrence_count,
            "affected_case_count": clean_affected_case_count,
            "distinct_source_count": clean_distinct_source_count,
            "batch_id": batch_id or "",
            "aggregate_digest": aggregate_digest or "",
        }
    return LessonRecord(
        lesson_id=f"{lesson_type}-{digest}",
        lesson_type=lesson_type,
        title=title,
        summary=clean_summary,
        evidence_refs=evidence_refs,
        target_scope=dict(target_scope),
        confidence=confidence,
        source_run_ids=clean_source_run_ids,
        source_task_ids=clean_source_task_ids,
        metrics=clean_metrics,
        occurrence_count=clean_occurrence_count,
        occurrence_ids=_bounded_unique_ids(
            occurrence_ids, limit=_MAX_LESSON_OCCURRENCE_IDS
        ),
        source_candidate_ids=clean_source_candidate_ids,
        affected_case_ids=_bounded_unique_ids(
            affected_case_ids, limit=_MAX_LESSON_SOURCE_IDS
        ),
        affected_case_count=clean_affected_case_count,
        distinct_source_count=clean_distinct_source_count,
        emission_ids=((emission_id,) if emission_id else ()),
        batch_ids=((batch_id,) if batch_id else ()),
        aggregate_digests=((aggregate_digest,) if aggregate_digest else ()),
        emission_stats=emission_stats,
    )


def _causal_lesson_records(
    feedback: EvaluationSummary,
    *,
    target_scope: Mapping[str, Any],
) -> list[LessonRecord]:
    raw_events = feedback.metrics.get("causal_failure_events")
    if not isinstance(raw_events, list):
        return []
    records: list[LessonRecord] = []
    for raw_event in raw_events[:64]:
        if not isinstance(raw_event, Mapping):
            continue
        try:
            aggregate = _typed_causal_aggregate(raw_event)
        except (TypeError, ValueError):
            if raw_event.get("schema_version") is not None:
                raise
            continue
        source_run_ids = _combined_source_ids(
            aggregate.source_run_ids, feedback.metrics.get("run_id")
        )
        source_task_ids = _combined_source_ids(
            aggregate.source_task_ids, feedback.metrics.get("task_id")
        )
        source_candidate_ids = _combined_source_ids(
            aggregate.source_candidate_ids, feedback.variant_id
        )
        artifact_refs = tuple(
            sanitize_path_ref(item)
            for item in aggregate.artifact_refs
            if item
        )
        metrics = {
            "causal_semantic_key": aggregate.semantic_key,
            "causal_code": aggregate.code,
            "causal_owner": aggregate.owner.value,
            "causal_stage": aggregate.stage.value,
            "causal_scope": aggregate.scope.value,
            "causal_category": aggregate.category,
            "repairable": aggregate.repairable,
            "capability_id": aggregate.capability_id,
            "requirement_id": aggregate.requirement_id,
            "contract_fingerprint": aggregate.contract_fingerprint,
            "capability_identity_digest": aggregate.capability_identity_digest,
            "requirement_identity_digest": aggregate.requirement_identity_digest,
            "contract_identity_digest": aggregate.contract_identity_digest,
        }
        records.append(
            _record(
                lesson_type="causal_failure_memory",
                title=f"Repair typed replay cause {aggregate.code}",
                summary=(
                    f"Replay cause {aggregate.code} at {aggregate.stage.value} is "
                    f"owned by {aggregate.owner.value}."
                ),
                evidence_refs=artifact_refs,
                target_scope=target_scope,
                confidence="medium",
                source_run_ids=source_run_ids,
                source_task_ids=source_task_ids,
                source_candidate_ids=source_candidate_ids,
                affected_case_ids=aggregate.affected_case_ids,
                affected_case_count=aggregate.affected_member_count,
                distinct_source_count=aggregate.distinct_source_count,
                occurrence_count=aggregate.occurrence_count,
                occurrence_ids=aggregate.occurrence_ids,
                metrics=metrics,
                emission_id=aggregate.emission_id,
                batch_id=aggregate.batch_id,
                aggregate_digest=aggregate.aggregate_digest,
                semantic_identity=aggregate.semantic_key,
            )
        )
    return records


def _typed_causal_aggregate(
    payload: Mapping[str, Any],
) -> AggregatedReplayFailure:
    if payload.get("schema_version") == AGGREGATED_FAILURE_SCHEMA_VERSION:
        return AggregatedReplayFailure.from_dict(payload)
    if payload.get("schema_version") is not None:
        event = ReplayFailureEvent.from_dict(payload)
        return aggregate_replay_failure_observations(
            (ReplayFailureObservation(event=event),)
        )[0]
    # Legacy aggregate rows have no schema or integrity fields.  Their caller
    # supplied semantic_key is audit-only; typed fields establish identity.
    return AggregatedReplayFailure.from_dict(payload)


def aggregate_lesson_records(
    records: Sequence[LessonRecord],
) -> tuple[LessonRecord, ...]:
    """Merge semantic duplicates without treating repeated copies as recurrence."""

    validate_lesson_records(records)
    groups: dict[str, list[LessonRecord]] = {}
    for record in records:
        groups.setdefault(record.lesson_id, []).append(record)
    merged: list[LessonRecord] = []
    for lesson_id in sorted(groups):
        items = groups[lesson_id]
        first = min(items, key=_lesson_record_canonical_key)
        all_occurrence_ids = sorted(
            {
                sanitize_text(value, max_chars=160)
                for item in items
                for value in item.occurrence_ids
                if str(value).strip()
            }
        )
        occurrence_ids = tuple(all_occurrence_ids[:_MAX_LESSON_OCCURRENCE_IDS])
        merged_emission_stats: dict[str, dict[str, Any]] = {}
        for item in items:
            for emission_id, stats in _record_emission_stats(item).items():
                existing = merged_emission_stats.get(emission_id)
                if existing is None:
                    merged_emission_stats[emission_id] = dict(stats)
                    continue
                for identity_field in ("batch_id", "aggregate_digest"):
                    previous = str(existing.get(identity_field) or "")
                    incoming = str(stats.get(identity_field) or "")
                    if previous and incoming and previous != incoming:
                        raise ValueError(
                            f"lesson emission {emission_id} has conflicting {identity_field}"
                        )
                    if not previous and incoming:
                        existing[identity_field] = incoming
                for count_field in (
                    "occurrence_count",
                    "affected_case_count",
                    "distinct_source_count",
                ):
                    existing[count_field] = max(
                        int(existing.get(count_field) or 0),
                        int(stats.get(count_field) or 0),
                    )
        if merged_emission_stats:
            occurrence_count = sum(
                max(1, int(item["occurrence_count"]))
                for item in merged_emission_stats.values()
            )
            affected_case_count = sum(
                max(0, int(item["affected_case_count"]))
                for item in merged_emission_stats.values()
            )
            distinct_source_count = sum(
                max(0, int(item["distinct_source_count"]))
                for item in merged_emission_stats.values()
            )
        elif all_occurrence_ids:
            occurrence_count = max(
                len(all_occurrence_ids),
                max(item.occurrence_count for item in items),
            )
            affected_case_count = max(item.affected_case_count for item in items)
            distinct_source_count = max(item.distinct_source_count for item in items)
        else:
            counts_by_source: dict[tuple[tuple[str, ...], ...], int] = {}
            for item in items:
                source_key = (
                    item.source_run_ids,
                    item.source_task_ids,
                    item.source_candidate_ids,
                    item.affected_case_ids,
                )
                counts_by_source[source_key] = max(
                    counts_by_source.get(source_key, 0), item.occurrence_count
                )
            occurrence_count = sum(counts_by_source.values())
            affected_case_count = max(item.affected_case_count for item in items)
            distinct_source_count = max(item.distinct_source_count for item in items)
        source_run_ids = _bounded_unique_ids(
            (value for item in items for value in item.source_run_ids),
            limit=_MAX_LESSON_SOURCE_IDS,
        )
        source_task_ids = _bounded_unique_ids(
            (value for item in items for value in item.source_task_ids),
            limit=_MAX_LESSON_SOURCE_IDS,
        )
        source_candidate_ids = _bounded_unique_ids(
            (value for item in items for value in item.source_candidate_ids),
            limit=_MAX_LESSON_SOURCE_IDS,
        )
        affected_case_ids = _bounded_unique_ids(
            (value for item in items for value in item.affected_case_ids),
            limit=_MAX_LESSON_SOURCE_IDS,
        )
        evidence_refs = _bounded_unique_ids(
            (value for item in items for value in item.evidence_refs),
            limit=_MAX_LESSON_EVIDENCE_REFS,
        )
        merged.append(
            LessonRecord(
                lesson_id=lesson_id,
                lesson_type=first.lesson_type,
                title=first.title,
                summary=first.summary,
                evidence_refs=evidence_refs,
                target_scope=first.target_scope,
                generality=first.generality,
                confidence=max(
                    (item.confidence for item in items),
                    key=lambda value: {"low": 0, "medium": 1, "high": 2}.get(value, 0),
                ),
                source_run_ids=source_run_ids,
                source_task_ids=source_task_ids,
                metrics=first.metrics,
                occurrence_count=max(1, occurrence_count),
                occurrence_ids=occurrence_ids,
                source_candidate_ids=source_candidate_ids,
                affected_case_ids=affected_case_ids,
                affected_case_count=max(
                    len(affected_case_ids),
                    affected_case_count,
                ),
                distinct_source_count=max(
                    distinct_source_count,
                    _distinct_source_count(
                        source_run_ids, source_task_ids, source_candidate_ids
                    ),
                ),
                emission_ids=tuple(sorted(merged_emission_stats)),
                batch_ids=tuple(
                    sorted(
                        {
                            str(stats.get("batch_id"))
                            for stats in merged_emission_stats.values()
                            if stats.get("batch_id")
                        }
                    )
                ),
                aggregate_digests=tuple(
                    sorted(
                        {
                            str(stats.get("aggregate_digest"))
                            for stats in merged_emission_stats.values()
                            if stats.get("aggregate_digest")
                        }
                    )
                ),
                emission_stats={
                    key: dict(merged_emission_stats[key])
                    for key in sorted(merged_emission_stats)
                },
            )
        )
    return tuple(merged)


def validate_lesson_records(records: Sequence[LessonRecord]) -> None:
    """Fail closed when an id is reused for a different semantic lesson."""

    semantic_payloads: dict[str, str] = {}
    for record in records:
        canonical = _lesson_record_canonical_key(record)
        previous = semantic_payloads.setdefault(record.lesson_id, canonical)
        if previous != canonical:
            raise ValueError(
                f"lesson_id {record.lesson_id!r} has conflicting semantic payloads"
            )
        _record_emission_stats(record)


def _lesson_record_canonical_key(record: LessonRecord) -> str:
    semantic_metrics: Mapping[str, Any]
    if record.lesson_type == "causal_failure_memory":
        semantic_metrics = record.metrics
    else:
        semantic_metrics = {
            key: record.metrics[key]
            for key in ("required_behaviors",)
            if key in record.metrics
        }
    return json.dumps(
        {
            "lesson_type": record.lesson_type,
            "title": record.title,
            "summary": record.summary,
            "target_scope": record.target_scope,
            "generality": record.generality,
            "metrics": semantic_metrics,
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def _record_emission_stats(
    record: LessonRecord,
) -> dict[str, dict[str, Any]]:
    if record.emission_stats:
        stats: dict[str, dict[str, Any]] = {}
        for emission_id, raw in record.emission_stats.items():
            if not isinstance(raw, Mapping):
                raise ValueError("lesson emission_stats values must be mappings")
            clean_id = str(emission_id)
            if not clean_id:
                raise ValueError("lesson emission_id must be non-empty")
            stats[clean_id] = {
                "occurrence_count": _exact_lesson_count(
                    raw.get("occurrence_count"), minimum=1
                ),
                "affected_case_count": _exact_lesson_count(
                    raw.get("affected_case_count"), minimum=0
                ),
                "distinct_source_count": _exact_lesson_count(
                    raw.get("distinct_source_count"), minimum=0
                ),
                "batch_id": str(raw.get("batch_id") or ""),
                "aggregate_digest": str(raw.get("aggregate_digest") or ""),
            }
        if record.emission_ids and set(record.emission_ids) != set(stats):
            raise ValueError("lesson emission_ids do not match emission_stats")
        return stats
    if record.lesson_type != "causal_failure_memory":
        return {}
    legacy_emission_id = "legacy-lesson-emission-" + hashlib.sha256(
        json.dumps(
            {
                "semantic": _lesson_record_canonical_key(record),
                "occurrence_ids": record.occurrence_ids,
                "source_run_ids": record.source_run_ids,
                "source_task_ids": record.source_task_ids,
                "source_candidate_ids": record.source_candidate_ids,
                "affected_case_ids": record.affected_case_ids,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        legacy_emission_id: {
            "occurrence_count": max(1, int(record.occurrence_count or 1)),
            "affected_case_count": max(0, int(record.affected_case_count or 0)),
            "distinct_source_count": max(
                0, int(record.distinct_source_count or 0)
            ),
            "batch_id": "",
            "aggregate_digest": "",
        }
    }


def _exact_lesson_count(value: Any, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"lesson emission count must be an integer >= {minimum}")
    return value


def _bounded_unique_ids(values: Any, *, limit: int) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                sanitize_text(item, max_chars=160)
                for item in values
                if str(item).strip()
            }
        )[:limit]
    )


def _combined_source_ids(*values: Any) -> tuple[str, ...]:
    return _bounded_unique_ids(
        (item for value in values for item in _source_ids(value)),
        limit=_MAX_LESSON_SOURCE_IDS,
    )


def _distinct_source_count(
    run_ids: Sequence[str],
    task_ids: Sequence[str],
    candidate_ids: Sequence[str],
) -> int:
    return max(len(set(run_ids)), len(set(task_ids)), len(set(candidate_ids)), 0)


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
            "evidence_bundle_valid",
            "evidence_bundle_entry_count",
            "evidence_manifest_entry_count",
            "evidence_manifest_invalid_entry_count",
            "invalid_entry_count",
            "replay_invalid_entry_count",
            "replay_evidence_manifest_invalid_entry_count",
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
        compaction = _evidence_compaction_summary(evidence)
        if compaction:
            payload["evidence_compaction"] = compaction
    failed_gates = summary.get("failed_gates")
    if isinstance(failed_gates, list):
        payload["failed_gates"] = [
            sanitize_text(item, max_chars=80) for item in failed_gates[:8]
        ]
    return payload


def _evidence_compaction_summary(evidence: Mapping[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    bool_fields = {
        "evidence_compacted": "raw_evidence_compacted",
        "evidence_incomplete": "raw_evidence_incomplete",
        "evidence_bundle_valid": "bundle_valid",
    }
    for source_key, target_key in bool_fields.items():
        value = evidence.get(source_key)
        if isinstance(value, bool):
            payload[target_key] = value
    number_fields = {
        "evidence_bundle_entry_count": "bundle_entry_count",
        "evidence_manifest_entry_count": "manifest_entry_count",
        "evidence_manifest_invalid_entry_count": "manifest_invalid_entry_count",
        "invalid_entry_count": "manifest_invalid_entry_count",
        "replay_invalid_entry_count": "replay_manifest_invalid_entry_count",
        "replay_evidence_manifest_invalid_entry_count": "replay_manifest_invalid_entry_count",
    }
    for source_key, target_key in number_fields.items():
        value = evidence.get(source_key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            payload[target_key] = int(value) if float(value).is_integer() else value
    replay_failure_types = _bounded_string_list(evidence.get("replay_failure_types"), max_chars=80)
    if replay_failure_types:
        payload["replay_failure_types"] = replay_failure_types
    replay_failure_reasons = _bounded_string_list(
        evidence.get("replay_failure_reasons"),
        max_chars=96,
    )
    if replay_failure_reasons:
        payload["replay_failure_reasons"] = replay_failure_reasons
    return payload


def _bounded_string_list(value: Any, *, max_chars: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        sanitize_text(item, max_chars=max_chars)
        for item in value[:3]
        if str(item).strip()
    ]


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
    if isinstance(value, (list, tuple)):
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
