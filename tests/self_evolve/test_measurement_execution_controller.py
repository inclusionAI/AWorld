from __future__ import annotations

import asyncio
import ast
import inspect
from dataclasses import replace
from types import SimpleNamespace

import pytest

from aworld.self_evolve.controllers import (
    measurement_execution as execution_module,
)
from aworld.self_evolve.controllers import (
    measurement_execution_admission as execution_admission_module,
    measurement_execution_datasets as execution_datasets_module,
    measurement_execution_progress as execution_progress_module,
)
from aworld.self_evolve.controllers.measurement_execution import (
    PairedReplayExecutionConfig,
    PairedReplayExecutionController,
    PairedReplayExecutionRequest,
    PairedReplayExecutionResult,
    PairedReplayExecutionRuntime,
)
from aworld.self_evolve.controllers.measurement_execution_progress import (
    _remaining_replay_phase_count,
    _replay_total_budget_admission,
)
from aworld.self_evolve.datasets import (
    DatasetRecipe,
    EvalCase,
    SelfEvolveDataset,
)
from aworld.self_evolve.measurement import MeasurementPolicyMode
from aworld.self_evolve.optimizers.base import CandidateSourceDisposition
from aworld.self_evolve.replay import CandidateReplayRequest
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.types import CandidateVariant, SelfEvolveTargetRef


def _config(*, replay_enabled: bool) -> PairedReplayExecutionConfig:
    return PairedReplayExecutionConfig(
        replay_enabled=replay_enabled,
        replay_backend=None,
        replay_agent=None,
        baseline_repetitions=1,
        candidate_repetitions=1,
        repetitions_explicit=False,
        minimum_independent_cases=1,
        timeout_seconds=30,
        total_timeout_seconds=None,
        max_steps=None,
        max_tokens=None,
        resume_replay_dir=None,
        invalid_control_patience=1,
        measurement_mode=MeasurementPolicyMode.OFF,
    )


def _request() -> PairedReplayExecutionRequest:
    return PairedReplayExecutionRequest(
        run_id="run-1",
        target=SimpleNamespace(),
        dataset=SimpleNamespace(),
        candidate=CandidateVariant(
            candidate_id="candidate-1",
            target=SelfEvolveTargetRef("skill", "demo", None),
            content="# Demo\n",
            rationale="exercise execution boundary",
        ),
        apply_policy="proposal",
        source_disposition=CandidateSourceDisposition(),
    )


def _runtime() -> PairedReplayExecutionRuntime:
    def unexpected(*_args, **_kwargs):
        raise AssertionError("disabled replay must not use runtime services")

    return PairedReplayExecutionRuntime(
        progress_callback=None,
        execution_telemetry=SimpleNamespace(),
        screening_case_observations={},
        screening_control_observations={},
        measurement_experiments={},
        prepare_replay_adaptation=unexpected,
        baseline_reuse_provenance=unexpected,
        compile_measurement_plan=unexpected,
        load_measurement_resume_request=unexpected,
    )


@pytest.mark.asyncio
async def test_disabled_paired_replay_returns_empty_result(tmp_path) -> None:
    controller = PairedReplayExecutionController(
        store=FilesystemSelfEvolveStore(tmp_path),
        config=_config(replay_enabled=False),
    )

    result = await controller.execute(_request(), _runtime())

    assert result.replay_result is None
    assert result.replay_dataset is None
    assert result.gate is None
    assert result.as_tuple() == (None, None, None)


@pytest.mark.asyncio
async def test_proposal_without_backend_skips_replay(tmp_path) -> None:
    controller = PairedReplayExecutionController(
        store=FilesystemSelfEvolveStore(tmp_path),
        config=_config(replay_enabled=True),
    )

    result = await controller.execute(_request(), _runtime())

    assert result.as_tuple() == (None, None, None)


@pytest.mark.asyncio
async def test_verified_apply_without_backend_fails_closed(tmp_path) -> None:
    controller = PairedReplayExecutionController(
        store=FilesystemSelfEvolveStore(tmp_path),
        config=_config(replay_enabled=True),
    )
    request = replace(_request(), apply_policy="verified_only")

    result = await controller.execute(request, _runtime())

    assert result.replay_result is None
    assert result.replay_dataset is None
    assert result.gate is not None
    assert result.gate.passed is False
    assert result.gate.gate_name == "candidate_replay"


