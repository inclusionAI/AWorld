from __future__ import annotations

import argparse
import asyncio
from types import SimpleNamespace

import pytest

from aworld_cli import async_runtime
from aworld_cli import main as main_module
from aworld_cli.top_level_commands.run_cmd import RunTopLevelCommand


def _run_args(*, non_interactive: bool) -> argparse.Namespace:
    return argparse.Namespace(
        task="test",
        agent="Aworld",
        skill=None,
        max_runs=None,
        max_cost=None,
        max_duration=None,
        completion_signal=None,
        completion_threshold=3,
        non_interactive=non_interactive,
        session_id=None,
        env_file=".env",
        remote_backend=None,
        agent_dir=None,
        agent_file=None,
        skill_path=None,
        evolve=None,
        judge_agent=None,
        judge_agent_name=None,
        judge_backend_ref=None,
        judge_model_profile=None,
        emit_trajectory=False,
        trajectory_output=None,
        outcome_output=None,
    )


@pytest.mark.parametrize("non_interactive", (False, True))
def test_run_command_only_uses_one_shot_loop_for_non_interactive_mode(
    monkeypatch: pytest.MonkeyPatch,
    non_interactive: bool,
) -> None:
    captured: list[bool] = []

    async def fake_direct_run(**_kwargs):
        return {"results": []}

    def capture_run(coro, *, one_shot: bool = False):
        captured.append(one_shot)
        return asyncio.run(coro)

    monkeypatch.setattr(
        "aworld_cli.top_level_commands.run_cmd.bootstrap_runtime",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(main_module, "_run_direct_mode", fake_direct_run)
    monkeypatch.setattr(
        "aworld_cli.top_level_commands.run_cmd.run_direct_async",
        capture_run,
    )

    assert RunTopLevelCommand().run(
        _run_args(non_interactive=non_interactive),
        SimpleNamespace(argv=("aworld-cli", "run")),
    ) == 0
    assert captured == [non_interactive]


@pytest.mark.parametrize("non_interactive", (False, True))
def test_run_dispatch_only_requests_hard_exit_for_non_interactive_mode(
    monkeypatch: pytest.MonkeyPatch,
    non_interactive: bool,
) -> None:
    captured: list[tuple[int, bool]] = []

    class FakeRunCommand:
        name = "run"

        def run(self, args, context):
            assert args.non_interactive is non_interactive
            assert context.argv == ("aworld-cli", "run")
            return 0

    monkeypatch.setattr(
        async_runtime,
        "hard_exit_direct_run_if_configured",
        lambda exit_code, *, one_shot=False: captured.append(
            (exit_code, one_shot)
        ),
    )

    assert main_module._run_top_level_command(
        FakeRunCommand(),
        _run_args(non_interactive=non_interactive),
        ["aworld-cli", "run"],
    ) is True
    assert captured == [(0, non_interactive)]

