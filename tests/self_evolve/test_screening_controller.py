from types import SimpleNamespace

import pytest

from aworld.self_evolve.controllers.screening import (
    CandidateScreeningController,
    ScreeningPopulationRequest,
)
from aworld.self_evolve.types import CandidateVariant, SelfEvolveTargetRef


def _candidate(candidate_id: str) -> CandidateVariant:
    return CandidateVariant(
        candidate_id=candidate_id,
        target=SelfEvolveTargetRef("skill", "demo"),
        content="# Demo\n",
        rationale="screening contract fixture",
    )


@pytest.mark.asyncio
async def test_screening_population_contract_returns_typed_result() -> None:
    candidate = _candidate("candidate-1")
    request = ScreeningPopulationRequest(
        run_id="run-1",
        target=SimpleNamespace(),
        dataset=SimpleNamespace(),
        candidates=(candidate,),
        apply_policy="auto_verified",
    )
    controller = CandidateScreeningController()
    runtime = SimpleNamespace(marker="runtime")

    async def execute(received, received_runtime):
        assert received is request
        assert received_runtime is runtime
        return received.candidates, {"screening_outcome": "completed"}

    result = await controller.screen_population(
        request,
        execute=execute,
        runtime=runtime,
    )

    assert result.candidates == (candidate,)
    assert result.report == {"screening_outcome": "completed"}


@pytest.mark.asyncio
async def test_screening_population_contract_rejects_foreign_candidate() -> None:
    request = ScreeningPopulationRequest(
        run_id="run-1",
        target=SimpleNamespace(),
        dataset=SimpleNamespace(),
        candidates=(_candidate("candidate-1"),),
        apply_policy="auto_verified",
    )
    controller = CandidateScreeningController()
    runtime = SimpleNamespace(marker="runtime")

    async def execute(_request, _runtime):
        return (_candidate("candidate-foreign"),), None

    with pytest.raises(ValueError, match="outside the requested population"):
        await controller.screen_population(
            request,
            execute=execute,
            runtime=runtime,
        )


def test_screening_controller_opens_only_the_exact_unhealthy_control() -> None:
    controller = CandidateScreeningController(
        support_control_failure_patience=2,
    )
    identity = {
        "control_identity_fingerprint": "sha256:exact-control",
        "case_id": "case-1",
    }
    observations = {
        "sha256:exact-control": {
            "baseline_attempt_count": 2,
            "baseline_success_count": 0,
            "baseline_timeout_count": 2,
        }
    }

    gate = controller.support_specific_control_circuit_breaker_gate(
        control_identity=identity,
        control_observations=observations,
    )

    assert gate is not None
    assert gate.details["code"] == "screening_support_control_circuit_open"
    assert gate.details["candidate_execution_observed"] is False
    assert (
        controller.support_specific_control_circuit_breaker_gate(
            control_identity={
                **identity,
                "control_identity_fingerprint": "sha256:different-envelope",
            },
            control_observations=observations,
        )
        is None
    )


def test_screening_stage_deadline_is_right_censored_not_candidate_failure() -> None:
    controller = CandidateScreeningController()

    gate = controller.stage_budget_censor_gate(
        hard_limit_seconds=30.0,
        elapsed_seconds=30.25,
        candidate_execution_observed=False,
    )
    attempt = {"details": gate.details}

    assert gate.passed is False
    assert gate.details["failure_owner"] == "framework"
    assert gate.details["repairable"] is False
    assert controller.gate_is_budget_censored(gate) is True
    assert controller.attempt_is_budget_censored(attempt) is True


def test_screening_controller_rejects_non_positive_failure_patience() -> None:
    with pytest.raises(ValueError, match="failure_patience"):
        CandidateScreeningController(support_control_failure_patience=0)
