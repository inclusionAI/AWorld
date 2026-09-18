# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
import asyncio
import enum
import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Union, List, Dict, Callable, Optional, Literal, TYPE_CHECKING, AsyncGenerator

from aworld.core.event.base import Message
from aworld.utils.serialized_util import to_serializable

from aworld.core.common import Config, Observation, StreamingMode, TaskStatus, TaskStatusValue
from aworld.core.context.base import Context
from aworld.core.tool.base import Tool, AsyncTool
from aworld.output.outputs import Outputs, DefaultOutputs
from aworld.core.context.amni.config import AmniContextConfig
from aworld.core.trajectory import TrajectoryBuildResult, TrajectoryDeliveryReceipt

if TYPE_CHECKING:
    from aworld.agents.llm_agent import Agent
    from aworld.core.agent.swarm import Swarm


class TaskFailureOrigin(str, enum.Enum):
    """Stable control-plane origin for an unsuccessful task response.

    This separates an agent that could not satisfy the user's task from a
    framework/provider failure that prevented a valid attempt.  The value is
    intentionally content-free so callers never have to parse ``msg``.
    """

    TASK = "task"
    INFRASTRUCTURE = "infrastructure"
    CANCELLED = "cancelled"


@dataclass
class Task:
    id: str = field(default_factory=lambda: uuid.uuid1().hex)
    name: str = field(default_factory=lambda: uuid.uuid1().hex)
    user_id: str = field(default=None)
    session_id: str = field(default=None)
    trace_id: str = field(default=None)
    input: Any = field(default=None)
    # task config
    conf: Config = field(default=None)
    # global tool instance
    tools: List[Union[Tool, AsyncTool]] = field(default_factory=list)
    # global tool names
    tool_names: List[str] = field(default_factory=list)
    # custom tool conf
    tools_conf: Config = field(default_factory=dict)
    # custom mcp servers conf
    mcp_servers_conf: Config = field(default_factory=dict)
    swarm: Optional['Swarm'] = field(default=None)
    agent: Optional['Agent'] = field(default=None)
    event_driven: bool = field(default=True)
    # for loop detect
    endless_threshold: int = field(default=3)
    # task_outputs
    outputs: Outputs = field(default_factory=DefaultOutputs)
    # task special runner class, for example: package.XXRunner
    runner_cls: Optional[str] = field(default=None)
    # such as: {"start": ["init_tool", "init_context", ...]}
    hooks: Dict[str, List[str]] = field(default_factory=dict)
    # task specified context
    context: 'Context' = field(default=None)
    context_config: Optional[AmniContextConfig] = None
    is_sub_task: bool = field(default=False)
    group_id: str = field(default=None)
    # parent task reference
    parent_task: Optional['Task'] = field(default=None, repr=False)
    max_retry_count: int = field(default=0)
    # None is unbounded. A supplied duration is bound once, never per retry.
    timeout: float | None = field(default=None)
    observation: Optional[Observation] = field(default=None)
    task_status: TaskStatus = field(default=TaskStatusValue.INIT)
    # streaming support
    streaming_mode: StreamingMode = field(default=None)
    # Explicit identity epoch for repeated executions of the same task id.
    trajectory_task_epoch: int | None = field(default=None)
    deadline_epoch_seconds: float | None = field(default=None)
    _deadline_monotonic: float | None = field(default=None, init=False, repr=False)
    _bound_deadline_epoch_seconds: float | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.bind_deadline()
        epoch = self.trajectory_task_epoch
        if epoch is not None and (
            isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0
        ):
            raise ValueError("trajectory_task_epoch must be a non-negative integer")

    def _validate_lifetime(self) -> None:
        for name in ("timeout", "deadline_epoch_seconds"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or (value <= 0 if name == "timeout" else value < 0)
            ):
                raise ValueError(f"{name} must be a finite positive number or None")

    def bind_deadline(self) -> float | None:
        """Bind the caller's budget once; wall-clock rollback cannot extend it.

        The epoch is the portable control plane for serialization/continuation.
        The monotonic deadline protects a live task against clock adjustments.
        A child task may tighten, but never extend, its parent's deadline.
        """
        self._validate_lifetime()
        now = time.time()
        parent_remaining = None
        candidates = [d for d in (self.deadline_epoch_seconds, self._bound_deadline_epoch_seconds) if d is not None]
        if self.timeout is not None and self._deadline_monotonic is None:
            candidates.append(now + self.timeout)
        if self.parent_task is not None:
            parent_deadline = self.parent_task.bind_deadline()
            if parent_deadline is not None:
                candidates.append(parent_deadline)
                parent_remaining = self.parent_task.remaining_seconds()
        if candidates:
            self.deadline_epoch_seconds = min(candidates)
            self._bound_deadline_epoch_seconds = self.deadline_epoch_seconds
            monotonic = time.monotonic() + max(0.0, self.deadline_epoch_seconds - now)
            if parent_remaining is not None:
                monotonic = min(monotonic, time.monotonic() + parent_remaining)
            self._deadline_monotonic = min(
                monotonic, self._deadline_monotonic
            ) if self._deadline_monotonic is not None else monotonic
        return self.deadline_epoch_seconds

    def remaining_seconds(self) -> float | None:
        self.bind_deadline()
        if self.deadline_epoch_seconds is None:
            return None
        return max(0.0, min(self.deadline_epoch_seconds - time.time(),
                            self._deadline_monotonic - time.monotonic()))

    def request_pause(self) -> None:
        self.task_status = TaskStatusValue.INTERRUPTED

    def request_cancel(self) -> None:
        self.task_status = TaskStatusValue.CANCELLED

    def to_dict(self) -> Dict[str, Any]:
        """Serialize Task to dict while excluding parent_task to avoid recursion.

        Returns:
            Dict[str, Any]: Serialized task dictionary without parent_task; includes parent_task_id instead.
        """
        return {
            "id": self.id,
            "name": self.name,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "input": to_serializable(self.input),
            "conf": to_serializable(self.conf),
            "tools": to_serializable(self.tools),
            "tool_names": to_serializable(self.tool_names),
            "tools_conf": to_serializable(self.tools_conf),
            "mcp_servers_conf": to_serializable(self.mcp_servers_conf),
            "swarm": to_serializable(self.swarm),
            "agent": to_serializable(self.agent),
            "event_driven": self.event_driven,
            "endless_threshold": self.endless_threshold,
            "outputs": to_serializable(self.outputs),
            "runner_cls": self.runner_cls,
            "hooks": to_serializable(self.hooks),
            "context": to_serializable(self.context),
            "is_sub_task": self.is_sub_task,
            "group_id": self.group_id,
            "max_retry_count": self.max_retry_count,
            "timeout": self.timeout,
            "deadline_epoch_seconds": self.deadline_epoch_seconds,
            "parent_task_id": self.parent_task.id if self.parent_task else None,
            "task_status": self.task_status,
            # Streaming-related fields (serializable)
            "streaming_mode": self.streaming_mode,
            "trajectory_task_epoch": self.trajectory_task_epoch,
        }


