"""
Task metadata for background task tracking.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Deque
from collections import deque
import asyncio

from aworld.output import StreamingOutputs


@dataclass
class TaskMetadata:
    """
    Metadata for tracking background tasks.

    Lifecycle: pending → running → completed/failed/cancelled/interrupted/timeout

    Attributes:
        task_id: Unique task identifier (e.g., "task-001")
        agent_name: Name of agent executing the task
        task_content: User's task description
        status: Current status (pending/running/completed/failed/cancelled/interrupted/timeout)
        submitted_at: When task was submitted
        started_at: When task execution started (None if pending)
        completed_at: When task finished (None if not finished)
        asyncio_task: Reference to asyncio.Task (internal)
        streaming_outputs: Reference to StreamingOutputs (internal)
        current_step: Description of current step being executed
        progress_percentage: Progress from 0.0 to 100.0
        result: Final result when completed (None otherwise)
        error: Error message when failed (None otherwise)
        output_file: Path to output log file (persistent across CLI sessions)
    """
    # Identification
    task_id: str

    # User input
    agent_name: str
    task_content: str

    # Status tracking
    status: str  # pending/running/completed/failed/cancelled
    submitted_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    # Execution tracking (internal, excluded from repr)
    asyncio_task: Optional[asyncio.Task] = field(default=None, repr=False)
    streaming_outputs: Optional[StreamingOutputs] = field(default=None, repr=False)

    # Output tracking (for follow command)
    output_buffer: Deque[tuple[datetime, str]] = field(default_factory=lambda: deque(maxlen=1000), repr=False)
    # Format: deque([(timestamp, formatted_output_line), ...])
    # Limited to 1000 entries to prevent memory issues

    # Progress tracking
    current_step: str = ""
    progress_percentage: float = 0.0

    # Result
    result: Optional[str] = None
    error: Optional[str] = None

    # File persistence
    output_file: Optional[str] = None  # Path to output log file

    def to_dict(self) -> dict:
        """
        Serialize metadata to a JSON-friendly dictionary.
        """
        return {
            "task_id": self.task_id,
            "agent_name": self.agent_name,
            "task_content": self.task_content,
            "status": self.status,
            "submitted_at": self.submitted_at.isoformat() if self.submitted_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "current_step": self.current_step,
            "progress_percentage": self.progress_percentage,
            "result": self.result,
            "error": self.error,
            "output_file": self.output_file,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TaskMetadata":
        """
        Deserialize metadata from persisted JSON.
        """
        return cls(
            task_id=data["task_id"],
            agent_name=data.get("agent_name", "Aworld"),
            task_content=data.get("task_content", ""),
            status=data.get("status", "pending"),
            submitted_at=cls._parse_datetime(data.get("submitted_at")) or datetime.now(),
            started_at=cls._parse_datetime(data.get("started_at")),
            completed_at=cls._parse_datetime(data.get("completed_at")),
            current_step=data.get("current_step", ""),
            progress_percentage=float(data.get("progress_percentage", 0.0) or 0.0),
            result=data.get("result"),
            error=data.get("error"),
            output_file=data.get("output_file"),
        )

    @staticmethod
    def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except Exception:
            return None

    @staticmethod
    def parse_task_index(task_id: str) -> int:
        """
        Extract numeric suffix from a task id like `task-001`.
        """
        match = re.match(r"task-(\d+)$", task_id or "")
        if not match:
            return -1
        return int(match.group(1))

    def elapsed_seconds(self) -> float:
        """
        Calculate elapsed time in seconds.

        Returns:
            Elapsed time from start to now (or completion)
        """
        if not self.started_at:
            return 0.0
        end_time = self.completed_at or datetime.now()
        return (end_time - self.started_at).total_seconds()

    def is_terminal(self) -> bool:
        """
        Check if task is in terminal state.

        Returns:
            True if task is completed/failed/cancelled/interrupted/timeout, False otherwise
        """
        return self.status in ('completed', 'failed', 'cancelled', 'interrupted', 'timeout')
