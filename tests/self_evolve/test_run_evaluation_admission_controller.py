from __future__ import annotations

import ast
import inspect
from decimal import Decimal
from types import SimpleNamespace

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
from aworld.self_evolve.controllers import (
    run_evaluation_admission as admission_module,
)
from aworld.self_evolve.controllers.run_evaluation_admission import (
    CandidateEvaluationAdmissionPolicy,
    CandidateEvaluationAdmissionRequest,
    CandidateEvaluationAdmissionRuntime,
    plan_candidate_evaluation_admission,
)
from aworld.self_evolve.controllers.run_execution import (
    CandidateEvaluationRequest,
)
from aworld.self_evolve.controllers.run_replay_execution import (
    CandidateReplayExecutionResult,
)
from aworld.self_evolve.datasets import EvalCase, SelfEvolveDataset
from aworld.self_evolve.failure_events import (
    FailureOwner,
    FailureScope,
    FailureStage,
    ReplayExecutionStatus,
    ReplayFailureEvent,
)
from aworld.self_evolve.replay import (
    CandidateReplayMemberResult,
    CandidateReplayRequest,
    CandidateReplayResult,
    ReplayVariantResult,
)
from aworld.self_evolve.types import (
    CandidateFileDelta,
    CandidateVariant,
    DatasetRecipe,
    EvaluationSummary,
    GateResult,
    SelfEvolveTargetRef,
)


def _dataset(case_count: int = 1) -> SelfEvolveDataset:
    cases = tuple(
        EvalCase(case_id=f"case-{index}", input={"content": "task"})
        for index in range(case_count)
    )
    return SelfEvolveDataset(
        cases=cases,
        recipe=DatasetRecipe(
            source={"kind": "evaluation-admission-test"},
            split_seed="seed",
            splits={"train": [case.case_id for case in cases]},
            trainable_case_ids=tuple(case.case_id for case in cases),
        ),
    )


def _candidate(
    *,
    content: str = "# Improved\n",
    files: tuple[CandidateFileDelta, ...] = (),
) -> CandidateVariant:
    return CandidateVariant(
        candidate_id="candidate-1",
        target=SelfEvolveTargetRef("skill", "demo", None),
        content=content,
        rationale="exercise evaluation admission",
        files=files,
    )


def _feedback_builder(**kwargs) -> tuple[EvaluationSummary, ...]:
    return (
        EvaluationSummary(
            variant_id=kwargs["candidate"].candidate_id,
            metrics={
                "failed_gates": [
                    gate.gate_name for gate in kwargs["failed_gates"]
                ],
            },
            dataset_split="validation",
        ),
    )


class _AttemptTracker:
    def __init__(self, stage=CandidateAttemptStage.LOCAL_GATES) -> None:
        self.stage = stage
        self.events: list[tuple[CandidateAttemptStage, str | None]] = []

    def terminal(self, _key):
        return False

    def has_stage(self, _key, *stages):
        return self.stage in stages

    def emit(self, _key, stage, *, reason_code=None, **_kwargs):
        self.stage = stage
        self.events.append((stage, reason_code))


class _BudgetContext:
    def __init__(self, total_tokens: int | None = None) -> None:
        self.ledger = RunBudgetLedger(
            BudgetCeilings(total_tokens, None, None)
        )
        self.reservations: list[tuple[BudgetStage, str, int]] = []
        self.releases: list[tuple[object, str]] = []

    def reserve(self, stage, item_id, *, units=1, **_kwargs):
        self.reservations.append((stage, item_id, units))
        return self.ledger.reserve(
            StageBudgetEstimate(
                stage=stage,
                item_id=item_id,
                tokens=units,
                cost_usd=Decimal("0"),
                wall_seconds=Decimal("0"),
                source=BudgetEstimateSource.CONFIGURED_COLD_START,
                confidence=BudgetEstimateConfidence.LOW,
                units=units,
            )
        )

    def release(self, decision, *, reason_code):
        if decision.reservation_id is not None:
            self.ledger.release(decision.reservation_id)
        self.releases.append((decision, reason_code))


