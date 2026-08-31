from __future__ import annotations

import ast
import inspect
from types import SimpleNamespace

import pytest

from aworld.self_evolve.controllers import (
    measurement_authority as authority_module,
)
from aworld.self_evolve.controllers.measurement_authority import (
    AuthoritativeMeasurementConfig,
    AuthoritativeMeasurementController,
    AuthoritativeMeasurementRequest,
    AuthoritativeMeasurementRuntime,
)
from aworld.self_evolve.measurement import MeasurementPolicyMode
from aworld.self_evolve.store import FilesystemSelfEvolveStore


def _request(*, measurement_stage: str = "authoritative"):
    return AuthoritativeMeasurementRequest(
        run_id="run-1",
        dataset=SimpleNamespace(),
        candidate=SimpleNamespace(candidate_id="candidate-1"),
        replay_adaptation=SimpleNamespace(),
        replay_backend_identity={"kind": "test"},
        member_timeout_seconds=30,
        measurement_stage=measurement_stage,
    )


def _runtime() -> AuthoritativeMeasurementRuntime:
    return AuthoritativeMeasurementRuntime(
        experiments={},
        load_resume_request=lambda **_kwargs: None,
    )


def test_measurement_off_bypasses_execution_stage_validation(tmp_path) -> None:
    controller = AuthoritativeMeasurementController(
        store=FilesystemSelfEvolveStore(tmp_path),
        config=AuthoritativeMeasurementConfig(
            mode=MeasurementPolicyMode.OFF,
            resume_run_id=None,
            campaign_wall_deadline_seconds=None,
        ),
    )

    result = controller.compile(
        _request(measurement_stage="unsupported"),
        _runtime(),
    )

    assert result.execution_bundle is None
    assert result.resumed is False
    assert result.shadow_only is False


def test_enabled_measurement_rejects_unknown_execution_stage(tmp_path) -> None:
    controller = AuthoritativeMeasurementController(
        store=FilesystemSelfEvolveStore(tmp_path),
        config=AuthoritativeMeasurementConfig(
            mode=MeasurementPolicyMode.ADVISORY,
            resume_run_id=None,
            campaign_wall_deadline_seconds=None,
        ),
    )

    with pytest.raises(ValueError, match="unsupported measurement"):
        controller.compile(
            _request(measurement_stage="unsupported"),
            _runtime(),
        )


def test_measurement_authority_controller_does_not_import_runner() -> None:
    tree = ast.parse(inspect.getsource(authority_module))
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
