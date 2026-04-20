"""
Batch executor for running multiple agent tasks concurrently.
"""
import asyncio
import inspect
import time
import uuid
from typing import List, Dict, Any, Optional, Set, Tuple
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from aworld_cli._globals import console as global_console
from aworld_cli.runtime.cli import CliRuntime
from aworld_cli.executors.continuous import ContinuousExecutor
from .source import CsvBatchSource
from .builder import SimpleTaskBuilder
from .sink import CsvBatchSink
from .config import BatchJobConfig
from .digest_stats import DigestLogStats
from aworld.logs.util import logger


class BatchExecutor:
    """
    Batch executor that runs multiple agent tasks concurrently.

    Orchestrates the batch execution pipeline:
    1. Load records from source
    2. Build tasks from records
    3. Execute tasks concurrently
    4. Write results to sink

    Example:
        >>> executor = BatchExecutor(console)
        >>> summary = await executor.run(config)
        >>> print(f"Success rate: {summary['success_rate']}%")
    """

    def __init__(self, console: Optional[Console] = None):
        """
        Initialize batch executor.

        Args:
            console: Rich console for output. If None, uses global console.
        """
        self.console = console if console is not None else global_console

    async def run(self, config: BatchJobConfig) -> Dict[str, Any]:
        """
        Run batch job with given configuration.

        Args:
            config: Batch job configuration

        Returns:
            Summary dictionary with statistics:
            - total: Total number of tasks
            - success_count: Number of successful tasks
            - failure_count: Number of failed tasks
            - total_cost: Total cost
            - duration: Total duration
            - output_path: Output file path

        Example:
            >>> config = load_batch_config("batch.yaml")
            >>> executor = BatchExecutor()
            >>> summary = await executor.run(config)
        """
        start_time = datetime.now()
        self.console.print(Panel(
            f"[bold]Batch Job Configuration[/bold]\n"
            f"Input: [cyan]{config.input.path}[/cyan]\n"
            f"Agent: [cyan]{config.agent.name}[/cyan]\n"
            f"Output: [cyan]{config.output.path}[/cyan]\n"
            f"Parallel: [cyan]{config.execution.parallel}[/cyan]",
            title="🚀 Starting Batch Job",
            border_style="blue"
        ))

        # Step 1: Load records from source
        self.console.print("[dim]📖 Loading records from source...[/dim]")
        source = CsvBatchSource(
            file_path=config.input.path,
            query_column=config.input.query_column,
            encoding=config.input.encoding,
            delimiter=config.input.delimiter
        )
        records = await source.load()

        if not records:
            self.console.print("[yellow]⚠️  No records to process[/yellow]")
            return {
                "total": 0,
                "success_count": 0,
                "failure_count": 0,
                "total_cost": 0.0,
                "duration": datetime.now() - start_time,
                "output_path": config.output.path
            }

        # Step 2: Initialize task builder and sink
        builder = SimpleTaskBuilder(config.agent, config.input.query_column)
        sink = CsvBatchSink(
            file_path=config.output.path,
            encoding=config.output.encoding,
            delimiter=config.output.delimiter
        )

        # Step 3: Create runtime and load agents
        self.console.print(f"[dim]🔄 Loading agent: {config.agent.name}...[/dim]")
        runtime = CliRuntime(
            remote_backends=[config.agent.remote_backend]
            if config.agent.remote_backend
            else None,
            disable_live_display=True,
        )
        all_agents = await runtime._load_agents()

        # Find the requested agent
        agent_info = None
        for agent in all_agents:
            if agent.name == config.agent.name:
                agent_info = agent
                break

        if not agent_info:
            raise ValueError(f"❌ Agent '{config.agent.name}' not found")

        # Step 4: Execute tasks concurrently
        self.console.print(f"[bold]🔄 Processing {len(records)} records with parallel={config.execution.parallel}...[/bold]")

        semaphore = asyncio.Semaphore(config.execution.parallel)
        tasks = []

        for record in records:
            task = self._execute_single_task(
                semaphore=semaphore,
                record=record,
                builder=builder,
                config=config,
                agent_info=agent_info,
                runtime=runtime
            )
            tasks.append(task)

        # Wait for all tasks to complete
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results and write to sink; collect task_ids for digest filter
        total_cost = 0.0
        batch_task_ids: Set[str] = set()
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                # Handle exceptions
                error_result = {
                    "record_id": records[idx].get("row_id", str(idx)),
                    "success": False,
                    "response": "",
                    "error": str(result),
                    "metrics": {},
                    "original_record": records[idx],
                }
                await sink.write(error_result)
            else:
                await sink.write(result)
                if result.get("task_id"):
                    batch_task_ids.add(result["task_id"])
                # Accumulate cost
                if result.get("success") and result.get("metrics", {}).get("cost"):
                    total_cost += result["metrics"]["cost"]

        # Finalize sink
        await sink.finalize()

        # Step 5: Display summary
        duration = datetime.now() - start_time
        summary = sink.get_summary()
        summary["total_cost"] = total_cost
        summary["duration"] = duration

        self.console.print(Panel(
            f"[bold]Batch Execution Summary[/bold]\n"
            f"Total Tasks: {summary['total']}\n"
            f"Successful: [green]{summary['success_count']}[/green]\n"
            f"Failed: [red]{summary['failure_count']}[/red]\n"
            f"Total Cost: ${total_cost:.3f}\n"
            f"Duration: {duration}\n"
            f"Output: [cyan]{summary['output_path']}[/cyan]",
            title="📊 Summary",
            border_style="green",
        ))

        # Step 6: Print digest_logger statistics if configured
        # Filter by task_id only when using remote backend (task_ids are passed in headers)
        if config.digest_log and config.digest_log.path:
            filter_task_ids = batch_task_ids if config.agent.remote_backend else None
            self._print_digest_stats(
                config.digest_log.path, task_ids=filter_task_ids
            )

        return summary

    def _print_digest_stats(
        self,
        digest_log_path: str,
        task_ids: Optional[Set[str]] = None,
    ) -> None:
        """
        Read and print digest_logger statistics, optionally filtered by task_id.

        Args:
            digest_log_path: Path to digest_logger.log file (e.g. from remote
                backend's log directory).
            task_ids: Optional set of task_ids to filter (batch's task_ids for
                current run). When provided, only stats for these tasks are shown.
        """
        try:
            stats, _ = DigestLogStats.parse_file(
                digest_log_path, task_ids=task_ids
            )
            if (
                stats.total_tasks == 0
                and stats.agent_run.count == 0
                and stats.llm_call.count == 0
            ):
                self.console.print(
                    f"[dim]📋 No digest_logger data found in {digest_log_path}[/dim]"
                )
                return
            self.console.print(
                Panel(
                    stats.format_summary(
                        filtered_by_task_id=task_ids is not None
                    ),
                    title="📋 Digest Logger 统计",
                    border_style="cyan",
                )
            )
        except Exception as e:  # pylint: disable=broad-except
            self.console.print(
                f"[yellow]⚠️ Failed to read digest_logger: {e}[/yellow]"
            )

    def _extract_usage_metrics(self, response: Any, agent_executor: Any) -> Tuple[float, int]:
        """
        Extract cost and tokens from response or agent_executor.
        
        Tries multiple strategies to extract usage information:
        1. Check if response has usage attribute (for object responses)
        2. Check if agent_executor has stored usage information
        3. Check if agent_executor has context with token_usage
        4. Fallback to 0.0 and 0 if no information is available
        
        Args:
            response: Response from agent_executor.chat()
            agent_executor: Agent executor instance
            
        Returns:
            Tuple of (cost: float, tokens: int)
            
        Example:
            >>> cost, tokens = executor._extract_usage_metrics(response, agent_executor)
            >>> print(f"Cost: ${cost:.3f}, Tokens: {tokens}")
        """
        cost = 0.0
        tokens = 0
        
        # Strategy 1: Check if response has usage attribute (for object responses)
        if hasattr(response, 'usage') and response.usage:
            usage = response.usage
            input_tokens = getattr(usage, 'input_tokens', 0)
            output_tokens = getattr(usage, 'output_tokens', 0)
            total_tokens = input_tokens + output_tokens
            
            if total_tokens > 0:
                tokens = total_tokens
                # Try to get cost from usage if available
                if hasattr(usage, 'cost'):
                    cost = float(usage.cost)
                elif hasattr(usage, 'total_cost'):
                    cost = float(usage.total_cost)
        
        # Strategy 2: Check if response is a dict with usage information
        elif isinstance(response, dict):
            if 'usage' in response:
                usage = response['usage']
                if isinstance(usage, dict):
                    tokens = usage.get('total_tokens', 
                                     usage.get('completion_tokens', 0) + usage.get('prompt_tokens', 0))
                    cost = float(usage.get('cost', usage.get('total_cost', 0.0)))
            elif 'cost' in response:
                cost = float(response.get('cost', 0.0))
            elif 'tokens' in response:
                tokens = int(response.get('tokens', 0))
        
        # Strategy 3: Check if agent_executor has stored usage information
        if tokens == 0 and cost == 0.0:
            # Check for context with token_usage
            if hasattr(agent_executor, 'context') and agent_executor.context:
                context = agent_executor.context
                if hasattr(context, '_token_usage') and context._token_usage:
                    token_usage = context._token_usage
                    if isinstance(token_usage, dict):
                        input_tokens = token_usage.get('input_tokens', token_usage.get('prompt_tokens', 0))
                        output_tokens = token_usage.get('output_tokens', token_usage.get('completion_tokens', 0))
                        tokens = input_tokens + output_tokens
                        cost = float(token_usage.get('cost', token_usage.get('total_cost', 0.0)))
            
            # Check for last_task or last_response with usage
            if hasattr(agent_executor, 'last_task') and agent_executor.last_task:
                task = agent_executor.last_task
                if hasattr(task, 'usage') and task.usage:
                    usage = task.usage
                    if hasattr(usage, 'input_tokens') and hasattr(usage, 'output_tokens'):
                        tokens = usage.input_tokens + usage.output_tokens
                    if hasattr(usage, 'cost'):
                        cost = float(usage.cost)
            
            # Check for accumulated usage metrics
            if hasattr(agent_executor, 'total_tokens'):
                tokens = int(agent_executor.total_tokens)
            if hasattr(agent_executor, 'total_cost'):
                cost = float(agent_executor.total_cost)
        
        return cost, tokens

    async def _execute_single_task(
            self,
            semaphore: asyncio.Semaphore,
            record: Dict[str, Any],
            builder: SimpleTaskBuilder,
            config: BatchJobConfig,
            agent_info: Any,
            runtime: Any
    ) -> Dict[str, Any]:
        """
        Execute a single task with concurrency control and error handling.

        Args:
            semaphore: Semaphore for concurrency control
            record: Record to process
            builder: Task builder
            agent_executor: Agent executor instance
            config: Batch job configuration

        Returns:
            Result dictionary with success status, response, error, and metrics
        """
        async with semaphore:

            # Create agent executor
            agent_executor = await runtime._create_executor(agent_info)
            if not agent_executor:
                raise ValueError(f"❌ Failed to create executor for agent '{config.agent.name}'")

            task_spec = builder.build_task(record)
            record_id = task_spec["record_id"]
            prompt = task_spec["prompt"]
            # Generate task_id for digest_logger correlation (remote backend)
            task_id = f"batch_{record_id}_{uuid.uuid4().hex[:8]}"

            start_time = time.time()
            chat_kwargs: Dict[str, Any] = {}
            if "task_id" in inspect.signature(agent_executor.chat).parameters:
                chat_kwargs["task_id"] = task_id

            try:
                # Execute task with timeout if configured
                if config.execution.timeout_per_task:
                    response = await asyncio.wait_for(
                        agent_executor.chat(prompt, **chat_kwargs),
                        timeout=config.execution.timeout_per_task,
                    )
                else:
                    response = await agent_executor.chat(prompt, **chat_kwargs)

                logger.info(f"response: {response}")
                latency = time.time() - start_time

                # Extract metrics from response and agent_executor
                cost, tokens = self._extract_usage_metrics(response, agent_executor)
                metrics = {
                    "cost": cost,
                    "tokens": tokens,
                    "latency": latency
                }

                self.console.print(f"[green]✅[/green] [dim]Record {record_id}: Success ({(latency):.2f}s)[/dim]")

                return {
                    "record_id": record_id,
                    "success": True,
                    "response": str(response) if response else "",
                    "error": None,
                    "metrics": metrics,
                    "original_record": record,
                    "task_id": task_id,
                }

            except asyncio.TimeoutError:
                latency = time.time() - start_time
                error_msg = f"Timeout after {config.execution.timeout_per_task}s"
                self.console.print(f"[red]❌[/red] [dim]Record {record_id}: {error_msg}[/dim]")

                return {
                    "record_id": record_id,
                    "success": False,
                    "response": "",
                    "error": error_msg,
                    "metrics": {"latency": latency},
                    "original_record": record,
                    "task_id": task_id,
                }

            except Exception as e:
                latency = time.time() - start_time
                error_msg = str(e)
                self.console.print(f"[red]❌[/red] [dim]Record {record_id}: {error_msg}[/dim]")

                return {
                    "record_id": record_id,
                    "success": False,
                    "response": "",
                    "error": error_msg,
                    "metrics": {"latency": latency},
                    "original_record": record,
                    "task_id": task_id,
                }
