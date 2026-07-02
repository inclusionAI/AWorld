import argparse
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))


def test_parse_command_invocation_args_merges_prefix_and_suffix_options() -> None:
    from aworld_cli.top_level_commands.invocation import parse_command_invocation_args

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--agent-dir", action="append")

    args = parse_command_invocation_args(
        ["aworld-cli", "--env-file", "custom.env", "interactive", "--agent-dir", "./agents"],
        command_name="interactive",
        parser=parser,
        options_with_values={"--env-file", "--agent-dir"},
    )

    assert args.env_file == "custom.env"
    assert args.agent_dir == ["./agents"]


def test_find_command_index_ignores_option_values() -> None:
    from aworld_cli.top_level_commands.invocation import find_command_index

    index = find_command_index(
        ["aworld-cli", "--agent", "interactive"],
        command_name="interactive",
        options_with_values={"--agent"},
    )

    assert index is None


def test_resume_parser_accepts_session_id_and_prompt() -> None:
    from aworld_cli.top_level_commands.resume_cmd import _parse_resume_invocation_args

    args = _parse_resume_invocation_args(
        ["aworld-cli", "resume", "session_123", "continue this work"]
    )

    assert args.session_id == "session_123"
    assert args.prompt == "continue this work"
    assert args.last is False


def test_resume_parser_accepts_options_after_session_id_before_prompt() -> None:
    from aworld_cli.top_level_commands.resume_cmd import _parse_resume_invocation_args

    args = _parse_resume_invocation_args(
        ["aworld-cli", "resume", "session_123", "--agent", "Other", "continue", "work"]
    )

    assert args.session_id == "session_123"
    assert args.agent == "Other"
    assert args.prompt == "continue work"


def test_resume_last_parser_treats_remaining_args_as_prompt() -> None:
    from aworld_cli.top_level_commands.resume_cmd import _parse_resume_invocation_args

    args = _parse_resume_invocation_args(
        ["aworld-cli", "resume", "--last", "continue", "this", "work"]
    )

    assert args.session_id is None
    assert args.prompt == "continue this work"
    assert args.last is True


def test_resume_parser_accepts_lookup_and_runtime_options() -> None:
    from aworld_cli.top_level_commands.resume_cmd import _parse_resume_invocation_args

    args = _parse_resume_invocation_args(
        [
            "aworld-cli",
            "--env-file",
            "custom.env",
            "resume",
            "--all",
            "--include-non-interactive",
            "--last",
            "--skill",
            "code-review",
            "--remote-backend",
            "http://localhost:8000",
            "--agent-dir",
            "./agents",
            "--agent-file",
            "./agent.py",
            "--skill-path",
            "./skills",
        ]
    )

    assert args.last is True
    assert args.all is True
    assert args.include_non_interactive is True
    assert args.env_file == "custom.env"
    assert args.skill == ["code-review"]
    assert args.remote_backend == ["http://localhost:8000"]
    assert args.agent_dir == ["./agents"]
    assert args.agent_file == ["./agent.py"]
    assert args.skill_path == ["./skills"]


def test_resume_command_registered_as_builtin() -> None:
    from aworld_cli.core.top_level_command_system import TopLevelCommandRegistry
    from aworld_cli.top_level_commands import register_builtin_top_level_commands

    registry = TopLevelCommandRegistry()
    register_builtin_top_level_commands(registry)

    assert registry.get("resume") is not None


def test_resume_command_runs_prompt_after_resuming(monkeypatch, tmp_path: Path) -> None:
    from aworld_cli.core.session_store import CliSessionRecord, CliSessionStore
    from aworld_cli.top_level_commands.resume_cmd import ResumeTopLevelCommand

    store = CliSessionStore(root=tmp_path)
    store.upsert_session(
        CliSessionRecord(
            session_id="session_resume",
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
            cwd=str(tmp_path.resolve()),
            agent_name="Aworld",
            mode="interactive",
        )
    )

    calls = {}

    async def fake_resume_mode(**kwargs):
        calls.update(kwargs)

    monkeypatch.setenv("AWORLD_CLI_SESSION_STORE_ROOT", str(tmp_path))
    monkeypatch.setattr("aworld_cli.top_level_commands.resume_cmd._run_resume_mode", fake_resume_mode)

    command = ResumeTopLevelCommand()
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    command.register_parser(subparsers)
    args = parser.parse_args(["resume", "session_resume", "next prompt"])

    exit_code = command.run(
        args,
        type("Context", (), {"cwd": str(tmp_path), "argv": ("aworld-cli", "resume", "session_resume", "next prompt")})(),
    )

    assert exit_code == 0
    assert calls["session_id"] == "session_resume"
    assert calls["agent_name"] == "Aworld"
    assert calls["initial_prompt"] == "next prompt"


