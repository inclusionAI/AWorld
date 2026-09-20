from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Protocol

from aworld.self_evolve.datasets import EvalCase, SelfEvolveDataset
from aworld.self_evolve.ingestion.types import fingerprint_json
from aworld.self_evolve.regression import (
    RegressionSuiteSpec,
    ResolvedRegressionSuite,
    dataset_case_fingerprints,
    regression_case_fingerprint,
)
from aworld.self_evolve.replay import replay_dataset_fingerprint
from aworld.self_evolve.types import CandidateVariant, to_json_dict
from aworld.self_evolve.budget import BudgetStage


CHALLENGER_PROPOSAL_SCHEMA_VERSION = "aworld.self_evolve.challenger_proposal.v1"
CHALLENGER_REPORT_SCHEMA_VERSION = "aworld.self_evolve.challenger_report.v1"
MAX_CHALLENGE_CASES = 8
DEFAULT_CHALLENGE_CASES = 2
MAX_CHALLENGE_INPUT_BYTES = 64 * 1024

TRANSFORM_REVERSE_MAPPING_ORDER = "reverse_mapping_order.v1"
TRANSFORM_PAD_TASK_TEXT = "pad_task_text.v1"
_SUPPORTED_TRANSFORMATIONS = {
    TRANSFORM_REVERSE_MAPPING_ORDER,
    TRANSFORM_PAD_TASK_TEXT,
}
_TASK_TEXT_KEYS = ("content", "query", "prompt", "task", "instruction")
_SUCCESS_STATUSES = {
    "complete",
    "completed",
    "ok",
    "pass",
    "passed",
    "success",
    "succeeded",
}


@dataclass(frozen=True)
class ChallengerRequest:
    candidate: CandidateVariant
    current_content: str
    regression_suites: tuple[ResolvedRegressionSuite, ...]
    max_cases: int = DEFAULT_CHALLENGE_CASES

    def __post_init__(self) -> None:
        if not self.regression_suites:
            raise ValueError("challenger requires independent regression suites")
        if isinstance(self.max_cases, bool) or not 0 < self.max_cases <= MAX_CHALLENGE_CASES:
            raise ValueError(
                f"challenger max_cases must be between 1 and {MAX_CHALLENGE_CASES}"
            )


