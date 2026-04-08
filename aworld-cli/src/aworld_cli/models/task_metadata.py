"""
Task metadata for background task tracking.
"""
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

    Lifecycle: pending → running → completed/failed/cancelled

    Attributes:
        task_id: Unique task identifier (e.g., "task-001")
        agent_name: Name of agent executing the task
        task_content: User's task description
        status: Current status (pending/running/completed/failed/cancelled)
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
            True if task is completed/failed/cancelled, False otherwise
        """
        return self.status in ('completed', 'failed', 'cancelled')
