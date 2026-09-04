"""Replay resource-evidence aggregation and legacy report projection."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from aworld.self_evolve.evaluation_reporting import (
    _summary_with_replay_evidence_metrics,
)
from aworld.self_evolve.gates import CostLatencyRegressionGate
from aworld.self_evolve.replay import (
    CandidateReplayResult,
    _aggregate_member_variant_results,
)
from aworld.self_evolve.types import EvaluationSummary


def reproject_missing_resource_evidence(
    report: dict[str, Any],
    *,
    replay_result: CandidateReplayResult,
) -> str | None:
    """Recover root resource metrics from completed replay member evidence."""

    members = replay_result.member_results or ()
    baseline_metrics = report.get("baseline_metrics")
    candidate_metrics = report.get("candidate_metrics")
    if (
        not members
        or not isinstance(baseline_metrics, Mapping)
        or not isinstance(candidate_metrics, Mapping)
    ):
        return None
    candidate_id = str(replay_result.candidate.variant_id or "").strip()
    if not candidate_id:
        return None
    baseline_replay = _aggregate_member_variant_results(
        base_variant_id="baseline",
        members=members,
        select=lambda member: member.baseline,
        artifact_dir=Path("."),
        persist=False,
    )
    candidate_replay = _aggregate_member_variant_results(
        base_variant_id=candidate_id,
        members=members,
        select=lambda member: member.candidate,
        artifact_dir=Path("."),
        persist=False,
    )
    baseline = _summary_with_replay_evidence_metrics(
        EvaluationSummary("baseline", baseline_metrics),
        baseline_replay,
    )
    candidate = _summary_with_replay_evidence_metrics(
        EvaluationSummary(candidate_id, candidate_metrics),
        candidate_replay,
    )
    gate = CostLatencyRegressionGate(
        max_cost_regression_ratio=0.25,
        max_latency_regression_ratio=0.5,
        require_resource_evidence=True,
    ).evaluate(baseline=baseline, candidate=candidate)
    if not gate.passed:
        return None
    report["baseline_metrics"] = dict(baseline.metrics)
    report["candidate_metrics"] = dict(candidate.metrics)
    raw_gates = report.get("gate_results")
    if not isinstance(raw_gates, list):
        return None
    replaced_gate = False
    for raw_gate in raw_gates:
        if (
            isinstance(raw_gate, dict)
            and raw_gate.get("gate_name") == "cost_latency_regression"
            and raw_gate.get("passed") is False
            and isinstance(raw_gate.get("details"), Mapping)
            and raw_gate["details"].get("code")
            == "resource_regression_evidence_missing"
        ):
            raw_gate.update(
                {
                    "passed": True,
                    "reason": gate.reason,
                    "details": dict(gate.details or {}),
                }
            )
            replaced_gate = True
    if not replaced_gate:
        return None
    rejection = report.get("rejection_attribution")
    if isinstance(rejection, Mapping):
        report["campaign_failure_attribution"] = dict(rejection)
    return candidate_id
