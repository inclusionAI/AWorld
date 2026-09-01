from __future__ import annotations

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
    PairedReplayExecutionRuntime,
)
from aworld.self_evolve.measurement import MeasurementPolicyMode
from aworld.self_evolve.optimizers.base import CandidateSourceDisposition
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
