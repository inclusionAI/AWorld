from pathlib import Path

import pytest

from aworld_cli.core.context import get_default_history_path
from aworld_cli.core.session_transcript import CliSessionTranscript
from aworld_cli.executors.base_executor import BaseAgentExecutor
from aworld_cli.executors.local import LocalAgentExecutor
from aworld_cli.executors import tool_logger as tool_logger_module


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