@dataclass(frozen=True)
class ChallengeProposal:
    proposal_id: str
    source_suite_id: str
    source_case_id: str
    source_case_fingerprint: str
    transformation_id: str
    invariant_id: str
    candidate_diff_fingerprint: str
    rationale: str
    schema_version: str = CHALLENGER_PROPOSAL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CHALLENGER_PROPOSAL_SCHEMA_VERSION:
            raise ValueError("unsupported challenger proposal schema")
        for name in (
            "proposal_id",
            "source_suite_id",
            "source_case_id",
            "source_case_fingerprint",
            "invariant_id",
            "candidate_diff_fingerprint",
            "rationale",
        ):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ValueError(f"challenger proposal requires {name}")
        if self.transformation_id not in _SUPPORTED_TRANSFORMATIONS:
            raise ValueError(
                f"unsupported challenger transformation: {self.transformation_id}"
            )

    @property
    def fingerprint(self) -> str:
        return fingerprint_json(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return to_json_dict(self)


@dataclass(frozen=True)
class ChallengeProposalBatch:
    candidate_id: str
    candidate_diff_fingerprint: str
    proposals: tuple[ChallengeProposal, ...]
    challenger_id: str
    batch_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def __post_init__(self) -> None:
        if not self.candidate_id or not self.challenger_id or not self.batch_id:
            raise ValueError("challenger proposal batch identity is incomplete")
        if len(self.proposals) > MAX_CHALLENGE_CASES:
            raise ValueError("challenger proposal batch exceeds the case limit")
        if len({item.proposal_id for item in self.proposals}) != len(self.proposals):
            raise ValueError("challenger proposal ids must be unique")
        if any(
            proposal.candidate_diff_fingerprint != self.candidate_diff_fingerprint
            for proposal in self.proposals
        ):
            raise ValueError("challenger proposals do not match the batch diff")

    @property
    def fingerprint(self) -> str:
        return fingerprint_json(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "candidate_id": self.candidate_id,
            "candidate_diff_fingerprint": self.candidate_diff_fingerprint,
            "challenger_id": self.challenger_id,
            "proposals": [proposal.to_dict() for proposal in self.proposals],
        }


class ChallengerBackend(Protocol):
    async def propose(self, request: ChallengerRequest) -> ChallengeProposalBatch:
        """Propose tests only. This interface intentionally has no approval result."""


@dataclass(frozen=True)
class ChallengeAdmission:
    proposal_id: str
    proposal_fingerprint: str
    admitted: bool
    reason_code: str
    materialized_case_id: str | None = None
    materialized_case_fingerprint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return to_json_dict(self)


@dataclass(frozen=True)
class ChallengeReport:
    batch: ChallengeProposalBatch
    admissions: tuple[ChallengeAdmission, ...]
    suites: tuple[ResolvedRegressionSuite, ...] = field(
        repr=False,
        compare=False,
    )
    schema_version: str = CHALLENGER_REPORT_SCHEMA_VERSION

    @property
    def admitted_count(self) -> int:
        return sum(item.admitted for item in self.admissions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "batch": self.batch.to_dict(),
            "batch_fingerprint": self.batch.fingerprint,
            "admitted_count": self.admitted_count,
            "rejected_count": len(self.admissions) - self.admitted_count,
            "admissions": [item.to_dict() for item in self.admissions],
            "suite_specs": [suite.spec.to_dict() for suite in self.suites],
            "approval_authority": False,
        }


class DeterministicInvariantChallenger:
    """Select framework-owned metamorphic probes from independent evidence."""

    challenger_id = "aworld.deterministic_invariant_challenger.v1"

    def proves_zero_budget_usage(self, stage: BudgetStage) -> bool:
        return stage is BudgetStage.CHALLENGER

    async def propose(self, request: ChallengerRequest) -> ChallengeProposalBatch:
        diff_fingerprint = candidate_diff_fingerprint(
            request.current_content,
            request.candidate,
        )
        proposals: list[ChallengeProposal] = []
        for suite in request.regression_suites:
            for case in suite.dataset.cases:
                if len(proposals) >= request.max_cases:
                    break
                if not _case_has_independent_oracle(case):
                    continue
                transformation_id = _transformation_for_input(case.input)
                if transformation_id is None:
                    continue
                proposals.append(
                    ChallengeProposal(
                        proposal_id=(
                            "challenge-"
                            + hashlib.sha256(
                                (
                                    f"{suite.spec.suite_id}:{case.case_id}:"
                                    f"{transformation_id}:{diff_fingerprint}"
                                ).encode("utf-8")
                            ).hexdigest()[:20]
                        ),
                        source_suite_id=suite.spec.suite_id,
                        source_case_id=case.case_id,
                        source_case_fingerprint=regression_case_fingerprint(case),
                        transformation_id=transformation_id,
                        invariant_id="preserve_independent_historical_behavior",
                        candidate_diff_fingerprint=diff_fingerprint,
                        rationale=(
                            "Exercise a framework-registered metamorphic relation "
                            "against an independently sourced behavior oracle."
                        ),
                    )
                )
            if len(proposals) >= request.max_cases:
                break
        return ChallengeProposalBatch(
            candidate_id=request.candidate.candidate_id,
            candidate_diff_fingerprint=diff_fingerprint,
            proposals=tuple(proposals),
            challenger_id=self.challenger_id,
        )


def admit_challenge_proposals(
    batch: ChallengeProposalBatch,
    *,
    candidate: CandidateVariant,
    current_content: str,
    regression_suites: tuple[ResolvedRegressionSuite, ...],
) -> ChallengeReport:
    """Materialize only registered transformations over immutable source cases."""

    expected_diff = candidate_diff_fingerprint(current_content, candidate)
    suites_by_id = {suite.spec.suite_id: suite for suite in regression_suites}
    cases_by_suite = {
        suite.spec.suite_id: {case.case_id: case for case in suite.dataset.cases}
        for suite in regression_suites
    }
    admissions: list[ChallengeAdmission] = []
    admitted_by_suite: dict[str, list[EvalCase]] = {}
    seen_semantics: set[str] = set()
    for proposal in batch.proposals:
        reason_code = "challenge_admitted"
        case: EvalCase | None = None
        transformed: EvalCase | None = None
        if batch.candidate_id != candidate.candidate_id:
            reason_code = "challenge_candidate_mismatch"
        elif batch.candidate_diff_fingerprint != expected_diff:
            reason_code = "challenge_diff_mismatch"
        elif proposal.source_suite_id not in suites_by_id:
            reason_code = "challenge_source_suite_missing"
        else:
            case = cases_by_suite[proposal.source_suite_id].get(
                proposal.source_case_id
            )
            if case is None:
                reason_code = "challenge_source_case_missing"
            elif regression_case_fingerprint(case) != proposal.source_case_fingerprint:
                reason_code = "challenge_source_case_changed"
            elif not _case_has_independent_oracle(case):
                reason_code = "challenge_source_oracle_missing"
            else:
                transformed_input = _apply_registered_transformation(
                    case.input,
                    proposal.transformation_id,
                )
                if transformed_input is None:
                    reason_code = "challenge_transformation_not_applicable"
                elif _serialized_size(transformed_input) > MAX_CHALLENGE_INPUT_BYTES:
                    reason_code = "challenge_input_too_large"
                else:
                    transformed = replace(
                        case,
                        case_id=f"{case.case_id}::{proposal.proposal_id}",
                        input=transformed_input,
                        metadata={
                            **dict(case.metadata),
                            "challenger": {
                                "proposal_id": proposal.proposal_id,
                                "proposal_fingerprint": proposal.fingerprint,
                                "source_suite_id": proposal.source_suite_id,
                                "source_case_id": proposal.source_case_id,
                                "transformation_id": proposal.transformation_id,
                                "invariant_id": proposal.invariant_id,
                            },
                        },
                        source={
                            "kind": "challenger",
                            "source_suite_id": proposal.source_suite_id,
                            "source_case_id": proposal.source_case_id,
                            "proposal_id": proposal.proposal_id,
                        },
                    )
                    source_transport_fingerprint = _transport_input_fingerprint(
                        case.input
                    )
                    transformed_transport_fingerprint = (
                        _transport_input_fingerprint(transformed.input)
                    )
                    transformed_fingerprint = regression_case_fingerprint(transformed)
                    if (
                        transformed_transport_fingerprint
                        == source_transport_fingerprint
                    ):
                        reason_code = "challenge_transformation_is_noop"
                        transformed = None
                    elif transformed_fingerprint in seen_semantics:
                        reason_code = "challenge_semantic_duplicate"
                        transformed = None
                    else:
                        seen_semantics.add(transformed_fingerprint)
        admissions.append(
            ChallengeAdmission(
                proposal_id=proposal.proposal_id,
                proposal_fingerprint=proposal.fingerprint,
                admitted=transformed is not None,
                reason_code=reason_code,
                materialized_case_id=(
                    transformed.case_id if transformed is not None else None
                ),
                materialized_case_fingerprint=(
                    regression_case_fingerprint(transformed)
                    if transformed is not None
                    else None
                ),
            )
        )
        if transformed is not None:
            admitted_by_suite.setdefault(proposal.source_suite_id, []).append(
                transformed
            )

    challenge_suites = tuple(
        _challenge_suite(
            batch=batch,
            source_suite=suites_by_id[suite_id],
            cases=tuple(cases),
        )
        for suite_id, cases in sorted(admitted_by_suite.items())
        if cases
    )
    return ChallengeReport(
        batch=batch,
        admissions=tuple(admissions),
        suites=challenge_suites,
    )


def candidate_diff_fingerprint(
    current_content: str,
    candidate: CandidateVariant,
) -> str:
    payload = {
        "target_fingerprint": candidate.target_fingerprint,
        "current_content": current_content,
        "candidate_content": candidate.content,
        "candidate_files": to_json_dict(candidate.files),
    }
    return fingerprint_json(payload)


def _challenge_suite(
    *,
    batch: ChallengeProposalBatch,
    source_suite: ResolvedRegressionSuite,
    cases: tuple[EvalCase, ...],
) -> ResolvedRegressionSuite:
    case_ids = [case.case_id for case in cases]
    dataset = SelfEvolveDataset(
        cases=cases,
        recipe=replace(
            source_suite.dataset.recipe,
            source={
                "kind": "challenger",
                "source_suite_id": source_suite.spec.suite_id,
                "source_suite_version": source_suite.spec.source_version,
                "proposal_batch_fingerprint": batch.fingerprint,
            },
            split_seed=f"challenger:{batch.fingerprint}",
            splits={
                "train": case_ids,
                "validation": [],
                "held_out": [],
            },
            trainable_case_ids=tuple(case_ids),
            held_out_case_ids=(),
        ),
    )
    case_fingerprints = dataset_case_fingerprints(dataset)
    suite_id = (
        f"challenger-{source_suite.spec.suite_id[:72]}-"
        f"{batch.fingerprint.removeprefix('sha256:')[:12]}"
    )
    return ResolvedRegressionSuite(
        spec=RegressionSuiteSpec(
            suite_id=suite_id,
            source_kind="challenger",
            source_ref=f"challenger:{batch.batch_id}",
            source_version=batch.fingerprint,
            dataset_fingerprint=replay_dataset_fingerprint(dataset),
            split_fingerprint=fingerprint_json(dataset.recipe.splits),
            case_fingerprints=case_fingerprints,
        ),
        dataset=dataset,
    )


def _case_has_independent_oracle(case: EvalCase) -> bool:
    if case.verification_command or case.expected_output is not None:
        return True
    if (
        isinstance(case.source, Mapping)
        and case.source.get("kind") == "target_contract"
        and isinstance(case.metadata.get("target_contract"), Mapping)
        and case.metadata["target_contract"].get("target_fingerprint")
    ):
        return True
    pack = case.trace_pack
    if pack is None or not pack.steps:
        return False
    status = str(pack.steps[-1].reward.get("status") or "").strip().casefold()
    return status in _SUCCESS_STATUSES


def _transformation_for_input(value: Any) -> str | None:
    if isinstance(value, Mapping) and len(value) > 1:
        return TRANSFORM_REVERSE_MAPPING_ORDER
    if isinstance(value, str):
        return TRANSFORM_PAD_TASK_TEXT
    if isinstance(value, Mapping):
        for key in _TASK_TEXT_KEYS:
            if isinstance(value.get(key), str) and value[key].strip():
                return TRANSFORM_PAD_TASK_TEXT
    return None


def _apply_registered_transformation(value: Any, transformation_id: str) -> Any | None:
    if transformation_id == TRANSFORM_REVERSE_MAPPING_ORDER:
        if not isinstance(value, Mapping) or len(value) <= 1:
            return None
        return {key: value[key] for key in reversed(tuple(value.keys()))}
    if transformation_id == TRANSFORM_PAD_TASK_TEXT:
        if isinstance(value, str) and value.strip():
            return f"\n{value.strip()}\n"
        if isinstance(value, Mapping):
            for key in _TASK_TEXT_KEYS:
                text = value.get(key)
                if isinstance(text, str) and text.strip():
                    return {
                        item_key: (
                            f"\n{text.strip()}\n" if item_key == key else item_value
                        )
                        for item_key, item_value in value.items()
                    }
        return None
    return None


def _serialized_size(value: Any) -> int:
    return len(
        json.dumps(
            to_json_dict(value),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    )


def _transport_input_fingerprint(value: Any) -> str:
    encoded = json.dumps(
        to_json_dict(value),
        ensure_ascii=False,
        sort_keys=False,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
