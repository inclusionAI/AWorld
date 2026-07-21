"""Cardinality-neutral maintenance contracts for the self-evolve framework.

Lifecycle, conformance, diagnostics, lesson, and budget behavior introduced by
plans 004--007 must be exercised with one case and multiple cases. Member
aggregation must remain order-stable, and failure semantics must not depend on
``member_results`` being empty for a single case. Multi-case cost control may
deduplicate equivalent requirement shapes, but it must not omit distinct
requirement or fixture shapes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Sequence

import pytest

from aworld.self_evolve.datasets import EvalCase, SelfEvolveDataset
from aworld.self_evolve.replay import (
    AWorldCliCandidateReplayBackend,
    CandidateReplayMemberResult,
    CandidateReplayResult,
    ReplayExecutionRequest,
    ReplayExecutionResult,
    build_replay_request,
    replay_dataset_fingerprint,
)
from aworld.self_evolve.replay_adaptation import ReplayCapabilityRequirement
from aworld.self_evolve.types import (
    CandidateVariant,
    DatasetRecipe,
    SelfEvolveTargetRef,
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
