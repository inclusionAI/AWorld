# coding: utf-8
# Copyright (c) 2026 inclusionAI.
"""Errors raised before admission or while observing an existing execution."""


class SessionNotFoundError(LookupError):
    """Loading an unknown session never creates a replacement session."""


class RunNotFoundError(LookupError):
    """No retained run exists with the requested identity."""


class SessionBusyError(RuntimeError):
    """Admission was rejected without allocating a run or changing history."""

    def __init__(self, session_id: str, active_run_id: str) -> None:
        self.session_id = session_id
        self.active_run_id = active_run_id
        super().__init__(f"Session {session_id} already has active run {active_run_id}")


class InputRejectedError(ValueError):
    """The configured executor rejected input before execution admission."""


class EventHistoryUnavailableError(LookupError):
    """The requested replay cannot be supplied without losing events."""


class RunInactiveError(RuntimeError):
    """A stopped execution tried to use its context or publish new output."""
