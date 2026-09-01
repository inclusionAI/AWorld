from __future__ import annotations

import ast
import inspect
from types import SimpleNamespace

import pytest

from aworld.self_evolve.controllers import run_execution as execution_module
from aworld.self_evolve.controllers.run_execution import (
    CandidateEvaluationRequest,
    CandidateEvaluationResult,
    CandidateEvaluationState,
    CandidateLocalAdmissionPolicy,
    CandidateReplayAdmissionPolicy,
    CandidateReplayAdmissionRuntime,
    ExplicitTargetRunRequest,
    execute_candidate_local_admission,
    execute_candidate_replay_admission,
)
from aworld.self_evolve.budget import (
    BudgetCeilings,
    BudgetEstimateConfidence,
    BudgetEstimateSource,
    CandidateAttemptKey,
    CandidateAttemptStage,
    RunBudgetLedger,
    StageBudgetEstimate,
)
from aworld.self_evolve.datasets import EvalCase, SelfEvolveDataset
from aworld.self_evolve.optimizers.base import CandidateSourceDisposition
from aworld.self_evolve.runner import SelfEvolveRunner
from aworld.self_evolve.types import (
    CandidateFileDelta,
    CandidateVariant,
    DatasetRecipe,
    EvaluationSummary,
    GateResult,
    SelfEvolveTargetRef,
)


def _candidate() -> CandidateVariant:
    return CandidateVariant(
        candidate_id="candidate-1",
        target=SelfEvolveTargetRef("skill", "demo", None),
        content="# Demo\n",
        rationale="exercise typed execution contracts",
    )


def _dataset() -> SelfEvolveDataset:
    return SelfEvolveDataset(
        cases=(EvalCase(case_id="case-1", input={"content": "task"}),),
        recipe=DatasetRecipe(
            source={"kind": "controller-test"},
            split_seed="seed",
            splits={"train": ["case-1"]},
            trainable_case_ids=("case-1",),
        ),
    )


def _replay_policy(**overrides) -> CandidateReplayAdmissionPolicy:
    values = {
        "replay_enabled": True,
        "replay_backend": object(),
        "repetitions_explicit": False,
        "measurement_min_independent_cases": 2,
        "baseline_repetitions": 1,
        "candidate_repetitions": 1,
        "judge_repetitions": 1,
        "replay_candidate_limit": 1,
        "per_attempt_replay_token_limit": None,
        "replay_tokens_per_unit": 10,
    }
    values.update(overrides)
    return CandidateReplayAdmissionPolicy(**values)


def _feedback_builder(**kwargs) -> tuple[EvaluationSummary, ...]:
    return (
        EvaluationSummary(
            variant_id=kwargs["candidate"].candidate_id,
            metrics={
                "failed_gates": [
                    gate.gate_name for gate in kwargs["failed_gates"]
                ],
                "candidate_status": "rejected",
            },
            dataset_split="validation",
        ),
    )


def test_candidate_evaluation_result_preserves_legacy_tuple_shape() -> None:
    state = {"status": "accepted"}
    report_item = {"candidate_id": "candidate-1"}
    feedback = (
        EvaluationSummary(
            variant_id="candidate-1",
            metrics={"score": 1.0},
            dataset_split="validation",
        ),
    )

    result = CandidateEvaluationResult(
        state=CandidateEvaluationState(state),
        report_item=report_item,
        feedback=feedback,
    )

    assert result.as_tuple() == (state, report_item, feedback)
    assert result.state.payload is state
    assert result.state.status == "accepted"
    assert result.report_item is report_item


def test_candidate_evaluation_request_validates_candidate_position() -> None:
    with pytest.raises(
        ValueError,
        match="candidate_number cannot exceed candidate_count",
    ):
        CandidateEvaluationRequest(
            run_id="run-1",
            target=SimpleNamespace(),
            dataset=SimpleNamespace(),
            candidate=_candidate(),
            apply_policy="proposal",
            target_provenance=None,
            iteration_number=1,
            candidate_number=2,
            candidate_count=1,
            source_disposition=CandidateSourceDisposition(),
        )


def test_candidate_evaluation_request_freezes_deduplication_sets() -> None:
    rejected = {"old-rejected"}
    accepted = {"old-accepted"}

    request = CandidateEvaluationRequest(
        run_id="run-1",
        target=SimpleNamespace(),
        dataset=SimpleNamespace(),
        candidate=_candidate(),
        apply_policy="proposal",
        target_provenance=None,
        iteration_number=1,
        candidate_number=1,
        candidate_count=1,
        rejected_candidate_ids=rejected,
        accepted_candidate_ids=accepted,
    )
    rejected.add("late-rejected")
    accepted.add("late-accepted")

    assert request.rejected_candidate_ids == frozenset({"old-rejected"})
    assert request.accepted_candidate_ids == frozenset({"old-accepted"})


