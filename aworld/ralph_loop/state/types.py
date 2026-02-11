# coding: utf-8
# Copyright (c) inclusionAI.

import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from aworld.core.context.base import Context
from aworld.core.task import Task
from aworld.logs.util import logger
from aworld.output import Artifact, ArtifactType, WorkSpace


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


class LoopContext(Context):
    """Loop context records the global information of the entire process."""

    def __init__(self, work_dir: str = ".", repo_root: str = ".", **kwargs):
        super().__init__()
        self._id = uuid.uuid4().hex
        self.work_dir = work_dir
        self.repo_root = repo_root
        self.workspace = WorkSpace(workspace_id=self.work_dir)

    def loop_dir(self) -> Path:
        return Path(self.work_dir) / "loop"

    def task_dir(self) -> Path:
        return Path(self.work_dir) / "task"

    def summary_dir(self) -> Path:
        return self.task_dir() / "summary"

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
        return Path(self.repo_root) / "loop" / "loop.lock"

    def check_directories(self):
        """Create necessary directories."""
        self.loop_dir().mkdir(parents=True, exist_ok=True)
        self.task_dir().mkdir(parents=True, exist_ok=True)
        self.checkpoints_dir().mkdir(parents=True, exist_ok=True)

    async def build_sub_context(self, sub_task_content: Any, sub_task_id: str = None, **kwargs):
        # no need agent info to sub context
        new_context = object.__new__(Context)
        self._deep_copy(new_context)
        new_context.task_id = sub_task_id
        new_context.task_input = sub_task_content
        self.add_task_node(sub_task_id, self.task_id, caller_agent_info={}, **kwargs)
        return new_context

    def merge_sub_context(self, sub_task_context: 'ApplicationContext', **kwargs):
        # default no need to merge for loop context
        pass

    async def read_to_task_context(self, task: Task, iter_num: int = 0, strategy: str = None, **kwargs):
        """Read strategy from a directory."""
        info = self.workspace.get_artifact_data(f"{self.reflect_dir()}_{task.id}_{iter_num - 1}")
        content = f'{info.get("content")}\n' if info else ''
        sub_task_content = f"{content}{task.input}"
        task.input = sub_task_content

        logger.info(f"Read for task {task.id} iteration {iter_num} content: {sub_task_content}")
        return await self.build_sub_context(sub_task_content=sub_task_content, sub_task_id=task.id, **kwargs)

    async def write_to_loop_context(self,
                                    content: Any,
                                    task_context: 'ApplicationContext',
                                    iter_num: int = 0,
                                    strategy: str = None):
        """Custom context merge."""
        self.merge_sub_context(task_context)

        if isinstance(content, str):
            artifact = Artifact(artifact_id=f"{self.reflect_dir()}_{task_context.get_task().id}_{iter_num}",
                                artifact_type=ArtifactType.TEXT, content=content,
                                metadata={"context_type": "reflect"})
            await self.workspace.add_artifact(artifact, index=False)
        else:
            # for non-text content, currently don't have a good way to handle
            pass
