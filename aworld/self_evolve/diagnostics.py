from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from aworld.self_evolve.replay import CandidateReplayResult
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
) -> tuple[HarnessDiagnostic, ...]:
    """Extract framework-level diagnostics without copying raw evidence text."""
    diagnostics: list[HarnessDiagnostic] = []
    failed_gate_names = tuple(gate.gate_name for gate in gate_results if not gate.passed)
    if "evidence_quality" in failed_gate_names:
        diagnostics.append(
            _diagnostic(
                kind=HarnessDiagnosticKind.ARTIFACT_LIFECYCLE,
                title="Evidence quality blocked verified apply",
                summary="Replay or evaluation evidence was missing, incomplete, compacted, or not artifact-backed enough for verified apply.",
                affected_gates=("evidence_quality",),
                metrics=_evidence_metrics(summaries),
                source_refs=_summary_refs(summaries),
            )
        )
    if "candidate_replay" in failed_gate_names or "replay_confidence" in failed_gate_names:
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
            )
        )
    if "evaluation" in failed_gate_names:
        diagnostics.append(
            _diagnostic(
                kind=HarnessDiagnosticKind.EVALUATION,
                title="Evaluation backend failed",
                summary="Evaluator execution failed before producing a trusted candidate decision.",
                affected_gates=("evaluation",),
                metrics=_gate_failure_details(gate_results, "evaluation"),
                source_refs=_summary_refs(summaries),
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
            )
        )
    return tuple(_dedupe_diagnostics(diagnostics))


def _diagnostic(
    *,
    kind: HarnessDiagnosticKind,
    title: str,
    summary: str,
    affected_gates: tuple[str, ...],
    metrics: Mapping[str, Any] | None = None,
    source_refs: tuple[str, ...] = (),
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
                refs.append(value)
    return tuple(dict.fromkeys(refs[:12]))


def _replay_refs(replay_result: CandidateReplayResult | None) -> tuple[str, ...]:
    if replay_result is None:
        return ()
    refs: list[str] = []
    for variant in (replay_result.baseline, replay_result.candidate):
        value = variant.metrics.get("artifact_dir")
        if isinstance(value, str) and value:
            refs.append(value)
    return tuple(dict.fromkeys(refs[:12]))


def _metric_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None