@dataclass
class TaskResponse:
    id: str = field(default=None)
    trace_id: str = field(default=None)
    answer: Any | None = field(default=None)
    raw_llm_resp: Optional[Any] = field(default=None)
    context: Context | None = field(default_factory=Context)
    llm_calls: List[Dict[str, Any]] = field(default_factory=list)
    usage: Dict[str, Any] | None = field(default_factory=dict)
    time_cost: float | None = field(default=0.0)
    success: bool = field(default=False)
    msg: str | None = field(default=None)
    trajectory: List[Dict[str, Any]]= field(default_factory=list)
    user_visible: bool = field(default=True)
    # task final status, e.g. success/failed/cancelled
    status: TaskStatus | None = field(default=TaskStatusValue.SUCCESS)
    # Canonical trajectory control-plane result. Inline trajectory remains the
    # compatibility data plane and is not reconstructed from this metadata.
    trajectory_build_result: TrajectoryBuildResult | None = field(default=None)
    trajectory_delivery_receipt: TrajectoryDeliveryReceipt | None = field(default=None)
    # Appended to preserve the positional constructor order of the public
    # TaskResponse contract. ``msg``/``answer`` remain the user-visible plane.
    failure_origin: str | None = field(default=None)
    failure_code: str | None = field(default=None)
    error_type: str | None = field(default=None)
    semantic_status: str | None = field(default=None)
    completion_reason: str | None = field(default=None)
    recoverable: bool | None = field(default=None)

    @property
    def trajectory_status(self) -> str | None:
        result = self.trajectory_build_result
        return result.status.value if result is not None else None

    @property
    def trajectory_fidelity(self) -> str | None:
        result = self.trajectory_build_result
        return result.fidelity.value if result is not None else None

    @property
    def trajectory_ref(self) -> str | None:
        result = self.trajectory_build_result
        return result.trajectory_ref if result is not None else None

    @property
    def trajectory_checksum(self) -> str | None:
        result = self.trajectory_build_result
        return result.trajectory_checksum if result is not None else None

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "id": self.id,
            "trace_id": self.trace_id,
            "answer": self.answer,
            "usage": self.usage,
            "llm_calls": self.llm_calls,
            "time_cost": self.time_cost,
            "success": self.success,
            "msg": self.msg,
            "trajectory": self.trajectory,
            "user_visible": self.user_visible,
            "status": self.status,
            "trajectory_build_result": (
                self.trajectory_build_result.to_dict()
                if self.trajectory_build_result is not None
                else None
            ),
            "trajectory_delivery_receipt": (
                self.trajectory_delivery_receipt.to_dict()
                if self.trajectory_delivery_receipt is not None
                else None
            ),
            "trajectory_status": self.trajectory_status,
            "trajectory_fidelity": self.trajectory_fidelity,
            "trajectory_ref": self.trajectory_ref,
            "trajectory_checksum": self.trajectory_checksum,
        }
        # Preserve the historical success payload exactly; the typed failure
        # keys are an additive control plane only when evidence exists.
        for key in ("failure_origin", "failure_code", "error_type",
                    "semantic_status", "completion_reason", "recoverable"):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        return payload


class Runner(object):
    __metaclass__ = abc.ABCMeta

    _use_demon: bool = False
    daemon_target: Callable[..., Any] = None
    context: Context = None

    async def pre_run(self):
        pass

    async def post_run(self):
        pass

    @abc.abstractmethod
    async def do_run(self):
        """Raise exception if not success."""

    async def _daemon_run(self):
        if self._use_demon and self.daemon_target and callable(self.daemon_target):
            import threading
            t = threading.Thread(target=self.daemon_target, name="daemon", daemon=True)
            t.start()

    @abc.abstractmethod
    async def streaming(self) -> AsyncGenerator[Message, None]:
        """Streaming run api."""

    async def run(self) -> Any:
        try:
            await self.pre_run()
            await self._daemon_run()
            ret = await self.do_run()
            return ret
        except BaseException as ex:
            self._exception = ex
            # do record or report
            raise ex
        finally:
            await self.post_run()
