from __future__ import annotations

import hashlib
import json

import pytest

from aworld.self_evolve.diagnostics import (
    HarnessDiagnosticKind,
    extract_harness_diagnostics,
)
from aworld.self_evolve.failure_events import (
    FailureOwner,
    FailureScope,
    FailureStage,
    AggregatedReplayFailure,
    ReplayFailureEvent,
    ReplayFailureObservation,
    aggregate_replay_failure_observations,
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


def test_failure_identity_uses_full_canonical_values_and_rejects_lossy_event_ids() -> None:
    shared_prefix = "capability/" + ("same-prefix-" * 20)
    first = ReplayFailureEvent(
        event_id="event-first",
        code="contract_rejected",
        owner=FailureOwner.CANDIDATE,
        stage=FailureStage.CAPABILITY_PREFLIGHT,
        scope=FailureScope.CANDIDATE,
        repairable=True,
        capability_id=shared_prefix + "first",
        requirement_id=shared_prefix + "requirement-first",
        contract_fingerprint=shared_prefix + "contract-first",
    )
    second = ReplayFailureEvent(
        event_id="event-second",
        code="contract_rejected",
        owner=FailureOwner.CANDIDATE,
        stage=FailureStage.CAPABILITY_PREFLIGHT,
        scope=FailureScope.CANDIDATE,
        repairable=True,
        capability_id=shared_prefix + "second",
        requirement_id=shared_prefix + "requirement-second",
        contract_fingerprint=shared_prefix + "contract-second",
    )

    assert first.semantic_key != second.semantic_key
    assert len(first.semantic_key.removeprefix("replay-failure-")) == 64
    assert ReplayFailureEvent.from_dict(first.to_dict()) == first
    tampered = first.to_dict()
    tampered["capability_identity_digest"] = "0" * 64
    with pytest.raises(ValueError, match="canonical identity"):
        ReplayFailureEvent.from_dict(tampered)
    with pytest.raises(ValueError, match="exceeds 160"):
        ReplayFailureEvent(
            event_id="x" * 161,
            code="contract_rejected",
            owner=FailureOwner.CANDIDATE,
            stage=FailureStage.CAPABILITY_PREFLIGHT,
            scope=FailureScope.CANDIDATE,
            repairable=True,
        )
    with pytest.raises(ValueError, match="reused for a different occurrence"):
        aggregate_replay_failure_observations(
            (
                ReplayFailureObservation(event=first),
                ReplayFailureObservation(
                    event=ReplayFailureEvent(
                        event_id=first.event_id,
                        code="different_contract_rejected",
                        owner=FailureOwner.CANDIDATE,
                        stage=FailureStage.CAPABILITY_PREFLIGHT,
                        scope=FailureScope.CANDIDATE,
                        repairable=True,
                    )
                ),
            )
        )


def test_aggregate_round_trip_preserves_exact_counts_beyond_bounded_samples() -> None:
    def observations(batch: str) -> tuple[ReplayFailureObservation, ...]:
        return tuple(
            ReplayFailureObservation(
                event=ReplayFailureEvent(
                    event_id=f"{batch}-event-{index:03d}",
                    code="member_contract_rejected",
                    owner=FailureOwner.CANDIDATE,
                    stage=FailureStage.TASK_ROLLOUT,
                    scope=FailureScope.MEMBER,
                    repairable=True,
                    capability_id="generic-capability",
                ),
                case_id=f"{batch}-case-{index:03d}",
                run_id=f"{batch}-run-{index:03d}",
                task_id=f"{batch}-task-{index:03d}",
                candidate_id=f"{batch}-candidate-{index:03d}",
            )
            for index in range(70)
        )

    first = aggregate_replay_failure_observations(observations("first"))[0]
    repeated = aggregate_replay_failure_observations(observations("first"))[0]
    second = aggregate_replay_failure_observations(observations("second"))[0]

    assert first.occurrence_count == 70
    assert first.affected_member_count == 70
    assert first.distinct_source_count == 70
    assert len(first.occurrence_ids) == 64
    assert len(first.source_task_ids) == 32
    loaded = AggregatedReplayFailure.from_dict(first.to_dict())
    assert loaded.to_dict() == first.to_dict()
    tampered = first.to_dict()
    tampered["occurrence_count"] = 71
    with pytest.raises(ValueError, match="aggregate_digest"):
        AggregatedReplayFailure.from_dict(tampered)
    assert repeated.batch_id == first.batch_id
    assert repeated.emission_id == first.emission_id
    assert second.batch_id != first.batch_id
    assert second.emission_id != first.emission_id


def test_v1_typed_aggregate_is_verified_before_provenance_migration() -> None:
    event = ReplayFailureEvent(
        event_id="legacy-event",
        code="member_contract_rejected",
        owner=FailureOwner.CANDIDATE,
        stage=FailureStage.TASK_ROLLOUT,
        scope=FailureScope.MEMBER,
        repairable=True,
    )
    aggregate = aggregate_replay_failure_observations(
        (
            ReplayFailureObservation(
                event=event,
                case_id="case-a",
                run_id="run-a",
                task_id="task-a",
                candidate_id="candidate-a",
            ),
        )
    )[0]
    payload = aggregate.to_dict()
    payload["schema_version"] = "aworld.self_evolve.replay_failure_aggregate.v1"
    payload.pop("affected_case_identity_digests")
    payload.pop("source_identity_digests")
    digest_payload = {
        key: payload[key]
        for key in (
            "semantic_key",
            "code",
            "owner",
            "stage",
            "scope",
            "repairable",
            "category",
            "capability_identity_digest",
            "requirement_identity_digest",
            "contract_identity_digest",
            "occurrence_count",
            "affected_member_count",
            "distinct_source_count",
            "occurrence_ids",
            "affected_case_ids",
            "source_run_ids",
            "source_task_ids",
            "source_candidate_ids",
            "source_kinds",
            "batch_id",
        )
    }
    aggregate_digest = "replay-aggregate-sha256-" + hashlib.sha256(
        json.dumps(
            digest_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    payload["aggregate_digest"] = aggregate_digest
    payload["emission_id"] = "replay-emission-sha256-" + hashlib.sha256(
        json.dumps(
            {"batch_id": payload["batch_id"], "aggregate_digest": aggregate_digest},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    migrated = AggregatedReplayFailure.from_dict(payload)
    assert migrated.affected_member_count == 1
    assert len(migrated.affected_case_identity_digests) == 1
    tampered = {**payload, "affected_member_count": 2}
    with pytest.raises(ValueError, match="v1 aggregate_digest"):
        AggregatedReplayFailure.from_dict(tampered)


def test_typed_task_evaluation_cause_suppresses_only_explained_judge_noise() -> None:
    event = ReplayFailureEvent(
        code="judge_execution_failed",
        owner=FailureOwner.TASK,
        stage=FailureStage.EVALUATION,
        scope=FailureScope.MEMBER,
        repairable=False,
    )
    diagnostics = extract_harness_diagnostics(
        gate_results=(GateResult("evaluation", False, "judge failed"),),
        summaries=(
            EvaluationSummary(
                variant_id="candidate",
                dataset_split="validation",
                metrics={"judge_failure_count": 2, "judge_success_count": 0},
            ),
        ),
        causal_events=(event,),
    )

    assert len(diagnostics) == 1
    assert diagnostics[0].metrics["code"] == "judge_execution_failed"


def test_shared_infrastructure_cause_retains_independent_context_evidence_and_tool_issue() -> None:
    event = ReplayFailureEvent(
        code="sandbox_unavailable",
        owner=FailureOwner.INFRASTRUCTURE,
        stage=FailureStage.CAPABILITY_PREFLIGHT,
        scope=FailureScope.SHARED_RUN,
        repairable=False,
    )
    diagnostics = extract_harness_diagnostics(
        gate_results=(
            GateResult("candidate_replay", False, "sandbox unavailable"),
            GateResult("evidence_quality", False, "evidence incomplete"),
        ),
        summaries=(
            EvaluationSummary(
                variant_id="candidate",
                dataset_split="validation",
                metrics={
                    "trajectory_context_missing": True,
                    "replay_failure_types": ["invalid_tool_arguments"],
                },
            ),
        ),
        causal_events=(event,),
    )

    kinds = [item.kind for item in diagnostics]
    assert HarnessDiagnosticKind.WORKFLOW in kinds
    assert HarnessDiagnosticKind.ARTIFACT_LIFECYCLE in kinds
    assert HarnessDiagnosticKind.CONTEXT in kinds
    assert HarnessDiagnosticKind.TOOL_PROTOCOL in kinds


def test_candidate_capability_cause_retains_independent_judge_issue() -> None:
    event = ReplayFailureEvent(
        code="evaluation_candidate_protocol_failed",
        owner=FailureOwner.CANDIDATE,
        stage=FailureStage.CAPABILITY_PREFLIGHT,
        scope=FailureScope.CANDIDATE,
        repairable=True,
        capability_id="private-capability-identity",
        requirement_id="private-requirement-identity",
    )
    diagnostics = extract_harness_diagnostics(
        gate_results=(),
        summaries=(
            EvaluationSummary(
                variant_id="candidate",
                dataset_split="validation",
                metrics={"judge_failure_count": 1, "judge_success_count": 0},
            ),
        ),
        causal_events=(event,),
    )

    assert [item.kind for item in diagnostics].count(HarnessDiagnosticKind.EVALUATION) == 1
    assert [item.kind for item in diagnostics].count(HarnessDiagnosticKind.TOOL_PROTOCOL) == 1
    causal = next(item for item in diagnostics if item.kind is HarnessDiagnosticKind.TOOL_PROTOCOL)
    assert "capability_id" not in causal.metrics
    assert "requirement_id" not in causal.metrics
    assert causal.metrics["capability_identity_digest"]
    assert causal.metrics["requirement_identity_digest"]
    assert "private-capability-identity" not in str(causal)