class _EvaluationBackend:
    async def evaluate_variant(self, request):
        raise AssertionError(f"planning must not evaluate {request.variant_id}")


class _ProbabilisticEvaluationBackend(_EvaluationBackend):
    probabilistic_only = True


def _request(
    *,
    candidate: CandidateVariant | None = None,
    dataset: SelfEvolveDataset | None = None,
    apply_policy: str = "proposal",
    attempt_tracker=None,
    budget_context=None,
    replay_dataset: SelfEvolveDataset | None = None,
    replay_result: CandidateReplayResult | None = None,
    replay_gates: tuple[GateResult, ...] | None = None,
) -> CandidateEvaluationAdmissionRequest:
    evaluation = CandidateEvaluationRequest(
        run_id="run-1",
        target=SimpleNamespace(load_current_content=lambda: "# Old\n"),
        dataset=dataset or _dataset(),
        candidate=candidate or _candidate(),
        apply_policy=apply_policy,
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
    replay = CandidateReplayExecutionResult(
        gate_results=(
            replay_gates
            if replay_gates is not None
            else (
                GateResult(
                    gate_name="candidate_replay",
                    passed=True,
                    reason="replay admitted",
                ),
            )
        ),
        replay_result=replay_result,
        replay_dataset=replay_dataset,
        replay_started=replay_dataset is not None,
    )
    return CandidateEvaluationAdmissionRequest(
        evaluation=evaluation,
        replay=replay,
    )


def _runtime() -> CandidateEvaluationAdmissionRuntime:
    return CandidateEvaluationAdmissionRuntime(
        typed_gate_failure=lambda gate: gate,
        feedback_builder=_feedback_builder,
    )


def _recovery_replay_result(
    dataset: SelfEvolveDataset,
) -> CandidateReplayResult:
    target = SelfEvolveTargetRef("skill", "demo", None)

    def request_for(case: EvalCase) -> CandidateReplayRequest:
        return CandidateReplayRequest(
            run_id="run-1",
            task_id=case.case_id,
            workspace_root="/tmp/recovery-replay",
            target=target,
            candidate_id="candidate-1",
            overlay_skill_root="/tmp/recovery-replay/candidate",
            task_input=case.input,
        )

    failure = ReplayFailureEvent(
        code="baseline_task_failed",
        owner=FailureOwner.TASK,
        stage=FailureStage.EVIDENCE_FINALIZATION,
        scope=FailureScope.MEMBER,
        repairable=False,
        category="task_completion",
        summary="baseline did not produce canonical evidence",
    )
    members = tuple(
        CandidateReplayMemberResult(
            case_id=case.case_id,
            request=request_for(case),
            baseline=ReplayVariantResult(
                variant_id="baseline",
                status=ReplayExecutionStatus.FAILED,
                trajectory=[{"action": {"content": "baseline attempted task"}}],
                failure=failure,
            ),
            candidate=ReplayVariantResult(
                variant_id="candidate-1",
                status=ReplayExecutionStatus.SUCCEEDED,
                trajectory=[{"action": {"content": "candidate completed task"}}],
            ),
        )
        for case in dataset.cases
    )
    return CandidateReplayResult(
        request=members[0].request,
        baseline=members[0].baseline,
        candidate=members[0].candidate,
        member_results=members,
    )


def test_target_behavior_admission_rejects_noop_candidate() -> None:
    tracker = _AttemptTracker()
    request = _request(
        candidate=_candidate(content="# Old\n"),
        attempt_tracker=tracker,
    )

    result = plan_candidate_evaluation_admission(
        request,
        CandidateEvaluationAdmissionPolicy(
            replay_enabled=False,
            evaluation_backend=None,
            judge_repetitions=1,
        ),
        _runtime(),
    )

    assert result.terminal_result is not None
    assert result.terminal_result.state.status == "rejected"
    assert result.terminal_result.report_item["failed_gates"] == [
        "target_behavior_delta"
    ]
    assert tracker.events == [
        (CandidateAttemptStage.REJECTED, "target_behavior_delta_missing")
    ]


def test_target_behavior_admission_preserves_support_prerequisite() -> None:
    tracker = _AttemptTracker()
    request = _request(
        candidate=_candidate(
            content="# Old\n",
            files=(
                CandidateFileDelta(
                    path="replay/runtime.py",
                    content="def replay():\n    return True\n",
                ),
            ),
        ),
        attempt_tracker=tracker,
    )

    result = plan_candidate_evaluation_admission(
        request,
        CandidateEvaluationAdmissionPolicy(
            replay_enabled=False,
            evaluation_backend=None,
            judge_repetitions=1,
        ),
        _runtime(),
    )

    assert result.terminal_result is not None
    assert result.terminal_result.state.status == "prerequisite"
    assert tracker.events == [
        (
            CandidateAttemptStage.PREREQUISITE_READY,
            "evaluation_support_bootstrap_ready",
        )
    ]


def test_evaluation_planning_reserves_full_verified_workload() -> None:
    budget = _BudgetContext()
    cases = tuple(
        EvalCase(
            case_id=f"case-{index}",
            input={"content": "task"},
            verification_command="true",
        )
        for index in range(2)
    )
    dataset = SelfEvolveDataset(
        cases=cases,
        recipe=DatasetRecipe(
            source={"kind": "trajectory_set"},
            split_seed="seed",
            splits={"held_out": [case.case_id for case in cases]},
            held_out_case_ids=tuple(case.case_id for case in cases),
        ),
    )
    request = _request(
        dataset=dataset,
        apply_policy="verified_only",
        budget_context=budget,
    )

    result = plan_candidate_evaluation_admission(
        request,
        CandidateEvaluationAdmissionPolicy(
            replay_enabled=False,
            evaluation_backend=_EvaluationBackend(),
            judge_repetitions=3,
            min_eval_cases=2,
            regression_suite_case_counts=(3,),
            challenger_enabled=True,
            challenger_max_cases=2,
        ),
        _runtime(),
    )

    assert result.terminal_result is None
    assert result.evaluation_units == 20
    assert result.expected_judge_summary_count == 2
    assert result.evaluation_budget is not None
    assert result.evaluation_budget.allowed is True
    assert result.judge_budget is not None
    assert result.judge_budget.allowed is True
    assert budget.reservations == [
        (BudgetStage.EVALUATION, "candidate-1-evaluation", 20),
        (BudgetStage.JUDGE, "candidate-1-judge", 60),
    ]


def test_verified_planning_rejects_unreachable_confidence_before_budget() -> None:
    budget = _BudgetContext()
    request = _request(
        dataset=_dataset(2),
        apply_policy="verified_only",
        budget_context=budget,
    )

    result = plan_candidate_evaluation_admission(
        request,
        CandidateEvaluationAdmissionPolicy(
            replay_enabled=False,
            evaluation_backend=_ProbabilisticEvaluationBackend(),
            judge_repetitions=1,
            min_eval_cases=30,
        ),
        _runtime(),
    )

    assert result.terminal_result is not None
    gate = next(
        gate
        for gate in result.gate_results
        if gate.gate_name == "verification_feasibility"
    )
    assert gate.passed is False
    assert gate.details["code"] == "verified_confidence_unreachable"
    assert budget.reservations == []


def test_verified_recovery_pairs_admit_judge_despite_strict_replay_failure() -> None:
    cases = tuple(
        EvalCase(case_id=f"held-out-{index}", input={"content": "task"})
        for index in range(2)
    )
    dataset = SelfEvolveDataset(
        cases=cases,
        recipe=DatasetRecipe(
            source={"kind": "trajectory_set", "held_out_member_count": 2},
            split_seed="seed",
            splits={"held_out": [case.case_id for case in cases]},
            held_out_case_ids=tuple(case.case_id for case in cases),
        ),
    )
    replay_result = _recovery_replay_result(dataset)
    assert replay_result.succeeded is False
    budget = _BudgetContext()
    request = _request(
        dataset=dataset,
        replay_dataset=dataset,
        replay_result=replay_result,
        replay_gates=(
            GateResult("candidate_replay", True, "paired outcomes comparable"),
            GateResult("replay_confidence", True, "confidence admitted"),
            GateResult(
                "replay_evaluator_admission",
                True,
                "evidence invariants admitted",
            ),
        ),
        apply_policy="verified_only",
        budget_context=budget,
    )

    result = plan_candidate_evaluation_admission(
        request,
        CandidateEvaluationAdmissionPolicy(
            replay_enabled=True,
            evaluation_backend=_ProbabilisticEvaluationBackend(),
            judge_repetitions=1,
            min_eval_cases=30,
        ),
        _runtime(),
    )

    assert result.terminal_result is None
    feasibility = next(
        gate
        for gate in result.gate_results
        if gate.gate_name == "verification_feasibility"
    )
    assert feasibility.passed is True
    assert feasibility.details["deterministic_replay_available"] is True
    assert feasibility.details["trajectory_set_validation_available"] is True
    admission = feasibility.details["deterministic_replay_admission"]
    assert admission["reason_code"] == (
        "deterministic_comparable_replay_admitted"
    )
    assert admission["comparable"] is True
    assert admission["strict_execution_succeeded"] is False
    assert result.judge_budget is not None
    assert any(stage is BudgetStage.JUDGE for stage, _, _ in budget.reservations)


def test_verified_recovery_pairs_do_not_override_failed_replay_gate() -> None:
    dataset = _dataset(1)
    replay_result = _recovery_replay_result(dataset)
    request = _request(
        dataset=dataset,
        replay_dataset=dataset,
        replay_result=replay_result,
        replay_gates=(
            GateResult(
                "candidate_replay",
                False,
                "framework replay failed",
                details={"failure_class": "framework"},
            ),
            GateResult("replay_confidence", True, "confidence admitted"),
        ),
        apply_policy="verified_only",
    )

    result = plan_candidate_evaluation_admission(
        request,
        CandidateEvaluationAdmissionPolicy(
            replay_enabled=True,
            evaluation_backend=_ProbabilisticEvaluationBackend(),
            judge_repetitions=1,
            min_eval_cases=30,
        ),
        _runtime(),
    )

    assert result.terminal_result is not None
    feasibility = next(
        gate
        for gate in result.gate_results
        if gate.gate_name == "verification_feasibility"
    )
    assert feasibility.passed is False
    admission = feasibility.details["deterministic_replay_admission"]
    assert admission["reason_code"] == "typed_replay_gate_failed"
    assert admission["comparable"] is False
    assert admission["failed_gate_names"] == ["candidate_replay"]


def test_evaluation_budget_denial_releases_dependent_reservation() -> None:
    tracker = _AttemptTracker(CandidateAttemptStage.PAIRED_REPLAY_STARTED)
    budget = _BudgetContext(total_tokens=3)
    request = _request(
        attempt_tracker=tracker,
        budget_context=budget,
    )

    result = plan_candidate_evaluation_admission(
        request,
        CandidateEvaluationAdmissionPolicy(
            replay_enabled=False,
            evaluation_backend=_EvaluationBackend(),
            judge_repetitions=1,
        ),
        _runtime(),
    )

    assert result.terminal_result is not None
    failed_gate = next(
        gate for gate in result.gate_results if not gate.passed
    )
    assert failed_gate.gate_name == "run_budget_judge"
    assert failed_gate.details["code"] == "judge_budget_denied"
    assert budget.releases[0][1] == "dependent_evaluation_budget_denied"
    assert tracker.events == [
        (CandidateAttemptStage.REJECTED, "judge_budget_denied")
    ]


def test_verified_replay_block_skips_evaluation_reservation() -> None:
    budget = _BudgetContext()
    request = _request(
        apply_policy="verified_only",
        budget_context=budget,
        replay_dataset=None,
    )

    result = plan_candidate_evaluation_admission(
        request,
        CandidateEvaluationAdmissionPolicy(
            replay_enabled=True,
            evaluation_backend=_EvaluationBackend(),
            judge_repetitions=1,
        ),
        _runtime(),
    )

    assert result.replay_blocked_verified_apply is True
    assert result.evaluation_budget is None
    assert result.judge_budget is None
    assert budget.reservations == []


def test_run_evaluation_admission_controller_does_not_import_runner() -> None:
    tree = ast.parse(inspect.getsource(admission_module))
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
