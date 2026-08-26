from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

import pytest

from aworld.cloud.errors import (
    CloudErrorCode,
    InvalidTransitionError,
    WorkspaceBusyError,
)
from aworld.cloud.models import (
    ACTIVE_RUN_STATES,
    TERMINAL_RUN_STATES,
    Run,
    RunId,
    RunState,
    Workspace,
    WorkspaceId,
    WorkspaceState,
    allowed_run_transitions,
    allowed_workspace_transitions,
    as_utc,
    create_retry_run,
    ensure_workspace_accepts_run,
    format_utc_timestamp,
    parse_utc_timestamp,
    transition_run,
    transition_workspace,
    utc_now,
)

NOW = datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc)


def _workspace(state: WorkspaceState = WorkspaceState.READY) -> Workspace:
    return Workspace(
        id=WorkspaceId("workspace-1"),
        name="Cloud workspace",
        profile_name="aworld-development",
        state=state,
        revision=1,
        runtime_image="registry.example/codex@sha256:abc",
        writable_repo_path=Path("/srv/aworld/workspaces/workspace-1"),
        codex_home_path=Path("/srv/aworld/codex/workspace-1"),
        workdir=PurePosixPath("/workspace/aworld"),
        created_at=NOW,
        updated_at=NOW,
    )


def _run(state: RunState = RunState.QUEUED, *, run_id: str = "run-1") -> Run:
    terminal = state in TERMINAL_RUN_STATES
    return Run(
        id=RunId(run_id),
        workspace_id=WorkspaceId("workspace-1"),
        state=state,
        revision=0,
        attempt=1,
        task="Update the documentation",
        created_at=NOW,
        started_at=NOW if state not in {RunState.QUEUED, RunState.STARTING} else None,
        finished_at=NOW + timedelta(seconds=1) if terminal else None,
    )


def test_timestamp_helpers_always_return_utc_aware_values() -> None:
    local_offset = timezone(timedelta(hours=8))
    normalized = as_utc(datetime(2026, 8, 20, 16, 0, tzinfo=local_offset))

    assert normalized == NOW
    assert normalized.tzinfo is timezone.utc
    assert format_utc_timestamp(normalized) == "2026-08-20T08:00:00+00:00"
    assert parse_utc_timestamp("2026-08-20T08:00:00Z") == NOW
    assert utc_now().utcoffset() == timedelta(0)


def test_timestamp_helpers_reject_naive_values() -> None:
    with pytest.raises(ValueError, match="UTC offset"):
        as_utc(NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="UTC offset"):
        parse_utc_timestamp("2026-08-20T08:00:00")


def test_domain_records_are_frozen_and_normalize_timestamps() -> None:
    workspace = _workspace()

    with pytest.raises(FrozenInstanceError):
        workspace.state = WorkspaceState.BUSY  # type: ignore[misc]
    assert workspace.created_at.tzinfo is timezone.utc


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        (WorkspaceState.CREATING, {WorkspaceState.READY, WorkspaceState.FAILED}),
        (
            WorkspaceState.READY,
            {WorkspaceState.BUSY, WorkspaceState.RELEASING, WorkspaceState.FAILED},
        ),
        (WorkspaceState.BUSY, {WorkspaceState.READY, WorkspaceState.FAILED}),
        (
            WorkspaceState.RELEASING,
            {WorkspaceState.RELEASED, WorkspaceState.FAILED},
        ),
        (WorkspaceState.RELEASED, set()),
        (WorkspaceState.FAILED, set()),
    ],
)
def test_workspace_state_machine_is_explicit(
    current: WorkspaceState,
    expected: set[WorkspaceState],
) -> None:
    assert allowed_workspace_transitions(current) == expected


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        (RunState.QUEUED, {RunState.STARTING, RunState.CANCELLED}),
        (RunState.STARTING, {RunState.RUNNING, RunState.CANCELLING, RunState.FAILED}),
        (RunState.RUNNING, {RunState.CANCELLING, RunState.SUCCEEDED, RunState.FAILED}),
        (RunState.CANCELLING, {RunState.CANCELLED, RunState.FAILED}),
        (RunState.SUCCEEDED, set()),
        (RunState.FAILED, set()),
        (RunState.CANCELLED, set()),
    ],
)
def test_run_state_machine_is_explicit(
    current: RunState, expected: set[RunState]
) -> None:
    assert allowed_run_transitions(current) == expected


