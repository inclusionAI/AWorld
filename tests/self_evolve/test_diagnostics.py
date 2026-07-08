from __future__ import annotations

from aworld.self_evolve.diagnostics import (
    HarnessDiagnosticKind,
    extract_harness_diagnostics,
)
from aworld.self_evolve.types import EvaluationSummary, GateResult


def test_extract_harness_diagnostics_covers_context_tool_and_evaluation_signals() -> None:
    diagnostics = extract_harness_diagnostics(
        gate_results=(
            GateResult(
                gate_name="score_improvement",
                passed=False,
                reason="candidate did not improve",
            ),
        ),
        summaries=(
            EvaluationSummary(
                variant_id="candidate",
                dataset_split="validation",
                metrics={
                    "context_window_exceeded": True,
                    "trajectory_context_missing": True,
                    "replay_failure_types": ["invalid_tool_arguments"],
                    "replay_failure_reasons": [
                        "tool argument schema error included SECRET_TOKEN=abc123"
                    ],
                    "judge_failure_count": 2,
                    "judge_success_count": 0,
                    "judge_failures": [
                        {"type": "JSONParseError", "reason": "bad json"}
                    ],
                },
            ),
        ),
    )

    kinds = {diagnostic.kind for diagnostic in diagnostics}
    assert HarnessDiagnosticKind.CONTEXT in kinds
    assert HarnessDiagnosticKind.TOOL_PROTOCOL in kinds
    assert HarnessDiagnosticKind.EVALUATION in kinds
    serialized = "\n".join(str(diagnostic) for diagnostic in diagnostics)
    assert "abc123" not in serialized
    assert "SECRET_TOKEN" not in serialized


def test_extract_harness_diagnostics_covers_existing_gate_categories() -> None:
    diagnostics = extract_harness_diagnostics(
        gate_results=(
            GateResult("evidence_quality", False, "bad evidence"),
            GateResult("candidate_replay", False, "replay failed"),
            GateResult("duplicate_rejected_candidate", False, "duplicate"),
            GateResult("protected_path", False, "protected"),
        ),
        summaries=(
            EvaluationSummary(
                variant_id="candidate",
                dataset_split="validation",
                metrics={"evidence_compacted": True, "evidence_block_count": 0},
            ),
        ),
    )

    kinds = {diagnostic.kind for diagnostic in diagnostics}
    assert HarnessDiagnosticKind.ARTIFACT_LIFECYCLE in kinds
    assert HarnessDiagnosticKind.WORKFLOW in kinds
    assert HarnessDiagnosticKind.MEMORY in kinds
    assert HarnessDiagnosticKind.PERMISSION_BOUNDARY in kinds
