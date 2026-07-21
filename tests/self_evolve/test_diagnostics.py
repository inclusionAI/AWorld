from __future__ import annotations

from aworld.self_evolve.diagnostics import (
    HarnessDiagnosticKind,
    extract_harness_diagnostics,
)
from aworld.self_evolve.failure_events import (
    FailureOwner,
    FailureScope,
    FailureStage,
    ReplayFailureEvent,
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


def test_failure_event_semantic_key_excludes_occurrence_metadata() -> None:
    first = ReplayFailureEvent(
        event_id="occurrence-a",
        code="capability_contract_rejected",
        owner=FailureOwner.CANDIDATE,
        stage=FailureStage.CAPABILITY_PREFLIGHT,
        scope=FailureScope.CANDIDATE,
        repairable=True,
        category="capability_contract",
        capability_id="generic-capability",
        summary="SECRET_TOKEN=first /private/one",
        diagnostics={"case_id": "case-a", "raw_response": "first"},
        artifact_refs=("/private/one",),
    )
    second = ReplayFailureEvent(
        event_id="occurrence-b",
        code="capability_contract_rejected",
        owner=FailureOwner.CANDIDATE,
        stage=FailureStage.CAPABILITY_PREFLIGHT,
        scope=FailureScope.CANDIDATE,
        repairable=True,
        category="capability_contract",
        capability_id="generic-capability",
        summary="different free form reason",
        diagnostics={"case_id": "case-b", "raw_response": "second"},
        artifact_refs=("/private/two",),
    )

    assert first.semantic_key == second.semantic_key
    assert first.semantic_key != ReplayFailureEvent(
        code="capability_contract_rejected",
        owner=FailureOwner.CANDIDATE,
        stage=FailureStage.TASK_ROLLOUT,
        scope=FailureScope.CANDIDATE,
        repairable=True,
        category="capability_contract",
        capability_id="generic-capability",
    ).semantic_key


def test_extract_harness_diagnostics_prefers_typed_cause_over_generic_confidence() -> None:
    event = ReplayFailureEvent(
        code="capability_contract_rejected",
        owner=FailureOwner.CANDIDATE,
        stage=FailureStage.CAPABILITY_PREFLIGHT,
        scope=FailureScope.CANDIDATE,
        repairable=True,
        category="capability_contract",
        artifact_refs=("/workspace/private/replay.json",),
        diagnostics={"raw_response": "SECRET_TOKEN=abc123"},
    )
    diagnostics = extract_harness_diagnostics(
        gate_results=(GateResult("candidate_replay", False, "replay failed"),),
        causal_events=(event,),
    )

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.kind is HarnessDiagnosticKind.TOOL_PROTOCOL
    assert diagnostic.metrics["code"] == "capability_contract_rejected"
    assert diagnostic.metrics["owner"] == "candidate"
    assert diagnostic.metrics["occurrence_count"] == 1
    assert diagnostic.promotion_status.value == "promoted_to_strategy_hint"
    serialized = str(diagnostic)
    assert "abc123" not in serialized
    assert "raw_response" not in serialized


def test_extract_harness_diagnostics_maps_shared_infrastructure_to_workflow() -> None:
    event = ReplayFailureEvent(
        code="sandbox_unavailable",
        owner=FailureOwner.INFRASTRUCTURE,
        stage=FailureStage.CAPABILITY_PREFLIGHT,
        scope=FailureScope.SHARED_RUN,
        repairable=False,
        category="replay_environment",
    )
    diagnostics = extract_harness_diagnostics(
        gate_results=(GateResult("candidate_replay", False, "replay failed"),),
        causal_events=(event,),
    )

    assert len(diagnostics) == 1
    assert diagnostics[0].kind is HarnessDiagnosticKind.WORKFLOW
    assert diagnostics[0].metrics["scope"] == "shared_run"
