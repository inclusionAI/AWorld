# coding: utf-8
# Copyright (c) 2026 inclusionAI.
"""Session-first contracts for the AWorld 1.0 in-process kernel."""

from __future__ import annotations

from typing import AsyncIterator, Callable, Protocol

from aworld.core.context.simple import Context, ContextPolicy, ContextStorage as HistoryStore

from .models import RunEvent, RunOptions, RunResult, RunSnapshot, SessionEntry, SessionSnapshot


class RunHandle(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def session_id(self) -> str: ...

    async def snapshot(self) -> RunSnapshot: ...

    def events(self, *, after_seq: int = 0) -> AsyncIterator[RunEvent]:
        """Replay then follow live events, with one cursor per subscriber.

        after_seq is the last consumed sequence in this run. Invalid cursors
        raise ValueError. Closing a subscription never cancels execution.
        The stream closes after the unique run.finished event.
        """
        ...

    async def result(self) -> RunResult:
        """Await a terminal value; cancellation of a waiter does not stop work."""
        ...

    async def cancel(self) -> None:
        """Admit an idempotent cancellation request; result() waits for cleanup."""
        ...


class Session(Protocol):
    async def close(self) -> None:
        """Stop execution and close scoped tool resources; reject new input."""
        ...

    @property
    def id(self) -> str: ...

    @property
    def context(self) -> Context: ...

    async def snapshot(self) -> SessionSnapshot: ...

    async def history(self) -> tuple[SessionEntry, ...]:
        """Return detached confirmed facts, not a trimmed model-request view."""
        ...

    async def get_run(self, run_id: str) -> RunHandle:
        """Open an existing run in this session without re-executing it."""
        ...

    async def submit(self, input: object, *, options: RunOptions | None = None) -> RunHandle:
        """Validate and admit input, then immediately return its run handle.

        Only one nonterminal run is allowed. Busy/invalid submissions do not
        allocate visible runs or alter history. Mutable input is detached.
        """
        ...


class RunContext(Protocol):
    """Execution-scoped access, fenced once stopping begins or execution finishes."""

    @property
    def remaining_seconds(self) -> float | None: ...

    @property
    def run_id(self) -> str: ...

    @property
    def session_id(self) -> str: ...

    async def get_context(self) -> tuple[SessionEntry, ...]:
        """Prepare a fresh view using the session's bound context policy."""
        ...

    async def prepare_request(self, request, *, model):
        """Apply the Context policy with the complete request envelope."""
        ...

    def append(self, kind: str, data: object) -> None:
        """Confirm an executor-owned context fact without replacing history."""
        ...

    def emit(self, type: str, data: object = None) -> None:
        """Publish a detached executor event; run.* types are kernel-owned."""
        ...

    def set_output(self, output: object) -> None:
        """Save partial output for a possible cancellation or failure result."""
        ...

    def resource(self, key: object, factory: Callable[[], object]) -> object:
        """Session-local tool state; async aclose() is called at Session.close()."""
        ...


class AgentExecutor(Protocol):
    """Minimum injected execution capability; a model/tool loop can implement it."""

    def validate_input(self, input: object) -> None:
        """Synchronously reject unsupported input before admission."""
        ...

    async def run(self, input: object, context: RunContext) -> object: ...
