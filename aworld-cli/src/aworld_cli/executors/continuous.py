"""
Continuous execution executor for running agents in a loop.
"""
import asyncio
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Union, List
from rich.console import Console
from rich.panel import Panel
from .base import AgentExecutor
from .._globals import console as global_console


class ContinuousExecutor:
    """
    Continuous executor that runs agent tasks in a loop with various limits.
    
    Example:
        >>> executor = ContinuousExecutor(agent_executor, console)
        >>> await executor.run_continuous(
        ...     prompt="add unit tests",
        ...     max_runs=5,
        ...     max_duration="2h"
        ... )
    """
    
    def __init__(self, agent_executor: AgentExecutor, console: Optional[Console] = None):
        """
        Initialize continuous executor.
        
        Args:
            agent_executor: Agent executor instance
            console: Rich console for output. If None, uses global console.
        """
        self.agent_executor = agent_executor
        # Use global console if not provided
        self.console = console if console is not None else global_console
        self.total_cost: float = 0.0
        self.start_time: Optional[datetime] = None
        self.response_history: List[str] = []  # Track recent responses for repetition detection
        
    def _parse_duration(self, duration_str: str) -> timedelta:
        """
        Parse duration string like "2h", "30m", "1h30m" into timedelta.
        
        Args:
            duration_str: Duration string (e.g., "2h", "30m", "1h30m")
            
        Returns:
            Parsed timedelta object
            
        Example:
            >>> executor._parse_duration("2h")
            datetime.timedelta(seconds=7200)
        """
        duration_str = duration_str.lower().strip()
        total_seconds = 0
        
        # Parse hours
        if 'h' in duration_str:
            parts = duration_str.split('h', 1)
            hours = int(parts[0])
            total_seconds += hours * 3600
            duration_str = parts[1] if len(parts) > 1 else ""
        
        # Parse minutes
        if 'm' in duration_str:
            parts = duration_str.split('m', 1)
            minutes = int(parts[0])
            total_seconds += minutes * 60
        
        return timedelta(seconds=total_seconds)
    
    def _check_duration_limit(self, max_duration: Optional[str]) -> bool:
        """
        Check if duration limit has been reached.
        
        Args:
            max_duration: Maximum duration string (e.g., "2h")
            
        Returns:
            True if limit reached, False otherwise
        """
        if not max_duration or not self.start_time:
            return False
        
        duration_limit = self._parse_duration(max_duration)
        elapsed = datetime.now() - self.start_time
        return elapsed >= duration_limit
    
    def _check_cost_limit(self, max_cost: Optional[float]) -> bool:
        """
        Check if cost limit has been reached.
        
        Args:
            max_cost: Maximum cost in USD
            
        Returns:
            True if limit reached, False otherwise
        """
        if max_cost is None:
            return False
        return self.total_cost >= max_cost
    
    async def run_iteration(self, iteration: int, prompt: Union[str, tuple[str, List[str]]], completion_signal: Optional[str] = None) -> Dict[str, Any]:
        """
        Run a single iteration.
        
        Args:
            iteration: Current iteration number
            prompt: Task prompt (string or multimodal content list)
            completion_signal: Signal phrase that indicates completion
            
        Returns:
            Dictionary with iteration results including response, cost, and completion status
        """

        session_id = getattr(self.agent_executor, 'session_id', 'unknown')
        self.console.print(f"\n[bold cyan]🔄({iteration}) Starting iteration  session: {session_id}[/bold cyan]")
        
        try:
            # Ensure agent_executor uses the same console for output rendering
            # Use global console to ensure consistent output
            # This MUST be set before calling chat() to ensure output is displayed
            if hasattr(self.agent_executor, 'console'):
                # Force set to global console to ensure output is displayed
                self.agent_executor.console = global_console
                # Verify it was set correctly
                if self.agent_executor.console is not global_console:
                    self.console.print(f"[yellow]⚠️ Warning: Failed to set agent_executor.console[/yellow]")
            
            response = await self.agent_executor.chat(prompt)

            # Check for completion signal (only check if response is string)
            is_complete = False
            if completion_signal and isinstance(response, str) and completion_signal.lower() in response.lower():
                is_complete = True
                self.console.print(f"[green]✅ ({iteration}) Completion signal detected![/green]")

            # Smart task completion detection (only after first iteration)
            if not is_complete and isinstance(response, str) and iteration == 1:
                # Check if agent gave a definitive answer (not asking questions or saying it will try)
                normalized_response = response.lower()

                # Definitive completion indicators (command execution)
                execution_indicators = [
                    "成功执行",
                    "执行成功",
                    "命令执行成功",
                    "任务完成",
                    "已完成",
                    "输出结果",
                    "执行结果",
                    "successfully executed",
                    "execution successful",
                    "command executed",
                    "task completed",
                ]

                # Definitive answer indicators (Q&A tasks)
                answer_indicators = [
                    "作者是",
                    "答案是",
                    "结果是",
                    "主要是",
                    "根据.*信息",
                    "具体信息如下",
                    "关键信息",
                    "the author is",
                    "the answer is",
                    "the result is",
                    "according to",
                    "based on",
                ]

                # Continuation indicators (agent wants to keep working)
                continuation_indicators = [
                    "让我",
                    "我将",
                    "接下来",
                    "需要继续",
                    "还需要",
                    "应该继续",
                    "让我们继续",
                    "let me",
                    "i will",
                    "i'll",
                    "we should continue",
                    "we need to",
                    "next, i",
                    "next, we",
                ]

                has_execution = any(indicator in normalized_response for indicator in execution_indicators)
                has_answer = any(indicator in normalized_response for indicator in answer_indicators)
                has_continuation = any(indicator in normalized_response for indicator in continuation_indicators)

                # Response length check: if response is substantial (>200 chars) and structured
                is_substantial = len(response) > 200 and ("\n" in response or "：" in response or ":" in response)

                # Decision logic:
                # 1. Command execution task: has execution indicator + no continuation
                # 2. Q&A task: has answer indicator OR (substantial response + no continuation)
                if (has_execution or has_answer or is_substantial) and not has_continuation:
                    is_complete = True
                    completion_reason = "execution" if has_execution else ("answer" if has_answer else "substantial response")
                    self.console.print(f"[green]✅ ({iteration}) Task completed - agent gave definitive {completion_reason}![/green]")

            # Intelligent repetition detection: Check if agent is repeating the same answer
            if not is_complete and isinstance(response, str):
                # Normalize response for comparison (remove extra whitespace, lowercase)
                normalized_response = " ".join(response.lower().split())

                # Check if this response is very similar to recent responses
                if len(self.response_history) >= 1:
                    # Compare with last response (reduced from 2 to make it more sensitive)
                    recent_responses = self.response_history[-1:]
                    similarity_scores = []

                    for past_response in recent_responses:
                        # Simple similarity: check if 70%+ of words are the same (reduced from 80%)
                        words_current = set(normalized_response.split())
                        words_past = set(past_response.split())

                        if not words_current:
                            continue

                        intersection = words_current & words_past
                        similarity = len(intersection) / len(words_current)
                        similarity_scores.append(similarity)

                    # If last response is 70%+ similar, consider task complete
                    if similarity_scores and all(s >= 0.7 for s in similarity_scores):
                        is_complete = True
                        self.console.print(f"[green]✅ ({iteration}) Repetition detected - task appears complete![/green]")

                # Add current response to history (keep last 3)
                self.response_history.append(normalized_response)
                if len(self.response_history) > 3:
                    self.response_history.pop(0)

            # TODO: Extract actual cost from response if available
            # For now, we'll use a placeholder
            cost = 0.0  # This should be extracted from the actual response

            self.console.print(f"[dim]💰 ({iteration}) Cost: ${cost:.3f}[/dim]")

            return {
                "iteration": iteration,
                "response": response,
                "cost": cost,
                "completed": is_complete,
                "immediate_stop": is_complete and iteration == 1,  # First iteration with definitive answer
                "success": True
            }
            
        except Exception as e:
            self.console.print(f"[red]❌ ({iteration}) Error: {e}[/red]")
            return {
                "iteration": iteration,
                "response": str(e),
                "cost": 0.0,
                "completed": False,
                "success": False
            }
    
    async def run_continuous(
        self,
        prompt: Union[str, tuple[str, List[str]]],
        agent_name: str,
        max_runs: Optional[int] = None,
        max_cost: Optional[float] = None,
        max_duration: Optional[str] = None,
        completion_signal: Optional[str] = None,
        completion_threshold: int = 3,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Run agent tasks continuously with various limits.
        
        Args:
            prompt: Task prompt for the agent (string or tuple of (text, image_urls) for multimodal)
            agent_name: Name of the agent
            max_runs: Maximum number of iterations (0 for infinite)
            max_cost: Maximum cost in USD
            max_duration: Maximum duration (e.g., "2h", "30m", "1h30m")
            completion_signal: Signal phrase that indicates completion
            completion_threshold: Number of consecutive completion signals required
            **kwargs: Additional arguments passed to agent executor
            
        Returns:
            Summary dictionary with total runs, cost, and results
            
        Example:
            >>> await executor.run_continuous(
            ...     prompt="add unit tests",
            ...     agent_name="TestAgent",
            ...     max_runs=5,
            ...     max_duration="2h"
            ... )
        """
        self.start_time = datetime.now()
        self.total_cost = 0.0
        self.response_history = []  # Reset history for new task
        
        # Format prompt for display
        if isinstance(prompt, tuple):
            prompt_text, image_urls = prompt
            image_count = len(image_urls) if image_urls else 0
            prompt_display = prompt_text
            if image_count > 0:
                prompt_display += f" [📷 {image_count} image(s)]"
        else:
            prompt_display = prompt
        
        # Display start banner with adaptive layout
        from rich.table import Table
        from rich import box

        start_table = Table(show_header=False, box=None, padding=(0, 1))
        start_table.add_column("Label", style="bold", no_wrap=True)
        start_table.add_column("Value", style="cyan")

        start_table.add_row("Mode", "Continuous Execution")
        start_table.add_row("Agent", f"[cyan]{agent_name}[/cyan]")
        start_table.add_row("Prompt", f"[yellow]{prompt_display}[/yellow]")
        start_table.add_row("Max Runs", str(max_runs if max_runs else '∞'))
        start_table.add_row("Max Cost", f"${max_cost if max_cost else '∞'}")
        start_table.add_row("Max Duration", str(max_duration if max_duration else '∞'))

        self.console.print(Panel(
            start_table,
            title="🚀 Starting",
            border_style="blue",
            expand=False
        ))
        
        iteration = 0
        consecutive_completions = 0
        results = []
        
        try:
            while True:
                # Check limits before incrementing iteration
                if max_runs is not None and max_runs > 0 and iteration >= max_runs:
                    self.console.print(f"\n[yellow]⏸️  Max runs ({max_runs}) reached.[/yellow]")
                    break
                
                iteration += 1
                
                if self._check_cost_limit(max_cost):
                    self.console.print(f"\n[yellow]⏸️  Max cost (${max_cost:.2f}) reached.[/yellow]")
                    break
                
                if self._check_duration_limit(max_duration):
                    self.console.print(f"\n[yellow]⏸️  Max duration ({max_duration}) reached.[/yellow]")
                    break
                
                # Run iteration
                result = await self.run_iteration(iteration, prompt, completion_signal)
                results.append(result)

                self.total_cost += result["cost"]

                # Check for immediate stop (first iteration with definitive answer)
                if result.get("immediate_stop", False):
                    self.console.print(f"\n[green]🎉 Task completed successfully![/green]")
                    break

                # Check completion signal
                if result["completed"]:
                    consecutive_completions += 1
                    if consecutive_completions >= completion_threshold:
                        self.console.print(f"\n[green]🎉 Project complete! ({consecutive_completions} consecutive completion signals)[/green]")
                        break
                else:
                    consecutive_completions = 0
                
                # Small delay between iterations
                await asyncio.sleep(1)
                
        except KeyboardInterrupt:
            self.console.print("\n[yellow]⚠️  Interrupted by user.[/yellow]")
        
        # Display summary
        elapsed = datetime.now() - self.start_time if self.start_time else timedelta(0)
        successful_runs = sum(1 for r in results if r["success"])
        
        self.console.print(Panel(
            f"[bold]Execution Summary[/bold]\n"
            f"Total Iterations: {iteration}\n"
            f"Successful: {successful_runs}\n"
            f"Failed: {iteration - successful_runs}\n"
            f"Total Cost: ${self.total_cost:.3f}\n"
            f"Duration: {elapsed}",
            title="📊 Summary",
            border_style="green"
        ))
        
        return {
            "total_runs": iteration,
            "successful_runs": successful_runs,
            "total_cost": self.total_cost,
            "duration": elapsed,
            "results": results
        }

__all__ = ["ContinuousExecutor"]

