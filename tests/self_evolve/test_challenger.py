from __future__ import annotations

from dataclasses import replace

import pytest

from aworld.self_evolve.challenger import (
    ChallengeProposal,
    ChallengeProposalBatch,
    ChallengerRequest,
    DeterministicInvariantChallenger,
    TRANSFORM_PAD_TASK_TEXT,
    TRANSFORM_REVERSE_MAPPING_ORDER,
    admit_challenge_proposals,
    candidate_diff_fingerprint,
)
from aworld.self_evolve.datasets import (
    EvalCase,
    SelfEvolveDataset,
    SelfEvolveEvalSourceConfig,
    build_dataset_from_source,
)
from aworld.self_evolve.regression import (
    RegressionSuiteSpec,
    ResolvedRegressionSuite,
    dataset_case_fingerprints,
)
from aworld.self_evolve.replay import replay_dataset_fingerprint
from aworld.self_evolve.types import (
    CandidateVariant,
    DatasetRecipe,
    SelfEvolveTargetRef,
)


def _candidate() -> CandidateVariant:
    return CandidateVariant(
        candidate_id="candidate-one",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        content="---\nname: demo\n---\n# Demo\n\nNew behavior.\n",
        rationale="test challenger",
        target_fingerprint="sha256:baseline",
    )


def _suite(dataset: SelfEvolveDataset) -> ResolvedRegressionSuite:
    return ResolvedRegressionSuite(
        spec=RegressionSuiteSpec(
            suite_id="independent-suite",
            source_kind="jsonl",
            source_ref="independent.jsonl",
            source_version="sha256:source",
            dataset_fingerprint=replay_dataset_fingerprint(dataset),
            split_fingerprint="sha256:split",
            case_fingerprints=dataset_case_fingerprints(dataset),
        ),
        dataset=dataset,
    )


@pytest.mark.asyncio
async def test_challenger_proposes_registered_transform_without_approval_authority() -> None:
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent"},
            "state": {"input": {"content": "Run independent workflow."}},
            "action": {"content": "Workflow complete."},
            "reward": {"status": "succeeded"},
        }
    ]
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="historical-success",
    )
    candidate = _candidate()

    batch = await DeterministicInvariantChallenger().propose(
        ChallengerRequest(
            candidate=candidate,
            current_content="---\nname: demo\n---\n# Demo\n\nOld behavior.\n",
            regression_suites=(_suite(dataset),),
        )
    )

    assert len(batch.proposals) == 1
    assert batch.proposals[0].transformation_id == TRANSFORM_PAD_TASK_TEXT
    assert "passed" not in batch.to_dict()
    assert "score" not in batch.to_dict()


@pytest.mark.asyncio
async def test_challenger_admission_materializes_framework_transform_and_inherits_oracle() -> None:
    case = EvalCase(
        case_id="oracle-case",
        input={"content": "Check the resource."},
        expected_output={"status": "complete"},
        verification_command="python -m verify_result",
    )
    dataset = SelfEvolveDataset(
        cases=(case,),
        recipe=DatasetRecipe(
            source={"kind": "jsonl"},
            split_seed="seed",
            splits={"train": [case.case_id], "validation": [], "held_out": []},
            trainable_case_ids=(case.case_id,),
        ),
    )
    candidate = _candidate()
    current_content = "---\nname: demo\n---\n# Demo\n\nOld behavior.\n"
    suite = _suite(dataset)
    batch = await DeterministicInvariantChallenger().propose(
        ChallengerRequest(
            candidate=candidate,
            current_content=current_content,
            regression_suites=(suite,),
        )
    )

    report = admit_challenge_proposals(
        batch,
        candidate=candidate,
        current_content=current_content,
        regression_suites=(suite,),
    )

    assert report.admitted_count == 1
    assert report.to_dict()["approval_authority"] is False
    assert len(report.suites) == 1
    materialized = report.suites[0].dataset.cases[0]
    assert materialized.input == {"content": "\nCheck the resource.\n"}
    assert materialized.expected_output == case.expected_output
    assert materialized.verification_command == case.verification_command
    assert report.suites[0].spec.source_kind == "challenger"


