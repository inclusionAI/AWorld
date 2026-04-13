"""
Background task manager for aworld-cli.

Manages session-level background tasks using asyncio and StreamingOutputs.
Task outputs and task metadata are persisted to .aworld/tasks/ directory.
"""
import asyncio
import json
import os
import traceback
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from rich.console import Console

from aworld.core.common import TaskStatusValue
from aworld.core.task import Task, TaskResponse
from aworld.runner import Runners
from aworld.logs.util import logger
from aworld_cli.models.task_metadata import TaskMetadata


class BackgroundTaskManager:
    """
    Session-level background task manager.

    Manages background tasks created via /dispatch command.
    Tasks are tracked in memory and cleared on CLI exit.

    Thread-safety: Uses asyncio.Lock for concurrent access protection.

    Attributes:
        session_id: Session ID for task isolation
        console: Optional Rich console for logging
        tasks: Dict mapping task_id to TaskMetadata
        output_dir: Directory for task output log files (default: .aworld/tasks)
    """

    def __init__(self, session_id: str, console: Optional[Console] = None, output_dir: Optional[str] = None):
        """
        Initialize background task manager.

        Args:
            session_id: Session ID for task isolation
            console: Optional Rich console for logging
            output_dir: Optional output directory path (default: .aworld/tasks)
        """
        self.session_id = session_id
        self.console = console
        self.tasks: Dict[str, TaskMetadata] = {}
        self._task_counter = 0
        self._lock = asyncio.Lock()

        # Setup output directory for task logs
        self.output_dir = Path(output_dir or ".aworld/tasks")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_file = self.output_dir / "tasks.json"
        logger.info(f"[BackgroundTaskManager] Output directory: {self.output_dir}")
        self._load_persisted_tasks()

    def _get_task_output_path(self, task_id: str) -> Path:
        """
        Get output file path for task.

        Args:
            task_id: Task ID

        Returns:
            Path to output log file
        """
        return self.output_dir / f"{task_id}.log"

    def _build_persist_payload(self) -> dict:
        return {
            "version": 1,
            "session_id": self.session_id,
            "task_counter": self._task_counter,
            "updated_at": datetime.now().isoformat(),
            "tasks": [task.to_dict() for task in self.list_tasks()],
        }

    def _write_persisted_tasks(self, payload: dict) -> None:
        temp_path = self.metadata_file.with_suffix(".json.tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, self.metadata_file)

    def _persist_tasks_sync(self) -> None:
        """
        Persist task metadata to disk synchronously.

        This is reserved for synchronous startup/shutdown paths where no event loop
        handoff is available.
        """
        self._write_persisted_tasks(self._build_persist_payload())

    async def _persist_tasks(self) -> None:
        """
        Persist task metadata to disk without blocking the event loop.
        """
        payload = self._build_persist_payload()
        await asyncio.to_thread(self._write_persisted_tasks, payload)

    def _load_persisted_tasks(self) -> None:
        """
        Load persisted task metadata from disk.
        """
        if not self.metadata_file.exists():
            return

        try:
            with open(self.metadata_file, "r", encoding="utf-8") as f:
                payload = json.load(f)

            loaded_tasks = {}
            max_task_index = -1
            for raw_task in payload.get("tasks", []):
                task = TaskMetadata.from_dict(raw_task)

                # Background tasks are process-local. If the previous process exited while
                # a task was pending/running, it cannot still be active after restart.
                if task.status in {"pending", "running"}:
                    task.status = "interrupted"
                    task.error = task.error or "Task interrupted because aworld-cli exited or restarted"
                    task.completed_at = task.completed_at or datetime.now()
                    if task.output_file:
                        self._append_restart_note(task)

                self._load_output_buffer_from_log(task)
                loaded_tasks[task.task_id] = task
                max_task_index = max(max_task_index, TaskMetadata.parse_task_index(task.task_id))

            self.tasks = loaded_tasks
            persisted_counter = int(payload.get("task_counter", -1) or -1)
            self._task_counter = max(persisted_counter, max_task_index + 1, 0)

            # Save back immediately if we normalized stale statuses.
            self._persist_tasks_sync()
            logger.info(f"[BackgroundTaskManager] Loaded {len(self.tasks)} persisted tasks")
        except Exception as e:
            logger.warning(
                f"[BackgroundTaskManager] Failed to load persisted tasks: {e}\n{traceback.format_exc()}"
            )

    def _append_restart_note(self, task: TaskMetadata) -> None:
        """
        Append a note to the task log when a stale running task is recovered after restart.
        """
        try:
            output_path = Path(task.output_file)
            if not output_path.exists():
                return

            note = (
                f"\n{'=' * 80}\n"
                f"Status: interrupted\n"
                f"Interrupted at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"Error: Task interrupted because aworld-cli exited or restarted\n"
            )

            with open(output_path, "rb") as f:
                existing = f.read()
                if b"Task interrupted because aworld-cli exited or restarted" in existing:
                    return

            with open(output_path, "a", encoding="utf-8") as f:
                f.write(note)
        except Exception as e:
            logger.warning(f"[BackgroundTaskManager] Failed to append restart note: {e}")

    def _load_output_buffer_from_log(self, task: TaskMetadata) -> None:
        """
        Rebuild in-memory output buffer from the persisted task log file.
        """
        if task.output_buffer:
            return
        if not task.output_file:
            return

        output_path = Path(task.output_file)
        if not output_path.exists():
            return

        try:
            relevant_lines = deque(maxlen=1000)
            with open(output_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    relevant_lines.append(line.rstrip("\n"))

            base_date = (task.submitted_at or datetime.now()).date()
            for line in relevant_lines:
                if len(line) >= 11 and line[0] == "[" and line[9] == "]":
                    time_part = line[1:9]
                    try:
                        timestamp = datetime.combine(base_date, datetime.strptime(time_part, "%H:%M:%S").time())
                        content = line[11:] if len(line) > 11 else ""
                        task.output_buffer.append((timestamp, content))
                    except Exception:
                        continue
        except Exception as e:
            logger.warning(f"[BackgroundTaskManager] Failed to load output buffer from log: {e}")

    def _normalize_final_status(self, task_response) -> str:
        """
        Normalize AWorld TaskResponse status to CLI background task status.

        Returns one of:
        pending/running/completed/failed/cancelled/interrupted/timeout
        """
        if task_response is None:
            return "failed"

        status = getattr(task_response, "status", None)
        success = getattr(task_response, "success", None)

        if status == TaskStatusValue.CANCELLED:
            return "cancelled"
        if status == TaskStatusValue.INTERRUPTED:
            return "interrupted"
        if status == TaskStatusValue.TIMEOUT:
            return "timeout"
        if status == TaskStatusValue.FAILED:
            return "failed"
        if status == TaskStatusValue.RUNNING:
            return "running"
        if status in (TaskStatusValue.SUCCESS, "finished", "completed") and success is True:
            return "completed"
        if success is False:
            return "failed"

        return "failed"

    async def _get_definitive_task_response(
        self,
        aworld_task_id: str,
        streaming_outputs,
    ) -> Optional[TaskResponse]:
        """
        Resolve the final AWorld TaskResponse from the underlying run task.

        `StreamingOutputs.stream_events()` only reflects stream lifecycle. A stream can
        end slightly before the producer coroutine fully returns, so `/dispatch` should
        treat the underlying run result as source of truth for terminal state.
        """
        run_impl_task = getattr(streaming_outputs, "_run_impl_task", None)

        if run_impl_task is not None:
            run_result = await run_impl_task
            if isinstance(run_result, dict):
                task_response = run_result.get(aworld_task_id)
                if task_response is not None:
                    return task_response

        task_response = streaming_outputs.response()
        if task_response is not None:
            return task_response

        # Give mark_completed a brief chance to publish its final TaskResponse.
        for _ in range(10):
            await asyncio.sleep(0.05)
            task_response = streaming_outputs.response()
            if task_response is not None:
                return task_response

        return None

    async def submit_task(
        self,
        agent_name: str,
        task_content: str,
        swarm,
        context_config=None
    ) -> str:
        """
        Submit task to background execution.

        Args:
            agent_name: Name of agent to execute task (default: "Aworld")
            task_content: User's task description
            swarm: Swarm instance from executor
            context_config: Context configuration for task

        Returns:
            task_id: Generated task ID (e.g., "task-001")

        Example:
            >>> task_id = await manager.submit_task(
            ...     agent_name="Aworld",
            ...     task_content="Run GAIA benchmark",
            ...     swarm=executor.swarm
            ... )
            >>> print(task_id)
            task-000
        """
        async with self._lock:
            # Generate task ID
            task_id = f"task-{self._task_counter:03d}"
            self._task_counter += 1

            # Create metadata
            metadata = TaskMetadata(
                task_id=task_id,
                status="pending",
                agent_name=agent_name,
                task_content=task_content,
                submitted_at=datetime.now()
            )
            self.tasks[task_id] = metadata
            await self._persist_tasks()

            # Submit to background
            asyncio_task = asyncio.create_task(
                self._run_task_background(
                    task_id=task_id,
                    agent_name=agent_name,
                    task_content=task_content,
                    swarm=swarm,
                    context_config=context_config
                )
            )
            metadata.asyncio_task = asyncio_task

            logger.info(f"[BackgroundTaskManager] Submitted task {task_id}: {task_content[:50]}...")
            return task_id

    async def _run_task_background(
        self,
        task_id: str,
        agent_name: str,
        task_content: str,
        swarm,
        context_config=None
    ):
        """
        Execute task in background.

        This method runs as an asyncio.Task and manages the full lifecycle:
        1. Create Task object
        2. Run with StreamingOutputs
        3. Track progress
        4. Extract result
        5. Update metadata
        6. Persist output to file

        Args:
            task_id: Task ID to execute
            agent_name: Agent name
            task_content: Task description
            swarm: Swarm instance
            context_config: Optional context configuration
        """
        metadata = self.tasks[task_id]
        metadata.status = "running"
        metadata.started_at = datetime.now()
        await self._persist_tasks()

        # Setup output file
        output_path = self._get_task_output_path(task_id)
        metadata.output_file = str(output_path)

        try:
            # Open output file for writing
            with open(output_path, 'w', encoding='utf-8') as log_file:
                # Write header
                log_file.write(f"Task: {task_content}\n")
                log_file.write(f"Agent: {agent_name}\n")
                log_file.write(f"Started: {metadata.started_at.strftime('%Y-%m-%d %H:%M:%S')}\n")
                log_file.write("=" * 80 + "\n\n")
                log_file.flush()

                # Build Task object (reuse existing pattern from LocalAgentExecutor)
                task = Task(
                    input=task_content,
                    swarm=swarm,
                    session_id=self.session_id,
                    context_config=context_config
                )

                # Run with streaming (existing infrastructure)
                streaming_outputs = Runners.streamed_run_task(
                    task,
                    cancel_run_impl_task_on_cleanup=False,
                )
                metadata.streaming_outputs = streaming_outputs

                # Track progress by consuming stream
                message_count = 0
                async for output in streaming_outputs.stream_events():
                    # Count messages for progress estimation
                    message_count += 1

                    # Format and save output to buffer for follow command
                    timestamp = datetime.now()
                    formatted_output = self._format_output(output)
                    if formatted_output:
                        # Write to file (persistent)
                        log_line = f"[{timestamp.strftime('%H:%M:%S')}] {formatted_output}\n"
                        log_file.write(log_line)
                        log_file.flush()  # Ensure real-time write

                        # Save to buffer (in-memory for follow command)
                        metadata.output_buffer.append((timestamp, formatted_output))
                        logger.debug(f"[BackgroundTaskManager] Added output: {formatted_output[:100]}")

                    # Update current step display
                    metadata.current_step = f"Processing step {message_count}..."

                    # Update progress (basic estimation)
                    # Note: In Phase 2, could extract actual step counts from output
                    if hasattr(output, 'output_type'):
                        output_type = output.output_type()
                        if output_type == 'step':
                            # StepOutput detected
                            if hasattr(output, 'name'):
                                metadata.current_step = f"Step: {output.name}"

                # Wait for the real underlying task completion and extract final status/result.
                # Stream completion alone is not authoritative enough.
                task_response = await self._get_definitive_task_response(task.id, streaming_outputs)
                metadata.status = self._normalize_final_status(task_response)

                result_text = None
                if task_response and hasattr(task_response, 'answer'):
                    result_text = task_response.answer
                if not result_text:
                    try:
                        result_text = streaming_outputs.get_message_output_content()
                    except Exception:
                        result_text = None

                if metadata.status == "completed":
                    metadata.result = result_text or "Task completed (no result available)"
                    metadata.progress_percentage = 100.0
                elif metadata.status in {"failed", "cancelled", "interrupted", "timeout"}:
                    metadata.error = (
                        getattr(task_response, "msg", None)
                        or getattr(task_response, "answer", None)
                        or result_text
                        or f"Task ended with status: {metadata.status}"
                    )
                    metadata.result = result_text
                    metadata.progress_percentage = 100.0
                else:
                    metadata.result = result_text

                # Write footer to log file
                log_file.write(f"\n{'=' * 80}\n")
                log_file.write(f"Status: {metadata.status}\n")
                log_file.write(f"Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                if metadata.error and metadata.status != "completed":
                    log_file.write(f"\nError:\n{metadata.error}\n")
                if metadata.result:
                    log_file.write(f"\nResult:\n{metadata.result}\n")
                log_file.flush()

                logger.info(f"[BackgroundTaskManager] Task {task_id} finished with status={metadata.status}")
                await self._persist_tasks()

        except asyncio.CancelledError:
            metadata.status = "cancelled"
            metadata.error = "Task cancelled by user"
            logger.info(f"[BackgroundTaskManager] Task {task_id} was cancelled")

            # Write cancellation to log file
            try:
                with open(output_path, 'a', encoding='utf-8') as log_file:
                    log_file.write(f"\n{'=' * 80}\n")
                    log_file.write(f"Status: cancelled\n")
                    log_file.write(f"Cancelled at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            except Exception as e:
                logger.error(f"[BackgroundTaskManager] Failed to write cancellation to log: {e}")
            await self._persist_tasks()

            raise  # Re-raise to properly cancel the task

        except Exception as e:
            metadata.status = "failed"
            metadata.error = str(e)
            logger.error(f"[BackgroundTaskManager] Task {task_id} failed: {e}")

            # Write error to log file
            try:
                with open(output_path, 'a', encoding='utf-8') as log_file:
                    log_file.write(f"\n{'=' * 80}\n")
                    log_file.write(f"Status: failed\n")
                    log_file.write(f"Error: {str(e)}\n")
                    log_file.write(f"Failed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            except Exception as write_error:
                logger.error(f"[BackgroundTaskManager] Failed to write error to log: {write_error}")
            await self._persist_tasks()

        finally:
            metadata.completed_at = datetime.now()
            try:
                await self._persist_tasks()
            except Exception as e:
                logger.warning(f"[BackgroundTaskManager] Failed to persist tasks in finally: {e}")

    def list_tasks(self) -> List[TaskMetadata]:
        """
        List all tasks (sorted by submission time, newest first).

        Returns:
            List of TaskMetadata objects, sorted newest first

        Example:
            >>> tasks = manager.list_tasks()
            >>> for task in tasks:
            ...     print(f"{task.task_id}: {task.status}")
        """
        return sorted(
            self.tasks.values(),
            key=lambda t: t.submitted_at,
            reverse=True
        )

    def get_task(self, task_id: str) -> Optional[TaskMetadata]:
        """
        Get task by ID.

        Args:
            task_id: Task ID to retrieve

        Returns:
            TaskMetadata or None if not found

        Example:
            >>> task = manager.get_task("task-001")
            >>> if task:
            ...     print(task.status)
        """
        return self.tasks.get(task_id)

    async def cancel_task(self, task_id: str) -> bool:
        """
        Cancel running task.

        Args:
            task_id: Task ID to cancel

        Returns:
            True if task was cancelled, False if not running or not found

        Example:
            >>> success = await manager.cancel_task("task-001")
            >>> if success:
            ...     print("Task cancelled")
        """
        async with self._lock:
            metadata = self.tasks.get(task_id)
            if not metadata:
                return False

            if metadata.status != "running":
                return False

            # Cancel asyncio task
            if metadata.asyncio_task and not metadata.asyncio_task.done():
                metadata.asyncio_task.cancel()
                logger.info(f"[BackgroundTaskManager] Cancelled task {task_id}")
                await self._persist_tasks()
                return True

            return False

    def get_stats(self) -> Dict[str, int]:
        """
        Get task statistics.

        Returns:
            Dict with counts: total, running, completed, failed, cancelled, pending

        Example:
            >>> stats = manager.get_stats()
            >>> print(f"Total: {stats['total']}, Running: {stats['running']}")
        """
        stats = {
            "total": len(self.tasks),
            "running": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
            "interrupted": 0,
            "timeout": 0,
            "pending": 0
        }

        for task in self.tasks.values():
            if task.status in stats:
                stats[task.status] += 1

        return stats

    def _format_output(self, output) -> Optional[str]:
        """
        Format output object to human-readable string.

        Args:
            output: Output object from StreamingOutputs.stream_events()

        Returns:
            Formatted string or None if output should be ignored
        """
        # Debug: Log output type and attributes (will be removed later)
        logger.debug(f"[BackgroundTaskManager] Processing output: type={type(output).__name__}")
        if hasattr(output, 'output_type'):
            logger.debug(f"[BackgroundTaskManager] output_type()={output.output_type()}")

        # Check if output has output_type method
        if not hasattr(output, 'output_type'):
            # Try to handle raw output objects
            logger.debug(f"[BackgroundTaskManager] No output_type, attributes: {dir(output)}")
            return None

        output_type = output.output_type()

        if output_type == 'message':
            # MessageOutput: LLM response
            if hasattr(output, 'response') and output.response:
                return f"💬 {output.response[:200]}"  # Truncate long messages
            if hasattr(output, 'reasoning') and output.reasoning:
                return f"💭 {output.reasoning[:200]}"

        elif output_type == 'step':
            # StepOutput: Agent step
            if hasattr(output, 'name') and hasattr(output, 'status'):
                emoji = "🔄" if output.status == "START" else "✅" if output.status == "FINISHED" else "❌"

                # Use alias_name if available (more readable), otherwise use name
                display_name = output.name
                if hasattr(output, 'alias_name') and output.alias_name:
                    display_name = output.alias_name

                # Also show step number if available
                step_info = ""
                if hasattr(output, 'step_num'):
                    step_info = f" #{output.step_num}"

                return f"{emoji} Step{step_info}: {display_name} ({output.status})"

        elif output_type == 'tool_call':
            # ToolCallOutput: Tool being called
            # Data is stored in output.data as a ToolCall object
            logger.debug(f"[BackgroundTaskManager] Tool call detected, attributes: {dir(output)}")

            tool_name = None
            args_str = ""

            # Try to extract from data field (ToolCall object)
            if hasattr(output, 'data') and output.data:
                tool_call = output.data
                if hasattr(tool_call, 'function'):
                    # OpenAI-style ToolCall
                    if hasattr(tool_call.function, 'name'):
                        tool_name = tool_call.function.name
                    if hasattr(tool_call.function, 'arguments'):
                        args_str = str(tool_call.function.arguments)[:100]
                elif hasattr(tool_call, 'name'):
                    # Direct name field
                    tool_name = tool_call.name
                    if hasattr(tool_call, 'arguments'):
                        args_str = str(tool_call.arguments)[:100]

            # Fallback: try direct attributes
            if not tool_name and hasattr(output, 'tool_name'):
                tool_name = output.tool_name

            if tool_name:
                return f"🔧 Tool: {tool_name}({args_str})"
            else:
                logger.debug(f"[BackgroundTaskManager] Could not extract tool_name from ToolCallOutput")

        elif output_type == 'tool_call_result':
            # ToolResultOutput: Tool execution result
            # Has tool_name field and result in data
            logger.debug(f"[BackgroundTaskManager] Tool result detected, attributes: {dir(output)}")

            tool_name = getattr(output, 'tool_name', None)
            result_str = ""

            # Try to get result from various fields
            if hasattr(output, 'data') and output.data:
                result_str = str(output.data)[:150]
            elif hasattr(output, 'result'):
                result_str = str(output.result)[:150]
            elif hasattr(output, 'content'):
                result_str = str(output.content)[:150]

            if tool_name:
                # Truncate result intelligently
                if len(result_str) > 150:
                    result_str = result_str[:147] + "..."
                return f"✅ Tool Result: {tool_name} → {result_str}"
            else:
                logger.debug(f"[BackgroundTaskManager] Could not extract tool_name from ToolResultOutput")

        elif output_type == 'chunk':
            # ChunkOutput: Streaming LLM chunks (skip to reduce noise)
            return None

        # Unknown output type - log for debugging
        logger.debug(f"[BackgroundTaskManager] Unknown output_type: {output_type}, attributes: {dir(output)}")
        return None

    async def cleanup(self):
        """
        Cleanup all running tasks.

        Called when CLI exits. Cancels all running tasks gracefully.

        Example:
            >>> await manager.cleanup()
            [INFO] Cleaning up background tasks...
        """
        logger.info("[BackgroundTaskManager] Cleaning up background tasks...")

        async with self._lock:
            for task_id, metadata in self.tasks.items():
                if metadata.status == "running" and metadata.asyncio_task:
                    if not metadata.asyncio_task.done():
                        metadata.asyncio_task.cancel()
                        logger.info(f"[BackgroundTaskManager] Cancelled task {task_id} during cleanup")

            # Wait briefly for tasks to cancel
            await asyncio.sleep(0.1)
