from __future__ import annotations

from dataclasses import replace

import pytest

from aworld.self_evolve.constitution import (
    AgenticRole,
    AgenticStageReportV1,
    AgenticStageStatus,
    ConstitutionContractError,
    SelfEvolveConstitutionV1,
    SelfEvolveStage,
    SemanticRolloutPolicyV1,
    SemanticRolloutStage,
    default_self_evolve_constitution,
    default_semantic_rollout_policy,
    safe_rollout_fallback,
    validate_report_chain,
    validate_rollout_advance,
    validate_stage_transition,
)


def _fingerprint(character: str) -> str:
    return "sha256:" + character * 64


def _report(
    *,
    stage: SelfEvolveStage = SelfEvolveStage.DISCOVER,
    status: AgenticStageStatus = AgenticStageStatus.COMPLETE,
    next_stage: SelfEvolveStage | None = SelfEvolveStage.UNDERSTAND,
    attempt_count: int = 1,
) -> AgenticStageReportV1:
    contract = default_self_evolve_constitution().contract_for(stage)
    return AgenticStageReportV1(
        report_id=f"report-{stage.value}",
        stage=stage,
        input_fingerprints=(_fingerprint("1"),),
        output_fingerprints=(_fingerprint("2"),),
        agent_role=contract.allowed_roles[0],
        provider_fingerprint=_fingerprint("3"),
        model_fingerprint=_fingerprint("4"),
        protocol_fingerprint=_fingerprint("5"),
        independence_group="provider-a",
        attempt_count=attempt_count,
        status=status,
        reason_codes=(),
        next_stage_proposal=next_stage,
        input_schema_versions=contract.required_input_schemas,
        output_schema_versions=contract.required_output_schemas,
        model_call_count=1,
        source_bytes_consumed=1024,
        token_count=128,
    )


def test_default_constitution_round_trips_with_stable_fingerprint() -> None:
    constitution = default_self_evolve_constitution()
    restored = SelfEvolveConstitutionV1.from_dict(constitution.to_dict())

    assert restored == constitution
    assert restored.fingerprint == constitution.fingerprint
    assert restored.stages == tuple(SelfEvolveStage)


def test_complete_report_advances_exactly_one_stage() -> None:
    constitution = default_self_evolve_constitution()
    report = _report()

    validate_stage_transition(
        constitution,
        report,
        SelfEvolveStage.UNDERSTAND,
    )
    with pytest.raises(
        ConstitutionContractError,
        match="exactly one stage",
    ):
        validate_stage_transition(
            constitution,
            report,
            SelfEvolveStage.FREEZE,
        )


def test_revision_backtracks_once_and_respects_budget() -> None:
    constitution = default_self_evolve_constitution()
    report = _report(
        stage=SelfEvolveStage.EXTRACT,
        status=AgenticStageStatus.NEEDS_REVISION,
        next_stage=SelfEvolveStage.UNDERSTAND,
    )

    validate_stage_transition(
        constitution,
        report,
        SelfEvolveStage.UNDERSTAND,
    )
    with pytest.raises(
        ConstitutionContractError,
        match="backtrack budget",
    ):
        validate_stage_transition(
            constitution,
            report,
            SelfEvolveStage.UNDERSTAND,
            prior_reports=(report, report),
        )


def test_terminal_or_incomplete_report_cannot_transition() -> None:
    constitution = default_self_evolve_constitution()
    rejected = _report(
        status=AgenticStageStatus.REJECTED,
        next_stage=None,
    )

    with pytest.raises(ConstitutionContractError):
        validate_stage_transition(
            constitution,
            rejected,
            SelfEvolveStage.UNDERSTAND,
        )
    with pytest.raises(ConstitutionContractError, match="output"):
        replace(_report(), output_fingerprints=())


def test_attempt_budget_and_schema_tamper_fail_closed() -> None:
    constitution = default_self_evolve_constitution()
    report = _report(attempt_count=4)
    with pytest.raises(ConstitutionContractError, match="budget"):
        validate_stage_transition(
            constitution,
            report,
            SelfEvolveStage.UNDERSTAND,
        )

    payload = constitution.to_dict()
    payload["schema_version"] = "other.v1"
    with pytest.raises(ConstitutionContractError, match="schema"):
        SelfEvolveConstitutionV1.from_dict(payload)


