import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from aworld_cli import main as main_module
from aworld_cli.main import DirectRunOutcome, DirectRunStatus
from aworld_cli.top_level_commands.run_cmd import RunTopLevelCommand


def _failure_payload(stderr: str) -> dict:
    marker = "AWORLD_RUN_FAILURE="
    line = next(line for line in stderr.splitlines() if line.startswith(marker))
    return json.loads(line.removeprefix(marker))


def _marker_payload(stderr: str, marker: str) -> dict:
    line = next(line for line in stderr.splitlines() if line.startswith(marker))
    return json.loads(line.removeprefix(marker))


def test_typed_outcome_preserves_legacy_truth_value_contract() -> None:
    successful = DirectRunOutcome.from_summary(
        {},
        status=DirectRunStatus.SUCCEEDED,
    )
    failed_with_partial_summary = DirectRunOutcome.from_summary(
        {"results": [{"success": False}]},
        status=DirectRunStatus.TASK_FAILED,
    )

    assert bool(successful) is True
    assert bool(failed_with_partial_summary) is False


def test_task_failure_exit_code_is_opt_in_for_supervised_runtimes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kwargs = {
        "stage": "agent_execution",
        "error_code": "agent_task_failed",
        "agent_name": "Aworld",
        "status": DirectRunStatus.TASK_FAILED,
    }

    monkeypatch.delenv("AWORLD_TASK_FAILURE_EXIT_CODE", raising=False)
    assert main_module._direct_run_failure_outcome(**kwargs).process_exit_code == 1

    monkeypatch.setenv("AWORLD_TASK_FAILURE_EXIT_CODE", "64")
    assert main_module._direct_run_failure_outcome(**kwargs).process_exit_code == 64

    monkeypatch.setenv("AWORLD_TASK_FAILURE_EXIT_CODE", "125")
    assert main_module._direct_run_failure_outcome(**kwargs).process_exit_code == 1