@pytest.mark.asyncio
async def test_resume_mode_displays_initial_prompt_as_terminal_turn(monkeypatch) -> None:
    from aworld_cli.top_level_commands.resume_cmd import _run_resume_mode

    calls = {}

    async def fake_direct_mode(**kwargs):
        calls["direct"] = kwargs

    async def fake_interactive_mode(**kwargs):
        calls["interactive"] = kwargs

    monkeypatch.setattr("aworld_cli.main._run_direct_mode", fake_direct_mode)
    monkeypatch.setattr("aworld_cli.main._run_interactive_mode", fake_interactive_mode)

    await _run_resume_mode(
        session_id="session_resume",
        agent_name="Aworld",
        requested_skill_names=None,
        initial_prompt="next prompt",
        remote_backends=None,
        local_dirs=None,
        agent_files=None,
        resume_record=object(),
        session_store=object(),
        require_same_resume_agent=True,
        resume_cwd="/workspace",
        fail_on_missing_agent=True,
    )

    assert calls["direct"]["show_start_banner"] is False
    assert calls["direct"]["show_iteration_header"] is False
    assert calls["direct"]["echo_prompt_as_turn"] is True
    assert calls["direct"]["max_runs"] == 1
    assert calls["interactive"]["session_id"] == "session_resume"


def test_resume_command_prompts_for_session_when_no_id_or_last(monkeypatch, tmp_path: Path) -> None:
    from aworld_cli.core.session_store import CliSessionRecord, CliSessionStore
    from aworld_cli.top_level_commands.resume_cmd import ResumeTopLevelCommand

    store = CliSessionStore(root=tmp_path)
    store.upsert_session(
        CliSessionRecord(
            session_id="session_pick",
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
            cwd=str(tmp_path.resolve()),
            agent_name="Aworld",
            mode="interactive",
        )
    )

    calls = {}

    async def fake_resume_mode(**kwargs):
        calls.update(kwargs)

    monkeypatch.setenv("AWORLD_CLI_SESSION_STORE_ROOT", str(tmp_path))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "1")
    monkeypatch.setattr("aworld_cli.top_level_commands.resume_cmd._run_resume_mode", fake_resume_mode)

    command = ResumeTopLevelCommand()
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    command.register_parser(subparsers)
    args = parser.parse_args(["resume"])

    exit_code = command.run(
        args,
        type("Context", (), {"cwd": str(tmp_path), "argv": ("aworld-cli", "resume")})(),
    )

    assert exit_code == 0
    assert calls["session_id"] == "session_pick"


def test_resume_missing_explicit_session_message_mentions_all_and_sessions(monkeypatch, tmp_path: Path, capsys) -> None:
    from aworld_cli.top_level_commands.resume_cmd import ResumeTopLevelCommand

    monkeypatch.setenv("AWORLD_CLI_SESSION_STORE_ROOT", str(tmp_path))

    command = ResumeTopLevelCommand()
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    command.register_parser(subparsers)
    args = parser.parse_args(["resume", "missing_session"])

    exit_code = command.run(
        args,
        type("Context", (), {"cwd": str(tmp_path), "argv": ("aworld-cli", "resume", "missing_session")})(),
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "Session not found: missing_session" in output
    assert "--all" in output
    assert "/sessions list" in output


def test_resume_last_without_match_message_mentions_cwd_and_all(monkeypatch, tmp_path: Path, capsys) -> None:
    from aworld_cli.top_level_commands.resume_cmd import ResumeTopLevelCommand

    monkeypatch.setenv("AWORLD_CLI_SESSION_STORE_ROOT", str(tmp_path))

    command = ResumeTopLevelCommand()
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    command.register_parser(subparsers)
    args = parser.parse_args(["resume", "--last"])

    exit_code = command.run(
        args,
        type("Context", (), {"cwd": str(tmp_path), "argv": ("aworld-cli", "resume", "--last")})(),
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert f"No resumable sessions found for {tmp_path}" in output
    assert "--all" in output
