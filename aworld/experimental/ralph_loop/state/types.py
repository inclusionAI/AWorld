# coding: utf-8
# Copyright (c) inclusionAI.
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path


class LoopStatus:
    INIT = "INIT"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PAUSED = "PAUSED"
    TERMINATED = "TERMINATED"


@dataclass
class LoopState:
    iteration: int = 0
    start_time: float = field(default_factory=time.time)
    cumulative_cost: float = 0.0
    consecutive_failures: int = 0
    completion_confirmations: int = 0

    def elapsed(self) -> float:
        return time.time() - self.start_time


@dataclass
class LoopContext:
    """Loop context."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    workspace: str = field(default=".")
    repo_root: str = field(default=".")
    is_primary: bool = field(default=True)

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
