# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Cron job executor - converts CronJob to Task and executes.
"""
import asyncio
import inspect
import traceback
from typing import Dict, Any, Callable, Optional, Awaitable

from aworld.core.task import Task, TaskResponse
from aworld.logs.util import logger
from .types import CronJob


class CronExecutor:
    """
    Cron job executor.

    Responsibilities:
    - Build Task from CronJob
    - Call Runners.run() to execute
    - Handle retry logic
    """

    def __init__(self, swarm_resolver: Optional[Callable[[str], Any]] = None):
        """Initialize executor."""
        self._agent_cache: Dict[str, Any] = {}
        self.swarm_resolver = swarm_resolver
        self.default_agent_name: Optional[str] = None

    def set_swarm_resolver(self, swarm_resolver: Optional[Callable[[str], Any]]) -> None:
        """Configure the swarm resolver used by cron jobs."""
        self.swarm_resolver = swarm_resolver
        self._agent_cache.clear()

    def set_default_agent_name(self, agent_name: Optional[str]) -> None:
        """Bind cron jobs created in the current CLI session to the selected root agent."""
        candidate = (agent_name or "").strip()
        self.default_agent_name = candidate or None

    def get_default_agent_name(self) -> Optional[str]:
        """Return the CLI-selected default agent name for newly created cron jobs."""
        return self.default_agent_name

    def _resolve_effective_tool_names(self, job: CronJob) -> list[str]:
        """Aworld/root-agent cron tasks should not be constrained by persisted tool allowlists."""
        if job.payload.agent_name == "Aworld":
            return []
        return job.payload.tool_names

    def _truncate_text(self, value: Any, limit: int = 400) -> Optional[str]:
        """Convert stream payloads to compact, readable progress lines."""
        if value is None:
            return None

        text = value if isinstance(value, str) else str(value)
        text = text.strip()
        if not text:
            return None

        if len(text) > limit:
            return f"{text[:limit - 3]}..."
        return text

    def _format_stream_output(self, output: Any) -> Optional[tuple[str, str]]:
        """Translate AWorld streaming outputs into human-readable cron follow logs."""
        if not hasattr(output, "output_type"):
            return None

        output_type = output.output_type()

        if output_type == "message":
            response = self._truncate_text(getattr(output, "response", None), limit=800)
            if response:
                return "info", f"Agent 输出：\n{response}"

            reasoning = self._truncate_text(getattr(output, "reasoning", None), limit=500)
            if reasoning:
                return "info", f"Agent 思考：\n{reasoning}"
            return None

        if output_type == "step":
            display_name = getattr(output, "alias_name", None) or getattr(output, "name", None)
            status = (getattr(output, "status", None) or "").upper()
            step_num = getattr(output, "step_num", None)
            step_prefix = f"步骤 #{step_num}" if step_num is not None else "步骤"
            display_name = display_name or "unknown"
            if status == "START":
                return "info", f"{step_prefix} 开始：{display_name}"
            if status == "FINISHED":
                return "success", f"{step_prefix} 完成：{display_name}"
            if status in {"FAILED", "ERROR"}:
                return "error", f"{step_prefix} 失败：{display_name}"
            return "info", f"{step_prefix} 状态更新：{display_name} ({status or 'UNKNOWN'})"

        if output_type == "tool_call":
            tool_name = None
            args_text = None
            tool_call = getattr(output, "data", None)

            if tool_call is not None:
                function = getattr(tool_call, "function", None)
                if function is not None:
                    tool_name = getattr(function, "name", None)
                    args_text = self._truncate_text(getattr(function, "arguments", None), limit=240)
                else:
                    tool_name = getattr(tool_call, "name", None)
                    args_text = self._truncate_text(getattr(tool_call, "arguments", None), limit=240)

            tool_name = tool_name or getattr(output, "tool_name", None) or "unknown"
            return "info", f"工具调用：{tool_name}({args_text or ''})"

        if output_type == "tool_call_result":
            tool_name = getattr(output, "tool_name", None) or "unknown"
            result_text = (
                self._truncate_text(getattr(output, "data", None), limit=500)
                or self._truncate_text(getattr(output, "result", None), limit=500)
                or self._truncate_text(getattr(output, "content", None), limit=500)
            )
            if result_text:
                return "success", f"工具结果：{tool_name}\n{result_text}"
            return "success", f"工具结果：{tool_name}"

        return None

    async def _get_definitive_task_response(
        self,
        aworld_task_id: str,
        streaming_outputs: Any,
    ) -> Optional[TaskResponse]:
        """Wait for the underlying run coroutine and return the final TaskResponse."""
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

        for _ in range(10):
            await asyncio.sleep(0.05)
            task_response = streaming_outputs.response()
            if task_response is not None:
                return task_response

        return None

    async def _execute_with_streaming(
        self,
        job: CronJob,
        swarm: Any,
        progress_callback: Callable[[str, str], Optional[Awaitable[None]]],
    ) -> TaskResponse:
        """Execute cron jobs with stream-aware progress so `/cron show` can follow real steps."""
        from aworld.runner import Runners

        task = Task(
            input=job.payload.message,
            swarm=swarm,
            tool_names=self._resolve_effective_tool_names(job),
            event_driven=bool(getattr(swarm, "event_driven", False)),
            session_id=None,
        )

        streaming_outputs = Runners.streamed_run_task(
            task=task,
            cancel_run_impl_task_on_cleanup=False,
        )

        async for output in streaming_outputs.stream_events():
            formatted = self._format_stream_output(output)
            if formatted:
                level, message = formatted
                await self._emit_progress(progress_callback, level, message)

        result = await self._get_definitive_task_response(task.id, streaming_outputs)
        if result is None:
            fallback_answer = None
            try:
                fallback_answer = self._truncate_text(
                    streaming_outputs.get_message_output_content(),
                    limit=1200,
                )
            except Exception:
                fallback_answer = None

            return TaskResponse(
                success=False,
                answer=fallback_answer,
                msg="Execution completed without a final task response",
            )

        final_answer = self._truncate_text(getattr(result, "answer", None), limit=4000)
        if final_answer:
            await self._emit_progress(progress_callback, "success", f"最终回答：\n{final_answer}")
        elif result.msg:
            await self._emit_progress(
                progress_callback,
                "success" if result.success else "error",
                f"最终结果：{result.msg}",
            )

        return result

    async def _emit_progress(
        self,
        progress_callback: Optional[Callable[[str, str], Optional[Awaitable[None]]]],
        level: str,
        message: str,
    ) -> None:
        """Best-effort progress callback for live cron execution logs."""
        if not progress_callback:
            return

        try:
            result = progress_callback(level, message)
            if inspect.isawaitable(result):
                await result
        except Exception as e:
            logger.debug(f"Failed to emit cron progress log: {e}")

    async def execute(
        self,
        job: CronJob,
        progress_callback: Optional[Callable[[str, str], Optional[Awaitable[None]]]] = None,
    ) -> TaskResponse:
        """
        Execute job (isolated mode only).

        Args:
            job: Job to execute

        Returns:
            Task response
        """
        from aworld.runner import Runners

        try:
            # Resolve swarm (not agent - preserve TeamSwarm configuration)
            swarm = await self._resolve_swarm(job.payload.agent_name)
            if not swarm:
                return TaskResponse(
                    success=False,
                    msg=f"Agent not found: {job.payload.agent_name}"
                )

            # Execute using Runners.run() or stream-aware mode for richer live logs.
            logger.info(f"Executing cron job: {job.id} ({job.name})")
            if progress_callback:
                result = await self._execute_with_streaming(job, swarm, progress_callback)
            else:
                result = await Runners.run(
                    input=job.payload.message,
                    swarm=swarm,
                    tool_names=self._resolve_effective_tool_names(job),
                    session_id=None,  # Isolated mode: always None
                )

            if result.success:
                logger.info(f"Cron job completed: {job.id}")
            else:
                logger.warning(f"Cron job failed: {job.id} - {result.msg}")

            return result

        except Exception as e:
            logger.error(
                f"Cron job execution error: {job.id}\n{traceback.format_exc()}"
            )
            return TaskResponse(
                success=False,
                msg=f"Execution error: {str(e)}"
            )

    async def execute_with_retry(
        self,
        job: CronJob,
        max_retries: int = 3,
        progress_callback: Optional[Callable[[str, str], Optional[Awaitable[None]]]] = None,
    ) -> TaskResponse:
        """
        Execute with exponential backoff retry.

        Args:
            job: Job to execute
            max_retries: Maximum number of retries

        Returns:
            Task response
        """
        backoff_base = 2

        for attempt in range(max_retries + 1):
            try:
                await self._emit_progress(
                    progress_callback,
                    "info",
                    f"开始第 {attempt + 1}/{max_retries + 1} 次执行，agent={job.payload.agent_name}",
                )
                result = await self.execute(job, progress_callback=progress_callback)

                if result.success:
                    await self._emit_progress(
                        progress_callback,
                        "info",
                        f"第 {attempt + 1} 次执行成功",
                    )
                    return result

                if attempt >= max_retries:
                    logger.error(f"Job {job.id} failed after {max_retries} retries")
                    await self._emit_progress(
                        progress_callback,
                        "error",
                        f"第 {attempt + 1} 次执行失败：{result.msg}",
                    )
                    return result

                # Exponential backoff
                wait_seconds = backoff_base ** attempt
                logger.warning(
                    f"Job {job.id} failed (attempt {attempt+1}/{max_retries+1}), "
                    f"retrying in {wait_seconds}s..."
                )
                await self._emit_progress(
                    progress_callback,
                    "warning",
                    f"第 {attempt + 1} 次执行失败：{result.msg}；将在 {wait_seconds}s 后重试",
                )
                await asyncio.sleep(wait_seconds)

            except Exception as e:
                if attempt >= max_retries:
                    await self._emit_progress(
                        progress_callback,
                        "error",
                        f"第 {attempt + 1} 次执行异常：{e}",
                    )
                    return TaskResponse(
                        success=False,
                        msg=f"Execution failed after {max_retries} retries: {str(e)}"
                    )

                wait_seconds = backoff_base ** attempt
                logger.warning(f"Job {job.id} error, retrying in {wait_seconds}s: {e}")
                await self._emit_progress(
                    progress_callback,
                    "warning",
                    f"第 {attempt + 1} 次执行异常：{e}；将在 {wait_seconds}s 后重试",
                )
                await asyncio.sleep(wait_seconds)

        # Should not reach here
        return TaskResponse(success=False, msg="Unexpected retry loop exit")

    async def _resolve_swarm(self, agent_name: str):
        """
        Resolve swarm using the injected resolver (with cache).

        This method preserves the full swarm topology (e.g., TeamSwarm with sub-agents)
        rather than extracting a single agent.

        Args:
            agent_name: Agent name

        Returns:
            Swarm instance or None
        """
        if agent_name in self._agent_cache:
            return self._agent_cache.get(agent_name)

        if not self.swarm_resolver:
            logger.error(f"No swarm resolver configured for cron agent: {agent_name}")
            return None

        try:
            swarm = self.swarm_resolver(agent_name)
            if inspect.isawaitable(swarm):
                swarm = await swarm

            if not swarm:
                logger.error(f"Agent not found via configured swarm resolver: {agent_name}")
                return None

            # Cache the entire swarm (preserves TeamSwarm/sub-agents)
            self._agent_cache[agent_name] = swarm
            logger.debug(f"Cached swarm from configured resolver: {agent_name}")
        except Exception as e:
            logger.error(
                f"Failed to resolve swarm for {agent_name}\n{traceback.format_exc()}"
            )
            return None

        return self._agent_cache.get(agent_name)