def test_challenger_admission_rejects_changed_source_and_diff() -> None:
    case = EvalCase(
        case_id="oracle-case",
        input="Check the resource.",
        expected_output="complete",
    )
    dataset = SelfEvolveDataset(
        cases=(case,),
        recipe=DatasetRecipe(
            source={"kind": "jsonl"},
            split_seed="seed",
            splits={"train": [case.case_id], "validation": [], "held_out": []},
            trainable_case_ids=(case.case_id,),
        ),
    )
    candidate = _candidate()
    current_content = "old"
    suite = _suite(dataset)
    diff_fingerprint = candidate_diff_fingerprint(current_content, candidate)
    proposal = ChallengeProposal(
        proposal_id="challenge-one",
        source_suite_id=suite.spec.suite_id,
        source_case_id=case.case_id,
        source_case_fingerprint="sha256:tampered",
        transformation_id=TRANSFORM_PAD_TASK_TEXT,
        invariant_id="preserve_independent_historical_behavior",
        candidate_diff_fingerprint=diff_fingerprint,
        rationale="test tampering",
    )
    batch = ChallengeProposalBatch(
        candidate_id=candidate.candidate_id,
        candidate_diff_fingerprint=diff_fingerprint,
        proposals=(proposal,),
        challenger_id="test",
    )

    changed_source = admit_challenge_proposals(
        batch,
        candidate=candidate,
        current_content=current_content,
        regression_suites=(suite,),
    )
    changed_proposal = replace(
        proposal,
        source_case_fingerprint=suite.spec.case_fingerprints[0],
        candidate_diff_fingerprint="sha256:changed",
    )
    changed_diff = admit_challenge_proposals(
        replace(
            batch,
            candidate_diff_fingerprint="sha256:changed",
            proposals=(changed_proposal,),
        ),
        candidate=candidate,
        current_content=current_content,
        regression_suites=(suite,),
    )

    assert changed_source.admitted_count == 0
    assert changed_source.admissions[0].reason_code == "challenge_source_case_changed"
    assert changed_diff.admitted_count == 0
    assert changed_diff.admissions[0].reason_code == "challenge_diff_mismatch"


@pytest.mark.asyncio
async def test_challenger_skips_cases_without_independent_oracle() -> None:
    case = EvalCase(case_id="no-oracle", input="Do something.")
    dataset = SelfEvolveDataset(
        cases=(case,),
        recipe=DatasetRecipe(
            source={"kind": "jsonl"},
            split_seed="seed",
            splits={"train": [case.case_id], "validation": [], "held_out": []},
            trainable_case_ids=(case.case_id,),
        ),
    )

    batch = await DeterministicInvariantChallenger().propose(
        ChallengerRequest(
            candidate=_candidate(),
            current_content="old",
            regression_suites=(_suite(dataset),),
        )
    )

    assert batch.proposals == ()


@pytest.mark.asyncio
async def test_challenger_materializes_mapping_order_as_transport_probe() -> None:
    case = EvalCase(
        case_id="mapping-order",
        input={"content": "Run it.", "mode": "strict"},
        expected_output="complete",
    )
    dataset = SelfEvolveDataset(
        cases=(case,),
        recipe=DatasetRecipe(
            source={"kind": "jsonl"},
            split_seed="seed",
            splits={"train": [case.case_id]},
            trainable_case_ids=(case.case_id,),
        ),
    )
    candidate = _candidate()
    suite = _suite(dataset)
    batch = await DeterministicInvariantChallenger().propose(
        ChallengerRequest(
            candidate=candidate,
            current_content="old",
            regression_suites=(suite,),
        )
    )

    report = admit_challenge_proposals(
        batch,
        candidate=candidate,
        current_content="old",
        regression_suites=(suite,),
    )

    assert batch.proposals[0].transformation_id == TRANSFORM_REVERSE_MAPPING_ORDER
    assert report.admitted_count == 1
    assert tuple(report.suites[0].dataset.cases[0].input) == ("mode", "content")
