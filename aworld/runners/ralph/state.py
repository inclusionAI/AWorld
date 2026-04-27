# coding: utf-8
# Copyright (c) inclusionAI.
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from aworld.core.context.amni import ApplicationContext, TaskInput, AmniConfigFactory, AmniContext
from aworld.core.context.amni.config import AmniConfigLevel
from aworld.core.context.base import Context
from aworld.core.task import Task
from aworld.logs.util import logger
from aworld.output import WorkSpace
from aworld.runners.ralph.input_builder import IterationInputBuilder
from aworld.runners.ralph.memory import LoopMemoryStore
from aworld.runners.ralph.policy import RalphLoopPolicy
from aworld.runners.ralph.types import CompletionCriteria
from aworld.sandbox import Sandbox
from aworld.utils.common import convert_to_subclass


@dataclass
class LoopState:
    """LoopState records the overall information during the task process."""
    iteration: int = 0
    start_time: float = field(default_factory=time.time)
    cumulative_cost: float = 0.0
    consecutive_failures: int = 0
    completion_confirmations: int = 0
    confirmation_threshold: int = 1

    # Additional metrics
    successful_steps: int = 0
    failed_steps: int = 0
    total_tokens: int = 0

    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    def elapsed(self) -> float:
        """Get elapsed time in seconds."""
        return time.time() - self.start_time

    def success_rate(self) -> float:
        """Calculate success rate."""
        total = self.successful_steps + self.failed_steps
        return self.successful_steps / total if total > 0 else 1.0

    def copy(self) -> 'LoopState':
        """Create a deep copy of this state."""
        return deepcopy(self)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)


