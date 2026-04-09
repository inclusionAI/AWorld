# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Cron job executor - converts CronJob to Task and executes.
"""
import asyncio
from typing import Dict, Any

from aworld.core.task import TaskResponse
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

    def __init__(self):
        """Initialize executor."""
        self._agent_cache: Dict[str, Any] = {}

    async def execute(self, job: CronJob) -> TaskResponse:
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

            # Execute using Runners.run()
            logger.info(f"Executing cron job: {job.id} ({job.name})")
            result = await Runners.run(
                input=job.payload.message,
                swarm=swarm,
                tool_names=job.payload.tool_names,
                session_id=None,  # Isolated mode: always None
            )

            if result.success:
                logger.info(f"Cron job completed: {job.id}")
            else:
                logger.warning(f"Cron job failed: {job.id} - {result.msg}")

            return result

        except Exception as e:
            logger.error(f"Cron job execution error: {job.id} - {e}", exc_info=True)
            return TaskResponse(
                success=False,
                msg=f"Execution error: {str(e)}"
            )

    async def execute_with_retry(self, job: CronJob, max_retries: int = 3) -> TaskResponse:
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
                result = await self.execute(job)

                if result.success:
                    return result

                if attempt >= max_retries:
                    logger.error(f"Job {job.id} failed after {max_retries} retries")
                    return result

                # Exponential backoff
                wait_seconds = backoff_base ** attempt
                logger.warning(
                    f"Job {job.id} failed (attempt {attempt+1}/{max_retries+1}), "
                    f"retrying in {wait_seconds}s..."
                )
                await asyncio.sleep(wait_seconds)

            except Exception as e:
                if attempt >= max_retries:
                    return TaskResponse(
                        success=False,
                        msg=f"Execution failed after {max_retries} retries: {str(e)}"
                    )

                wait_seconds = backoff_base ** attempt
                logger.warning(f"Job {job.id} error, retrying in {wait_seconds}s: {e}")
                await asyncio.sleep(wait_seconds)

        # Should not reach here
        return TaskResponse(success=False, msg="Unexpected retry loop exit")

    async def _resolve_swarm(self, agent_name: str):
        """
        Resolve swarm from LocalAgentRegistry (with cache).

        This method preserves the full swarm topology (e.g., TeamSwarm with sub-agents)
        rather than extracting a single agent.

        Args:
            agent_name: Agent name

        Returns:
            Swarm instance or None
        """
        if agent_name not in self._agent_cache:
            try:
                from aworld_cli.core.agent_registry import LocalAgentRegistry

                # Get registry instance
                registry = LocalAgentRegistry()

                # Get LocalAgent
                local_agent = registry.get(agent_name)
                if not local_agent:
                    logger.error(f"Agent not found in registry: {agent_name}")
                    return None

                # Get swarm from LocalAgent (async call)
                swarm = await local_agent.get_swarm()
                if not swarm:
                    logger.error(f"Failed to get swarm from agent: {agent_name}")
                    return None

                # Cache the entire swarm (preserves TeamSwarm/sub-agents)
                self._agent_cache[agent_name] = swarm
                logger.debug(f"Cached swarm from LocalAgentRegistry: {agent_name}")

            except ImportError:
                logger.error(
                    "Cannot import LocalAgentRegistry. "
                    "This module requires aworld-cli to be installed."
                )
                return None
            except Exception as e:
                logger.error(f"Failed to resolve swarm for {agent_name}: {e}", exc_info=True)
                return None

        return self._agent_cache.get(agent_name)