def test_local_admission_builds_terminal_duplicate_result(tmp_path) -> None:
    candidate = _candidate()
    emitted: list[tuple[CandidateAttemptStage, str | None]] = []

    class AttemptTracker:
        def terminal(self, _key):
            return False

        def emit(self, _key, stage, *, reason_code=None, **_kwargs):
            emitted.append((stage, reason_code))

    def local_gates(*_args, **_kwargs):
        return [
            GateResult(
                gate_name="local_contracts",
                passed=True,
                reason="local contracts passed",
            )
        ]

    request = CandidateEvaluationRequest(
        run_id="run-1",
        target=SimpleNamespace(load_current_content=lambda: "# Old\n"),
        dataset=SimpleNamespace(),
        candidate=candidate,
        apply_policy="auto_verified",
        target_provenance=None,
        iteration_number=1,
        candidate_number=1,
        candidate_count=1,
        rejected_candidate_ids={candidate.candidate_id},
        accepted_candidate_ids=set(),
        attempt_key=CandidateAttemptKey("run-1", 0, 0),
        attempt_tracker=AttemptTracker(),
    )
    result = execute_candidate_local_admission(
        request,
        CandidateLocalAdmissionPolicy(
            workspace_root=tmp_path,
            max_candidate_chars=10_000,
            allow_generated_target_mutation=False,
            allow_external_target_mutation=False,
            target_intent=None,
            inferred_new_skill_policy="auto_verified",
            skip_duplicate_rejected_candidate_gate=False,
            gate_evaluator=local_gates,
        ),
    )

    assert result.terminal_result is not None
    assert result.terminal_result.state.status == "rejected"
    assert result.terminal_result.report_item["failed_gates"] == [
        "duplicate_rejected_candidate"
    ]
    assert result.terminal_result.feedback[0].metrics == {
        "failed_gates": ["duplicate_rejected_candidate"],
        "candidate_status": "rejected",
    }
    assert emitted == [
        (CandidateAttemptStage.LOCAL_GATES, None),
        (CandidateAttemptStage.REJECTED, "duplicate_prior_candidate"),
    ]


@pytest.mark.asyncio
async def test_replay_admission_plans_reserves_and_validates_capabilities() -> None:
    candidate = _candidate()
    reserved: list[tuple[object, str, int]] = []
    validated: list[dict[str, object]] = []
    ledger = RunBudgetLedger(BudgetCeilings(None, None, None))

    class BudgetContext:
        def reserve(self, stage, item_id, *, units=1, **_kwargs):
            reserved.append((stage, item_id, units))
            return ledger.reserve(
                StageBudgetEstimate(
                    stage=stage,
                    item_id=item_id,
                    tokens=units,
                    cost_usd=None,
                    wall_seconds=None,
                    source=BudgetEstimateSource.CONFIGURED_COLD_START,
                    confidence=BudgetEstimateConfidence.LOW,
                )
            )

    async def validate_capabilities(**kwargs):
        validated.append(kwargs)
        return [
            GateResult(
                gate_name="candidate_capability_replay",
                passed=True,
                reason="capability contract passed",
            )
        ]

    request = CandidateEvaluationRequest(
        run_id="run-1",
        target=SimpleNamespace(load_current_content=lambda: "# Old\n"),
        dataset=_dataset(),
        candidate=candidate,
        apply_policy="proposal",
        target_provenance=None,
        iteration_number=1,
        candidate_number=1,
        candidate_count=1,
        budget_context=BudgetContext(),
    )
    result = await execute_candidate_replay_admission(
        request,
        _replay_policy(),
        CandidateReplayAdmissionRuntime(
            reusable_baseline_case_count=lambda **_kwargs: 0,
            validate_capabilities=validate_capabilities,
            typed_gate_failure=lambda gate: gate,
            feedback_builder=_feedback_builder,
        ),
        initial_gate_results=(),
    )

    assert result.replay_planned is True
    assert result.replay_case_count == 1
    assert result.replay_budget is not None
    assert result.replay_budget.allowed is True
    assert result.capability_blocked is False
    assert result.terminal_result is None
    assert reserved == [
        (
            result.replay_budget.stage,
            "candidate-1-paired-replay",
            2,
        )
    ]
    assert validated[0]["candidate"] is candidate


