import json
from types import SimpleNamespace

import pytest

from aworld_cli import main as main_module
from aworld_cli.top_level_commands.run_cmd import RunTopLevelCommand


def _failure_payload(stderr: str) -> dict:
    marker = "AWORLD_RUN_FAILURE="
    line = next(line for line in stderr.splitlines() if line.startswith(marker))
    return json.loads(line.removeprefix(marker))


@pytest.mark.asyncio
async def test_direct_run_reports_agent_load_failure_and_returns_false(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class DummyRuntime:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def _load_agents(self):
            return [SimpleNamespace(name="OtherAgent")]

    monkeypatch.setattr(main_module, "CliRuntime", DummyRuntime)

    succeeded = await main_module._run_direct_mode(
        prompt="test",
        agent_name="Aworld",
    )

    assert succeeded is False
    payload = _failure_payload(capsys.readouterr().err)
    assert payload == {
        "agent_name": "Aworld",
        "details": {"available_agents": ["OtherAgent"]},
        "error_code": "agent_not_found",
        "llm_call_count": 0,
        "schema_version": "aworld.run.failure.v1",
        "stage": "agent_load",
        "status": "failed",
        "trajectory_fidelity": "unavailable",
    }


@pytest.mark.asyncio
async def test_direct_run_reports_executor_creation_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class DummyRuntime:
        def __init__(self, *args, **kwargs) -> None:
            self._scheduler = None

        async def _load_agents(self):
            return [SimpleNamespace(name="Aworld")]

        def _bind_scheduler_default_agent(self, _agent_name: str) -> None:
            pass

        async def _create_executor(self, _agent):
            return None

    monkeypatch.setattr(main_module, "CliRuntime", DummyRuntime)
    monkeypatch.setattr("aworld.core.scheduler.get_scheduler", lambda: object())

    succeeded = await main_module._run_direct_mode(
        prompt="test",
        agent_name="Aworld",
    )

    assert succeeded is False
    payload = _failure_payload(capsys.readouterr().err)
    assert payload["error_code"] == "executor_creation_failed"
    assert payload["stage"] == "executor_create"
    assert payload["trajectory_fidelity"] == "unavailable"
    assert payload["llm_call_count"] == 0


@pytest.mark.asyncio
async def test_direct_run_classifies_agent_loader_exception(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class DummyRuntime:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def _load_agents(self):
            raise ImportError("native module is incompatible")

    monkeypatch.setattr(main_module, "CliRuntime", DummyRuntime)

    succeeded = await main_module._run_direct_mode(
        prompt="test",
        agent_name="Aworld",
    )

    assert succeeded is False
    payload = _failure_payload(capsys.readouterr().err)
    assert payload["error_code"] == "agent_load_failed"
    assert payload["stage"] == "agent_load"
    assert payload["details"] == {
        "error_type": "ImportError",
        "message": "native module is incompatible",
    }


def test_run_command_returns_nonzero_when_direct_run_does_not_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failed_direct_run(**_kwargs) -> bool:
        return False

    monkeypatch.setattr(main_module, "_run_direct_mode", failed_direct_run)
    monkeypatch.setattr(
        "aworld_cli.top_level_commands.run_cmd.bootstrap_runtime",
        lambda **_kwargs: None,
    )

    args = SimpleNamespace(
        task="test",
        agent="Aworld",
        skill=None,
        max_runs=None,
        max_cost=None,
        max_duration=None,
        completion_signal=None,
        completion_threshold=3,
        non_interactive=True,
        session_id=None,
        env_file=".env",
        remote_backend=None,
        agent_dir=None,
        agent_file=None,
        skill_path=None,
    )

    exit_code = RunTopLevelCommand().run(
        args,
        SimpleNamespace(argv=("aworld-cli", "run")),
    )

    assert exit_code == 1
