# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Built-in SpawnSubagent Tool

This is a pre-registered tool for subagent delegation, avoiding the dynamic
code generation issues of @be_tool decorator.

Supports multiple execution modes:
- spawn: Execute single subagent task (blocking)
- spawn_parallel: Execute multiple subagent tasks concurrently (blocking)
- spawn_background: Start subagent in background, returns immediately (non-blocking)
- check_task: Check status of background task
- wait_task: Wait for background task to complete
- cancel_task: Cancel a running background task
"""

import asyncio
import json
import time
import traceback
import uuid
from typing import List, Dict, Any, Tuple, Optional
from aworld.core.tool.base import AsyncTool, ToolFactory
from aworld.core.tool.action import ToolAction
from aworld.core.common import Observation, ActionModel, ToolActionInfo, ParamInfo, ActionResult
from aworld.logs.util import logger


class SpawnSubagentAction(ToolAction):
    """Action definitions for SpawnSubagentTool"""
    SPAWN = ToolActionInfo(
        name="spawn",
        desc="Delegate a subtask to a specialized subagent",
        input_params={
            "name": ParamInfo(
                name="name",
                type="string",
                required=True,
                desc=(
                    "Name of the subagent to invoke (must match an available subagent). "
                    "Check the 'Available Subagents' section in your system prompt for valid names. "
                    "Examples: 'code_analyzer', 'web_searcher', 'report_writer'."
                )
            ),
            "directive": ParamInfo(
                name="directive",
                type="string",
                required=True,
                desc=(
                    "Clear, specific instruction for the subagent. Be explicit about: "
                    "(1) what task to perform, (2) what output format you expect, "
                    "(3) any constraints or requirements. Example: 'Analyze the code in "
                    "src/parser.py and identify performance bottlenecks. Return a bulleted "
                    "list with line numbers and improvement suggestions.'"
                )
            ),
            "model": ParamInfo(
                name="model",
                type="string",
                required=False,
                desc=(
                    "Optional: Override the subagent's default model. Use 'inherit' (default) "
                    "to use the same model as the parent agent. Examples: 'gpt-4o', 'claude-sonnet-4'"
                )
            ),
            "disallowedTools": ParamInfo(
                name="disallowedTools",
                type="string",
                required=False,
                desc=(
                    "Optional: Comma-separated list of tools to deny access to. Use for security: "
                    "prevent subagent from executing dangerous operations. "
                    "Example: 'terminal,write_file,git_commit'. Leave empty to allow all "
                    "subagent's configured tools (subject to parent agent's tool set)."
                )
            )
        }
    )

    SPAWN_PARALLEL = ToolActionInfo(
        name="spawn_parallel",
        desc="Delegate multiple independent subtasks to subagents in parallel for faster execution",
        input_params={
            "tasks": ParamInfo(
                name="tasks",
                type="array",
                required=True,
                items={"type": "object"},  # Array of task objects
                desc=(
                    "Array of task objects, each containing: "
                    "{'name': '<subagent_name>', 'directive': '<task_instruction>', "
                    "'model': '<optional_model>', 'disallowedTools': '<optional_comma_separated_list>'}. "
                    "Example: [{\"name\": \"code_analyzer\", \"directive\": \"Analyze module A\"}, "
                    "{\"name\": \"doc_writer\", \"directive\": \"Write docs for module B\"}]. "
                    "Use this when you have multiple independent subtasks that can run concurrently."
                )
            ),
            "max_concurrent": ParamInfo(
                name="max_concurrent",
                type="integer",
                required=False,
                default_value=10,
                desc=(
                    "Optional: Maximum number of subagents to run concurrently (default: 10). "
                    "Use lower values to limit resource usage, higher values for faster execution "
                    "when tasks are I/O-bound."
                )
            ),
            "aggregate": ParamInfo(
                name="aggregate",
                type="boolean",
                required=False,
                default_value=True,
                desc=(
                    "Optional: Whether to aggregate results into a single summary (default: true). "
                    "Set to false to get structured JSON output with individual task results."
                )
            ),
            "fail_fast": ParamInfo(
                name="fail_fast",
                type="boolean",
                required=False,
                default_value=False,
                desc=(
                    "Optional: Whether to stop execution on first task failure (default: false). "
                    "When true, remaining tasks are cancelled if any task fails. "
                    "When false, all tasks complete and failures are reported in results."
                )
            )
        }
    )

    SPAWN_BACKGROUND = ToolActionInfo(
        name="spawn_background",
        desc="Start a subagent in background, returns immediately with task_id (non-blocking)",
        input_params={
            "name": ParamInfo(
                name="name",
                type="string",
                required=True,
                desc=(
                    "Name of the subagent to invoke (must match an available subagent). "
                    "Check the 'Available Subagents' section in your system prompt for valid names."
                )
            ),
            "directive": ParamInfo(
                name="directive",
                type="string",
                required=True,
                desc=(
                    "Clear, specific instruction for the subagent. Be explicit about task requirements."
                )
            ),
            "model": ParamInfo(
                name="model",
                type="string",
                required=False,
                desc=(
                    "Optional: Override the subagent's default model. Use 'inherit' (default) "
                    "to use the same model as the parent agent."
                )
            ),
            "disallowedTools": ParamInfo(
                name="disallowedTools",
                type="string",
                required=False,
                desc=(
                    "Optional: Comma-separated list of tools to deny access to."
                )
            ),
            "task_id": ParamInfo(
                name="task_id",
                type="string",
                required=False,
                desc=(
                    "Optional: Custom task ID. If not provided, one will be generated automatically. "
                    "Must be unique among active background tasks."
                )
            )
        }
    )

    CHECK_TASK = ToolActionInfo(
        name="check_task",
        desc="Check status of a background task (running, completed, or error)",
        input_params={
            "task_id": ParamInfo(
                name="task_id",
                type="string",
                required=True,
                desc=(
                    "Task ID returned by spawn_background. "
                    "Use 'all' to check status of all background tasks."
                )
            ),
            "include_result": ParamInfo(
                name="include_result",
                type="boolean",
                required=False,
                default_value=True,
                desc=(
                    "Optional: Whether to include the full result if task is completed (default: true). "
                    "Set to false to only get status without retrieving large results."
                )
            )
        }
    )

    WAIT_TASK = ToolActionInfo(
        name="wait_task",
        desc="Wait for one or more background tasks to complete (blocking)",
        input_params={
            "task_ids": ParamInfo(
                name="task_ids",
                type="string",
                required=True,
                desc=(
                    "Comma-separated list of task IDs to wait for. "
                    "Use 'any' to wait for the first task to complete. "
                    "Use 'all' to wait for all background tasks to complete."
                )
            ),
            "timeout": ParamInfo(
                name="timeout",
                type="number",
                required=False,
                default_value=300,
                desc=(
                    "Optional: Maximum time to wait in seconds (default: 300). "
                    "Returns early if tasks complete before timeout. "
                    "Set to 0 for unlimited wait."
                )
            )
        }
    )

    CANCEL_TASK = ToolActionInfo(
        name="cancel_task",
        desc="Cancel a running background task",
        input_params={
            "task_id": ParamInfo(
                name="task_id",
                type="string",
                required=True,
                desc=(
                    "Task ID returned by spawn_background. "
                    "Use 'all' to cancel all background tasks."
                )
            )
        }
    )


@ToolFactory.register(
    name='spawn_subagent',
    desc='Delegate subtask to specialized subagent',
    supported_action=SpawnSubagentAction,  # Provide ToolAction enum
    asyn=True  # AsyncTool must set asyn=True
)
class SpawnSubagentTool(AsyncTool):
    """
    Built-in tool for delegating subtasks to specialized subagents.

    This tool allows LLM to spawn subagents dynamically based on task requirements.
    Unlike dynamically generated tools, this is a fixed class that can be reliably
    registered and discovered by ToolFactory.

    Supports multiple execution modes:
        - Foreground (blocking): spawn, spawn_parallel
        - Background (non-blocking): spawn_background
        - Task management: check_task, wait_task, cancel_task

    Design:
        - Registered once globally to ToolFactory (as a class)
        - SubagentManager instance obtained dynamically from agent context
        - Each agent's SubagentManager accessible via BaseAgent._get_current_agent()
        - Implements AsyncTool interface for aworld tool system
        - Background tasks managed via asyncio.create_task() with task registry

    Usage:
        # Global registration (once, in module import):
        @ToolFactory.register(name='spawn_subagent', desc='Delegate to subagent')
        class SpawnSubagentTool(AsyncTool): ...
    """

    def __init__(self, subagent_manager=None, conf=None, **kwargs):
        """
        Initialize SpawnSubagentTool.

        Args:
            subagent_manager: Optional SubagentManager instance.
                             If None, will be retrieved from current agent at runtime.
            conf: Optional tool configuration. If None, an empty dict is used.
            **kwargs: Additional configuration passed to AsyncTool
        """
        # subagent_manager can be None for global registration
        # It will be obtained from current agent context during execution
        self.subagent_manager = subagent_manager

        # Background task registry
        # Format: {task_id: {'task': asyncio.Task, 'name': str, 'directive': str,
        #                    'start_time': float, 'status': str, 'result': Any, 'error': str}}
        self._background_tasks: Dict[str, Dict[str, Any]] = {}

        # Lock for background task registry operations
        self._bg_lock = asyncio.Lock()

        # Use empty dict as conf if not provided (for testing)
        if conf is None:
            conf = {}

        super().__init__(conf=conf, **kwargs)

        if subagent_manager:
            # Try to get agent name for logging, but don't fail if not available (e.g., in tests)
            try:
                agent_name = subagent_manager.agent.name()
                logger.info(f"SpawnSubagentTool initialized for agent '{agent_name}'")
            except (AttributeError, TypeError):
                logger.info("SpawnSubagentTool initialized with subagent_manager")
        else:
            logger.debug("SpawnSubagentTool initialized (global registration, no agent context)")

    async def reset(self, *, seed: int | None = None, options: Dict[str, str] | None = None) -> Tuple[
            Any, dict[str, Any]]:
        """
        Reset tool state (required by AsyncTool interface).

        For spawn_subagent, there's no persistent state to reset.
        """
        if self.subagent_manager is None:
            bound_manager = self._get_subagent_manager(context=getattr(self, 'context', None))
            if bound_manager is not None:
                self.subagent_manager = bound_manager
        return (Observation(content="SpawnSubagentTool ready"), {})

    async def do_step(
        self,
        action: List[ActionModel],
        **kwargs
    ) -> Tuple[Observation, float, bool, bool, Dict[str, Any]]:
        """
        Execute subagent spawning (single or parallel).

        This is called when LLM invokes the spawn_subagent tool.

        Args:
            action: List of ActionModel containing tool call parameters
            **kwargs: Additional context (message, etc.)

        Returns:
            Tuple of (observation, reward, terminated, truncated, info)
            - observation: Subagent's result wrapped in Observation
            - reward: 1.0 for success, 0.0 for failure
            - terminated: Always False (tool execution doesn't end episode)
            - truncated: Always False
            - info: Metadata about the spawn operation
        """
        if not action or len(action) == 0:
            error_msg = "spawn_subagent: No action provided"
            logger.error(error_msg)
            return self._error_response(error_msg, {'error': 'No action'})

        # Extract action_name to route to appropriate handler
        action_model = action[0]
        action_name = action_model.action_name if hasattr(action_model, 'action_name') else 'spawn'

        logger.debug(f"spawn_subagent: Routing to action '{action_name}'")

        # Route to appropriate handler
        if action_name == 'spawn' or not action_name:  # Default to spawn for backward compatibility
            return await self._spawn_single(action_model, **kwargs)
        elif action_name == 'spawn_parallel':
            return await self._spawn_parallel(action_model, **kwargs)
        elif action_name == 'spawn_background':
            return await self._spawn_background(action_model, **kwargs)
        elif action_name == 'check_task':
            return await self._check_task(action_model, **kwargs)
        elif action_name == 'wait_task':
            return await self._wait_task(action_model, **kwargs)
        elif action_name == 'cancel_task':
            return await self._cancel_task(action_model, **kwargs)
        else:
            error_msg = f"spawn_subagent: Unknown action '{action_name}'"
            logger.error(error_msg)
            return self._error_response(error_msg, {'error': f'Unknown action: {action_name}'})

    async def _spawn_single(
        self,
        action_model: ActionModel,
        **kwargs
    ) -> Tuple[Observation, float, bool, bool, Dict[str, Any]]:
        """
        Execute single subagent spawning (original behavior).

        Args:
            action_model: ActionModel containing tool call parameters
            **kwargs: Additional context

        Returns:
            Tuple of (observation, reward, terminated, truncated, info)
        """
        params = action_model.params if hasattr(action_model, 'params') else {}

        name = params.get('name')
        directive = params.get('directive')
        model = params.get('model')
        disallowed_tools_str = params.get('disallowedTools')

        # Validate required parameters
        if not name:
            error_msg = "spawn_subagent: 'name' parameter is required"
            logger.error(error_msg)
            return self._error_response(error_msg, {'error': 'Missing name parameter'})

        if not directive:
            error_msg = "spawn_subagent: 'directive' parameter is required"
            logger.error(error_msg)
            return self._error_response(error_msg, {'error': 'Missing directive parameter'})

        # Parse disallowedTools
        disallowed_tools = []
        if disallowed_tools_str:
            disallowed_tools = [t.strip() for t in disallowed_tools_str.split(',') if t.strip()]

        # Build spawn kwargs
        spawn_kwargs = {}
        if model:
            spawn_kwargs['model'] = model
        if disallowed_tools:
            spawn_kwargs['disallowedTools'] = disallowed_tools

        # Log the spawn operation
        logger.debug(
            f"spawn_subagent: Delegating to subagent '{name}' "
            f"with directive: {directive[:100]}..."
        )
        parent_context = self._resolve_context(kwargs)

        try:
            # Get SubagentManager
            subagent_manager = self._get_subagent_manager(
                action_model=action_model,
                context=parent_context
            )
            if not subagent_manager:
                error_msg = "spawn_subagent: No SubagentManager available"
                logger.error(error_msg)
                return self._error_response(error_msg, {'error': 'No SubagentManager'})

            # Call SubagentManager.spawn()
            result = await subagent_manager.spawn(
                name=name,
                directive=directive,
                context=parent_context,
                **spawn_kwargs
            )

            logger.info(
                f"spawn_subagent: Successfully spawned '{name}', "
                f"result length: {len(str(result))}"
            )

            # Return successful observation
            return (
                Observation(content=str(result)),
                1.0,  # Success reward
                False,  # Not terminated
                False,  # Not truncated
                {
                    'action': 'spawn',
                    'subagent': name,
                    'directive_length': len(directive),
                    'result_length': len(str(result))
                }
            )

        except Exception as e:
            error_msg = f"spawn_subagent: Failed to spawn '{name}': {str(e)}"
            logger.error(f"{error_msg}\n{traceback.format_exc()}")
            return self._error_response(error_msg, {'error': str(e), 'subagent': name})

    async def _spawn_parallel(
        self,
        action_model: ActionModel,
        **kwargs
    ) -> Tuple[Observation, float, bool, bool, Dict[str, Any]]:
        """
        Execute parallel subagent spawning (batch mode).

        Args:
            action_model: ActionModel containing tasks array and options
            **kwargs: Additional context

        Returns:
            Tuple of (observation, reward, terminated, truncated, info)
        """
        import time
        start_time = time.time()

        params = action_model.params if hasattr(action_model, 'params') else {}
        parent_context = self._resolve_context(kwargs)

        tasks = params.get('tasks', [])
        max_concurrent = params.get('max_concurrent', 10)
        aggregate = params.get('aggregate', True)
        fail_fast = params.get('fail_fast', False)

        # Validate required parameters
        if not tasks:
            error_msg = "spawn_parallel: 'tasks' parameter is required and cannot be empty"
            logger.error(error_msg)
            return self._error_response(error_msg, {'error': 'Missing or empty tasks parameter'})

        if not isinstance(tasks, list):
            error_msg = f"spawn_parallel: 'tasks' must be an array, got {type(tasks)}"
            logger.error(error_msg)
            return self._error_response(error_msg, {'error': 'Invalid tasks type'})

        logger.info(
            f"spawn_parallel: Starting {len(tasks)} subagents in parallel "
            f"(max_concurrent={max_concurrent}, aggregate={aggregate}, fail_fast={fail_fast})"
        )

        try:
            # Get SubagentManager
            subagent_manager = self._get_subagent_manager(
                action_model=action_model,
                context=parent_context
            )
            if not subagent_manager:
                error_msg = "spawn_parallel: No SubagentManager available"
                logger.error(error_msg)
                return self._error_response(error_msg, {'error': 'No SubagentManager'})

            # Execute tasks in parallel with concurrency control
            results = await self._execute_tasks_parallel(
                subagent_manager,
                tasks,
                parent_context,
                max_concurrent,
                fail_fast
            )

            elapsed = time.time() - start_time

            # Check if any tasks failed
            failed_count = sum(1 for r in results if r.get('status') == 'error')
            success_count = len(results) - failed_count

            logger.info(
                f"spawn_parallel: Completed {len(results)} tasks in {elapsed:.2f}s "
                f"({success_count} succeeded, {failed_count} failed)"
            )

            # Format results
            if aggregate:
                formatted_result = self._aggregate_results(results)
            else:
                formatted_result = self._format_structured_results(results)

            # Determine reward (1.0 if all succeeded, proportional otherwise)
            reward = success_count / len(results) if results else 0.0

            return (
                Observation(content=formatted_result),
                reward,
                False,  # Not terminated
                False,  # Not truncated
                {
                    'action': 'spawn_parallel',
                    'total_tasks': len(results),
                    'success_count': success_count,
                    'failed_count': failed_count,
                    'elapsed_seconds': round(elapsed, 2),
                    'aggregate': aggregate
                }
            )

        except Exception as e:
            elapsed = time.time() - start_time
            error_msg = f"spawn_parallel: Exception during execution after {elapsed:.2f}s: {str(e)}"
            logger.error(f"{error_msg}\n{traceback.format_exc()}")
            return self._error_response(error_msg, {'error': str(e), 'elapsed_seconds': round(elapsed, 2)})

    async def _execute_tasks_parallel(
        self,
        subagent_manager,
        tasks: List[Dict[str, Any]],
        parent_context,
        max_concurrent: int,
        fail_fast: bool
    ) -> List[Dict[str, Any]]:
        """
        Execute multiple subagent tasks in parallel with concurrency control.

        Args:
            subagent_manager: SubagentManager instance
            tasks: List of task configurations
            max_concurrent: Maximum number of concurrent executions
            fail_fast: Whether to stop on first failure

        Returns:
            List of result dictionaries, each containing:
            - name: Subagent name
            - status: 'success' or 'error'
            - result: Result string (for success)
            - error: Error message (for error)
            - elapsed: Execution time in seconds
        """
        semaphore = asyncio.Semaphore(max_concurrent)

        async def spawn_with_limit(task: Dict[str, Any], task_idx: int):
            """Execute single task with semaphore limit and error handling"""
            import time
            task_start = time.time()

            # Extract task configuration
            name = task.get('name')
            directive = task.get('directive')

            if not name or not directive:
                logger.warning(
                    f"spawn_parallel: Task #{task_idx} missing required fields "
                    f"(name={name}, directive={'<present>' if directive else '<missing>'}), skipping"
                )
                return {
                    'name': name or f'task_{task_idx}',
                    'status': 'error',
                    'error': 'Missing required field: name or directive',
                    'elapsed': 0.0,
                    'task_index': task_idx
                }

            # Parse optional parameters
            model = task.get('model')
            disallowed_tools_str = task.get('disallowedTools')
            disallowed_tools = []
            if disallowed_tools_str:
                if isinstance(disallowed_tools_str, str):
                    disallowed_tools = [t.strip() for t in disallowed_tools_str.split(',') if t.strip()]
                elif isinstance(disallowed_tools_str, list):
                    disallowed_tools = disallowed_tools_str

            spawn_kwargs = {}
            if model:
                spawn_kwargs['model'] = model
            if disallowed_tools:
                spawn_kwargs['disallowedTools'] = disallowed_tools

            # Execute with concurrency control
            async with semaphore:
                try:
                    logger.debug(
                        f"spawn_parallel: Task #{task_idx} ({name}) starting "
                        f"with directive: {directive[:80]}..."
                    )

                    result = await subagent_manager.spawn(
                        name=name,
                        directive=directive,
                        context=parent_context,
                        **spawn_kwargs
                    )

                    elapsed = time.time() - task_start

                    logger.debug(
                        f"spawn_parallel: Task #{task_idx} ({name}) succeeded "
                        f"in {elapsed:.2f}s, result length: {len(str(result))}"
                    )

                    return {
                        'name': name,
                        'status': 'success',
                        'result': str(result),
                        'elapsed': round(elapsed, 2),
                        'task_index': task_idx
                    }

                except Exception as e:
                    elapsed = time.time() - task_start

                    logger.error(
                        f"spawn_parallel: Task #{task_idx} ({name}) failed "
                        f"after {elapsed:.2f}s: {str(e)}"
                    )

                    return {
                        'name': name,
                        'status': 'error',
                        'error': str(e),
                        'elapsed': round(elapsed, 2),
                        'task_index': task_idx
                    }

        # Create all tasks (wrap coroutines as Tasks for proper cancellation support)
        spawn_tasks = [
            asyncio.create_task(spawn_with_limit(task, idx))
            for idx, task in enumerate(tasks, start=1)
        ]

        # Execute based on fail_fast setting
        if fail_fast:
            # Stop on first failure
            results = []
            for coro in asyncio.as_completed(spawn_tasks):
                result = await coro
                results.append(result)

                if result.get('status') == 'error':
                    logger.warning(
                        f"spawn_parallel: Task {result.get('name')} failed, "
                        f"cancelling remaining tasks (fail_fast=True)"
                    )
                    # Cancel remaining tasks
                    for task in spawn_tasks:
                        if not task.done():
                            task.cancel()
                    break

            # Sort by task_index to maintain order
            results.sort(key=lambda r: r.get('task_index', 0))
            return results
        else:
            # Wait for all tasks to complete (return_exceptions=False, exceptions already caught)
            results = await asyncio.gather(*spawn_tasks)
            return list(results)

    def _aggregate_results(self, results: List[Dict[str, Any]]) -> str:
        """
        Aggregate multiple subagent results into a human-readable summary.

        Args:
            results: List of result dictionaries

        Returns:
            Formatted markdown summary
        """
        summary = "## Parallel Subagent Execution Results\n\n"

        # Summary statistics
        total = len(results)
        success_count = sum(1 for r in results if r.get('status') == 'success')
        failed_count = total - success_count
        total_time = sum(r.get('elapsed', 0) for r in results)

        summary += f"**Summary:** {success_count}/{total} tasks succeeded "
        summary += f"(Total execution time: {total_time:.2f}s)\n\n"

        if failed_count > 0:
            summary += f"⚠️ **Warning:** {failed_count} task(s) failed\n\n"

        summary += "---\n\n"

        # Individual task results
        for idx, res in enumerate(results, 1):
            name = res.get('name', f'task_{idx}')
            status = res.get('status', 'unknown')
            elapsed = res.get('elapsed', 0)

            # Status emoji
            status_emoji = "✅" if status == 'success' else "❌"

            summary += f"### {status_emoji} Task {idx}: {name}\n"
            summary += f"**Status:** {status} (took {elapsed}s)\n\n"

            if status == 'success':
                result_text = res.get('result', '')

                # Truncate very long results
                max_length = 800
                if len(result_text) > max_length:
                    summary += f"**Result (truncated):**\n```\n{result_text[:max_length]}\n... (truncated {len(result_text) - max_length} chars)\n```\n\n"
                else:
                    summary += f"**Result:**\n```\n{result_text}\n```\n\n"
            else:
                error_msg = res.get('error', 'Unknown error')
                summary += f"**Error:** {error_msg}\n\n"

            summary += "---\n\n"

        return summary

    def _format_structured_results(self, results: List[Dict[str, Any]]) -> str:
        """
        Format results as structured JSON output.

        Args:
            results: List of result dictionaries

        Returns:
            JSON string with structured results
        """
        # Create structured output
        output = {
            'summary': {
                'total_tasks': len(results),
                'success_count': sum(1 for r in results if r.get('status') == 'success'),
                'failed_count': sum(1 for r in results if r.get('status') == 'error'),
                'total_elapsed': round(sum(r.get('elapsed', 0) for r in results), 2)
            },
            'tasks': []
        }

        for res in results:
            task_result = {
                'name': res.get('name'),
                'status': res.get('status'),
                'elapsed': res.get('elapsed')
            }

            if res.get('status') == 'success':
                task_result['result'] = res.get('result')
            else:
                task_result['error'] = res.get('error')

            output['tasks'].append(task_result)

        return json.dumps(output, indent=2, ensure_ascii=False)

    def _resolve_context(self, kwargs: Dict[str, Any]) -> Any:
        message = kwargs.get('message')
        if message is not None and getattr(message, 'context', None) is not None:
            return message.context
        return getattr(self, 'context', None)

    @staticmethod
    def _get_agent_info_value(agent_info: Any, key: str) -> Any:
        """Read an agent_info value from either a mapping or an object."""
        if agent_info is None:
            return None
        if isinstance(agent_info, dict):
            return agent_info.get(key)
        return getattr(agent_info, key, None)

    def _get_subagent_manager(self, action_model: Optional[ActionModel] = None, context=None):
        """
        Get SubagentManager from current agent context.

        Returns:
            SubagentManager instance or None
        """
        subagent_manager = self.subagent_manager

        if subagent_manager is None:
            # Retrieve from current agent context
            from aworld.core.agent.base import BaseAgent
            current_agent = BaseAgent._get_current_agent()

            if current_agent and hasattr(current_agent, 'subagent_manager') and current_agent.subagent_manager:
                subagent_manager = current_agent.subagent_manager

        if subagent_manager is None and context is not None:
            try:
                swarm = context.swarm
            except Exception:
                swarm = None

            if swarm and getattr(swarm, 'agents', None):
                candidate_agent_ids = []
                if action_model and getattr(action_model, 'agent_name', None):
                    candidate_agent_ids.append(action_model.agent_name)
                agent_info = getattr(context, 'agent_info', None)
                current_agent_id = self._get_agent_info_value(agent_info, 'current_agent_id')
                if current_agent_id:
                    candidate_agent_ids.append(current_agent_id)

                seen = set()
                for agent_id in candidate_agent_ids:
                    if not agent_id or agent_id in seen:
                        continue
                    seen.add(agent_id)
                    agent = swarm.agents.get(agent_id)
                    if agent and getattr(agent, 'subagent_manager', None):
                        subagent_manager = agent.subagent_manager
                        break

        if subagent_manager is None:
            logger.error(
                "spawn_subagent: No SubagentManager available "
                "(agent context missing, current agent not set, or subagent not enabled)"
            )
            return None

        return subagent_manager

    def _error_response(
        self,
        error_msg: str,
        info: Dict[str, Any]
    ) -> Tuple[Observation, float, bool, bool, Dict[str, Any]]:
        """
        Create standardized error response.

        Args:
            error_msg: Error message
            info: Additional info dict

        Returns:
            Error response tuple
        """
        observation = Observation(
            content=error_msg,
            action_result=[
                ActionResult(
                    is_done=True,
                    success=False,
                    content=error_msg,
                    error=info.get('error', error_msg)
                )
            ]
        )
        return (
            observation,
            0.0,  # Failed reward
            False,  # Not terminated
            False,  # Not truncated
            info
        )

    # ==================== Background Task Management ====================

    async def _spawn_background(
        self,
        action_model: ActionModel,
        **kwargs
    ) -> Tuple[Observation, float, bool, bool, Dict[str, Any]]:
        """
        Start a subagent in background, returns immediately (non-blocking).

        This method creates a background asyncio.Task and returns a task_id
        immediately without waiting for the subagent to complete. The orchestrator
        can continue with other work while the subagent runs in parallel.

        Args:
            action_model: ActionModel containing tool call parameters
            **kwargs: Additional context

        Returns:
            Tuple with task_id in observation content
        """
        params = action_model.params if hasattr(action_model, 'params') else {}

        name = params.get('name')
        directive = params.get('directive')
        model = params.get('model')
        disallowed_tools_str = params.get('disallowedTools')
        custom_task_id = params.get('task_id')

        # Validate required parameters
        if not name:
            error_msg = "spawn_background: 'name' parameter is required"
            logger.error(error_msg)
            return self._error_response(error_msg, {'error': 'Missing name parameter'})

        if not directive:
            error_msg = "spawn_background: 'directive' parameter is required"
            logger.error(error_msg)
            return self._error_response(error_msg, {'error': 'Missing directive parameter'})

        # Generate unique task_id
        if custom_task_id:
            task_id = custom_task_id
            # Check for duplicates
            async with self._bg_lock:
                if task_id in self._background_tasks:
                    error_msg = f"spawn_background: Task ID '{task_id}' already exists"
                    logger.error(error_msg)
                    return self._error_response(error_msg, {'error': 'Duplicate task_id'})
        else:
            task_id = f"bg_{name}_{uuid.uuid4().hex[:8]}"

        # Parse disallowedTools
        disallowed_tools = []
        if disallowed_tools_str:
            disallowed_tools = [t.strip() for t in disallowed_tools_str.split(',') if t.strip()]

        # Build spawn kwargs
        spawn_kwargs = {}
        if model:
            spawn_kwargs['model'] = model
        if disallowed_tools:
            spawn_kwargs['disallowedTools'] = disallowed_tools
        parent_context = self._resolve_context(kwargs)
        if parent_context is not None:
            spawn_kwargs['context'] = parent_context

        # ✅ Pass task_id as sub_task_id to ensure ID consistency between
        # Tool layer (_background_tasks) and Context layer (sub_task_list)
        spawn_kwargs['sub_task_id'] = task_id

        # Get SubagentManager
        subagent_manager = self._get_subagent_manager(
            action_model=action_model,
            context=parent_context
        )
        if not subagent_manager:
            error_msg = "spawn_background: No SubagentManager available"
            logger.error(error_msg)
            return self._error_response(error_msg, {'error': 'No SubagentManager'})

        # Create background task (DO NOT await)
        bg_task = asyncio.create_task(
            self._execute_background_task(
                task_id=task_id,
                subagent_manager=subagent_manager,
                name=name,
                directive=directive,
                spawn_kwargs=spawn_kwargs
            )
        )

        # Register task in registry
        async with self._bg_lock:
            self._background_tasks[task_id] = {
                'task': bg_task,
                'name': name,
                'directive': directive,
                'start_time': time.time(),
                'status': 'running',
                'result': None,
                'error': None
            }

        logger.info(
            f"spawn_background: Started '{name}' as task '{task_id}' "
            f"with directive: {directive[:80]}..."
        )

        # ✅ Return immediately (non-blocking)
        return (
            Observation(content=f"Background task started: {task_id}"),
            1.0,  # Success
            False,
            False,
            {
                'action': 'spawn_background',
                'task_id': task_id,
                'subagent': name,
                'directive_length': len(directive)
            }
        )

    async def _execute_background_task(
        self,
        task_id: str,
        subagent_manager,
        name: str,
        directive: str,
        spawn_kwargs: Dict[str, Any]
    ):
        """
        Execute background task and update registry when complete.

        This is the actual background execution coroutine. It runs the subagent,
        captures the result/error, and updates the task registry.

        Args:
            task_id: Task identifier
            subagent_manager: SubagentManager instance
            name: Subagent name
            directive: Task instruction
            spawn_kwargs: Additional spawn parameters
        """
        try:
            logger.debug(f"spawn_background: Task '{task_id}' executing...")

            # Execute subagent (this is where the actual work happens)
            result = await subagent_manager.spawn(
                name=name,
                directive=directive,
                task_type='background',
                **spawn_kwargs
            )

            # Update registry: mark as completed
            async with self._bg_lock:
                if task_id in self._background_tasks:
                    task_info = self._background_tasks[task_id]
                    task_info['status'] = 'completed'
                    task_info['result'] = str(result)
                    elapsed = time.time() - task_info['start_time']

            # Sync status to Context's sub_task_list
            self._sync_status_to_context(task_id, 'completed', str(result))

            logger.info(
                f"spawn_background: Task '{task_id}' completed in {elapsed:.2f}s, "
                f"result length: {len(str(result))}"
            )

        except Exception as e:
            # Update registry: mark as error
            async with self._bg_lock:
                if task_id in self._background_tasks:
                    task_info = self._background_tasks[task_id]
                    task_info['status'] = 'error'
                    task_info['error'] = str(e)
                    elapsed = time.time() - task_info['start_time']

            # Sync status to Context's sub_task_list
            self._sync_status_to_context(task_id, 'error', None, error=str(e))

            logger.error(
                f"spawn_background: Task '{task_id}' failed after {elapsed:.2f}s: {e}"
            )

    async def _check_task(
        self,
        action_model: ActionModel,
        **kwargs
    ) -> Tuple[Observation, float, bool, bool, Dict[str, Any]]:
        """
        Check status of one or all background tasks.

        Args:
            action_model: ActionModel containing task_id parameter
            **kwargs: Additional context

        Returns:
            Tuple with task status information
        """
        params = action_model.params if hasattr(action_model, 'params') else {}
        task_id = params.get('task_id')
        include_result = params.get('include_result', True)

        if not task_id:
            error_msg = "check_task: 'task_id' parameter is required"
            logger.error(error_msg)
            return self._error_response(error_msg, {'error': 'Missing task_id'})

        async with self._bg_lock:
            # Handle 'all' - check all tasks
            if task_id == 'all':
                if not self._background_tasks:
                    return (
                        Observation(content="No background tasks"),
                        1.0,
                        False,
                        False,
                        {'action': 'check_task', 'total_tasks': 0}
                    )

                # Build summary of all tasks
                summary = "## All Background Tasks\n\n"
                for tid, info in self._background_tasks.items():
                    status = info['status']
                    name = info['name']
                    elapsed = time.time() - info['start_time']

                    summary += f"- **{tid}** ({name}): {status} ({elapsed:.1f}s)\n"

                    if status == 'completed' and include_result and info.get('result'):
                        result_preview = info['result'][:200]
                        summary += f"  Result: {result_preview}...\n"
                    elif status == 'error' and info.get('error'):
                        summary += f"  Error: {info['error']}\n"

                return (
                    Observation(content=summary),
                    1.0,
                    False,
                    False,
                    {
                        'action': 'check_task',
                        'task_id': 'all',
                        'total_tasks': len(self._background_tasks)
                    }
                )

            # Check specific task
            if task_id not in self._background_tasks:
                error_msg = f"check_task: Task '{task_id}' not found"
                available = ', '.join(self._background_tasks.keys())
                logger.error(f"{error_msg}. Available: {available}")
                return self._error_response(
                    error_msg,
                    {'error': 'Task not found', 'available_tasks': list(self._background_tasks.keys())}
                )

            task_info = self._background_tasks[task_id]
            status = task_info['status']
            name = task_info['name']
            elapsed = time.time() - task_info['start_time']

            # Build response
            response = f"Task '{task_id}' ({name}): **{status}** ({elapsed:.1f}s elapsed)\n\n"

            if status == 'running':
                response += "Task is still executing. Use check_task again to poll status."
            elif status == 'completed':
                if include_result and task_info.get('result'):
                    response += f"**Result:**\n{task_info['result']}"
                else:
                    response += f"Task completed (result length: {len(task_info.get('result', ''))} chars). Use include_result=true to retrieve."
            elif status == 'error':
                response += f"**Error:**\n{task_info['error']}"

            return (
                Observation(content=response),
                1.0 if status in ['completed', 'running'] else 0.0,
                False,
                False,
                {
                    'action': 'check_task',
                    'task_id': task_id,
                    'status': status,
                    'elapsed': round(elapsed, 2)
                }
            )

    async def _wait_task(
        self,
        action_model: ActionModel,
        **kwargs
    ) -> Tuple[Observation, float, bool, bool, Dict[str, Any]]:
        """
        Wait for one or more background tasks to complete (blocking).

        This method blocks until the specified task(s) complete or timeout occurs.

        Args:
            action_model: ActionModel containing task_ids and timeout
            **kwargs: Additional context

        Returns:
            Tuple with completion status
        """
        params = action_model.params if hasattr(action_model, 'params') else {}
        task_ids_str = params.get('task_ids')
        timeout = params.get('timeout', 300)

        if not task_ids_str:
            error_msg = "wait_task: 'task_ids' parameter is required"
            logger.error(error_msg)
            return self._error_response(error_msg, {'error': 'Missing task_ids'})

        # Parse task_ids
        if task_ids_str == 'all':
            async with self._bg_lock:
                task_ids = list(self._background_tasks.keys())
        elif task_ids_str == 'any':
            async with self._bg_lock:
                task_ids = list(self._background_tasks.keys())
            wait_mode = 'any'
        else:
            task_ids = [tid.strip() for tid in task_ids_str.split(',') if tid.strip()]
            wait_mode = 'all'

        if not task_ids:
            error_msg = "wait_task: No tasks to wait for"
            logger.error(error_msg)
            return self._error_response(error_msg, {'error': 'No tasks specified'})

        # Validate all task_ids exist
        async with self._bg_lock:
            missing_tasks = [tid for tid in task_ids if tid not in self._background_tasks]
            if missing_tasks:
                error_msg = f"wait_task: Tasks not found: {', '.join(missing_tasks)}"
                logger.error(error_msg)
                return self._error_response(error_msg, {'error': 'Tasks not found'})

            # Get asyncio tasks to wait for
            tasks_to_wait = []
            for tid in task_ids:
                task_info = self._background_tasks[tid]
                if task_info['status'] == 'running':
                    tasks_to_wait.append(task_info['task'])

        if not tasks_to_wait:
            # All tasks already completed
            return (
                Observation(content=f"All specified tasks already completed: {', '.join(task_ids)}"),
                1.0,
                False,
                False,
                {'action': 'wait_task', 'task_ids': task_ids, 'already_completed': True}
            )

        # Wait for tasks
        start_time = time.time()
        try:
            if task_ids_str == 'any':
                # Wait for first completion
                done, pending = await asyncio.wait(
                    tasks_to_wait,
                    timeout=timeout if timeout > 0 else None,
                    return_when=asyncio.FIRST_COMPLETED
                )
            else:
                # Wait for all completions
                done, pending = await asyncio.wait(
                    tasks_to_wait,
                    timeout=timeout if timeout > 0 else None
                )

            elapsed = time.time() - start_time

            # Build response
            completed_count = len(done)
            pending_count = len(pending)

            if pending_count > 0:
                response = f"⏱️ Timeout after {elapsed:.1f}s: {completed_count} tasks completed, {pending_count} still running"
            else:
                response = f"✅ All {completed_count} tasks completed in {elapsed:.1f}s"

            # Add individual task results
            response += "\n\n**Task Results:**\n"
            async with self._bg_lock:
                for tid in task_ids:
                    task_info = self._background_tasks[tid]
                    status = task_info['status']
                    response += f"- {tid}: {status}\n"

            logger.info(
                f"wait_task: Completed waiting for {len(task_ids)} tasks "
                f"in {elapsed:.1f}s ({completed_count} done, {pending_count} pending)"
            )

            return (
                Observation(content=response),
                1.0 if pending_count == 0 else 0.5,
                False,
                False,
                {
                    'action': 'wait_task',
                    'task_ids': task_ids,
                    'completed': completed_count,
                    'pending': pending_count,
                    'elapsed': round(elapsed, 2),
                    'timed_out': pending_count > 0
                }
            )

        except Exception as e:
            elapsed = time.time() - start_time
            error_msg = f"wait_task: Exception after {elapsed:.1f}s: {str(e)}"
            logger.error(f"{error_msg}\n{traceback.format_exc()}")
            return self._error_response(error_msg, {'error': str(e), 'elapsed': round(elapsed, 2)})

    async def _cancel_task(
        self,
        action_model: ActionModel,
        **kwargs
    ) -> Tuple[Observation, float, bool, bool, Dict[str, Any]]:
        """
        Cancel one or all running background tasks.

        Args:
            action_model: ActionModel containing task_id parameter
            **kwargs: Additional context

        Returns:
            Tuple with cancellation status
        """
        params = action_model.params if hasattr(action_model, 'params') else {}
        task_id = params.get('task_id')

        if not task_id:
            error_msg = "cancel_task: 'task_id' parameter is required"
            logger.error(error_msg)
            return self._error_response(error_msg, {'error': 'Missing task_id'})

        async with self._bg_lock:
            # Handle 'all' - cancel all tasks
            if task_id == 'all':
                if not self._background_tasks:
                    return (
                        Observation(content="No background tasks to cancel"),
                        1.0,
                        False,
                        False,
                        {'action': 'cancel_task', 'cancelled_count': 0}
                    )

                # Cancel all running tasks
                cancelled_count = 0
                for tid, task_info in self._background_tasks.items():
                    if task_info['status'] == 'running':
                        task_info['task'].cancel()
                        task_info['status'] = 'cancelled'
                        task_info['error'] = 'Task cancelled by user'
                        cancelled_count += 1

                        # Sync status to Context
                        self._sync_status_to_context(tid, 'cancelled', error='Task cancelled by user')

                logger.info(f"cancel_task: Cancelled {cancelled_count} background tasks")

                return (
                    Observation(content=f"Cancelled {cancelled_count} background tasks"),
                    1.0,
                    False,
                    False,
                    {'action': 'cancel_task', 'task_id': 'all', 'cancelled_count': cancelled_count}
                )

            # Cancel specific task
            if task_id not in self._background_tasks:
                error_msg = f"cancel_task: Task '{task_id}' not found"
                available = ', '.join(self._background_tasks.keys())
                logger.error(f"{error_msg}. Available: {available}")
                return self._error_response(
                    error_msg,
                    {'error': 'Task not found', 'available_tasks': list(self._background_tasks.keys())}
                )

            task_info = self._background_tasks[task_id]
            status = task_info['status']

            if status != 'running':
                return (
                    Observation(content=f"Task '{task_id}' is not running (status: {status}), cannot cancel"),
                    0.0,
                    False,
                    False,
                    {'action': 'cancel_task', 'task_id': task_id, 'status': status, 'cancelled': False}
                )

            # Cancel the task
            task_info['task'].cancel()
            task_info['status'] = 'cancelled'
            task_info['error'] = 'Task cancelled by user'

            # Sync status to Context
            self._sync_status_to_context(task_id, 'cancelled', error='Task cancelled by user')

            logger.info(f"cancel_task: Cancelled task '{task_id}'")

            return (
                Observation(content=f"Task '{task_id}' cancelled successfully"),
                1.0,
                False,
                False,
                {'action': 'cancel_task', 'task_id': task_id, 'cancelled': True}
            )

    def _sync_status_to_context(
        self,
        task_id: str,
        status: str,
        result: Optional[str] = None,
        error: Optional[str] = None
    ):
        """
        Synchronize background task status to Context's sub_task_list.

        This ensures that the Tool layer's _background_tasks registry and the
        Context layer's sub_task_list remain in sync.

        Note: As of the fix, background task_id is passed as sub_task_id to
        SubagentManager.spawn(), ensuring ID consistency across layers.

        Args:
            task_id: Background task ID (same as sub_task_id in Context)
            status: Task status ('completed', 'error', 'cancelled')
            result: Task result (for completed tasks)
            error: Error message (for failed tasks)
        """
        try:
            # Get current context
            from aworld.core.agent.base import BaseAgent
            current_context = BaseAgent._get_current_context()

            if not current_context:
                logger.warning(
                    f"_sync_status_to_context: No active context found for task '{task_id}', "
                    f"status sync skipped"
                )
                return

            # Access sub_task_list (safe for different Context types)
            # ApplicationContext has task_state.working_state.sub_task_list structure
            task_state = getattr(current_context, 'task_state', None)
            if not task_state:
                logger.debug(
                    f"_sync_status_to_context: Context does not have task_state, "
                    f"status sync skipped for task '{task_id}' (context type: {type(current_context).__name__})"
                )
                return

            working_state = getattr(task_state, 'working_state', None)
            if not working_state:
                logger.debug(
                    f"_sync_status_to_context: task_state does not have working_state, "
                    f"status sync skipped for task '{task_id}'"
                )
                return

            sub_task_list = getattr(working_state, 'sub_task_list', None)
            if not sub_task_list:
                logger.warning(
                    f"_sync_status_to_context: No sub_task_list found in working_state for task '{task_id}'"
                )
                return

            # Find and update the matching sub_task by exact task_id match
            for sub_task in sub_task_list:
                if sub_task.task_id == task_id:
                    # Found matching sub_task, update status
                    sub_task.status = status

                    if result:
                        # Create TaskOutput if needed
                        from aworld.core.context.amni.state.common import TaskOutput
                        sub_task.result = TaskOutput(
                            task_id=sub_task.task_id,
                            task_content=result,
                            final_answer=result
                        )

                    logger.debug(
                        f"_sync_status_to_context: Updated sub_task '{sub_task.task_id}' "
                        f"status to '{status}'"
                    )
                    return

            # If we reach here, no matching sub_task was found
            logger.warning(
                f"_sync_status_to_context: No matching sub_task found for task_id '{task_id}' "
                f"in context's sub_task_list (searched {len(sub_task_list)} sub_tasks)"
            )

        except Exception as e:
            logger.error(
                f"_sync_status_to_context: Failed to sync status for task '{task_id}': {e}\n"
                f"{traceback.format_exc()}"
            )
