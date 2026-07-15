from __future__ import annotations

from typing import Any, Mapping, Sequence


_RELEASE_CHECK_GROUPS = (
    {
        "check_id": "candidate_shape",
        "label": "Candidate shape",
        "gate_names": {
            "noop_candidate",
            "malformed_candidate",
            "skill_markdown",
            "prompt_section",
            "tool_description",
            "token_limit",
            "protected_path",
            "trust_provenance",
            "external_code_evolution",
            "auto_apply_target_type",
        },
    },
    {
        "check_id": "quality_improvement",
        "label": "Quality improvement",
        "gate_names": {"score_improvement", "replay_stability", "replay_confidence"},
    },
    {
        "check_id": "cost_latency",
        "label": "Cost and latency",
        "gate_names": {"cost_latency_regression", "budget"},
    },
    {
        "check_id": "evidence_integrity",
        "label": "Evidence integrity",
        "gate_names": {
            "candidate_capability_replay",
            "candidate_replay",
            "evidence_quality",
            "judge_only_signal",
            "replay_adaptation",
            "replay_capability",
        },
    },
    {
        "check_id": "verification",
        "label": "Verification",
        "gate_names": {"required_verification", "held_out_verification"},
    },
    {
        "check_id": "regression_safety",
        "label": "Regression safety",
        "gate_names": {"global_regression_benchmark", "post_apply"},
    },
)


def build_release_checklist(
    *,
    apply_policy: str,
    gate_results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Render low-level gate results as a stable, user-facing release checklist."""

    gates_by_name = _gate_results_by_name(gate_results)
    checks = [
        _build_group_check(group, gates_by_name, blocking=apply_policy == "auto_verified")
        for group in _RELEASE_CHECK_GROUPS
    ]
    failed_blocking = [
        check["check_id"]
        for check in checks
        if check["blocking"] and check["status"] == "failed"
    ]
    if failed_blocking:
        status = "blocked"
    elif not any(check["status"] != "not_run" for check in checks):
        status = "not_run"
    elif apply_policy == "auto_verified" and all(
        check["status"] in {"passed", "not_run"} for check in checks
    ):
        status = "passed"
    else:
        status = "diagnostic"
    return {
        "status": status,
        "apply_policy": apply_policy,
        "blocking_failed_checks": failed_blocking,
        "checks": checks,
    }


def build_content_quality_diagnostics(metrics: Mapping[str, Any] | None) -> dict[str, Any]:
    """Best-effort non-blocking content quality diagnostics derived from evaluator metrics."""

    metrics = metrics or {}
    if not _has_content_quality_metrics(metrics):
        checks = [
            _not_run_check("evidence_presence", "Evidence presence"),
            _not_run_check("citation_integrity", "Citation integrity"),
            _not_run_check("claim_support", "Claim support"),
            _not_run_check("structure_completeness", "Structure completeness"),
            _not_run_check("redundancy_control", "Redundancy control"),
            _not_run_check("publication_risk", "Publication risk"),
        ]
        return {
            "status": "not_run",
            "blocking": False,
            "failed_checks": [],
            "checks": checks,
        }
    checks = [
        _metric_check(
            check_id="evidence_presence",
            label="Evidence presence",
            passed=(
                _number_metric(metrics, "has_evidence") == 1.0
                or _number_metric(metrics, "evidence_block_count", default=0.0) > 0
            ),
            details={
                "has_evidence": metrics.get("has_evidence"),
                "evidence_block_count": metrics.get("evidence_block_count", 0),
            },
        ),
        _metric_check(
            check_id="citation_integrity",
            label="Citation integrity",
            passed=_number_metric(
                metrics,
                "evidence_manifest_invalid_entry_count",
                default=0.0,
            )
            == 0,
            details={
                "evidence_manifest_invalid_entry_count": metrics.get(
                    "evidence_manifest_invalid_entry_count",
                    0,
                )
            },
        ),
        _metric_check(
            check_id="claim_support",
            label="Claim support",
            passed=_number_metric(metrics, "unsupported_claim_count", default=0.0) == 0,
            details={"unsupported_claim_count": metrics.get("unsupported_claim_count", 0)},
        ),
        _metric_check(
            check_id="structure_completeness",
            label="Structure completeness",
            passed=metrics.get("answer_structure_passed", True) is not False,
            details={"answer_structure_passed": metrics.get("answer_structure_passed")},
        ),
        _metric_check(
            check_id="redundancy_control",
            label="Redundancy control",
            passed=_number_metric(metrics, "redundant_section_count", default=0.0) == 0,
            details={"redundant_section_count": metrics.get("redundant_section_count", 0)},
        ),
        _metric_check(
            check_id="publication_risk",
            label="Publication risk",
            passed=_number_metric(metrics, "publication_risk_count", default=0.0) == 0,
            details={"publication_risk_count": metrics.get("publication_risk_count", 0)},
        ),
    ]
    failed = [check["check_id"] for check in checks if check["status"] == "failed"]
    return {
        "status": "attention_needed" if failed else "passed",
        "blocking": False,
        "failed_checks": failed,
        "checks": checks,
    }


def _build_group_check(
    group: Mapping[str, Any],
    gates_by_name: Mapping[str, list[Mapping[str, Any]]],
    *,
    blocking: bool,
) -> dict[str, Any]:
    group_gate_names = set(group["gate_names"])
    gates = [
        gate
        for gate_name in group_gate_names
        for gate in gates_by_name.get(gate_name, [])
    ]
    if not gates:
        status = "not_run"
    elif any(gate.get("passed") is False for gate in gates):
        status = "failed"
    else:
        status = "passed"
    return {
        "check_id": group["check_id"],
        "label": group["label"],
        "status": status,
        "blocking": bool(blocking and status == "failed"),
        "gate_names": sorted(group_gate_names),
        "failed_gates": [
            str(gate.get("gate_name"))
            for gate in gates
            if gate.get("passed") is False
        ],
        "reasons": [
            str(gate.get("reason"))
            for gate in gates
            if isinstance(gate.get("reason"), str)
        ],
    }


def _gate_results_by_name(
    gate_results: Sequence[Mapping[str, Any]],
) -> dict[str, list[Mapping[str, Any]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for gate in gate_results:
        gate_name = gate.get("gate_name")
        if isinstance(gate_name, str):
            grouped.setdefault(gate_name, []).append(gate)
    return grouped


def _metric_check(
    *,
    check_id: str,
    label: str,
    passed: bool,
    details: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "label": label,
        "status": "passed" if passed else "failed",
        "blocking": False,
        "details": dict(details),
    }


def _not_run_check(check_id: str, label: str) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "label": label,
        "status": "not_run",
        "blocking": False,
        "details": {},
    }


def _has_content_quality_metrics(metrics: Mapping[str, Any]) -> bool:
    return any(
        key in metrics
        for key in (
            "has_evidence",
            "evidence_block_count",
            "evidence_manifest_invalid_entry_count",
            "unsupported_claim_count",
            "answer_structure_passed",
            "redundant_section_count",
            "publication_risk_count",
        )
    )


def _number_metric(
    metrics: Mapping[str, Any],
    key: str,
    *,
    default: float | None = None,
) -> float | None:
    value = metrics.get(key)
    if isinstance(value, (int, float)):
        return float(value)
    return default
