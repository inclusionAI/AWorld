from __future__ import annotations

import inspect
import time
from typing import Any, AsyncGenerator, Optional

from aworld.config import RunConfig
from aworld.core.common import TaskStatusValue
from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.core.task import Task, TaskResponse
from aworld.memory.scope import LocalMemoryScope
from aworld.runners.event_runner import TaskEventRunner
from aworld.runners.task_runner import TaskRunner


class _SelfEvolveWorkItemRunner(TaskRunner):
    """Base adapter for one non-agent-oriented self-evolve work item."""

    stage = "self_evolve"

    def __init__(
        self,
        task: Task,
        *,
        run_conf: Optional[RunConfig] = None,
    ) -> None:
        self.run_conf = run_conf
        self._task_response: Optional[TaskResponse] = None
        streaming_mode = task.streaming_mode
        task.streaming_mode = None
        try:
            super().__init__(task, agent_oriented=False)
        finally:
            task.streaming_mode = streaming_mode

    async def do_run(self, context: Context = None) -> TaskResponse:
        started_at = time.monotonic()
        payload = self.task.input
        executor = payload if callable(payload) else getattr(payload, "execute", None)
        answer = executor() if callable(executor) else payload
        if inspect.isawaitable(answer):
            answer = await answer
        if isinstance(answer, TaskResponse):
            self._task_response = answer
        else:
            self._task_response = TaskResponse(
                id=self.task.id,
                answer=answer,
                context=getattr(self, "context", self.task.context),
                success=True,
                status=TaskStatusValue.SUCCESS,
                time_cost=time.monotonic() - started_at,
            )
        return self._task_response

    async def streaming(self) -> AsyncGenerator[Message, None]:
        if False:  # pragma: no cover - establishes the async-generator contract
            yield Message()


class SelfEvolveTaskRunner(_SelfEvolveWorkItemRunner):
    """Outer adapter for deterministic self-evolve orchestration."""

    stage = "self_evolve"


class SelfEvolveCandidateTaskRunner(TaskEventRunner):
    """Standard event-driven Agent runner with task-local memory ownership."""

    def __init__(
        self,
        task: Task,
        *,
        run_conf: Optional[RunConfig] = None,
    ) -> None:
        self.run_conf = run_conf
        self._local_memory_scope: Optional[LocalMemoryScope] = None
        super().__init__(task)

    def _candidate_memory(self) -> Any:
        memory = getattr(self.task.context, "local_memory", None)
        if memory is None:
            raise ValueError(
                "SelfEvolveCandidateTaskRunner requires a context with owned local memory"
            )
        return memory

    async def pre_run(self) -> None:
        if self._local_memory_scope is not None:
            raise RuntimeError("candidate local memory scope is already active")
        self._local_memory_scope = LocalMemoryScope(self._candidate_memory())
        await self._local_memory_scope.__aenter__()
        await super().pre_run()

    async def post_run(self) -> None:
        try:
            await super().post_run()
        finally:
            scope = self._local_memory_scope
            self._local_memory_scope = None
            if scope is not None:
                error = getattr(self, "_exception", None)
                await scope.__aexit__(
                    type(error) if error is not None else None,
                    error,
                    error.__traceback__ if error is not None else None,
                )


class SelfEvolveReplayTaskRunner(_SelfEvolveWorkItemRunner):
    """Adapter for one immutable replay work item."""

    stage = "replay"


class SelfEvolveEvaluationTaskRunner(_SelfEvolveWorkItemRunner):
    """Adapter for one immutable evaluation work item."""

    stage = "evaluation"


__all__ = [
    "SelfEvolveTaskRunner",
    "SelfEvolveCandidateTaskRunner",
    "SelfEvolveReplayTaskRunner",
    "SelfEvolveEvaluationTaskRunner",
]
