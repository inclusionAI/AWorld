from pathlib import Path

import pytest

from aworld_cli.core.context import get_default_history_path
from aworld_cli.core.config import AWorldConfig
from aworld_cli.core.installed_skill_manager import (
    default_installed_skill_root,
    default_skill_home as default_installed_skill_home,
)
from aworld_cli.core.plugin_manager import (
    _get_cli_package_dir,
    get_default_plugin_dir,
)
from aworld_cli.core.session_transcript import CliSessionTranscript
from aworld_cli.core.skill_registry import get_default_skill_source_paths
from aworld_cli.core.skill_state_manager import (
    SkillStateManager,
    default_skill_home,
)
from aworld_cli.executors.base_executor import BaseAgentExecutor
from aworld_cli.executors.local import LocalAgentExecutor
from aworld_cli.executors import tool_logger as tool_logger_module
from aworld.memory.main import _default_file_memory_store


def test_cli_operational_paths_use_control_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    control_root = tmp_path / "control"
    monkeypatch.setenv("AWORLD_CONTROL_ROOT", str(control_root))
    tool_logger_module._logger_instance = None

    executor = LocalAgentExecutor.__new__(LocalAgentExecutor)
    transcript = CliSessionTranscript()
    logger = tool_logger_module.get_tool_logger()

    assert BaseAgentExecutor._get_session_history_file(executor) == (
        control_root / "workspaces" / ".session_history.json"
    )
    assert transcript.path_for("session-1") == (
        control_root / "sessions" / "transcripts" / "session-1.jsonl"
    )
    assert get_default_history_path() == control_root / "cli_history.jsonl"
    assert logger.log_dir == control_root / "tool_calls"
    assert AWorldConfig().config_dir == control_root
    assert default_skill_home() == control_root / "skills"
    assert SkillStateManager().state_path == (
        control_root / "skills" / ".skill-state.json"
    )
    assert default_installed_skill_home() == control_root / "skills"
    assert default_installed_skill_root() == control_root / ".installed-skills"
    assert get_default_plugin_dir() == control_root / "plugins"
    assert get_default_skill_source_paths()[0] == control_root / "skills"
    assert _default_file_memory_store().memory_root == control_root / "memory"

    tool_logger_module._logger_instance = None


def test_explicit_transcript_root_takes_precedence_over_control_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AWORLD_CONTROL_ROOT", str(tmp_path / "control"))
    explicit_root = tmp_path / "explicit"

    transcript = CliSessionTranscript(root=explicit_root)

    assert transcript.path_for("session-1") == (
        explicit_root / ".aworld" / "sessions" / "transcripts" / "session-1.jsonl"
    )


def test_control_root_disables_task_workspace_source_checkout_shadowing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_package = tmp_path / "aworld-cli" / "src" / "aworld_cli"
    fake_package.mkdir(parents=True)
    (fake_package / "__init__.py").write_text("# task-controlled shadow\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AWORLD_CONTROL_ROOT", str(tmp_path / "control"))

    assert _get_cli_package_dir() != fake_package.resolve()