def test_stage_role_schema_usage_and_prior_chain_are_framework_checked() -> None:
    constitution = default_self_evolve_constitution()
    discover = _report()
    understand = _report(
        stage=SelfEvolveStage.UNDERSTAND,
        next_stage=SelfEvolveStage.EXTRACT,
    )
    understand = replace(
        understand,
        input_fingerprints=discover.output_fingerprints,
    )

    validate_stage_transition(
        constitution,
        understand,
        SelfEvolveStage.EXTRACT,
        prior_reports=(discover,),
    )
    with pytest.raises(ConstitutionContractError, match="prior"):
        validate_stage_transition(
            constitution,
            understand,
            SelfEvolveStage.EXTRACT,
        )
    with pytest.raises(ConstitutionContractError, match="role"):
        validate_stage_transition(
            constitution,
            replace(
                discover,
                agent_role=AgenticRole.EVIDENCE_EXTRACTION,
            ),
            SelfEvolveStage.UNDERSTAND,
        )
    budget = constitution.budget_for(SelfEvolveStage.DISCOVER)
    with pytest.raises(ConstitutionContractError, match="usage budget"):
        validate_stage_transition(
            constitution,
            replace(
                discover,
                source_bytes_consumed=budget.max_source_bytes + 1,
            ),
            SelfEvolveStage.UNDERSTAND,
        )


def test_report_chain_rejects_duplicates_broken_edges_and_prior_overuse() -> None:
    constitution = default_self_evolve_constitution()
    discover = _report()
    understand = replace(
        _report(
            stage=SelfEvolveStage.UNDERSTAND,
            next_stage=SelfEvolveStage.EXTRACT,
        ),
        input_fingerprints=discover.output_fingerprints,
        output_fingerprints=(_fingerprint("6"),),
    )

    assert validate_report_chain(
        constitution,
        (discover, understand),
    ) == (discover, understand)

    with pytest.raises(
        ConstitutionContractError,
        match="multiple complete reports",
    ):
        validate_report_chain(
            constitution,
            (
                discover,
                replace(discover, report_id="report-discover-retry"),
            ),
        )
    with pytest.raises(
        ConstitutionContractError,
        match="fingerprint edge",
    ):
        validate_report_chain(
            constitution,
            (
                discover,
                replace(
                    understand,
                    input_fingerprints=(_fingerprint("7"),),
                ),
            ),
        )
    budget = constitution.budget_for(SelfEvolveStage.DISCOVER)
    with pytest.raises(ConstitutionContractError, match="usage budget"):
        validate_report_chain(
            constitution,
            (
                replace(
                    discover,
                    token_count=budget.max_tokens + 1,
                ),
            ),
        )


def test_rollout_defaults_to_shadow_and_advances_one_step() -> None:
    default = default_semantic_rollout_policy()
    capability = _fingerprint("a")
    proposal = SemanticRolloutPolicyV1(
        policy_id="proposal-rollout",
        enabled_stage=SemanticRolloutStage.PROPOSAL,
        capability_fingerprints=(capability,),
        prerequisite_capabilities=(capability,),
    )

    assert default.enabled_stage is SemanticRolloutStage.SHADOW
    assert safe_rollout_fallback(None, capabilities_available=True) is (
        SemanticRolloutStage.SHADOW
    )
    validate_rollout_advance(default, proposal)
    assert SemanticRolloutPolicyV1.from_dict(proposal.to_dict()) == proposal


def test_rollout_cannot_jump_to_verified_or_ignore_missing_capabilities() -> None:
    capability = _fingerprint("a")
    verified = SemanticRolloutPolicyV1(
        policy_id="verified-rollout",
        enabled_stage=SemanticRolloutStage.VERIFIED,
        capability_fingerprints=(capability,),
        prerequisite_capabilities=(capability,),
    )
    with pytest.raises(ConstitutionContractError, match="exactly one stage"):
        validate_rollout_advance(None, verified)

    assert safe_rollout_fallback(
        verified,
        capabilities_available=False,
    ) is SemanticRolloutStage.SHADOW
