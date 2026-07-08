from __future__ import annotations

from aworld.self_evolve.release_checks import (
    build_content_quality_diagnostics,
    build_release_checklist,
)


def test_release_checklist_groups_gate_results_into_user_facing_checks() -> None:
    checklist = build_release_checklist(
        apply_policy="auto_verified",
        gate_results=[
            {
                "gate_name": "score_improvement",
                "passed": True,
                "reason": "score improvement meets minimum delta",
            },
            {
                "gate_name": "cost_latency_regression",
                "passed": True,
                "reason": "cost and latency regressions are within policy",
            },
            {
                "gate_name": "evidence_quality",
                "passed": False,
                "reason": "evaluation evidence is compacted or incomplete",
            },
            {
                "gate_name": "required_verification",
                "passed": True,
                "reason": "required verification commands passed",
            },
            {
                "gate_name": "global_regression_benchmark",
                "passed": True,
                "reason": "global regression benchmark passed",
            },
        ],
    )

    assert checklist["status"] == "blocked"
    assert checklist["blocking_failed_checks"] == ["evidence_integrity"]
    checks = {check["check_id"]: check for check in checklist["checks"]}
    assert checks["quality_improvement"]["status"] == "passed"
    assert checks["cost_latency"]["status"] == "passed"
    assert checks["evidence_integrity"]["status"] == "failed"
    assert checks["evidence_integrity"]["blocking"] is True
    assert checks["verification"]["status"] == "passed"
    assert checks["regression_safety"]["status"] == "passed"


def test_release_checklist_marks_missing_checks_as_not_run_without_blocking_proposals() -> None:
    checklist = build_release_checklist(
        apply_policy="proposal",
        gate_results=[
            {
                "gate_name": "noop_candidate",
                "passed": True,
                "reason": "candidate changes target content",
            }
        ],
    )

    assert checklist["status"] == "diagnostic"
    assert checklist["blocking_failed_checks"] == []
    checks = {check["check_id"]: check for check in checklist["checks"]}
    assert checks["quality_improvement"]["status"] == "not_run"
    assert checks["quality_improvement"]["blocking"] is False
    assert checks["candidate_shape"]["status"] == "passed"


def test_content_quality_diagnostics_flags_supported_content_risks() -> None:
    diagnostics = build_content_quality_diagnostics(
        {
            "has_evidence": 1.0,
            "evidence_block_count": 2,
            "evidence_manifest_invalid_entry_count": 1,
            "unsupported_claim_count": 2,
            "redundant_section_count": 1,
            "answer_structure_passed": False,
            "publication_risk_count": 1,
        }
    )

    assert diagnostics["status"] == "attention_needed"
    assert diagnostics["blocking"] is False
    assert diagnostics["failed_checks"] == [
        "citation_integrity",
        "claim_support",
        "structure_completeness",
        "redundancy_control",
        "publication_risk",
    ]
    checks = {check["check_id"]: check for check in diagnostics["checks"]}
    assert checks["citation_integrity"]["status"] == "failed"
    assert checks["evidence_presence"]["status"] == "passed"
    assert checks["claim_support"]["details"]["unsupported_claim_count"] == 2


def test_content_quality_diagnostics_are_not_run_when_metrics_are_absent() -> None:
    diagnostics = build_content_quality_diagnostics({})

    assert diagnostics["status"] == "not_run"
    assert diagnostics["failed_checks"] == []
    assert all(check["status"] == "not_run" for check in diagnostics["checks"])