class LoopContext(ApplicationContext):
    """Loop context records the global information of the entire process."""

    def __init__(
        self,
        completion_criteria: Optional[CompletionCriteria] = None,
        loop_state: Optional[LoopState] = None,
        work_dir: str = ".",
        **kwargs,
    ):
        if "task_state" not in kwargs:
            base_context = ApplicationContext.create(task_content="")
            kwargs = {
                "task_state": base_context.task_state,
                "workspace": getattr(base_context, "_workspace", None),
                "parent": getattr(base_context, "_parent", None),
                "context_config": base_context.get_config(),
                "working_dir": getattr(base_context, "_working_dir", None),
                **kwargs,
            }
        super().__init__(**kwargs)
        self.loop_init(
            completion_criteria=completion_criteria,
            loop_state=loop_state,
            work_dir=work_dir,
        )

    def loop_init(self,
                  completion_criteria: Optional[CompletionCriteria] = None,
                  loop_state: Optional[LoopState] = None,
                  work_dir: str = "."):
        self._id = uuid.uuid4().hex
        self._completion_criteria = completion_criteria or CompletionCriteria()
        self.loop_state = loop_state or LoopState()
        self.work_dir = work_dir
        self.workspace = WorkSpace(workspace_id=self.work_dir)
        self.check_directories()
        # use sandbox to manager file IO
        self.sand_box = (
            Sandbox.builder()
            .builtin_tools(["filesystem", "terminal"])
            .workspaces([work_dir])
            .build()
        )
        self.memory = LoopMemoryStore(self)

    @property
    def completion_criteria(self) -> CompletionCriteria:
        return self._completion_criteria

    @completion_criteria.setter
    def completion_criteria(self, value: CompletionCriteria):
        self._completion_criteria = value

    @property
    def iteration(self):
        return self.loop_state.iteration

    @iteration.setter
    def iteration(self, value: int):
        self.loop_state.iteration = value

    def loop_dir(self) -> Path:
        return Path(self.work_dir) / "loop"

    def task_dir(self) -> Path:
        return Path(self.work_dir) / "task"

    def summary_dir(self) -> Path:
        return self.task_dir() / "summary"

    def answer_dir(self) -> Path:
        return self.task_dir() / "answer"

    def reflect_dir(self) -> Path:
        return self.loop_dir() / "reflect"

    def stop_dir(self) -> Path:
        return self.loop_dir() / "stop"

    def checkpoints_dir(self) -> Path:
        """Directory for state checkpoints."""
        return self.loop_dir() / "checkpoints"

    def tasks_path(self) -> Path:
        return self.loop_dir() / "tasks.jsonl"

    def loop_lock_path(self) -> Path:
        return self.loop_dir() / "loop" / "loop.lock"

    def check_directories(self):
        """Create necessary directories."""
        self.loop_dir().mkdir(parents=True, exist_ok=True)
        self.task_dir().mkdir(parents=True, exist_ok=True)
        self.answer_dir().mkdir(parents=True, exist_ok=True)
        self.summary_dir().mkdir(parents=True, exist_ok=True)
        self.reflect_dir().mkdir(parents=True, exist_ok=True)
        self.stop_dir().mkdir(parents=True, exist_ok=True)
        self.checkpoints_dir().mkdir(parents=True, exist_ok=True)

    async def build_sub_context(self, sub_task_content: Any, sub_task_id: str = None, **kwargs):
        # no need agent info to sub context
        context_config = AmniConfigFactory.create(AmniConfigLevel.NAVIGATOR)
        context_config.agent_config.neuron_names.clear()

        task = kwargs.get("task")
        task_input = TaskInput(
            user_id=task.user_id or '',
            session_id=task.session_id or uuid.uuid4().hex,
            task_id=task.id,
            task_content=task.input,
            origin_user_input=task.input
        )
        context_config = None
        new_context = await ApplicationContext.from_input(task_input, context_config=context_config)

        if task.agent:
            await new_context.build_agents_state([task.agent])
        if task.swarm:
            await new_context.build_agents_state(task.swarm.topology)
        task.conf = {}

        new_context.task_id = sub_task_id
        new_context.task_input = sub_task_content
        self.add_task_node(sub_task_id, self.task_id, caller_agent_info={}, **kwargs)
        return new_context

    async def read_to_task_context(self, task: Task, iter_num: int = 0, strategy: str = None,
                                   reuse_context: bool = False, **kwargs):
        """Read feedback/reflection from previous iteration and inject into task context.

        Args:
            task: Task to execute
            iter_num: Current iteration number
            strategy: Read strategy (e.g., 'feedback', 'reflect', 'decision')
            **kwargs: Additional parameters

        Returns:
            Context with injected feedback
        """
        builder = IterationInputBuilder(
            policy=RalphLoopPolicy(
                execution_mode="reuse_context" if reuse_context else "fresh_context",
                verify_enabled=False,
            ),
            memory_store=self.memory,
        )
        iteration_input = await builder.build(
            task_id=task.id,
            original_task=kwargs.get("original_task", task.input),
            iteration=iter_num,
        )
        task_content = iteration_input.task_input

        task.input = task_content
        logger.debug(f"Task input after feedback injection: {task_content[:200]}...")

        if iteration_input.reuse_context:
            return self
        return await self.build_sub_context(sub_task_content=task_content, sub_task_id=task.id, task=task, **kwargs)

    async def write_to_loop_context(self,
                                    content: Any,
                                    task_context: 'ApplicationContext',
                                    iter_num: int = 0,
                                    strategy: str = None,
                                    content_type: str = "feedback",
                                    reuse_context: bool = True):
        """Write feedback/reflection to loop context for next iteration.

        Args:
            content: Content to write (string, dict, or list)
            task_context: Task context
            iter_num: Current iteration number
            strategy: Write strategy
            content_type: Type of content ('reflect', 'feedback', 'decision')
        """

        if iter_num < 2 or not content:
            return

        task_id = task_context.get_task().id if task_context.get_task() else "unknown"

        # Handle different content types
        if isinstance(content, str):
            artifact_content = content
        elif isinstance(content, dict):
            # Convert dict to formatted string
            artifact_content = self._format_dict_content(content)
        elif isinstance(content, list):
            # Convert list to formatted string
            artifact_content = self._format_list_content(content)
        else:
            logger.warning(f"Unsupported content type: {type(content)}, converting to string")
            artifact_content = str(content)

        await self.memory.write_reflection_feedback_artifact(
            task_id=task_id,
            iteration=iter_num,
            text=artifact_content,
            metadata={
                "context_type": content_type,
                "iteration": iter_num,
                "task_id": task_id,
                "timestamp": time.time(),
                "kind": "reflection_feedback",
            },
        )
        logger.info(
            f"Written {content_type} to loop context: "
            f"{self.memory.reflection_feedback_artifact_id(task_id, iter_num)} "
            f"(length: {len(artifact_content)})"
        )

    def _format_dict_content(self, content: dict) -> str:
        """Format dictionary content as readable text."""
        lines = []
        for key, value in content.items():
            if isinstance(value, (list, dict)):
                lines.append(f"{key}:")
                if isinstance(value, list):
                    for item in value:
                        lines.append(f"  - {item}")
                else:
                    for k, v in value.items():
                        lines.append(f"  {k}: {v}")
            else:
                lines.append(f"{key}: {value}")
        return "\n".join(lines)

    def _format_list_content(self, content: list) -> str:
        """Format list content as readable text."""
        lines = []
        for i, item in enumerate(content, 1):
            if isinstance(item, dict):
                lines.append(f"{i}. {self._format_dict_content(item)}")
            else:
                lines.append(f"{i}. {item}")
        return "\n".join(lines)

    def deep_copy(self) -> 'LoopContext':
        return self

    async def add_file(self, filename: Optional[str], content: Optional[Any], mime_type: Optional[str] = "text",
                       namespace: str = "default", origin_type: str = None, origin_path: str = None,
                       refresh_workspace: bool = True) -> Tuple[bool, Optional[str], Optional[str]]:
        if not filename:
            return False, filename, content

        task_key, separator, suffix = filename.rpartition("_")
        if separator and suffix.isdigit():
            result = await self.memory.write_answer(task_key, int(suffix), content)
        else:
            result = await self.memory.write_answer_file(filename, content)
        res = result if isinstance(result, dict) else {"success": bool(result)}
        return res.get('success'), filename, content


def to_loop_context(context: Context, **kwargs) -> LoopContext:
    """Convert a general context to a loop context."""
    if isinstance(context, LoopContext):
        return context
    elif isinstance(context, ApplicationContext):
        loop_context = convert_to_subclass(context, LoopContext)
        loop_context.loop_init(**kwargs)
        return loop_context
    else:
        raise ValueError(f"Unsupported context type: {type(context)}")