def test_replay_total_budget_admission_uses_completed_member_phases() -> None:
    admission = _replay_total_budget_admission(
        payload={
            "event": "member_phase_started",
            "case_id": "case-11",
            "case_index": 11,
            "case_count": 11,
            "phase": "baseline",
        },
        replay_started_at=0.0,
        now=3_483.0,
        total_timeout_seconds=3_600.0,
        completed_phase_durations=(170.0,) * 20,
    )

    assert admission is not None
    assert admission["trigger"] == "insufficient_remaining_total_budget"
    assert admission["remaining_phase_count"] == 2
    assert admission["estimated_required_seconds"] == 340.0
    assert admission["remaining_budget_seconds"] == 117.0


def test_remaining_replay_phases_respects_resumed_case_position() -> None:
    assert (
        _remaining_replay_phase_count(
            {
                "case_index": 11,
                "case_count": 11,
                "phase": "baseline",
            }
        )
        == 2
    )
    assert (
        _remaining_replay_phase_count(
            {
                "case_index": 11,
                "case_count": 11,
                "phase": "candidate",
            }
        )
        == 1
    )


@pytest.mark.asyncio
async def test_replay_budget_admission_returns_resumable_typed_timeout(
    tmp_path,
) -> None:
    candidate = _request().candidate
    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="case-1", input={"content": "one"}),
            EvalCase(case_id="case-2", input={"content": "two"}),
        ),
        recipe=DatasetRecipe(
            source={"kind": "budget-admission"},
            split_seed="seed",
            splits={"train": ["case-1", "case-2"]},
            trainable_case_ids=("case-1", "case-2"),
        ),
    )
    replay_request = CandidateReplayRequest(
        run_id="run-budget-admission",
        task_id="case-1",
        workspace_root=str(tmp_path),
        target=candidate.target,
        candidate_id=candidate.candidate_id,
        overlay_skill_root=str(tmp_path / "overlay"),
        task_input={"content": "one"},
        timeout_seconds=30,
    )

    class ProgressBackend:
        supports_member_progress = True

        async def replay_candidate(
            self, request, *, candidate, dataset, progress_callback
        ):
            for phase in ("baseline", "candidate"):
                progress_callback(
                    {
                        "event": "member_phase_started",
                        "candidate_id": candidate.candidate_id,
                        "case_id": "case-1",
                        "case_index": 1,
                        "case_count": 2,
                        "phase": phase,
                    }
                )
                await asyncio.sleep(0.05)
                progress_callback(
                    {
                        "event": "member_phase_completed",
                        "candidate_id": candidate.candidate_id,
                        "case_id": "case-1",
                        "case_index": 1,
                        "case_count": 2,
                        "phase": phase,
                        "status": "succeeded",
                    }
                )
            progress_callback(
                {
                    "event": "member_phase_started",
                    "candidate_id": candidate.candidate_id,
                    "case_id": "case-2",
                    "case_index": 2,
                    "case_count": 2,
                    "phase": "baseline",
                }
            )
            await asyncio.sleep(10)
            raise AssertionError("budget admission did not cancel replay")

    controller = PairedReplayExecutionController(
        store=FilesystemSelfEvolveStore(tmp_path),
        config=replace(
            _config(replay_enabled=True),
            total_timeout_seconds=0.2,
        ),
    )
    result = await controller._execute_replay(
        replay_request=replay_request,
        dataset=dataset,
        candidate=candidate,
        backend=ProgressBackend(),
        apply_policy="proposal",
        progress_stage="candidate_replay",
        lifecycle_callback=None,
        effective_baseline_repetitions=1,
        effective_candidate_repetitions=1,
        repetition_policy="configured",
        member_timeout_seconds=30,
        runtime=_runtime(),
    )

    assert isinstance(result, PairedReplayExecutionResult)
    assert result.gate is not None
    assert result.gate.details["code"] == "replay_total_timeout"
    assert result.gate.details["timeout_trigger"] == "deadline_admission"
    assert result.gate.details["next_action"] == "continue_measurement"
    admission = result.gate.details["deadline_admission"]
    assert admission["case_id"] == "case-2"
    assert admission["resume_safe"] is True


@pytest.mark.parametrize(
    "module",
    (
        execution_module,
        execution_admission_module,
        execution_datasets_module,
        execution_progress_module,
    ),
)
def test_measurement_execution_modules_do_not_import_runner(module) -> None:
    tree = ast.parse(inspect.getsource(module))
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
