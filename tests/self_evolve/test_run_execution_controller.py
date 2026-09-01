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
    ExplicitTargetRunRequest,
    execute_candidate_local_admission,
)
from aworld.self_evolve.budget import CandidateAttemptKey, CandidateAttemptStage
from aworld.self_evolve.optimizers.base import CandidateSourceDisposition
from aworld.self_evolve.runner import SelfEvolveRunner
from aworld.self_evolve.types import (
    CandidateVariant,
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
