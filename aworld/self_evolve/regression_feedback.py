"""Bounded, candidate-bound observations from existing regression results."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

from aworld.self_evolve.sanitization import sanitize_text


_SCHEMA = "aworld.self_evolve.independent_regression_feedback.v1"
_MAX_SUITES = 4
_MAX_GATES = 4
_MAX_CHARS = 12_000
_GATE_DETAIL_FIELDS = (
    "code", "decision", "failure_owner", "failure_class", "repairable", "delta",
    "delta_confidence_lower_bound", "delta_confidence_upper_bound", "minimum_delta",
    "noninferiority_margin", "paired_sample_count",
)
_QUALITY_GATES = {"score_improvement", "cost_latency_regression", "evidence_quality"}
_EXECUTION_GATES = {"candidate_replay", "evaluation_runtime_health", "independent_regression_execution", "run_budget_regression_replay"}


def _finite_number(value: object) -> bool:
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _omitted(value: object) -> int:
    return max(0, value) if isinstance(value, int) and not isinstance(value, bool) else 0


def _observations(metrics: object, *, issue_count: int, text_chars: int) -> dict[str, Any]:
    if not isinstance(metrics, Mapping):
        return {}
    result = {"score": metrics["score"]} if _finite_number(metrics.get("score")) else {}
    raw_issues = metrics.get("issues", metrics.get("evidence_issues"))
    if isinstance(raw_issues, list):
        issues = list(dict.fromkeys(x for x in raw_issues if isinstance(x, str) and x.strip()))
        result["issues"] = [sanitize_text(x, max_chars=text_chars) for x in issues[:issue_count]]
        omitted = len(issues) - len(result["issues"]) + _omitted(metrics.get("omitted_issue_count"))
        if omitted:
            result["omitted_issue_count"] = omitted
    return result


def bounded_independent_regression_feedback(value: object) -> dict[str, Any] | None:
    """Re-apply the field allowlist at each optimizer projection boundary."""
    if not isinstance(value, Mapping) or value.get("schema_version") != _SCHEMA:
        return None
    candidate_id, fingerprint, raw_suites = value.get("candidate_id"), value.get("evidence_fingerprint"), value.get("suites")
    if not isinstance(candidate_id, str) or not isinstance(fingerprint, str) or not isinstance(raw_suites, list):
        return None
    selected = raw_suites[:_MAX_SUITES]
    for issue_count, text_chars in ((3, 240), (2, 200), (1, 160), (1, 120), (1, 80)):
        result: dict[str, Any] = {
            "schema_version": _SCHEMA, "candidate_id": sanitize_text(candidate_id, max_chars=160),
            "evidence_fingerprint": sanitize_text(fingerprint, max_chars=80), "suites": [],
            "omitted_suite_count": max(0, len(raw_suites) - _MAX_SUITES) + _omitted(value.get("omitted_suite_count")),
        }
        for raw in selected:
            if not isinstance(raw, Mapping) or not isinstance(raw.get("suite_id"), str):
                continue
            raw_gates = raw.get("failed_gates")
            if not isinstance(raw_gates, list) or not raw_gates:
                continue
            row = {
                key: sanitize_text(raw[key], max_chars=limit)
                for key, limit in (("suite_id", 128), ("source_kind", 32), ("source_ref", 160), ("dataset_fingerprint", 80), ("execution_id", 160))
                if isinstance(raw.get(key), str)
            }
            row["fresh_execution"] = raw.get("fresh_execution") is True
            row["baseline"] = _observations(raw.get("baseline"), issue_count=issue_count, text_chars=text_chars)
            row["candidate"] = _observations(raw.get("candidate"), issue_count=issue_count, text_chars=text_chars)
            row["failed_gates"] = []
            for gate in raw_gates[:_MAX_GATES]:
                if not isinstance(gate, Mapping) or gate.get("passed") is not False:
                    continue
                details = gate.get("details")
                projected = {}
                if isinstance(details, Mapping):
                    for key in _GATE_DETAIL_FIELDS:
                        item = details.get(key)
                        if isinstance(item, str):
                            projected[key] = sanitize_text(item, max_chars=96)
                        elif isinstance(item, bool) or _finite_number(item):
                            projected[key] = item
                row["failed_gates"].append({
                    "gate_name": sanitize_text(gate.get("gate_name"), max_chars=80), "passed": False,
                    "reason": sanitize_text(gate.get("reason"), max_chars=text_chars), "details": projected,
                })
            row["omitted_gate_count"] = max(0, len(raw_gates) - _MAX_GATES) + _omitted(raw.get("omitted_gate_count"))
            names = {g.get("gate_name") for g in raw_gates if isinstance(g, Mapping) and g.get("passed") is False}
            row["judged"] = bool(
                raw.get("judged", True) is True and row["fresh_execution"] and "score" in row["baseline"] and "score" in row["candidate"]
                and names.intersection(_QUALITY_GATES) and not names.intersection(_EXECUTION_GATES)
            )
            result["suites"].append(row)
        if issue_count != 3 or value.get("text_compacted") is True:
            result["text_compacted"] = True
        if len(json.dumps(result, ensure_ascii=False, separators=(",", ":"))) <= _MAX_CHARS:
            return result
    # The fixed identity/score/gate projection wins over extra suites if an
    # extreme legacy record still exceeds the diagnostic budget.
    while result["suites"] and len(json.dumps(result, ensure_ascii=False, separators=(",", ":"))) > _MAX_CHARS:
        result["suites"].pop()
        result["omitted_suite_count"] += 1
    return result


def project_independent_regression_feedback(
    evidence: object, *, candidate_id: str, expected_fingerprint: object = None,
) -> dict[str, Any] | None:
    if not isinstance(evidence, Mapping) or evidence.get("candidate_id") != candidate_id or evidence.get("schema_version") != "aworld.self_evolve.regression_evidence.v1":
        return None
    fingerprint = evidence.get("fingerprint")
    if not isinstance(fingerprint, str) or (expected_fingerprint is not None and fingerprint != expected_fingerprint):
        return None
    raw_suites = evidence.get("suite_results")
    if not isinstance(raw_suites, list):
        return None
    suites, seen = [], set()
    for raw in raw_suites:
        if not isinstance(raw, Mapping):
            return None
        spec, baseline, candidate = raw.get("spec"), raw.get("baseline_summary"), raw.get("candidate_summary")
        if not isinstance(spec, Mapping) or not isinstance(baseline, Mapping) or not isinstance(candidate, Mapping):
            return None
        if spec.get("schema_version") != "aworld.self_evolve.regression_suite.v1":
            return None
        suite_id = spec.get("suite_id")
        if not isinstance(suite_id, str) or suite_id in seen:
            return None
        seen.add(suite_id)
        if baseline.get("variant_id") != "baseline" or candidate.get("variant_id") != candidate_id:
            return None
        if baseline.get("dataset_split") != "regression" or candidate.get("dataset_split") != "regression":
            return None
        gates = raw.get("gate_results")
        if not isinstance(gates, list):
            return None
        failed = [g for g in gates if isinstance(g, Mapping) and g.get("passed") is False]
        if not failed:
            continue
        suites.append({
            "suite_id": suite_id, "source_kind": spec.get("source_kind"), "source_ref": spec.get("source_ref"),
            "dataset_fingerprint": spec.get("dataset_fingerprint"), "execution_id": raw.get("execution_id"),
            "fresh_execution": raw.get("fresh_execution"), "baseline": baseline.get("metrics"),
            "candidate": candidate.get("metrics"), "failed_gates": failed,
        })
    # Prefer recorded candidate failures; never derive ownership or acceptance
    # from score values or from judge prose.
    suites.sort(key=lambda row: not any(
        isinstance(g.get("details"), Mapping) and g["details"].get("failure_owner", g["details"].get("failure_class")) == "candidate"
        for g in row["failed_gates"]
    ))
    return bounded_independent_regression_feedback({
        "schema_version": _SCHEMA, "candidate_id": candidate_id,
        "evidence_fingerprint": fingerprint, "suites": suites,
    })


def has_judged_independent_regression(value: object, *, candidate_id: object) -> bool:
    projected = bounded_independent_regression_feedback(value)
    return bool(projected and projected["candidate_id"] == candidate_id and any(s["judged"] for s in projected["suites"]))


def independent_regression_for_gate(details: object, *, candidate_id: str, evidence: object = None) -> dict[str, Any] | None:
    if not isinstance(details, Mapping) or not isinstance(details.get("evidence_fingerprint"), str):
        return None
    if evidence is not None:
        return project_independent_regression_feedback(evidence, candidate_id=candidate_id, expected_fingerprint=details["evidence_fingerprint"])
    projected = bounded_independent_regression_feedback(details.get("independent_regression"))
    if projected and projected["candidate_id"] == candidate_id and projected["evidence_fingerprint"] == details["evidence_fingerprint"]:
        return projected
    return None
