from __future__ import annotations

from types import SimpleNamespace

import pytest

from aworld.self_evolve.campaign import (
    SelfImprovementDispositionKind,
    derive_self_improvement_disposition,
)
from aworld.self_evolve.skill_evolution_contract import (
    SKILL_EVOLUTION_CONTRACT_SCHEMA_VERSION,
    SkillEvolutionContract,
    evaluate_skill_evolution_replay,
)


def _contract(*, stable_cycles: int = 2) -> SkillEvolutionContract:
    return SkillEvolutionContract.from_dict(
        {
            "schema_version": SKILL_EVOLUTION_CONTRACT_SCHEMA_VERSION,
            "target_skill_id": "agent-browser",
            "objective": "Reliably handle large browser output",
            "capabilities": [
                {
                    "capability_id": "bounded_large_output",
                    "description": "Read large output without truncating evidence",
                    "case_ids": ["case-large", "case-tail"],
                    "required": True,
                },
                {
                    "capability_id": "preserve_navigation",
                    "description": "Preserve normal browser navigation",
                    "case_ids": ["case-nav"],
                    "required": True,
                },
            ],
            "preserved_invariants": ["Do not regress ordinary navigation"],
            "minimum_required_coverage": 1.0,
            "required_stable_cycles": stable_cycles,
        }
    )


def _member(case_id: str, *, attested: bool = True):
    return SimpleNamespace(
        case_id=case_id,
        candidate=SimpleNamespace(
            status="succeeded",
            metrics={
                "skill_activation_attested": attested,
                "candidate_intervention_observed": attested,
            },
        ),
    )


def test_contract_binds_target_and_dataset_cases() -> None:
    contract = _contract()

    contract.validate_run(
        target_type="skill",
        target_id="agent-browser",
        dataset_case_ids=("case-large", "case-tail", "case-nav"),
    )
    with pytest.raises(ValueError, match="unknown dataset cases"):
        contract.validate_run(
            target_type="skill",
            target_id="agent-browser",
            dataset_case_ids=("case-large",),
        )


def test_contract_coverage_requires_observed_candidate_activation() -> None:
    progress = evaluate_skill_evolution_replay(
        _contract(),
        SimpleNamespace(
            member_results=(
                _member("case-large"),
                _member("case-tail", attested=False),
                _member("case-nav"),
            )
        ),
    )

    assert progress["coverage_satisfied"] is False
    assert progress["covered_capability_ids"] == ["preserve_navigation"]
    assert progress["missing_required_capability_ids"] == [
        "bounded_large_output"
    ]


def test_contract_coverage_blocks_premature_campaign_completion() -> None:
    disposition = derive_self_improvement_disposition(
        {
            "status": "succeeded",
            "skill_evolution": {
                "coverage_satisfied": False,
                "covered_capability_ids": ["preserve_navigation"],
            },
        }
    )

    assert disposition.kind is SelfImprovementDispositionKind.CONTINUE_CANDIDATE
    assert disposition.reason_code == "skill_contract_coverage_incomplete"