@pytest.mark.asyncio
async def test_direct_run_reports_agent_load_failure_and_returns_typed_outcome(
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

    assert succeeded.status is DirectRunStatus.INFRASTRUCTURE_FAILED
    assert succeeded.process_exit_code == 1
    payload = _failure_payload(capsys.readouterr().err)
    assert payload == {
        "agent_name": "Aworld",
        "details": {"available_agent_count": 1},
        "error_code": "agent_not_found",
        "action_count": 0,
        "last_successful_checkpoint": None,
        "llm_call_count": 0,
        "schema_version": "aworld.run.failure.v1",
        "stage": "agent_load",
        "status": "failed",
        "tool_call_count": 0,
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

    assert succeeded.status is DirectRunStatus.INFRASTRUCTURE_FAILED
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

    assert succeeded.status is DirectRunStatus.INFRASTRUCTURE_FAILED
    payload = _failure_payload(capsys.readouterr().err)
    assert payload["error_code"] == "agent_load_failed"
    assert payload["stage"] == "agent_load"
    assert payload["details"] == {
        "error_type": "ImportError",
    }


def test_failure_marker_drops_paths_messages_and_unknown_diagnostics(
    capsys: pytest.CaptureFixture[str],
) -> None:
    main_module._emit_direct_run_failure(
        stage="agent_load",
        error_code="agent_load_failed",
        agent_name="Aworld",
        details={
            "message": "token=secret at /Users/private/plugin.py",
            "location": "/Users/private/plugin.py",
            "available_agents": ["private-agent"],
            "load_failures": [
                {
                    "error_type": "ImportError",
                    "message": "https://example.invalid/?token=secret",
                    "location": "/Users/private/plugin.py",
                }
            ],
        },
    )

    stderr = capsys.readouterr().err
    payload = _failure_payload(stderr)
    assert payload["details"] == {
        "available_agent_count": 1,
        "load_failure_count": 1,
        "load_failure_error_types": ["ImportError"],
    }
    assert "secret" not in stderr
    assert "/Users/private" not in stderr


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


def test_run_command_seeds_incomplete_atif_before_execution(tmp_path) -> None:
    output_path = tmp_path / "trajectory.json"
    args = SimpleNamespace(
        task="perform a long task",
        trajectory_output=str(output_path),
    )

    receipt = RunTopLevelCommand._write_initial_atif_checkpoint(
        args=args,
        agent_name="Aworld",
    )

    assert receipt.status.value == "persisted"
    trajectory = json.loads(output_path.read_text(encoding="utf-8"))
    assert trajectory["schema_version"] == "ATIF-v1.7"
    assert trajectory["steps"] == [
        {"step_id": 1, "source": "user", "message": "perform a long task"}
    ]
    aworld = trajectory["extra"]["aworld"]
    assert aworld["completion_state"] == "incomplete"
    assert aworld["trajectory_fidelity"] == "partial"
    assert aworld["run_outcome"]["semantic_status"] == "in_progress"


def test_run_command_writes_minimal_atif_for_pre_execution_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    outcome = DirectRunOutcome.from_summary(
        None,
        status=DirectRunStatus.INFRASTRUCTURE_FAILED,
        failure_record={
            "stage": "agent_load",
            "error_code": "agent_not_found",
        },
    )

    async def failed_direct_run(**_kwargs):
        return outcome

    monkeypatch.setattr(main_module, "_run_direct_mode", failed_direct_run)
    monkeypatch.setattr(
        "aworld_cli.top_level_commands.run_cmd.bootstrap_runtime",
        lambda **_kwargs: None,
    )
    output_path = tmp_path / "trajectory.json"
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
        emit_trajectory=False,
        trajectory_output=str(output_path),
    )

    exit_code = RunTopLevelCommand().run(
        args,
        SimpleNamespace(argv=("aworld-cli", "run")),
    )

    assert exit_code == 1
    trajectory = json.loads(output_path.read_text(encoding="utf-8"))
    assert trajectory["steps"] == [
        {"step_id": 1, "source": "user", "message": "test"}
    ]
    projection = trajectory["extra"]["aworld"]
    assert projection["completion_state"] == "incomplete"
    assert projection["trajectory_fidelity"] == "unavailable"
    stderr = capsys.readouterr().err
    assert _marker_payload(stderr, "AWORLD_ATIF_EXPORT=")["status"] == "persisted"
    assert _marker_payload(stderr, "AWORLD_RUN_OUTCOME=")["process_exit_code"] == 1


def test_run_command_finalizes_caller_deadline_as_budget_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    from aworld_cli.async_runtime import DirectRunDeadlineExceeded

    def deadline_exceeded(_coro):
        _coro.close()
        raise DirectRunDeadlineExceeded

    monkeypatch.setattr(
        "aworld_cli.top_level_commands.run_cmd.run_direct_async",
        deadline_exceeded,
    )
    monkeypatch.setattr(
        "aworld_cli.top_level_commands.run_cmd.bootstrap_runtime",
        lambda **_kwargs: None,
    )
    monkeypatch.setenv("AWORLD_TASK_FAILURE_EXIT_CODE", "64")
    trajectory_path = tmp_path / "trajectory.json"
    outcome_path = tmp_path / "outcome.json"
    args = SimpleNamespace(
        task="perform a long task",
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
        emit_trajectory=False,
        trajectory_output=str(trajectory_path),
        outcome_output=str(outcome_path),
    )

    exit_code = RunTopLevelCommand().run(
        args,
        SimpleNamespace(argv=("aworld-cli", "run")),
    )

    assert exit_code == 64
    trajectory = json.loads(trajectory_path.read_text(encoding="utf-8"))
    aworld = trajectory["extra"]["aworld"]
    assert aworld["completion_state"] == "incomplete"
    assert aworld["run_outcome"]["semantic_status"] == "task_failed"
    assert aworld["run_outcome"]["process_exit_code"] == 64
    assert aworld["run_outcome"]["failure"] == {
        "stage": "agent_execution",
        "error_code": "agent_budget_exhausted",
    }
    persisted = json.loads(outcome_path.read_text(encoding="utf-8"))
    assert {
        key: value for key, value in persisted.items() if key != "atif_export"
    } == aworld["run_outcome"]
    assert persisted["atif_export"]["status"] == "persisted"
    stderr = capsys.readouterr().err
    assert _marker_payload(stderr, "AWORLD_RUN_FAILURE=")["details"] == {
        "error_type": "DirectRunDeadlineExceeded",
        "phase": "task_deadline",
    }
    assert _marker_payload(stderr, "AWORLD_RUN_OUTCOME=") == persisted


def test_run_command_preserves_startup_watchdog_stage(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    from aworld_cli.async_runtime import DirectRunDeadlineExceeded

    def startup_deadline_exceeded(_coro):
        _coro.close()
        raise DirectRunDeadlineExceeded(
            stage="provider_start",
            phase="awaiting_first_provider_attempt",
        )

    monkeypatch.setattr(
        "aworld_cli.top_level_commands.run_cmd.run_direct_async",
        startup_deadline_exceeded,
    )
    monkeypatch.setattr(
        "aworld_cli.top_level_commands.run_cmd.bootstrap_runtime",
        lambda **_kwargs: None,
    )
    monkeypatch.setenv("AWORLD_TASK_FAILURE_EXIT_CODE", "64")
    trajectory_path = tmp_path / "trajectory.json"
    args = SimpleNamespace(
        task="stalled startup",
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
        emit_trajectory=False,
        trajectory_output=str(trajectory_path),
        outcome_output=None,
    )

    assert RunTopLevelCommand().run(
        args,
        SimpleNamespace(argv=("aworld-cli", "run")),
    ) == 1

    trajectory = json.loads(trajectory_path.read_text(encoding="utf-8"))
    failure = trajectory["extra"]["aworld"]["run_outcome"]["failure"]
    assert failure == {
        "stage": "provider_start",
        "error_code": "provider_start_timeout",
    }
    assert trajectory["extra"]["aworld"]["run_outcome"]["semantic_status"] == (
        "infrastructure_failed"
    )
    failure_marker = _marker_payload(
        capsys.readouterr().err,
        "AWORLD_RUN_FAILURE=",
    )
    assert failure_marker["details"] == {
        "error_type": "DirectRunDeadlineExceeded",
        "phase": "awaiting_first_provider_attempt",
    }


def test_run_command_writes_partial_atif_before_returning_task_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    summary = {
        "results": [
            {
                "iteration": 1,
                "response": "failed",
                "success": False,
                "completed": False,
                "trajectory_capture_mode": "task_response",
                "trajectory": [
                    {
                        "meta": {"session_id": "session-1", "step": 1},
                        "action": {
                            "content": "Attempted the task.",
                            "tool_calls": [
                                {
                                    "id": "tool-1",
                                    "function": {
                                        "name": "execute_command",
                                        "arguments": {"command": "false"},
                                    },
                                }
                            ],
                        },
                    }
                ],
                "llm_calls": [
                    {"request_id": "request-1"},
                    {"request_id": "request-2"},
                ],
                "trajectory_build_result": {
                    "fidelity": "partial",
                    "llm_call_count": 2,
                    "tool_call_count": 1,
                },
            }
        ]
    }
    outcome = DirectRunOutcome.from_summary(
        summary,
        status=DirectRunStatus.TASK_FAILED,
        failure_record={
            "schema_version": "aworld.run.failure.v1",
            "status": "failed",
            "stage": "agent_execution",
            "error_code": "agent_task_failed",
            "agent_name": "Aworld",
        },
    )

    async def failed_direct_run(**_kwargs):
        return outcome

    monkeypatch.setattr(main_module, "_run_direct_mode", failed_direct_run)
    monkeypatch.setattr(
        "aworld_cli.top_level_commands.run_cmd.bootstrap_runtime",
        lambda **_kwargs: None,
    )
    output_path = tmp_path / "trajectory.json"
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
        emit_trajectory=False,
        trajectory_output=str(output_path),
    )

    exit_code = RunTopLevelCommand().run(
        args,
        SimpleNamespace(argv=("aworld-cli", "run")),
    )

    assert exit_code == 1
    trajectory = json.loads(output_path.read_text(encoding="utf-8"))
    assert trajectory["extra"]["aworld"]["run_outcome"]["semantic_status"] == "task_failed"
    assert trajectory["extra"]["aworld"]["llm_call_count"] == 2
    assert trajectory["extra"]["aworld"]["tool_call_count"] == 1
    stderr = capsys.readouterr().err
    export = _marker_payload(stderr, "AWORLD_ATIF_EXPORT=")
    assert export["status"] == "persisted"
    final_outcome = _marker_payload(stderr, "AWORLD_RUN_OUTCOME=")
    assert final_outcome["semantic_status"] == "task_failed"
    assert final_outcome["atif_export"]["status"] == "persisted"


def test_run_command_export_failure_forces_infrastructure_exit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    outcome = DirectRunOutcome.from_summary(
        {
            "results": [
                {
                    "success": True,
                    "completed": True,
                    "trajectory_capture_mode": "task_response",
                    "trajectory": [{"meta": {"step": 1}, "action": {"content": "done"}}],
                    "llm_calls": [{"request_id": "request-1"}],
                }
            ]
        },
        status=DirectRunStatus.TASK_FAILED,
        failure_record={
            "stage": "agent_execution",
            "error_code": "agent_task_failed",
        },
        process_exit_code=64,
    )

    async def successful_direct_run(**_kwargs):
        return outcome

    def fail_write(*_args, **_kwargs):
        raise OSError("private path detail")

    monkeypatch.setattr(main_module, "_run_direct_mode", successful_direct_run)
    monkeypatch.setattr(
        "aworld_cli.top_level_commands.run_cmd.bootstrap_runtime",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr("aworld_cli.atif.write_atif_trajectory", fail_write)
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
        emit_trajectory=False,
        trajectory_output=str(tmp_path / "trajectory.json"),
    )

    exit_code = RunTopLevelCommand().run(
        args,
        SimpleNamespace(argv=("aworld-cli", "run")),
    )

    assert exit_code == 1
    stderr = capsys.readouterr().err
    export = _marker_payload(stderr, "AWORLD_ATIF_EXPORT=")
    assert export["status"] == "failed"
    assert export["error_type"] == "OSError"
    assert "private path detail" not in stderr
    final_outcome = _marker_payload(stderr, "AWORLD_RUN_OUTCOME=")
    assert final_outcome["semantic_status"] == "infrastructure_failed"
    assert final_outcome["process_exit_code"] == 1
    assert final_outcome["failure"] == {
        "stage": "orchestration",
        "error_code": "atif_export_failed",
    }
    assert final_outcome["atif_export"]["status"] == "failed"


def test_outcome_sidecar_failure_drops_reserved_task_failure_exit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    outcome = DirectRunOutcome.from_summary(
        {
            "results": [
                {
                    "success": False,
                    "completed": False,
                    "trajectory_capture_mode": "task_response",
                    "trajectory": [
                        {"meta": {"step": 1}, "action": {"content": "failed"}}
                    ],
                    "llm_calls": [{"request_id": "request-1"}],
                }
            ]
        },
        status=DirectRunStatus.TASK_FAILED,
        failure_record={
            "stage": "agent_execution",
            "error_code": "agent_task_failed",
        },
        process_exit_code=64,
    )

    def fail_sidecar(*_args, **_kwargs):
        raise OSError("private path detail")

    monkeypatch.setattr(
        "aworld_cli.top_level_commands.run_cmd._write_outcome_sidecar",
        fail_sidecar,
    )
    args = SimpleNamespace(
        task="test",
        emit_trajectory=False,
        trajectory_output=str(tmp_path / "trajectory.json"),
        outcome_output=str(tmp_path / "outcome.json"),
    )

    exit_code = RunTopLevelCommand._finalize_outcome(
        args=args,
        agent_name="Aworld",
        outcome=outcome,
    )

    assert exit_code == 1
    final_outcome = _marker_payload(
        capsys.readouterr().err,
        "AWORLD_RUN_OUTCOME=",
    )
    assert final_outcome["semantic_status"] == "infrastructure_failed"
    assert final_outcome["process_exit_code"] == 1
    assert final_outcome["failure"]["error_code"] == "direct_run_exception"


def test_summary_payload_preserves_zero_step_task_response_evidence() -> None:
    payload = main_module._trajectory_payload_from_direct_run_summary(
        {
            "results": [
                {
                    "success": False,
                    "trajectory_capture_mode": "task_response",
                    "trajectory": [],
                    "llm_calls": [
                        {"request_id": "request-1"},
                        {"request_id": "request-2"},
                    ],
                    "trajectory_build_result": {
                        "fidelity": "partial",
                        "llm_call_count": 2,
                        "tool_call_count": 0,
                    },
                }
            ]
        },
        prompt="test",
        agent_name="Aworld",
    )

    assert payload["trajectory"] == []
    assert len(payload["llm_calls"]) == 2
    assert payload["trajectory_build_results"][0]["llm_call_count"] == 2
    assert payload["trajectory_capture_mode"] == "task_response"


def test_direct_run_outcome_uses_build_counts_and_last_successful_checkpoint() -> None:
    outcome = DirectRunOutcome.from_summary(
        {
            "results": [
                {
                    "iteration": 2,
                    "success": False,
                    "trajectory": [{"meta": {"step": 7}, "action": {"content": "working"}}],
                    "llm_calls": [{"request_id": "only-materialized-journal-row"}],
                    "trajectory_build_result": {
                        "status": "partial",
                        "fidelity": "partial",
                        "task_id": "task-1",
                        "session_id": "session-1",
                        "source_high_watermark": "event-9",
                        "completed_updates": 3,
                        "persisted_items": 1,
                        "source_agent_messages": 4,
                        "llm_call_count": 5,
                        "tool_call_count": 2,
                        "trajectory_checksum": "sha256:" + "a" * 64,
                    },
                }
            ]
        },
        status=DirectRunStatus.TASK_FAILED,
    )

    assert outcome.llm_call_count == 5
    assert outcome.tool_call_count == 2
    assert outcome.action_count == 4
    assert outcome.trajectory_fidelity == "partial"
    assert outcome.last_successful_checkpoint == {
        "kind": "trajectory_build",
        "result_iteration": 2,
        "task_id": "task-1",
        "session_id": "session-1",
        "source_high_watermark": "event-9",
        "completed_updates": 3,
        "persisted_items": 1,
        "trajectory_checksum": "sha256:" + "a" * 64,
    }


def test_live_provider_evidence_ignores_pre_provider_stream_state() -> None:
    calls: list[dict] = []

    class Context:
        task_id = "task-current"

        @staticmethod
        def get_llm_calls():
            return calls

    executor = SimpleNamespace(context=Context())
    cursor = main_module._live_provider_evidence_cursor(executor)
    calls.append(
        {
            "task_id": "task-current",
            "capture_stage": "compiled",
            "provider_invoked": False,
        }
    )
    # A StepOutput can exist before async_pre_run reaches the provider.  It
    # must not be accepted as provider-start evidence.
    executor._aworld_cli_execution_evidence_sequence = 99

    assert not main_module._new_live_provider_evidence(executor, cursor=cursor)

    calls[0].update(
        {
            "provider_invoked": True,
            "provider_attempt_status": "attempted",
        }
    )
    assert main_module._new_live_provider_evidence(executor, cursor=cursor)


def test_live_provider_evidence_reads_shared_deep_copy_fan_in() -> None:
    from aworld.core.context.amni import ApplicationContext

    context = ApplicationContext.create(
        task_id="task-current",
        task_content="test",
    )
    executor = SimpleNamespace(context=context)
    cursor = main_module._live_provider_evidence_cursor(executor)
    transport_copy = context.deep_copy()
    transport_copy.append_llm_call(
        {
            "task_id": "task-current",
            "request_id": "request-1",
            "provider_invoked": True,
            "provider_attempt_status": "attempted",
        }
    )

    assert context.get_llm_calls() == []
    assert main_module._new_live_provider_evidence(executor, cursor=cursor)


@pytest.mark.asyncio
async def test_deep_copy_provider_attempt_disarms_startup_watchdog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aworld.core.context.amni import ApplicationContext

    monkeypatch.setenv("AWORLD_DIRECT_RUN_FIRST_PROVIDER_TIMEOUT_SECONDS", "0.02")
    monkeypatch.setenv("AWORLD_TASK_DEADLINE_EPOCH_SECONDS", str(time.time() + 1))
    monkeypatch.setenv("AWORLD_TERMINAL_COMPLETION_RESERVE_SECONDS", "0")
    context = ApplicationContext.create(
        task_id="task-current",
        task_content="test",
    )
    executor = SimpleNamespace(context=context)
    cursor = main_module._live_provider_evidence_cursor(executor)

    async def provider_work() -> str:
        await asyncio.sleep(0.005)
        transport_copy = context.deep_copy()
        transport_copy.append_llm_call(
            {
                "task_id": "task-current",
                "request_id": "request-1",
                "provider_invoked": True,
                "provider_attempt_status": "attempted",
            }
        )
        await asyncio.sleep(0.05)
        return "complete"

    assert await main_module.run_with_first_provider_start_watchdog(
        provider_work(),
        evidence_observed=lambda: main_module._new_live_provider_evidence(
            executor,
            cursor=cursor,
        ),
    ) == "complete"


def test_live_provider_evidence_probe_errors_reach_fail_open_boundary(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class BrokenContext:
        task_id = "task-current"

        @staticmethod
        def get_reconciled_llm_calls():
            raise RuntimeError("fan-in unavailable")

    with pytest.raises(RuntimeError, match="fan-in unavailable"):
        main_module._live_provider_evidence_cursor(
            SimpleNamespace(context=BrokenContext())
        )

    assert main_module._initial_live_provider_evidence_cursor(
        SimpleNamespace(context=BrokenContext())
    ) is None
    assert "initial provider-evidence probe failed open" in caplog.text


@pytest.mark.asyncio
async def test_direct_run_defaults_to_one_complete_agent_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_agent = SimpleNamespace(name="Aworld")
    executor = SimpleNamespace()

    class DummyRuntime:
        def __init__(self, *args, **kwargs) -> None:
            self._scheduler = None

        async def _load_agents(self):
            return [selected_agent]

        def _bind_scheduler_default_agent(self, _agent_name: str) -> None:
            pass

        async def _create_executor(self, _agent):
            return executor

        def _restore_executor_session(self, *_args, **_kwargs) -> None:
            pass

    captured = {}

    class DummyContinuousExecutor:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def run_continuous(self, **kwargs):
            captured.update(kwargs)
            return {"results": []}

    monkeypatch.setattr(main_module, "CliRuntime", DummyRuntime)
    monkeypatch.setattr(main_module, "ContinuousExecutor", DummyContinuousExecutor)
    monkeypatch.setattr("aworld.core.scheduler.get_scheduler", lambda: object())

    await main_module._run_direct_mode(prompt="test", agent_name="Aworld")

    assert captured["max_runs"] == 1


@pytest.mark.asyncio
async def test_direct_run_watchdog_covers_pre_provider_executor_stall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aworld_cli.async_runtime import DirectRunDeadlineExceeded

    selected_agent = SimpleNamespace(name="Aworld")
    executor = SimpleNamespace(context=None)
    release = asyncio.Event()

    class DummyRuntime:
        def __init__(self, *args, **kwargs) -> None:
            self._scheduler = None

        async def _load_agents(self):
            return [selected_agent]

        def _bind_scheduler_default_agent(self, _agent_name: str) -> None:
            pass

        async def _create_executor(self, _agent):
            return executor

        def _restore_executor_session(self, *_args, **_kwargs) -> None:
            pass

    class DummyContinuousExecutor:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def run_continuous(self, **_kwargs):
            await release.wait()

    monkeypatch.setattr(main_module, "CliRuntime", DummyRuntime)
    monkeypatch.setattr(main_module, "ContinuousExecutor", DummyContinuousExecutor)
    monkeypatch.setattr("aworld.core.scheduler.get_scheduler", lambda: object())
    monkeypatch.setenv(
        "AWORLD_DIRECT_RUN_FIRST_PROVIDER_TIMEOUT_SECONDS",
        "0.02",
    )
    monkeypatch.setenv(
        "AWORLD_TASK_DEADLINE_EPOCH_SECONDS",
        str(time.time() + 1),
    )
    monkeypatch.setenv("AWORLD_TERMINAL_COMPLETION_RESERVE_SECONDS", "0")

    with pytest.raises(DirectRunDeadlineExceeded) as raised:
        await main_module._run_direct_mode(
            prompt="test",
            agent_name="Aworld",
            non_interactive=True,
        )
    assert raised.value.stage == "provider_start"
    assert raised.value.phase == "awaiting_first_provider_attempt"
    release.set()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_noninteractive_aworld_retries_zero_provider_capture_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_agent = SimpleNamespace(name="Aworld")
    executor = SimpleNamespace()

    class DummyRuntime:
        def __init__(self, *args, **kwargs) -> None:
            self._scheduler = None

        async def _load_agents(self):
            return [selected_agent]

        def _bind_scheduler_default_agent(self, _agent_name: str) -> None:
            pass

        async def _create_executor(self, _agent):
            return executor

        def _restore_executor_session(self, *_args, **_kwargs) -> None:
            pass

    calls = 0

    class DummyContinuousExecutor:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def run_continuous(self, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return {"results": [{"response": "", "success": True}]}
            return {
                "results": [
                    {
                        "response": "done",
                        "success": True,
                        "trajectory": [{"meta": {"step": 1}}],
                        "llm_calls": [{"request_id": "request-1"}],
                    }
                ]
            }

    monkeypatch.setattr(main_module, "CliRuntime", DummyRuntime)
    monkeypatch.setattr(main_module, "ContinuousExecutor", DummyContinuousExecutor)
    monkeypatch.setattr("aworld.core.scheduler.get_scheduler", lambda: object())

    summary = await main_module._run_direct_mode(
        prompt="test",
        agent_name="Aworld",
        non_interactive=True,
    )

    assert calls == 2
    assert summary["results"][0]["response"] == "done"


@pytest.mark.asyncio
async def test_noninteractive_aworld_fails_after_zero_provider_capture_retries(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    selected_agent = SimpleNamespace(name="Aworld")
    executor = SimpleNamespace()

    class DummyRuntime:
        def __init__(self, *args, **kwargs) -> None:
            self._scheduler = None

        async def _load_agents(self):
            return [selected_agent]

        def _bind_scheduler_default_agent(self, _agent_name: str) -> None:
            pass

        async def _create_executor(self, _agent):
            return executor

        def _restore_executor_session(self, *_args, **_kwargs) -> None:
            pass

    calls = 0

    class DummyContinuousExecutor:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def run_continuous(self, **_kwargs):
            nonlocal calls
            calls += 1
            return {"results": [{"response": "", "success": True}]}

    monkeypatch.setattr(main_module, "CliRuntime", DummyRuntime)
    monkeypatch.setattr(main_module, "ContinuousExecutor", DummyContinuousExecutor)
    monkeypatch.setattr("aworld.core.scheduler.get_scheduler", lambda: object())

    succeeded = await main_module._run_direct_mode(
        prompt="test",
        agent_name="Aworld",
        non_interactive=True,
    )

    assert succeeded.status is DirectRunStatus.INFRASTRUCTURE_FAILED
    assert calls == 2
    payload = _failure_payload(capsys.readouterr().err)
    assert payload["stage"] == "provider_start"
    assert payload["error_code"] == "provider_call_not_captured"
    assert payload["llm_call_count"] == 0
    assert payload["details"] == {
        "attempts": 2,
        "trajectory_capture_mode": "summary_synthetic",
    }


@pytest.mark.asyncio
async def test_noninteractive_aworld_propagates_terminal_task_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    selected_agent = SimpleNamespace(name="Aworld")
    executor = SimpleNamespace()

    class DummyRuntime:
        def __init__(self, *args, **kwargs) -> None:
            self._scheduler = None

        async def _load_agents(self):
            return [selected_agent]

        def _bind_scheduler_default_agent(self, _agent_name: str) -> None:
            pass

        async def _create_executor(self, _agent):
            return executor

        def _restore_executor_session(self, *_args, **_kwargs) -> None:
            pass

    class DummyContinuousExecutor:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def run_continuous(self, **_kwargs):
            return {
                "results": [
                    {
                        "response": "Task fail, cause: completion contract",
                        "success": False,
                        "trajectory": [{"meta": {"step": 1}}],
                        "failure_origin": "task",
                        "failure_code": "completion_contract_unsatisfied",
                    }
                ]
            }

    monkeypatch.setattr(main_module, "CliRuntime", DummyRuntime)
    monkeypatch.setattr(main_module, "ContinuousExecutor", DummyContinuousExecutor)
    monkeypatch.setattr("aworld.core.scheduler.get_scheduler", lambda: object())
    monkeypatch.setenv("AWORLD_TOOL_SURFACE_PROFILE", "one_shot")

    succeeded = await main_module._run_direct_mode(
        prompt="test",
        agent_name="Aworld",
        non_interactive=True,
    )

    assert succeeded.status is DirectRunStatus.TASK_FAILED
    payload = _failure_payload(capsys.readouterr().err)
    assert payload["stage"] == "agent_execution"
    assert payload["error_code"] == "agent_task_failed"
    assert payload["details"] == {"provider_evidence": True}


@pytest.mark.asyncio
async def test_one_shot_aworld_fails_closed_for_untyped_agent_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    selected_agent = SimpleNamespace(name="Aworld")
    executor = SimpleNamespace()

    class DummyRuntime:
        def __init__(self, *args, **kwargs) -> None:
            self._scheduler = None

        async def _load_agents(self):
            return [selected_agent]

        def _bind_scheduler_default_agent(self, _agent_name: str) -> None:
            pass

        async def _create_executor(self, _agent):
            return executor

        def _restore_executor_session(self, *_args, **_kwargs) -> None:
            pass

    class DummyContinuousExecutor:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def run_continuous(self, **_kwargs):
            return {
                "results": [
                    {
                        "response": "legacy failure",
                        "success": False,
                        "trajectory": [{"meta": {"step": 1}}],
                        "llm_calls": [{"request_id": "request-1"}],
                    }
                ]
            }

    monkeypatch.setattr(main_module, "CliRuntime", DummyRuntime)
    monkeypatch.setattr(main_module, "ContinuousExecutor", DummyContinuousExecutor)
    monkeypatch.setattr("aworld.core.scheduler.get_scheduler", lambda: object())
    monkeypatch.setenv("AWORLD_TOOL_SURFACE_PROFILE", "one_shot")

    outcome = await main_module._run_direct_mode(
        prompt="test",
        agent_name="Aworld",
        non_interactive=True,
    )

    assert outcome.status is DirectRunStatus.INFRASTRUCTURE_FAILED
    payload = _failure_payload(capsys.readouterr().err)
    assert payload["error_code"] == "agent_execution_untyped_failure"
    assert payload["details"] == {"provider_evidence": True}


@pytest.mark.asyncio
async def test_noninteractive_aworld_does_not_downgrade_typed_infrastructure_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    selected_agent = SimpleNamespace(name="Aworld")
    executor = SimpleNamespace()

    class DummyRuntime:
        def __init__(self, *args, **kwargs) -> None:
            self._scheduler = None

        async def _load_agents(self):
            return [selected_agent]

        def _bind_scheduler_default_agent(self, _agent_name: str) -> None:
            pass

        async def _create_executor(self, _agent):
            return executor

        def _restore_executor_session(self, *_args, **_kwargs) -> None:
            pass

    class DummyContinuousExecutor:
        calls = 0

        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def run_continuous(self, **_kwargs):
            type(self).calls += 1
            return {
                "results": [
                    {
                        "response": "redacted failure",
                        "success": False,
                        "trajectory": [{"meta": {"step": 1}}],
                        "llm_calls": [{"request_id": "request-1"}],
                        "failure_origin": "infrastructure",
                        "failure_code": "provider_timeout",
                        "error_type": "GenerationBudgetExceeded",
                    }
                ]
            }

    monkeypatch.setattr(main_module, "CliRuntime", DummyRuntime)
    monkeypatch.setattr(main_module, "ContinuousExecutor", DummyContinuousExecutor)
    monkeypatch.setattr("aworld.core.scheduler.get_scheduler", lambda: object())

    outcome = await main_module._run_direct_mode(
        prompt="test",
        agent_name="Aworld",
        non_interactive=True,
    )

    assert outcome.status is DirectRunStatus.INFRASTRUCTURE_FAILED
    assert DummyContinuousExecutor.calls == 1
    payload = _failure_payload(capsys.readouterr().err)
    assert payload["stage"] == "agent_execution"
    assert payload["error_code"] == "agent_execution_infrastructure_failed"
    assert payload["details"] == {
        "error_type": "GenerationBudgetExceeded",
        "failure_code": "provider_timeout",
    }


def test_one_shot_failure_classification_requires_explicit_task_origin() -> None:
    untyped = {
        "results": [
            {
                "success": False,
                "trajectory": [{"meta": {"step": 1}}],
                "llm_calls": [{"request_id": "request-1"}],
            }
        ]
    }
    typed = {
        "results": [
            {
                **untyped["results"][0],
                "failure_origin": "task",
                "failure_code": "completion_contract_unsatisfied",
            }
        ]
    }

    assert main_module._direct_run_has_explicit_task_failure(untyped) is False
    assert main_module._direct_run_has_explicit_task_failure(typed) is True
    assert main_module._direct_run_has_explicit_task_failure(
        {"results": [typed["results"][0], "malformed"]}
    ) is False


def test_cancel_detector_accepts_task_response_control_plane() -> None:
    assert main_module._direct_run_cancelled(
        {
            "results": [
                {
                    "success": False,
                    "failure_origin": "cancelled",
                    "task_status": "interrupted",
                }
            ]
        }
    )


@pytest.mark.asyncio
async def test_direct_run_cancellation_recovers_last_task_response_evidence(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    selected_agent = SimpleNamespace(name="Aworld")
    task_response = SimpleNamespace(
        success=False,
        status="cancelled",
        trajectory=[],
        llm_calls=[{"request_id": f"request-{index}"} for index in range(3)],
        trajectory_build_result=SimpleNamespace(
            to_dict=lambda: {
                "status": "partial",
                "fidelity": "partial",
                "llm_call_count": 3,
                "tool_call_count": 0,
                "source_agent_messages": 1,
                "completed_updates": 1,
                "persisted_items": 0,
                "source_high_watermark": "event-3",
                "task_id": "task-1",
            }
        ),
        trajectory_delivery_receipt=None,
    )
    executor = SimpleNamespace(last_task_response=task_response)

    class DummyRuntime:
        def __init__(self, *args, **kwargs) -> None:
            self._scheduler = None

        async def _load_agents(self):
            return [selected_agent]

        def _bind_scheduler_default_agent(self, _agent_name: str) -> None:
            pass

        async def _create_executor(self, _agent):
            return executor

        def _restore_executor_session(self, *_args, **_kwargs) -> None:
            pass

    class DummyContinuousExecutor:
        _attach_task_response_evidence = staticmethod(
            main_module.ContinuousExecutor._attach_task_response_evidence
        )

        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def run_continuous(self, **_kwargs):
            raise asyncio.CancelledError

    monkeypatch.setattr(main_module, "CliRuntime", DummyRuntime)
    monkeypatch.setattr(main_module, "ContinuousExecutor", DummyContinuousExecutor)
    monkeypatch.setattr("aworld.core.scheduler.get_scheduler", lambda: object())

    outcome = await main_module._run_direct_mode(
        prompt="test",
        agent_name="Aworld",
        non_interactive=True,
    )

    assert outcome.status is DirectRunStatus.CANCELLED
    assert outcome.process_exit_code == 130
    assert outcome.llm_call_count == 3
    assert outcome.action_count == 1
    assert outcome.trajectory_fidelity == "partial"
    assert outcome.last_successful_checkpoint["source_high_watermark"] == "event-3"
    payload = _failure_payload(capsys.readouterr().err)
    assert payload["error_code"] == "direct_run_cancelled"
    assert payload["llm_call_count"] == 3


@pytest.mark.asyncio
async def test_direct_run_preserves_executor_cancellation_without_retrying(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    selected_agent = SimpleNamespace(name="Aworld")
    executor = SimpleNamespace(last_task_response=None)

    class DummyRuntime:
        def __init__(self, *args, **kwargs) -> None:
            self._scheduler = None

        async def _load_agents(self):
            return [selected_agent]

        def _bind_scheduler_default_agent(self, _agent_name: str) -> None:
            pass

        async def _create_executor(self, _agent):
            return executor

        def _restore_executor_session(self, *_args, **_kwargs) -> None:
            pass

    class DummyContinuousExecutor:
        calls = 0

        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def run_continuous(self, **_kwargs):
            type(self).calls += 1
            return {
                "total_runs": 1,
                "successful_runs": 0,
                "results": [
                    {
                        "iteration": 1,
                        "response": "partial model output",
                        "success": False,
                        "completed": False,
                        "termination_status": "cancelled",
                    }
                ],
            }

    monkeypatch.setattr(main_module, "CliRuntime", DummyRuntime)
    monkeypatch.setattr(main_module, "ContinuousExecutor", DummyContinuousExecutor)
    monkeypatch.setattr("aworld.core.scheduler.get_scheduler", lambda: object())

    outcome = await main_module._run_direct_mode(
        prompt="test",
        agent_name="Aworld",
        non_interactive=True,
    )

    assert outcome.status is DirectRunStatus.CANCELLED
    assert outcome.process_exit_code == 130
    assert DummyContinuousExecutor.calls == 1
    payload = _failure_payload(capsys.readouterr().err)
    assert payload["error_code"] == "direct_run_cancelled"
