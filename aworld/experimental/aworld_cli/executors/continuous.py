"""
Continuous execution executor for running agents in a loop.
参考 continuous-claude 的设计，支持连续运行模式。
"""
import asyncio
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from rich.console import Console
from rich.panel import Panel
from .base import AgentExecutor


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
            console: Rich console for output
        """
        self.agent_executor = agent_executor
        self.console = console or Console()
        self.total_cost: float = 0.0
        self.start_time: Optional[datetime] = None
        
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
    
    async def run_iteration(self, iteration: int, prompt: str, completion_signal: Optional[str] = None) -> Dict[str, Any]:
        """
        Run a single iteration.
        
        Args:
            iteration: Current iteration number
            prompt: Task prompt
            completion_signal: Signal phrase that indicates completion
            
        Returns:
            Dictionary with iteration results including response, cost, and completion status
        """
        self.console.print(f"\n[bold cyan]🔄 ({iteration}) Starting iteration...[/bold cyan]")
        
        try:
            # Run the agent task
            self.console.print(f"[dim]🤖 ({iteration}) Running agent...[/dim]")
            response = await self.agent_executor.chat(prompt)
            
            # Check for completion signal
            is_complete = False
            if completion_signal and completion_signal.lower() in response.lower():
                is_complete = True
                self.console.print(f"[green]✅ ({iteration}) Completion signal detected![/green]")
            
            # TODO: Extract actual cost from response if available
            # For now, we'll use a placeholder
            cost = 0.0  # This should be extracted from the actual response
            
            self.console.print(f"[dim]💰 ({iteration}) Cost: ${cost:.3f}[/dim]")
            
            return {
                "iteration": iteration,
                "response": response,
                "cost": cost,
                "completed": is_complete,
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
        prompt: str,
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
            prompt: Task prompt for the agent
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
        
        # Display start banner
        self.console.print(Panel(
            f"[bold]Continuous Execution Mode[/bold]\n"
            f"Agent: [cyan]{agent_name}[/cyan]\n"
            f"Prompt: [yellow]{prompt}[/yellow]\n"
            f"Max Runs: {max_runs if max_runs else '∞'}\n"
            f"Max Cost: ${max_cost if max_cost else '∞'}\n"
            f"Max Duration: {max_duration if max_duration else '∞'}",
            title="🚀 Starting",
            border_style="blue"
        ))
        
        iteration = 0
        consecutive_completions = 0
        results = []
        
        try:
            while True:
                iteration += 1
                
                # Check limits
                if max_runs is not None and max_runs > 0 and iteration > max_runs:
                    self.console.print(f"\n[yellow]⏸️  Max runs ({max_runs}) reached.[/yellow]")
                    break
                
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