def test_transitions_return_new_revisions_with_actual_timestamps() -> None:
    starting = transition_run(_run(), RunState.STARTING, at=NOW + timedelta(seconds=1))
    running = transition_run(starting, RunState.RUNNING, at=NOW + timedelta(seconds=2))
    succeeded = transition_run(
        running,
        RunState.SUCCEEDED,
        at=NOW + timedelta(seconds=3),
        exit_code=0,
    )

    assert starting.revision == 1
    assert running.started_at == NOW + timedelta(seconds=2)
    assert succeeded.finished_at == NOW + timedelta(seconds=3)
    assert succeeded.exit_code == 0
    assert succeeded.revision == 3


@pytest.mark.parametrize(
    "terminal", sorted(TERMINAL_RUN_STATES, key=lambda state: state.value)
)
def test_terminal_runs_cannot_transition(terminal: RunState) -> None:
    run = _run(terminal)

    for target in RunState:
        with pytest.raises(InvalidTransitionError) as raised:
            transition_run(run, target, at=NOW + timedelta(seconds=2))
        assert raised.value.code is CloudErrorCode.INVALID_TRANSITION


def test_workspace_release_is_terminal_and_records_release_time() -> None:
    releasing = transition_workspace(_workspace(), WorkspaceState.RELEASING, at=NOW)
    released = transition_workspace(
        releasing,
        WorkspaceState.RELEASED,
        at=NOW + timedelta(seconds=1),
    )

    assert released.released_at == NOW + timedelta(seconds=1)
    with pytest.raises(InvalidTransitionError):
        transition_workspace(
            released, WorkspaceState.READY, at=NOW + timedelta(seconds=2)
        )


def test_retry_creates_a_new_queued_attempt_without_mutating_source() -> None:
    starting = transition_run(_run(), RunState.STARTING, at=NOW)
    failed = transition_run(
        starting,
        RunState.FAILED,
        at=NOW + timedelta(seconds=1),
        error_code="executor_failed",
    )

    retry = create_retry_run(
        failed,
        run_id=RunId("run-2"),
        created_at=NOW + timedelta(seconds=2),
    )

    assert failed.id == RunId("run-1")
    assert failed.state is RunState.FAILED
    assert retry.id == RunId("run-2")
    assert retry.state is RunState.QUEUED
    assert retry.attempt == 2
    assert retry.retry_of_run_id == failed.id
    assert retry.task == failed.task


def test_retry_rejects_non_failed_source() -> None:
    with pytest.raises(InvalidTransitionError) as raised:
        create_retry_run(_run(), run_id=RunId("run-2"), created_at=NOW)
    assert raised.value.code is CloudErrorCode.INVALID_TRANSITION


def test_one_active_run_per_workspace_invariant() -> None:
    active = _run(RunState.RUNNING)

    with pytest.raises(WorkspaceBusyError) as raised:
        ensure_workspace_accepts_run(_workspace(), [active])
    assert raised.value.code is CloudErrorCode.WORKSPACE_BUSY
    assert active.state in ACTIVE_RUN_STATES


def test_multiple_active_records_are_rejected_as_an_invariant_violation() -> None:
    with pytest.raises(WorkspaceBusyError):
        ensure_workspace_accepts_run(
            _workspace(),
            [
                _run(RunState.STARTING, run_id="run-1"),
                _run(RunState.RUNNING, run_id="run-2"),
            ],
        )


def test_ready_workspace_accepts_a_run_when_prior_runs_are_terminal() -> None:
    ensure_workspace_accepts_run(_workspace(), [_run(RunState.SUCCEEDED)])


def test_busy_workspace_rejects_submission_even_without_loaded_run_records() -> None:
    with pytest.raises(WorkspaceBusyError):
        ensure_workspace_accepts_run(_workspace(WorkspaceState.BUSY), [])
