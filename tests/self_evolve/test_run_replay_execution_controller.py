from __future__ import annotations

import ast
import inspect
from decimal import Decimal
from types import SimpleNamespace

import pytest

from aworld.self_evolve.budget import (
    BudgetCeilings,
    BudgetEstimateConfidence,
    BudgetEstimateSource,
    BudgetStage,
    CandidateAttemptKey,
    CandidateAttemptStage,
    RunBudgetLedger,
    StageBudgetEstimate,
)
from aworld.self_evolve.concurrency import SelfEvolveExecutionTelemetry
from aworld.self_evolve.controllers import run_replay_execution as replay_module
from aworld.self_evolve.controllers.run_execution import (
    CandidateEvaluationRequest,
    CandidateReplayAdmissionResult,
)
from aworld.self_evolve.controllers.run_replay_execution import (
    CandidateReplayExecutionRequest,
    CandidateReplayExecutionRuntime,
    execute_candidate_replay,
)
from aworld.self_evolve.datasets import EvalCase, SelfEvolveDataset
from aworld.self_evolve.types import (
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
        rationale="exercise replay execution controller",
    )


def _dataset() -> SelfEvolveDataset:
    return SelfEvolveDataset(
        cases=(EvalCase(case_id="case-1", input={"content": "task"}),),
        recipe=DatasetRecipe(
            source={"kind": "run-replay-controller-test"},
            split_seed="seed",
            splits={"train": ["case-1"]},
            trainable_case_ids=("case-1",),
        ),
    )


def _budget_decision():
    ledger = RunBudgetLedger(BudgetCeilings(None, None, None))
    return ledger.reserve(
        StageBudgetEstimate(
            stage=BudgetStage.PAIRED_REPLAY,
            item_id="candidate-1-paired-replay",
            tokens=20,
            cost_usd=Decimal("2"),
            wall_seconds=Decimal("10"),
            source=BudgetEstimateSource.CONFIGURED_COLD_START,
            confidence=BudgetEstimateConfidence.LOW,
        )
    )


def _feedback_builder(**kwargs) -> tuple[EvaluationSummary, ...]:
    return (
        EvaluationSummary(
            variant_id=kwargs["candidate"].candidate_id,
            metrics={
                "failed_gates": [
                    gate.gate_name for gate in kwargs["failed_gates"]
                ]
            },
            dataset_split="validation",
        ),
    )


class _AttemptTracker:
    def __init__(self) -> None:
        self.stage = CandidateAttemptStage.LOCAL_GATES
        self.events: list[tuple[CandidateAttemptStage, str | None]] = []

    def last_stage(self, _key):
        return self.stage

    def terminal(self, _key):
        return self.stage in {
            CandidateAttemptStage.REJECTED,
            CandidateAttemptStage.NOT_RUN,
        }

    def emit(self, _key, stage, *, reason_code=None, **_kwargs):
        self.stage = stage
        self.events.append((stage, reason_code))


class _BudgetContext:
    def __init__(self) -> None:
        self.debits: list[dict[str, object]] = []
        self.releases: list[tuple[object, str]] = []

    def debit(self, decision, **kwargs):
        self.debits.append({"decision": decision, **kwargs})

    def release(self, decision, *, reason_code):
        self.releases.append((decision, reason_code))


def _execution_request(
    *,
    budget_context=None,
    attempt_tracker=None,
    replay_budget=None,
    capability_blocked: bool = False,
) -> CandidateReplayExecutionRequest:
    evaluation = CandidateEvaluationRequest(
        run_id="run-1",
        target=SimpleNamespace(load_current_content=lambda: "# Old\n"),
        dataset=_dataset(),
        candidate=_candidate(),
        apply_policy="proposal",
        target_provenance=None,
        iteration_number=1,
        candidate_number=1,
        candidate_count=1,
        attempt_key=(
            CandidateAttemptKey("run-1", 0, 0)
            if attempt_tracker is not None
            else None
        ),
        attempt_tracker=attempt_tracker,
        budget_context=budget_context,
    )
    admission = CandidateReplayAdmissionResult(
        gate_results=(
            GateResult(
                gate_name="local_candidate_contracts",
                passed=True,
                reason="local gates passed",
            ),
        ),
        replay_case_count=1,
        replay_planned=True,
        reuses_replay_evidence=False,
        effective_baseline_repetitions=1,
        effective_candidate_repetitions=1,
        replay_budget=replay_budget,
        capability_gates=(),
        capability_blocked=capability_blocked,
    )
    return CandidateReplayExecutionRequest(
        evaluation=evaluation,
        admission=admission,
    )


