# coding: utf-8
# Copyright (c) 2026 inclusionAI.
"""A single-event-loop implementation with explicit, in-memory ownership.

Admission and commits do not await, so they are atomic on the bound loop.
There is no global current session, process manager, or execution registry.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import AsyncIterator, Mapping
from uuid import uuid4

from .errors import (
    InputRejectedError, RunInactiveError, RunNotFoundError,
    SessionBusyError, SessionNotFoundError,
)
from .models import (
    RunError, RunEvent, RunOptions, RunResult, RunSnapshot, RunStatus,
    RunStopReason, SessionEntry, SessionSnapshot,
)
from aworld.core.context.simple import Context
from .protocols import AgentExecutor, RunContext, RunHandle, Session


class InMemorySessionStore:
    """Retain live session state on one event loop; not durable across restarts."""

    def __init__(self) -> None:
        self._sessions: dict[str, _MemorySession] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    async def list_sessions(self) -> tuple[Session, ...]:
        """Retained live sessions in creation order, including closed ones."""
        self._bind()
        return tuple(self._sessions.values())

    def _bind(self) -> asyncio.AbstractEventLoop:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise RuntimeError("In-memory session state belongs to a different event loop")
        return loop


async def create_session(
    *, agent: AgentExecutor, context: Context | None = None,
    store: InMemorySessionStore | None = None,
    metadata: Mapping[str, object] | None = None,
) -> Session:
    """Bind an agent and context strategy; create an idle session with a fresh ID."""
    if not callable(getattr(agent, "validate_input", None)) or not callable(getattr(agent, "run", None)):
        raise TypeError("agent must implement validate_input() and async run()")
    owned_context = Context() if context is None else context
    if not isinstance(owned_context, Context):
        raise TypeError("context must be a Context")
    owned_store = InMemorySessionStore() if store is None else store
    owned_store._bind()
    session = _MemorySession(agent, owned_context, owned_store, deepcopy(dict(metadata or {})))
    from aworld.core.tool.sessions import _bind_session_tools
    _bind_session_tools(owned_context, owned_store)
    owned_store._sessions[session.id] = session
    return session


async def load_session(session_id: str, *, store: InMemorySessionStore) -> Session:
    """Find retained state without executing or fabricating history."""
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("session_id must be a non-empty string")
    store._bind()
    try:
        return store._sessions[session_id]
    except KeyError:
        raise SessionNotFoundError(session_id) from None


class _MemorySession:
    def __init__(
        self, agent: AgentExecutor, context: Context, store: InMemorySessionStore,
        metadata: dict[str, object],
    ) -> None:
        context._claim(self)
        self._id = context.id
        self._context = context
        self._agent = agent
        self._store = store
        self._metadata = metadata
        self._runs: dict[str, _MemoryRun] = {}
        self._active: _MemoryRun | None = None
        self._closing = False
        self._close_task: asyncio.Task | None = None

    async def close(self) -> None:
        self._store._bind()
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._close())
        await asyncio.shield(self._close_task)

    async def _close(self):
        if self._active is not None:
            run = self._active
            await run.cancel()
            await run.result()
        await self._context._close_resources()

    @property
    def id(self) -> str:
        return self._id

    @property
    def context(self) -> Context:
        return self._context

    async def snapshot(self) -> SessionSnapshot:
        self._store._bind()
        return SessionSnapshot(
            self.id, self._context.policy_id, self._context.policy_version,
            self._active.id if self._active else None, deepcopy(self._metadata),
        )

    async def history(self) -> tuple[SessionEntry, ...]:
        self._store._bind()
        return self._context.history()

    async def get_run(self, run_id: str) -> RunHandle:
        self._store._bind()
        try:
            return self._runs[run_id]
        except KeyError:
            raise RunNotFoundError(run_id) from None

    async def submit(self, input: object, *, options: RunOptions | None = None) -> RunHandle:
        self._store._bind()
        if self._closing:
            raise RuntimeError("Session is closed")
        if self._active is not None:
            raise SessionBusyError(self.id, self._active.id)
        if options is not None and not isinstance(options, RunOptions):
            raise TypeError("options must be RunOptions or None")
        try:
            owned_input = deepcopy(input)
            self._agent.validate_input(deepcopy(owned_input))
            entry_data = deepcopy(owned_input)
        except Exception as exc:
            raise InputRejectedError(str(exc)) from exc
        run = _MemoryRun(self, owned_input, options or RunOptions())
        self._context._append(SessionEntry(run.id, "input", entry_data))
        self._runs[run.id] = run
        self._active = run
        run._start()
        return run


class _ExecutionContext:
    def __init__(self, run: _MemoryRun) -> None:
        self._run = run

    @property
    def run_id(self) -> str:
        return self._run.id

    @property
    def session_id(self) -> str:
        return self._run.session_id

    @property
    def remaining_seconds(self) -> float | None:
        self._check()
        deadline = self._run._deadline
        return None if deadline is None else max(0.0, deadline.when() - asyncio.get_running_loop().time())

    def _check(self) -> None:
        run = self._run
        run._session._store._bind()
        if run._session._active is not run or run._status != RunStatus.RUNNING:
            raise RunInactiveError(f"Run {run.id} no longer owns session context")

    async def get_context(self) -> tuple[SessionEntry, ...]:
        self._check()
        view = await self._run._session.context.prepare()
        self._check()
        return view

    async def prepare_request(self, request, *, model):
        self._check()
        value = await self._run._session.context.prepare_request(request, model=model, execution=self)
        self._check()
        return value

    def append(self, kind: str, data: object) -> None:
        self._check()
        if not isinstance(kind, str) or not kind.strip() or kind in ("input", "output"):
            raise ValueError("Use a non-empty executor-owned context kind")
        session = self._run._session
        session._context._append(SessionEntry(self.run_id, kind, data))

    def emit(self, type: str, data: object = None) -> None:
        self._check()
        if not isinstance(type, str) or not type.strip() or type.startswith("run."):
            raise ValueError("run.* events are reserved for the kernel")
        self._run._append_event(type, data)

    def set_output(self, output: object) -> None:
        self._check()
        self._run._partial_output = deepcopy(output)

    def resource(self, key, factory):
        self._check()
        return self._run._session.context._resource(key, factory)


class _MemoryRun:
    def __init__(self, session: _MemorySession, input: object, options: RunOptions) -> None:
        self._id = uuid4().hex
        self._session = session
        self._input = input
        self._options = options
        self._status = RunStatus.PENDING
        self._events: list[RunEvent] = []
        self._changed = asyncio.Event()
        self._result: asyncio.Future[RunResult] = session._store._bind().create_future()
        self._stop_reason: RunStopReason | None = None
        self._partial_output: object = None
        self._agent_task: asyncio.Task[object] | None = None
        self._driver: asyncio.Task[None] | None = None
        self._deadline: asyncio.TimerHandle | None = None

    @property
    def id(self) -> str:
        return self._id

    @property
    def session_id(self) -> str:
        return self._session.id

    def _start(self) -> None:
        loop = self._session._store._bind()
        if self._options.timeout_seconds is not None:
            self._deadline = loop.call_later(
                self._options.timeout_seconds, self._request_stop, RunStopReason.DEADLINE_EXCEEDED,
            )
        self._driver = loop.create_task(self._execute(), name=f"aworld-run-{self.id}")

    async def snapshot(self) -> RunSnapshot:
        self._session._store._bind()
        return RunSnapshot(self.id, self.session_id, self._status)

    async def result(self) -> RunResult:
        self._session._store._bind()
        return deepcopy(await asyncio.shield(self._result))

    def events(self, *, after_seq: int = 0) -> AsyncIterator[RunEvent]:
        self._session._store._bind()
        if (
            isinstance(after_seq, bool) or not isinstance(after_seq, int)
            or after_seq < 0 or after_seq > len(self._events)
        ):
            raise ValueError("after_seq must be a valid committed sequence in this run")
        return self._iterate_events(after_seq)

    async def _iterate_events(self, cursor: int) -> AsyncIterator[RunEvent]:
        while True:
            while cursor < len(self._events):
                event = deepcopy(self._events[cursor])
                cursor += 1
                yield event
            if self._result.done():
                return
            self._changed.clear()
            await self._changed.wait()

    def _append_event(self, type: str, data: object = None) -> None:
        self._events.append(RunEvent(
            self.id, self.session_id, len(self._events) + 1, type, deepcopy(data),
        ))
        self._changed.set()

    async def cancel(self) -> None:
        self._session._store._bind()
        self._request_stop(RunStopReason.CANCELLED)

    def _request_stop(self, reason: RunStopReason) -> None:
        if self._result.done() or self._stop_reason is not None:
            return
        self._stop_reason = reason
        self._status = RunStatus.CANCELLING
        if self._agent_task is not None and not self._agent_task.done():
            self._agent_task.cancel()

    async def _execute(self) -> None:
        if self._stop_reason is not None:
            self._finish_stopped()
            return
        self._status = RunStatus.RUNNING
        self._append_event("run.started")
        context: RunContext = _ExecutionContext(self)
        try:
            self._agent_task = asyncio.create_task(self._session._agent.run(self._input, context))
            output = await self._agent_task
            if self._stop_reason is not None:
                self._finish_stopped()
            else:
                self._finish(RunStatus.COMPLETED, RunStopReason.END_TURN, output)
        except asyncio.CancelledError:
            if self._stop_reason is None:
                self._stop_reason = RunStopReason.CANCELLED
            self._finish_stopped()
        except Exception as exc:
            if self._stop_reason is not None:
                self._finish_stopped()
            else:
                self._finish(
                    RunStatus.FAILED, RunStopReason.ERROR, self._partial_output,
                    RunError("agent_error", str(exc)),
                )

    def _finish_stopped(self) -> None:
        if self._stop_reason == RunStopReason.DEADLINE_EXCEEDED:
            self._finish(
                RunStatus.FAILED, RunStopReason.DEADLINE_EXCEEDED, self._partial_output,
                RunError("deadline_exceeded", "Execution budget exhausted"),
            )
        else:
            self._finish(RunStatus.CANCELLED, RunStopReason.CANCELLED, self._partial_output)

    def _finish(
        self, status: RunStatus, reason: RunStopReason, output: object,
        error: RunError | None = None,
    ) -> None:
        if self._result.done():
            return
        owned_output = deepcopy(output)
        result = RunResult(self.id, self.session_id, status, reason, owned_output, error)
        # Prepare potentially fallible copies before committing state.
        event = RunEvent(self.id, self.session_id, len(self._events) + 1, "run.finished", deepcopy(result))
        entry = SessionEntry(self.id, "output", deepcopy(owned_output)) if status == RunStatus.COMPLETED else None
        if self._deadline is not None:
            self._deadline.cancel()
        if entry is not None:
            self._session._context._append(entry)
        self._status = status
        self._session._active = None
        self._events.append(event)
        self._result.set_result(result)
        self._changed.set()
