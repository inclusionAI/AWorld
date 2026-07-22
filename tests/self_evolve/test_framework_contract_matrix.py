"""Cardinality-neutral maintenance contracts for the self-evolve framework.

Lifecycle, conformance, diagnostics, lesson, and budget behavior introduced by
plans 004--007 must be exercised with one case and multiple cases. Member
aggregation must remain order-stable, and failure semantics must not depend on
``member_results`` being empty for a single case. Multi-case cost control may
deduplicate equivalent requirement shapes, but it must not omit distinct
requirement or fixture shapes.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, Sequence

import pytest

from aworld.self_evolve.budget import BudgetStage, StageWorkload
from aworld.self_evolve.datasets import EvalCase, SelfEvolveDataset
from aworld.self_evolve.credit_assignment import (
    TargetInventory,
    TargetSelectionReport,
    build_target_selection_decision,
)
from aworld.self_evolve.gates import TrustProvenanceGate
from aworld.self_evolve.failure_events import (
    aggregate_replay_failures,
    FailureOwner,
    FailureScope,
    FailureStage,
    ReplayExecutionStatus,
)
from aworld.self_evolve.diagnostics import HarnessDiagnosticKind, extract_harness_diagnostics
from aworld.self_evolve.lessons import extract_lesson_records
from aworld.self_evolve.replay import (
    AWorldCliCandidateReplayBackend,
    CandidateReplayMemberResult,
    CandidateReplayResult,
    ReplayExecutionRequest,
    ReplayExecutionResult,
    build_replay_request,
    candidate_replay_pair_coverage,
    normalize_replay_members,
    replay_dataset_fingerprint,
)
from aworld.self_evolve.replay_adaptation import ReplayCapabilityRequirement
from aworld.self_evolve.repair_conformance import (
    RepairConformanceContract,
    build_repair_conformance_probe_plan,
)
from aworld.self_evolve.replay_capability import (
    ReplayProtocolProbe,
    ReplayReadinessProbe,
    ReplayServiceSpec,
)
from aworld.self_evolve.types import (
    CandidateVariant,
    DatasetRecipe,
    SelfEvolveTargetRef,
    EvaluationSummary,
    GateResult,
)


RequirementShape = tuple[str, str]
MemberOutcome = Literal["succeeded", "failed"]


@dataclass(frozen=True)
class FrameworkContractDataset:
    """Generic test vocabulary shared by later framework contract plans."""

    name: str
    case_ids: tuple[str, ...]
    payloads: tuple[Any, ...]
    requirement_shapes: tuple[RequirementShape, ...]
    candidate_outcomes: tuple[MemberOutcome, ...]
    dataset: SelfEvolveDataset
    requirements: tuple[ReplayCapabilityRequirement, ...]


def _framework_contract_dataset(
    name: str,
    *,
    case_ids: Sequence[str],
    payloads: Sequence[Any],
    requirement_shapes: Sequence[RequirementShape],
    candidate_outcomes: Sequence[MemberOutcome] | None = None,
) -> FrameworkContractDataset:
    """Build a replay contract from arbitrary IDs, payloads, and shapes."""

    normalized_case_ids = tuple(case_ids)
    normalized_payloads = tuple(payloads)
    normalized_shapes = tuple(requirement_shapes)
    normalized_outcomes = tuple(
        candidate_outcomes or ("succeeded",) * len(normalized_case_ids)
    )
    field_lengths = {
        len(normalized_case_ids),
        len(normalized_payloads),
        len(normalized_shapes),
        len(normalized_outcomes),
    }
    if field_lengths != {len(normalized_case_ids)} or not normalized_case_ids:
        raise ValueError("contract case fields must be non-empty and have equal lengths")
    if len(set(normalized_case_ids)) != len(normalized_case_ids):
        raise ValueError("contract case IDs must be unique")

    cases = tuple(
        EvalCase(
            case_id=case_id,
            input=payload,
            source={"kind": "contract_matrix", "member_index": index},
        )
        for index, (case_id, payload) in enumerate(
            zip(normalized_case_ids, normalized_payloads, strict=True)
        )
    )
    dataset = SelfEvolveDataset(
        cases=cases,
        recipe=DatasetRecipe(
            source={"kind": "contract_matrix"},
            split_seed="contract-matrix",
            splits={
                "train": list(normalized_case_ids),
                "validation": [],
                "held_out": [],
            },
            trainable_case_ids=normalized_case_ids,
        ),
    )

    shape_order = tuple(dict.fromkeys(normalized_shapes))
    requirements = tuple(
        ReplayCapabilityRequirement(
            requirement_id=f"requirement-{index}",
            kind=kind,
            identifier=f"{kind}:{fixture_shape}",
            case_ids=tuple(
                case_id
                for case_id, case_shape in zip(
                    normalized_case_ids,
                    normalized_shapes,
                    strict=True,
                )
                if case_shape == shape
            ),
            evidence_refs=tuple(
                f"context:{case_id}"
                for case_id, case_shape in zip(
                    normalized_case_ids,
                    normalized_shapes,
                    strict=True,
                )
                if case_shape == shape
            ),
            status="bound",
            detail=f"fixture_shape={fixture_shape}",
        )
        for index, shape in enumerate(shape_order, start=1)
        for kind, fixture_shape in (shape,)
    )
    return FrameworkContractDataset(
        name=name,
        case_ids=normalized_case_ids,
        payloads=normalized_payloads,
        requirement_shapes=normalized_shapes,
        candidate_outcomes=normalized_outcomes,
        dataset=dataset,
        requirements=requirements,
    )


SINGLE_CASE = _framework_contract_dataset(
    "single_case",
    case_ids=("member-alpha",),
    payloads=({"task": "read a recorded snapshot"},),
    requirement_shapes=(("local_endpoint", "http-json"),),
)

THREE_SAME_SHAPE_CASES = _framework_contract_dataset(
    "three_same_shape_cases",
    case_ids=("member-kappa", "member-beta", "member-omega"),
    payloads=(
        {"task": "read snapshot one"},
        ["read", "snapshot two"],
        "read snapshot three",
    ),
    requirement_shapes=(
        ("local_endpoint", "http-json"),
        ("local_endpoint", "http-json"),
        ("local_endpoint", "http-json"),
    ),
)

THREE_DISTINCT_SHAPE_CASES = _framework_contract_dataset(
    "three_distinct_shape_cases",
    case_ids=("member-red", "member-green", "member-blue"),
    payloads=(
        {"task": "query the first recording"},
        {"task": "query the second recording"},
        {"task": "inspect the stateful transcript"},
    ),
    requirement_shapes=(
        ("local_endpoint", "http-json"),
        ("local_endpoint", "http-json"),
        ("stateful_tool", "record-list"),
    ),
)

MIXED_MEMBER_OUTCOMES = _framework_contract_dataset(
    "mixed_member_outcomes",
    case_ids=("member-north", "member-central", "member-south"),
    payloads=(
        {"task": "replay northern fixture"},
        {"task": "replay central fixture"},
        {"task": "replay southern fixture"},
    ),
    requirement_shapes=(
        ("local_endpoint", "http-json"),
        ("local_endpoint", "http-json"),
        ("stateful_tool", "record-list"),
    ),
    candidate_outcomes=("succeeded", "failed", "succeeded"),
)


CONTRACT_CARDINALITIES = (
    SINGLE_CASE,
    THREE_SAME_SHAPE_CASES,
    THREE_DISTINCT_SHAPE_CASES,
)


def _generic_conformance_plan(contract: FrameworkContractDataset):
    services = tuple(
        ReplayServiceSpec(
            service_id=f"service-{index}",
            requirement_id=requirement.requirement_id,
            transport="skill_runtime",
            response_fixture=f"fixtures/shape-{index}.json",
            runtime_entrypoint="replay/runtime.py",
            readiness=ReplayReadinessProbe(
                kind="http", timeout_seconds=1, path="/ready"
            ),
            protocol_probes=(
                ReplayProtocolProbe(
                    kind="http",
                    timeout_seconds=1,
                    path="/query",
                    request_text=(
                        '{"operation":"operation.' + str(index) + '"}'
                    ),
                    response_contains=f"private-recorded-value-{index}",
                ),
            ),
        )
        for index, requirement in enumerate(contract.requirements, start=1)
    )
    return build_repair_conformance_probe_plan(
        capability_id="generic-capability",
        services=services,
        requirements=contract.requirements,
        fixture_shape_fingerprints={
            service.response_fixture: (
                "sha256:" + contract.requirement_shapes[index - 1][1]
            )
            for index, service in enumerate(services, start=1)
        },
        contract=RepairConformanceContract(
            focus_candidate_id="candidate-parent",
            failure_codes=("generic_failure",),
            interaction_progress=1,
            base_file_fingerprints={"replay/runtime.py": "sha256:base"},
            required_branch_paths=("replay/runtime.py",),
            base_branch_fingerprints={"replay/runtime.py": "sha256:branch"},
        ),
        dataset_case_ids=contract.case_ids,
    )


@pytest.mark.parametrize(
    ("contract", "expected_group_count"),
    (
        (SINGLE_CASE, 1),
        (THREE_SAME_SHAPE_CASES, 1),
        (THREE_DISTINCT_SHAPE_CASES, 2),
    ),
    ids=("one-case", "three-repeated", "three-distinct"),
)
def test_conformance_probe_groups_cover_every_distinct_shape_at_any_cardinality(
    contract: FrameworkContractDataset,
    expected_group_count: int,
) -> None:
    plan = _generic_conformance_plan(contract)

    assert plan.total_case_count == len(contract.case_ids)
    assert plan.covered_case_ids == contract.case_ids
    assert len(plan.groups) == expected_group_count
    report = str(plan.to_dict())
    assert "private-recorded-value" not in report


def test_conformance_failed_distinct_group_has_bounded_affected_case_ids() -> None:
    plan = _generic_conformance_plan(THREE_DISTINCT_SHAPE_CASES)
    failed_group = next(
        group for group in plan.groups if group.case_ids == ("member-blue",)
    )

    assert failed_group.fingerprint.startswith("sha256:")
    assert failed_group.case_ids == ("member-blue",)
    assert set(
        case_id for group in plan.groups for case_id in group.case_ids
    ) == set(THREE_DISTINCT_SHAPE_CASES.case_ids)


def _normalized_members(
    result: CandidateReplayResult,
) -> tuple[CandidateReplayMemberResult, ...]:
    if result.member_results:
        return result.member_results
    return (
        CandidateReplayMemberResult(
            case_id=result.request.task_id,
            request=result.request,
            baseline=result.baseline,
            candidate=result.candidate,
        ),
    )


@pytest.mark.parametrize(
    "contract",
    CONTRACT_CARDINALITIES,
    ids=lambda contract: contract.name,
)
def test_contract_dataset_preserves_case_order_shapes_and_fingerprint(
    contract: FrameworkContractDataset,
) -> None:
    rebuilt = _framework_contract_dataset(
        contract.name,
        case_ids=contract.case_ids,
        payloads=contract.payloads,
        requirement_shapes=contract.requirement_shapes,
        candidate_outcomes=contract.candidate_outcomes,
    )

    assert tuple(case.case_id for case in contract.dataset.cases) == contract.case_ids
    assert tuple(case.input for case in contract.dataset.cases) == contract.payloads
    assert replay_dataset_fingerprint(contract.dataset) == replay_dataset_fingerprint(
        rebuilt.dataset
    )
    covered_case_ids = tuple(
        case_id
        for requirement in contract.requirements
        for case_id in requirement.case_ids
    )
    assert len(covered_case_ids) == len(contract.case_ids)
    assert set(covered_case_ids) == set(contract.case_ids)
    shape_order = tuple(dict.fromkeys(contract.requirement_shapes))
    assert tuple(
        (requirement.kind, requirement.detail, requirement.case_ids)
        for requirement in contract.requirements
    ) == tuple(
        (
            kind,
            f"fixture_shape={fixture_shape}",
            tuple(
                case_id
                for case_id, case_shape in zip(
                    contract.case_ids,
                    contract.requirement_shapes,
                    strict=True,
                )
                if case_shape == shape
            ),
        )
        for shape in shape_order
        for kind, fixture_shape in (shape,)
    )
    assert len(contract.requirements) == len(shape_order)


@pytest.mark.parametrize("trajectory_count", [1, 3])
def test_validated_generated_draft_scope_is_cardinality_neutral(
    tmp_path: Path,
    trajectory_count: int,
) -> None:
    target = SelfEvolveTargetRef(
        target_type="skill",
        target_id="generated-capability",
        path=None,
    )
    decisions = tuple(
        build_target_selection_decision(
            TargetSelectionReport(
                selected_target=target,
                confidence=0.99,
                evidence_step_ids=(f"member-{index}:step-1",),
                failure_category="skill",
                capability_fingerprint="sha256:" + "a" * 64,
            ),
            inventory=TargetInventory(entries=()),
            selection_origin="inferred",
        )
        for index in range(trajectory_count)
    )

    assert all(decision.report.selection_origin == "inferred" for decision in decisions)
    assert all(decision.provenance is not None for decision in decisions)
    assert all(
        TrustProvenanceGate().evaluate(
            decision.provenance,
            target_intent=decision.target_intent,
        ).passed is True
        for decision in decisions
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "contract",
    (*CONTRACT_CARDINALITIES, MIXED_MEMBER_OUTCOMES),
    ids=lambda contract: contract.name,
)
async def test_replay_contract_preserves_every_member_in_dataset_order(
    contract: FrameworkContractDataset,
    tmp_path: Path,
) -> None:
    target = SelfEvolveTargetRef(target_type="skill", target_id="contract-skill")
    candidate = CandidateVariant(
        candidate_id="candidate-contract",
        target=target,
        content="---\nname: contract-skill\n---\n# Contract skill\n",
        rationale="exercise the framework contract",
        target_fingerprint="sha256:contract-baseline",
    )
    outcomes = dict(zip(contract.case_ids, contract.candidate_outcomes, strict=True))
    calls: list[tuple[str, str]] = []

    async def fake_executor(
        request: ReplayExecutionRequest,
    ) -> ReplayExecutionResult:
        calls.append((request.task_id, request.variant_id))
        if request.variant_id == candidate.candidate_id:
            outcome = outcomes[request.task_id]
            if outcome == "failed":
                return ReplayExecutionResult(
                    status="failed",
                    trajectory=[],
                    failure={
                        "outcome": "candidate_failure",
                        "reason": "synthetic contract failure",
                    },
                )
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.variant_id}}],
        )

    request = build_replay_request(
        run_id=f"run-{contract.name}",
        workspace_root=tmp_path,
        target=target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay-skills",
        dataset=contract.dataset,
    )
    result = await AWorldCliCandidateReplayBackend(
        executor=fake_executor
    ).replay_candidate(
        request,
        candidate=candidate,
        dataset=contract.dataset,
    )
    members = _normalized_members(result)

    assert tuple(member.case_id for member in members) == contract.case_ids
    assert tuple(member.candidate.status for member in members) == (
        contract.candidate_outcomes
    )
    assert tuple(case_id for case_id, variant_id in calls if variant_id == "baseline") == (
        contract.case_ids
    )
    assert tuple(
        case_id
        for case_id, variant_id in calls
        if variant_id == candidate.candidate_id
    ) == contract.case_ids


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "contract",
    (SINGLE_CASE, THREE_DISTINCT_SHAPE_CASES),
    ids=lambda contract: contract.name,
)
@pytest.mark.parametrize(
    "scenario",
    (
        "all_success",
        "candidate_preflight",
        "candidate_task_failure",
        "baseline_task_failure",
        "shared_infrastructure",
    ),
)
async def test_replay_lifecycle_contract_is_cardinality_neutral(
    contract: FrameworkContractDataset,
    scenario: str,
    tmp_path: Path,
) -> None:
    target = SelfEvolveTargetRef(target_type="skill", target_id="lifecycle-skill")
    candidate = CandidateVariant(
        candidate_id="candidate-lifecycle",
        target=target,
        content="---\nname: lifecycle-skill\n---\n# Lifecycle skill\n",
        rationale="exercise typed replay lifecycle",
        target_fingerprint="sha256:lifecycle-baseline",
    )
    first_case_id = contract.case_ids[0]

    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        is_first = request.task_id == first_case_id
        if scenario == "candidate_preflight" and is_first and request.variant_id == "baseline":
            return ReplayExecutionResult(
                status="failed",
                trajectory=[],
                failure={
                    "type": "ReplayServiceProtocolError",
                    "outcome": "candidate_failure",
                    "reason": "synthetic capability preflight failure",
                },
            )
        if scenario == "shared_infrastructure" and is_first and request.variant_id == "baseline":
            return ReplayExecutionResult(
                status="failed",
                trajectory=[],
                failure={
                    "type": "ProcessError",
                    "outcome": "infrastructure_failure",
                    "reason": "synthetic shared runtime failure",
                },
            )
        if scenario == "baseline_task_failure" and is_first and request.variant_id == "baseline":
            return ReplayExecutionResult(
                status="failed",
                trajectory=[],
                failure={
                    "type": "TaskFailure",
                    "outcome": "task_failure",
                    "reason": "synthetic baseline task failure",
                },
            )
        if (
            scenario == "candidate_task_failure"
            and is_first
            and request.variant_id == candidate.candidate_id
        ):
            return ReplayExecutionResult(
                status="failed",
                trajectory=[],
                failure={
                    "type": "TaskFailure",
                    "outcome": "task_failure",
                    "reason": "synthetic candidate task failure",
                },
            )
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.variant_id}}],
        )

    request = build_replay_request(
        run_id=f"run-lifecycle-{scenario}-{contract.name}",
        workspace_root=tmp_path,
        target=target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay-skills",
        dataset=contract.dataset,
    )
    result = await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=candidate,
        dataset=contract.dataset,
    )
    normalized = normalize_replay_members(dataset=contract.dataset, replay_result=result)
    coverage = candidate_replay_pair_coverage(
        dataset=contract.dataset,
        replay_result=result,
    )

    assert normalized.valid
    assert tuple(member.case_id for member in normalized.members) == contract.case_ids
    assert result.member_results is not None
    assert len(result.member_results) == len(contract.case_ids)
    if scenario == "all_success":
        assert coverage["strict_pair_count"] == len(contract.case_ids)
        assert coverage["incomparable_pair_count"] == 0
        assert result.member_results is not None
        reversed_result = replace(
            result,
            member_results=tuple(reversed(result.member_results)),
        )
        assert tuple(
            member.case_id
            for member in normalize_replay_members(
                dataset=contract.dataset,
                replay_result=reversed_result,
            ).members
        ) == contract.case_ids
        missing = normalize_replay_members(
            dataset=contract.dataset,
            replay_result=replace(result, member_results=result.member_results[:-1]),
        )
        duplicate = normalize_replay_members(
            dataset=contract.dataset,
            replay_result=replace(
                result,
                member_results=(*result.member_results, result.member_results[0]),
            ),
        )
        assert len(missing.missing_case_ids) == 1
        assert missing.failure_events[0].owner is FailureOwner.FRAMEWORK
        assert duplicate.duplicate_case_ids == (contract.case_ids[0],)
    elif scenario == "candidate_preflight":
        event = normalized.members[0].baseline.failure
        assert event is not None
        assert (event.owner, event.stage, event.scope) == (
            FailureOwner.CANDIDATE,
            FailureStage.CAPABILITY_PREFLIGHT,
            FailureScope.CANDIDATE,
        )
        assert coverage["candidate_executed_count"] == 0
        assert coverage["candidate_failure_count"] == 0
        assert coverage["blocked_variant_count"] == (
            1 if len(contract.case_ids) == 1 else 2 * len(contract.case_ids) - 1
        )
        assert all(
            member.candidate.status is ReplayExecutionStatus.BLOCKED
            and member.candidate.blocked_by[0].event_id == event.event_id
            for member in normalized.members
        )
        causal = aggregate_replay_failures(result, normalized=normalized)
        assert len(causal) == 1
        assert causal[0].occurrence_count == 1
        assert causal[0].affected_member_count == len(contract.case_ids)
    elif scenario == "shared_infrastructure":
        event = normalized.members[0].baseline.failure
        assert event is not None
        assert event.owner is FailureOwner.INFRASTRUCTURE
        assert event.scope is FailureScope.SHARED_RUN
        assert coverage["candidate_executed_count"] == 0
        causal = aggregate_replay_failures(result, normalized=normalized)
        diagnostics = extract_harness_diagnostics(
            gate_results=(GateResult("candidate_replay", False, "failed"),),
            causal_events=causal,
        )
        assert len(diagnostics) == 1
        assert diagnostics[0].kind is HarnessDiagnosticKind.WORKFLOW
        assert diagnostics[0].metrics["affected_member_count"] == len(contract.case_ids)
    elif scenario == "candidate_task_failure":
        assert coverage["candidate_execution_failure_count"] == 1
        assert coverage["candidate_failure_count"] == 1
        assert coverage["blocked_variant_count"] == 0
    else:
        assert coverage["task_failure_pair_count"] == 1
        assert coverage["comparable_pair_count"] == len(contract.case_ids)


@pytest.mark.parametrize("trajectory_count", [1, 3])
def test_causal_lesson_contract_aggregates_by_semantics_not_cardinality(
    trajectory_count: int,
) -> None:
    feedback = tuple(
        EvaluationSummary(
            variant_id=f"candidate-{index}",
            dataset_split="validation",
            metrics={
                "task_id": f"task-{index}",
                "causal_failure_events": [
                    {
                        "semantic_key": "caller-key-is-audit-only",
                        "code": "generic_contract_rejected",
                        "owner": "candidate",
                        "stage": "capability_preflight",
                        "scope": "candidate",
                        "repairable": True,
                        "category": "capability_contract",
                        "occurrence_count": 1,
                        "occurrence_ids": [f"event-{index}"],
                        "affected_case_ids": [f"case-{index}"],
                    }
                ],
            },
        )
        for index in range(trajectory_count)
    )

    lessons = extract_lesson_records(
        feedback,
        target_scope={"target_type": "skill", "target_id": "generic"},
    )
    assert len(lessons) == 1
    assert lessons[0].occurrence_count == trajectory_count
    assert len(lessons[0].affected_case_ids) == trajectory_count


def test_causal_lesson_contract_keeps_heterogeneous_events_distinct() -> None:
    feedback = tuple(
        EvaluationSummary(
            variant_id=f"candidate-{index}",
            dataset_split="validation",
            metrics={
                "task_id": f"task-{index}",
                "causal_failure_events": [
                    {
                        "code": code,
                        "owner": "candidate",
                        "stage": "task_rollout",
                        "scope": "member",
                        "repairable": True,
                        "category": "task_contract",
                        "occurrence_ids": [f"event-{index}"],
                        "affected_case_ids": [f"case-{index}"],
                    }
                ],
            },
        )
        for index, code in enumerate(("first_contract", "first_contract", "second_contract"))
    )
    lessons = extract_lesson_records(
        feedback,
        target_scope={"target_type": "skill", "target_id": "generic"},
    )

    assert {lesson.metrics["causal_code"]: lesson.occurrence_count for lesson in lessons} == {
        "first_contract": 2,
        "second_contract": 1,
    }


@pytest.mark.parametrize(
    ("case_count", "repetitions", "shape_count", "replay_units", "shape_units"),
    (
        (1, 2, 2, 2, 2),
        (3, 2, 2, 6, 2),
    ),
)
def test_stage_budget_contract_uses_members_for_replay_and_shapes_for_conformance(
    case_count: int,
    repetitions: int,
    shape_count: int,
    replay_units: int,
    shape_units: int,
) -> None:
    workload = StageWorkload(
        case_count=case_count,
        repetitions=repetitions,
        distinct_conformance_shape_count=shape_count,
    )

    assert workload.units_for(BudgetStage.PAIRED_REPLAY) == replay_units
    assert workload.units_for(BudgetStage.EVALUATION) == replay_units
    assert workload.units_for(BudgetStage.CONFORMANCE) == shape_units
    assert workload.units_for(BudgetStage.SCREENING) == 1
