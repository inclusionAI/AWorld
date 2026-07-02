from pathlib import Path

import pytest

from aworld.runners.ralph.config import RalphConfig
from examples.aworld_quick_start.ralph_runner.example_setup import (
    RalphExamplePaths,
    build_ralph_runner_example_config,
    build_ralph_runner_example_criteria,
    ensure_ralph_runner_example_workspace,
)


def test_ensure_ralph_runner_example_workspace_creates_seed_files(tmp_path: Path) -> None:
    paths = RalphExamplePaths.from_root(tmp_path / "ralph-runner-example")

    ensure_ralph_runner_example_workspace(paths)

    assert paths.root == (tmp_path / "ralph-runner-example").resolve()
    assert paths.root.is_dir()
    assert paths.src_dir.is_dir()
    assert paths.tests_dir.is_dir()
    assert paths.rules_file.is_file()
    assert paths.module_file.is_file()
    assert paths.test_file.is_file()
    assert paths.marker_file.is_file()
    assert "order pricing" in paths.rules_file.read_text(encoding="utf-8").lower()
    assert "NotImplementedError" in paths.module_file.read_text(encoding="utf-8")
    assert "calculate_quote" in paths.test_file.read_text(encoding="utf-8")


def test_ensure_ralph_runner_example_workspace_reset_clears_existing_content(tmp_path: Path) -> None:
    paths = RalphExamplePaths.from_root(tmp_path / "ralph-runner-example")
    ensure_ralph_runner_example_workspace(paths)

    stale_file = paths.root / "stale.txt"
    stale_file.write_text("old-data", encoding="utf-8")
    assert stale_file.exists()

    ensure_ralph_runner_example_workspace(paths, reset=True)

    assert paths.root.is_dir()
    assert not stale_file.exists()
    assert paths.marker_file.is_file()


def test_ensure_ralph_runner_example_workspace_reset_requires_marker(tmp_path: Path) -> None:
    paths = RalphExamplePaths.from_root(tmp_path / "ralph-runner-example")
    paths.root.mkdir(parents=True)
    paths.src_dir.mkdir()
    paths.tests_dir.mkdir()

    with pytest.raises(ValueError, match="marker"):
        ensure_ralph_runner_example_workspace(paths, reset=True)


def test_build_ralph_runner_example_config_uses_fresh_context_and_verify(tmp_path: Path) -> None:
    config = build_ralph_runner_example_config(workspace=str(tmp_path))

    assert isinstance(config, RalphConfig)
    assert config.workspace == str(tmp_path)
    assert config.execution_mode == "fresh_context"
    assert config.reuse_context is False
    assert config.verify.enabled is True
    assert config.verify.run_on_each_iteration is True
    assert config.verify.run_before_completion is True
    assert config.verify.commands == ["PYTHONPATH=. pytest -q"]


def test_build_ralph_runner_example_criteria_sets_reasonable_iteration_budget() -> None:
    criteria = build_ralph_runner_example_criteria()

    assert criteria.max_iterations == 5
    assert criteria.custom_stop is None


def test_build_ralph_runner_example_criteria_can_add_verify_based_stop() -> None:
    criteria = build_ralph_runner_example_criteria(task_id="task-1")

    assert criteria.max_iterations == 5
    assert callable(criteria.custom_stop)
