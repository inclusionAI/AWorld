# coding: utf-8
# Copyright (c) inclusionAI.
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from aworld.core.context.base import Context


@dataclass
class LoopState:
    """LoopState records the overall information during the task process, mainly used for stoping detection."""
    iteration: int = 0
    start_time: float = field(default_factory=time.time)
    cumulative_cost: float = 0.0
    consecutive_failures: int = 0
    completion_confirmations: int = 0

    def elapsed(self) -> float:
        return time.time() - self.start_time


class LoopContext(Context):
    """Loop context records the global information of the entire process, primarily aimed at intermediate connections."""

    def __init__(self, workspace: str = ".", repo_root=".", **kwargs):
        super().__init__()
        self._id = uuid.uuid4().hex
        self.workspace = workspace
        self.repo_root = repo_root

    def loop_dir(self) -> Path:
        return Path(self.workspace) / "loop"

    def agent_dir(self) -> Path:
        return Path(self.workspace) / "agent"

    def tasks_path(self) -> Path:
        return self.agent_dir() / "tasks.jsonl"

    def summary_dir(self) -> Path:
        return self.agent_dir() / "summary"

    def reflect_dir(self) -> Path:
        return self.loop_dir() / "reflect"

    def loop_lock_path(self) -> Path:
        return Path(self.repo_root) / "loop" / "loop.lock"

    def check_directories(self):
        self.loop_dir().mkdir(parents=True, exist_ok=True)
        self.agent_dir().mkdir(parents=True, exist_ok=True)