@pytest.mark.asyncio
async def test_replay_admission_per_attempt_denial_is_terminal() -> None:
    emitted: list[tuple[CandidateAttemptStage, str | None]] = []

    class AttemptTracker:
        def emit(self, _key, stage, *, reason_code=None, **_kwargs):
            emitted.append((stage, reason_code))

    async def must_not_validate(**_kwargs):
        raise AssertionError("denied replay must not validate capabilities")

    request = CandidateEvaluationRequest(
        run_id="run-1",
        target=SimpleNamespace(load_current_content=lambda: "# Old\n"),
        dataset=_dataset(),
        candidate=_candidate(),
        apply_policy="proposal",
        target_provenance=None,
        iteration_number=1,
        candidate_number=1,
        candidate_count=1,
        attempt_key=CandidateAttemptKey("run-1", 0, 0),
        attempt_tracker=AttemptTracker(),
    )
    result = await execute_candidate_replay_admission(
        request,
        _replay_policy(per_attempt_replay_token_limit=1),
        CandidateReplayAdmissionRuntime(
            reusable_baseline_case_count=lambda **_kwargs: 0,
            validate_capabilities=must_not_validate,
            typed_gate_failure=lambda gate: gate,
            feedback_builder=_feedback_builder,
        ),
        initial_gate_results=(),
    )

    assert result.terminal_result is not None
    assert result.terminal_result.state.status == "rejected"
    assert result.terminal_result.report_item["failed_gates"] == ["budget"]
    assert emitted == [
        (
            CandidateAttemptStage.NOT_RUN,
            "per_attempt_replay_budget_denied",
        )
    ]


@pytest.mark.asyncio
async def test_replay_admission_owns_evaluation_support_terminal_lane() -> None:
    candidate = CandidateVariant(
        candidate_id="support-1",
        target=SelfEvolveTargetRef("skill", "demo", None),
        content="# Old\n",
        rationale="add deterministic replay support",
        files=(
            CandidateFileDelta(
                path="replay/runtime.py",
                content="def replay():\n    return True\n",
            ),
        ),
    )

    async def validate_capabilities(**_kwargs):
        return [
            GateResult(
                gate_name="candidate_capability_replay",
                passed=True,
                reason="operational preflight passed",
                details={"operational_preflight": True},
            )
        ]

    request = CandidateEvaluationRequest(
        run_id="run-1",
        target=SimpleNamespace(load_current_content=lambda: "# Old\n"),
        dataset=_dataset(),
        candidate=candidate,
        apply_policy="verified_only",
        target_provenance=None,
        iteration_number=1,
        candidate_number=1,
        candidate_count=1,
    )
    result = await execute_candidate_replay_admission(
        request,
        _replay_policy(),
        CandidateReplayAdmissionRuntime(
            reusable_baseline_case_count=lambda **_kwargs: 0,
            validate_capabilities=validate_capabilities,
            typed_gate_failure=lambda gate: gate,
            feedback_builder=_feedback_builder,
        ),
        initial_gate_results=(
            GateResult(
                gate_name="local_candidate_contracts",
                passed=True,
                reason="local gates passed",
            ),
        ),
    )

    assert result.terminal_result is not None
    assert result.terminal_result.state.status == "prerequisite"
    support_gate = next(
        gate
        for gate in result.gate_results
        if gate.gate_name == "evaluation_support_prerequisite"
    )
    assert support_gate.passed is True


@pytest.mark.asyncio
async def test_runner_legacy_evaluation_adapter_uses_typed_boundary() -> None:
    captured: list[CandidateEvaluationRequest] = []
    state = {"status": "rejected", "gate_results": []}

    async def execute(request: CandidateEvaluationRequest):
        captured.append(request)
        return CandidateEvaluationResult(
            state=CandidateEvaluationState(state),
            report_item={"candidate_id": request.candidate.candidate_id},
            feedback=(),
        )

    facade = SimpleNamespace(_execute_iteration_candidate=execute)
    result = await SelfEvolveRunner._evaluate_iteration_candidate(
        facade,
        run_id="run-1",
        target=SimpleNamespace(),
        dataset=SimpleNamespace(),
        candidate=_candidate(),
        apply_policy="proposal",
        target_provenance=None,
        iteration_number=1,
        candidate_number=1,
        candidate_count=1,
        rejected_candidate_ids=set(),
        accepted_candidate_ids=set(),
    )

    assert len(captured) == 1
    assert result == (state, {"candidate_id": "candidate-1"}, ())


def test_explicit_target_run_request_freezes_iterables() -> None:
    traces = [SimpleNamespace()]

    request = ExplicitTargetRunRequest(
        run_id=" run-1 ",
        target=SimpleNamespace(),
        dataset=SimpleNamespace(),
        trace_packs=traces,
        campaign_prior_run_ids=["prior-1"],
    )

    traces.append(SimpleNamespace())
    assert request.run_id == " run-1 "
    assert len(request.trace_packs) == 1
    assert request.campaign_prior_run_ids == ("prior-1",)


def test_run_execution_controller_does_not_import_runner() -> None:
    tree = ast.parse(inspect.getsource(execution_module))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )

    assert "aworld.self_evolve.runner" not in imported_modules
