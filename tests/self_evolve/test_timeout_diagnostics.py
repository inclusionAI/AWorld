"""Timeout attribution must not infer LLM behavior from tool-boundary state."""

from pathlib import Path

import pytest

from aworld.self_evolve.datasets import EvalCase
from aworld.self_evolve.controllers.measurement_execution_admission import (
    _paired_candidate_completion_failure,
)
from aworld.self_evolve.failure_events import (
    FailureOwner,
    FailureScope,
    FailureStage,
    ReplayFailureEvent,
)
from aworld.self_evolve.types import SelfEvolveTargetRef
from aworld.self_evolve.replay import (
    CandidateReplayRequest,
    NormalizedReplayMember,
    NormalizedReplayMembers,
    ReplayExecutionRequest,
    ReplayVariantResult,
    _timeout_termination_diagnostics,
)


@pytest.mark.parametrize("phase", ["collecting", "finalizing"])
@pytest.mark.parametrize("attempts", [0, 8, 11])
def test_supervisor_timeout_does_not_infer_tool_exhaustion_or_synthesis(
    tmp_path: Path, phase: str, attempts: int
) -> None:
    request = ReplayExecutionRequest(
        variant_id="candidate",
        task_id="task",
        candidate_id="candidate",
        workspace_root=str(tmp_path),
        task_input={},
        task_text="Summarize the retrieved material.",
        skill_root=str(tmp_path / "skills"),
        artifact_dir=str(tmp_path / "artifacts"),
        timeout_seconds=900,
    )
    diagnostics = _timeout_termination_diagnostics(
        request,
        {
            "evidence_runtime_policy_tool_call_attempt_count": attempts,
            "evidence_runtime_policy_phase": phase,
        },
        max_tool_calls=8,
    )

    assert diagnostics["termination_budget_axis"] == "wall_time"
    assert diagnostics["tool_calls_used"] == attempts
    assert diagnostics["tool_calls_used_scope"] == "evidence_directory"
    assert diagnostics["max_tool_calls_scope"] == "task"
    assert diagnostics["evidence_phase"] == phase
    assert "terminal_synthesis_attempted" not in diagnostics


@pytest.mark.parametrize(
    ("observations", "expected"),
    [
        ([None], None),
        ([False], False),
        ([False, None], None),
        ([True, None], True),
        ([False, False], False),
    ],
)
def test_paired_completion_preserves_unknown_synthesis_attempts(
    tmp_path: Path, observations: list[bool | None], expected: bool | None
) -> None:
    request = CandidateReplayRequest(
        run_id="run",
        task_id="task",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="candidate",
        overlay_skill_root=str(tmp_path / "skills"),
        task_input={},
    )
    members = []
    for index, observation in enumerate(observations):
        diagnostics = {
            "completed_data_plane_operations": ["content"],
            "termination_budget_axis": "wall_time",
        }
        if observation is not None:
            diagnostics["terminal_synthesis_attempted"] = observation
        failure = ReplayFailureEvent(
            code="replay_task_timeout_with_recoverable_evidence",
            owner=FailureOwner.TASK,
            stage=FailureStage.TASK_ROLLOUT,
            scope=FailureScope.MEMBER,
            repairable=False,
            category="task_completion",
            diagnostics=diagnostics,
        )
        members.append(
            NormalizedReplayMember(
                case=EvalCase(case_id=f"case-{index}", input="Summarize material."),
                request=request,
                baseline=ReplayVariantResult("baseline", "succeeded", []),
                candidate=ReplayVariantResult(
                    "candidate", "failed", [], failure=failure
                ),
            )
        )

    result = _paired_candidate_completion_failure(
        NormalizedReplayMembers(members=tuple(members))
    )

    assert result is not None
    event, evidence = result
    assert evidence["terminal_synthesis_attempted"] is expected
    assert event.diagnostics["terminal_synthesis_attempted"] is expected
    assert event.code == "target_behavior_completion_missing"
    assert evidence["candidate_timeout_count"] == len(observations)
