"""Child loops behind ordinary tools, with session-local handles and cleanup."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Mapping
from uuid import uuid4

from aworld.core.context.simple import Context
from aworld.core.session import create_session
from aworld.core.session.protocols import AgentExecutor
from .function import Tool


def _outcome(result):
    return {"session_id": result.session_id, "run_id": result.run_id,
            "status": result.status.value, "output": result.output,
            "error": None if result.error is None else {"code": result.error.code, "message": result.error.message}}


class _Child:
    def __init__(self, agent, input, semaphore):
        self.id = uuid4().hex
        self.session = self.run = None
        self.result = None
        self.cancel_requested = False
        self.task = asyncio.create_task(self._execute(agent, input, semaphore))

    async def _execute(self, agent, input, semaphore):
        try:
            async with semaphore:
                self.session = await create_session(agent=agent, context=Context())
                self.run = await self.session.submit(input)
                self.result = _outcome(await self.run.result())
        except asyncio.CancelledError:
            if self.run is not None:
                await self.run.cancel()
                self.result = _outcome(await self.run.result())
            else:
                self._cancelled()
        except Exception as exc:
            self.result = {"status": "failed", "output": None,
                           "error": {"code": "child_error", "message": str(exc)},
                           "session_id": self.session.id if self.session else None,
                           "run_id": self.run.id if self.run else None}
        finally:
            if self.session is not None:
                await self.session.close()

    def _cancelled(self):
        self.result = {"status": "cancelled", "output": None, "error": None,
                       "session_id": None, "run_id": None}

    def snapshot(self):
        if self.task.cancelled() and self.result is None:
            self._cancelled()
        if self.result is not None and self.task.done():
            return deepcopy({"task_id": self.id, **self.result})
        status = "cancelling" if self.cancel_requested else ("running" if self.run else "pending")
        return {"task_id": self.id, "status": status,
                "session_id": self.session.id if self.session else None,
                "run_id": self.run.id if self.run else None}

    async def wait(self):
        try:
            await asyncio.shield(self.task)
        except asyncio.CancelledError:
            if not self.task.cancelled():
                raise
            self._cancelled()
        return self.snapshot()

    async def cancel(self):
        if not self.task.done() and not self.cancel_requested:
            self.cancel_requested = True
            self.task.cancel()
        return await self.wait()


class _Children:
    def __init__(self, limit):
        self.semaphore = asyncio.Semaphore(limit)
        self.children = {}

    def start(self, agent, input):
        child = _Child(agent, input, self.semaphore)
        self.children[child.id] = child
        return child

    def get(self, task_id):
        try:
            return self.children[task_id]
        except KeyError:
            raise LookupError(f"Unknown subagent task in this session: {task_id}") from None

    async def aclose(self):
        await asyncio.gather(*(child.cancel() for child in self.children.values()))
        self.children.clear()


def subagent_tools(agents: Mapping[str, AgentExecutor], *, max_concurrent: int = 4) -> tuple[Tool, ...]:
    """Return spawn/parallel/check/wait/cancel/list tools, without a global manager.

    Foreground children attach to the invoking run. Explicit background children
    live across parent runs until completion/cancel or Session.close(). Only
    returned results enter parent history. Child tools define physical access.
    """
    configured = dict(agents)
    if not configured or not all(isinstance(name, str) and name.strip() for name in configured):
        raise ValueError("Provide named child agents")
    if not all(callable(getattr(agent, "validate_input", None)) and callable(getattr(agent, "run", None))
               for agent in configured.values()):
        raise TypeError("Children must implement AgentExecutor")
    if isinstance(max_concurrent, bool) or not isinstance(max_concurrent, int) or max_concurrent < 1:
        raise ValueError("max_concurrent must be a positive integer")
    scope_key = object()

    def scope(parent):
        return parent.resource(scope_key, lambda: _Children(max_concurrent))

    def validate(arguments):
        name = arguments.get("agent")
        if not isinstance(name, str) or name not in configured:
            raise ValueError(f"Unknown agent: {name}")
        configured[name].validate_input(arguments.get("input"))
        return configured[name], deepcopy(arguments["input"])

    def get(arguments, parent):
        task_id = arguments.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task_id must be non-empty text")
        return scope(parent).get(task_id)

    async def attached(child):
        try:
            return await child.wait()
        finally:
            await child.cancel()

    async def spawn(arguments, parent):
        agent, input = validate(arguments)
        background = arguments.get("background", False)
        if not isinstance(background, bool):
            raise ValueError("background must be boolean")
        child = scope(parent).start(agent, input)
        parent.emit("subagent.started", {"task_id": child.id, "background": background})
        if background:
            return child.snapshot()
        result = await attached(child)
        parent.emit("subagent.finished", result)
        return result

    async def parallel(arguments, parent):
        tasks = arguments.get("tasks")
        if not isinstance(tasks, list) or not tasks or not all(isinstance(item, dict) for item in tasks):
            raise ValueError("tasks must be a non-empty array of agent/input objects")
        validated = [validate(item) for item in tasks]
        children = [scope(parent).start(agent, input) for agent, input in validated]
        try:
            return list(await asyncio.gather(*(child.wait() for child in children)))
        finally:
            await asyncio.gather(*(child.cancel() for child in children))

    async def check(arguments, parent):
        return get(arguments, parent).snapshot()

    async def wait(arguments, parent):
        return await get(arguments, parent).wait()

    async def cancel(arguments, parent):
        return await get(arguments, parent).cancel()

    async def list_tasks(arguments, parent):
        return [child.snapshot() for child in scope(parent).children.values()]

    task_schema = {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]}
    input_schema = {"type": "object", "properties": {"agent": {"type": "string", "enum": list(configured)},
                    "input": {"type": "string"}, "background": {"type": "boolean", "default": False}},
                    "required": ["agent", "input"]}
    return (
        Tool("spawn_subagent", "Delegate to a named child. Wait by default; background returns a task handle.", input_schema, spawn),
        Tool("parallel_subagents", "Delegate independent tasks concurrently, with a session-local concurrency cap.",
             {"type": "object", "properties": {"tasks": {"type": "array", "items": input_schema}}, "required": ["tasks"]}, parallel),
        Tool("check_subagent", "Read child task status without waiting.", task_schema, check),
        Tool("wait_subagent", "Wait for a background result. Cancelling this wait does not stop that child.", task_schema, wait),
        Tool("cancel_subagent", "Cancel a child and wait for its cleanup.", task_schema, cancel),
        Tool("list_subagents", "List child tasks belonging to this session.", {"type": "object", "properties": {}}, list_tasks),
    )


def subagent_tool(agent: AgentExecutor, *, name: str = "spawn_subagent") -> Tool:
    """One foreground delegation capability, using the same implementation."""
    spawn = subagent_tools({"child": agent})[0]

    async def execute(arguments, parent):
        return await spawn.execute({"agent": "child", "input": arguments.get("input")}, parent)

    return Tool(name, "Delegate one task to an isolated child agent", {
        "type": "object", "properties": {"input": {"type": "string"}}, "required": ["input"],
    }, execute)
