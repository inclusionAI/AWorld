# coding: utf-8
# Copyright (c) inclusionAI.

import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from aworld.core.context.base import Context


@dataclass
class LoopState:
    """LoopState records the overall information during the task process."""
    iteration: int = 0
    start_time: float = field(default_factory=time.time)
    cumulative_cost: float = 0.0
    consecutive_failures: int = 0
    completion_confirmations: int = 0

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

    def __init__(self, workspace: str = ".", repo_root: str = ".", **kwargs):
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

    def checkpoints_dir(self) -> Path:
        """Directory for state checkpoints."""
        return self.loop_dir() / "checkpoints"

    def state_history_path(self) -> Path:
        """Path for state history."""
        return self.loop_dir() / "state_history.jsonl"

    def check_directories(self):
        """Create necessary directories."""
        self.loop_dir().mkdir(parents=True, exist_ok=True)
        self.agent_dir().mkdir(parents=True, exist_ok=True)
        self.checkpoints_dir().mkdir(parents=True, exist_ok=True)