def _runtime(
    replay_candidate,
    telemetry: SelfEvolveExecutionTelemetry,
    *,
    evaluator_gate=lambda *_args, **_kwargs: None,
) -> CandidateReplayExecutionRuntime:
    return CandidateReplayExecutionRuntime(
        replay_candidate=replay_candidate,
        execution_telemetry=telemetry,
        replay_confidence_gate=lambda *_args, **_kwargs: None,
        replay_evaluator_admission_gate=evaluator_gate,
        typed_gate_failure=lambda gate: gate,
        feedback_builder=_feedback_builder,
    )


@pytest.mark.asyncio
async def test_replay_execution_emits_lifecycle_and_debits_telemetry() -> None:
    telemetry = SelfEvolveExecutionTelemetry()
    tracker = _AttemptTracker()
    budget = _BudgetContext()
    decision = _budget_decision()

    async def replay_candidate(**kwargs):
        lifecycle = kwargs["lifecycle_callback"]
        lifecycle("adaptation_completed", {})
        lifecycle("replay_started", {})
        telemetry.record(
            "replay",
            {
                "item_count": 2,
                "elapsed_seconds": 5,
                "cost_usd": 4,
                "token_usage": {"total_tokens": 23},
            },
        )
        lifecycle("replay_completed", {})
        lifecycle("replay_comparable", {})
        return (
            None,
            kwargs["dataset"],
            GateResult("candidate_replay", True, "replay passed"),
        )

    result = await execute_candidate_replay(
        _execution_request(
            budget_context=budget,
            attempt_tracker=tracker,
            replay_budget=decision,
        ),
        _runtime(replay_candidate, telemetry),
    )

    assert result.replay_started is True
    assert result.replay_dataset is not None
    assert tracker.events == [
        (CandidateAttemptStage.ADAPTATION, None),
        (CandidateAttemptStage.PAIRED_REPLAY_STARTED, None),
        (CandidateAttemptStage.PAIRED_REPLAY_COMPLETED, None),
        (CandidateAttemptStage.PAIRED_REPLAY_COMPARABLE, None),
    ]
    assert len(budget.debits) == 1
    observation = budget.debits[0]["usage_observation"]
    assert observation.known_lower_bound.tokens == 23
    assert observation.known_lower_bound.cost_usd == Decimal("4")
    assert observation.known_lower_bound.wall_seconds >= Decimal("5")
    assert budget.releases == []


@pytest.mark.asyncio
async def test_replay_execution_releases_blocked_capability_reservation() -> None:
    telemetry = SelfEvolveExecutionTelemetry()
    budget = _BudgetContext()
    decision = _budget_decision()

    async def must_not_replay(**_kwargs):
        raise AssertionError("blocked capability must skip replay")

    result = await execute_candidate_replay(
        _execution_request(
            budget_context=budget,
            replay_budget=decision,
            capability_blocked=True,
        ),
        _runtime(must_not_replay, telemetry),
    )

    assert result.replay_started is False
    assert budget.debits == []
    assert budget.releases == [(decision, "capability_gate_blocked")]


@pytest.mark.asyncio
async def test_replay_execution_normalizes_backend_exception() -> None:
    telemetry = SelfEvolveExecutionTelemetry()

    async def failed_replay(**kwargs):
        kwargs["lifecycle_callback"]("replay_started", {})
        raise RuntimeError("shared backend unavailable")

    result = await execute_candidate_replay(
        _execution_request(),
        _runtime(failed_replay, telemetry),
    )

    gate = next(
        gate for gate in result.gate_results
        if gate.gate_name == "candidate_replay"
    )
    assert gate.passed is False
    assert gate.details["code"] == "candidate_replay_infrastructure_error"
    assert gate.details["failure_owner"] == "infrastructure"
    assert gate.details["failure_scope"] == "shared_run"
    assert gate.details["error"] == "shared backend unavailable"
    assert gate.details["failure_event"]["diagnostics"]["replay_started"] is True


@pytest.mark.asyncio
async def test_replay_evaluator_admission_builds_terminal_result() -> None:
    telemetry = SelfEvolveExecutionTelemetry()
    tracker = _AttemptTracker()

    async def replay_candidate(**kwargs):
        return None, kwargs["dataset"], None

    def evaluator_gate(*_args, **_kwargs):
        return GateResult(
            gate_name="replay_evaluator_admission",
            passed=False,
            reason="candidate regressed replay evidence",
        )

    result = await execute_candidate_replay(
        _execution_request(attempt_tracker=tracker),
        _runtime(
            replay_candidate,
            telemetry,
            evaluator_gate=evaluator_gate,
        ),
    )

    assert result.terminal_result is not None
    assert result.terminal_result.state.status == "rejected"
    assert result.terminal_result.state.payload["replay_dataset"] is not None
    assert tracker.events == [
        (
            CandidateAttemptStage.REJECTED,
            "deterministic_replay_evidence_regressed",
        )
    ]


def test_run_replay_execution_controller_does_not_import_runner() -> None:
    tree = ast.parse(inspect.getsource(replay_module))
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
