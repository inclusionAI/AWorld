"""
/tasks command - Manage background tasks

This is a Tool Command (direct execution, no LLM).

Subcommands:
- /tasks list              List all tasks
- /tasks status <task-id>  Show task status and progress
- /tasks follow <task-id>  Follow task output in real-time
- /tasks cancel <task-id>  Cancel running task
"""
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Confirm
from rich.console import Group
from rich import box
from io import StringIO
from rich.console import Console as RichConsole

from aworld_cli.core.command_system import Command, CommandContext, register_command


@register_command
class TasksCommand(Command):
    """
    Manage background tasks.

    Type: Tool Command (direct execution)

    Subcommands:
    - list: Show all tasks in a table
    - status <id>: Show detailed task status
    - follow <id>: Follow task output in real-time (like tail -f)
    - cancel <id>: Cancel a running task

    Example:
        > /tasks list
        > /tasks status task-001
        > /tasks follow task-001  # Real-time output
        > /tasks cancel task-002
    """

    @property
    def name(self) -> str:
        return "tasks"

    @property
    def description(self) -> str:
        return "Manage background tasks (list, status, follow, cancel)"

    @property
    def command_type(self) -> str:
        return "tool"

    async def execute(self, context: CommandContext) -> str:
        """
        Execute /tasks command with subcommand routing.

        Args:
            context: CommandContext with background task manager access

        Returns:
            Formatted output based on subcommand
        """
        # Get task manager
        task_manager = context.background_task_manager
        if not task_manager:
            return "[red]Error: Background task manager not available[/red]"

        # Parse subcommand
        args = context.user_args.strip().split(maxsplit=1)

        if not args or args[0] == "list":
            return await self._list_tasks(task_manager)
        elif args[0] == "status":
            if len(args) < 2:
                return "[yellow]Usage: /tasks status <task-id>[/yellow]\n[dim]Example: /tasks status task-001[/dim]"
            return await self._show_status(args[1], task_manager)
        elif args[0] == "follow":
            if len(args) < 2:
                return "[yellow]Usage: /tasks follow <task-id>[/yellow]\n[dim]Example: /tasks follow task-001[/dim]"
            return await self._follow_task(args[1], task_manager)
        elif args[0] == "cancel":
            if len(args) < 2:
                return "[yellow]Usage: /tasks cancel <task-id>[/yellow]\n[dim]Example: /tasks cancel task-001[/dim]"
            return await self._cancel_task(args[1], task_manager)
        else:
            return self._help_text()

    async def _list_tasks(self, task_manager) -> str:
        """
        List all tasks in a Rich table.

        Args:
            task_manager: BackgroundTaskManager instance

        Returns:
            Formatted table as string
        """
        tasks = task_manager.list_tasks()

        if not tasks:
            return "[dim]No background tasks found.[/dim]\n[dim]Use /dispatch to submit a task[/dim]"

        # Create Rich table with optimized layout
        table = Table(
            title="📋 Background Tasks",
            show_header=True,
            box=box.SIMPLE,
            padding=(0, 1),
            collapse_padding=False
        )
        table.add_column("ID", style="cyan", no_wrap=True, width=10)
        table.add_column("Status", style="bold", no_wrap=True, width=12)
        table.add_column("Time", style="dim", no_wrap=True, width=10)
        table.add_column("Task", style="white", width=50)
        table.add_column("Output", style="dim cyan", width=25)

        for task in tasks:
            # Status with simple icon
            status_icons = {
                "pending": ("⏳", "yellow"),
                "running": ("▶", "blue"),
                "completed": ("✓", "green"),
                "failed": ("✗", "red"),
                "cancelled": ("◼", "dim")
            }
            icon, color = status_icons.get(task.status, ("?", "white"))
            status_display = f"[{color}]{icon} {task.status}[/{color}]"

            # Format elapsed time
            elapsed = task.elapsed_seconds()
            if task.status == "running":
                if elapsed < 60:
                    time_display = f"{elapsed:.0f}s"
                elif elapsed < 3600:
                    time_display = f"{elapsed/60:.1f}m"
                else:
                    time_display = f"{elapsed/3600:.1f}h"
            else:
                # Show submission date for completed/failed/cancelled
                time_display = task.submitted_at.strftime("%m-%d %H:%M")

            # Smart truncate task content (preserve complete words)
            max_len = 47  # Leave room for "..."
            if len(task.task_content) > max_len:
                truncated = task.task_content[:max_len].rsplit(' ', 1)[0]
                content = truncated + "..."
            else:
                content = task.task_content

            # Format output file path (show relative path)
            output_display = ""
            if task.output_file:
                # Extract filename only for cleaner display
                from pathlib import Path
                output_file_path = Path(task.output_file)
                output_display = output_file_path.name

            table.add_row(
                task.task_id,
                status_display,
                time_display,
                content,
                output_display
            )

        # Add enhanced summary with color-coded stats
        stats = task_manager.get_stats()
        summary_parts = []
        if stats['running'] > 0:
            summary_parts.append(f"[blue]▶ {stats['running']} running[/blue]")
        if stats['completed'] > 0:
            summary_parts.append(f"[green]✓ {stats['completed']} completed[/green]")
        if stats['failed'] > 0:
            summary_parts.append(f"[red]✗ {stats['failed']} failed[/red]")
        if stats['pending'] > 0:
            summary_parts.append(f"[yellow]⏳ {stats['pending']} pending[/yellow]")
        if stats['cancelled'] > 0:
            summary_parts.append(f"[dim]◼ {stats['cancelled']} cancelled[/dim]")

        summary = f"\n[bold]Summary:[/bold] {' | '.join(summary_parts) if summary_parts else '[dim]No tasks[/dim]'}"

        # Render table to string
        buffer = StringIO()
        temp_console = RichConsole(file=buffer, force_terminal=True, width=120)
        temp_console.print(table)
        temp_console.print(summary)

        return buffer.getvalue()

    async def _show_status(self, task_id: str, task_manager) -> str:
        """
        Show detailed task status.

        Args:
            task_id: Task ID to show
            task_manager: BackgroundTaskManager instance

        Returns:
            Formatted status panel as string
        """
        task = task_manager.get_task(task_id)

        if not task:
            return f"[red]Task not found:[/red] {task_id}\n[dim]Use /tasks list to see available tasks[/dim]"

        # Format elapsed time
        elapsed = task.elapsed_seconds()
        if elapsed < 60:
            elapsed_str = f"{elapsed:.0f}s"
        elif elapsed < 3600:
            elapsed_str = f"{elapsed/60:.1f}m"
        else:
            elapsed_str = f"{elapsed/3600:.1f}h"

        # Status emoji
        status_emoji = {
            "pending": "⏳",
            "running": "🔄",
            "completed": "✅",
            "failed": "❌",
            "cancelled": "🚫"
        }.get(task.status, "❓")

        # Build status display
        lines = [
            f"[bold]Task ID:[/bold] {task.task_id}",
            f"[bold]Status:[/bold] {status_emoji} {task.status}",
            f"[bold]Agent:[/bold] {task.agent_name}",
            f"[bold]Submitted:[/bold] {task.submitted_at.strftime('%Y-%m-%d %H:%M:%S')}",
        ]

        # Add output file path
        if task.output_file:
            lines.append(f"[bold]Output File:[/bold] [cyan]{task.output_file}[/cyan]")

        if task.started_at:
            lines.append(f"[bold]Started:[/bold] {task.started_at.strftime('%Y-%m-%d %H:%M:%S')}")
            if task.status == "running":
                lines.append(f"[bold]Running time:[/bold] {elapsed_str}")

        if task.completed_at:
            lines.append(f"[bold]Completed:[/bold] {task.completed_at.strftime('%Y-%m-%d %H:%M:%S')}")
            lines.append(f"[bold]Total time:[/bold] {elapsed_str}")

        # Add progress info if running
        if task.status == "running" and task.current_step:
            lines.append(f"\n[bold]Current step:[/bold] {task.current_step}")
            if task.progress_percentage > 0:
                lines.append(f"[bold]Progress:[/bold] {task.progress_percentage:.1f}%")

        # Add task content
        lines.append(f"\n[bold]Task:[/bold]\n{task.task_content}")

        # Add result if completed
        if task.status == "completed" and task.result:
            result_preview = task.result[:500] + "..." if len(task.result) > 500 else task.result
            lines.append(f"\n[bold green]Result:[/bold green]\n{result_preview}")

        # Add error if failed
        if task.status == "failed" and task.error:
            lines.append(f"\n[bold red]Error:[/bold red]\n{task.error}")

        # Render panel to string
        buffer = StringIO()
        temp_console = RichConsole(file=buffer, force_terminal=True, width=120)
        panel = Panel(
            "\n".join(lines),
            title=f"📊 Task Status: {task_id}",
            border_style="cyan",
            box=box.ROUNDED
        )
        temp_console.print(panel)

        return buffer.getvalue()

    async def _follow_task(self, task_id: str, task_manager) -> str:
        """
        Follow task output in real-time (like tail -f).

        Args:
            task_id: Task ID to follow
            task_manager: BackgroundTaskManager instance

        Returns:
            Success message when task completes or user stops
        """
        import asyncio
        from rich.console import Console as RichConsole

        task = task_manager.get_task(task_id)

        if not task:
            return f"[red]Task not found:[/red] {task_id}\n[dim]Use /tasks list to see available tasks[/dim]"

        # Create console for output
        console = RichConsole()

        # Show header with exit instructions
        console.print(f"\n[bold cyan]🔄 Following task: {task_id}[/bold cyan]")
        if task.output_file:
            console.print(f"[dim]Log file: {task.output_file}[/dim]")
        console.print(f"[yellow]Press Ctrl+C to stop following[/yellow] [dim](task continues running)[/dim]")
        console.print(f"[dim]💡 Better experience: [cyan]tail -f {task.output_file}[/cyan][/dim]\n")

        # Show historical output first
        if task.output_buffer:
            console.print("[dim]--- Historical Output ---[/dim]")
            for timestamp, output_line in task.output_buffer:
                time_str = timestamp.strftime("%H:%M:%S")
                console.print(f"[dim][{time_str}][/dim] {output_line}")
            console.print("[dim]--- Live Output ---[/dim]")

        # Track last seen output count
        last_seen_count = len(task.output_buffer)

        try:
            # Poll for new output until task completes
            while not task.is_terminal():
                # Check for new output
                current_count = len(task.output_buffer)
                if current_count > last_seen_count:
                    # New output available
                    for i in range(last_seen_count, current_count):
                        timestamp, output_line = list(task.output_buffer)[i]
                        time_str = timestamp.strftime("%H:%M:%S")
                        console.print(f"[dim][{time_str}][/dim] {output_line}")
                    last_seen_count = current_count

                # Short sleep for responsive Ctrl+C (100ms)
                await asyncio.sleep(0.1)

            # Task completed
            console.print(f"\n[bold green]✅ Task completed:[/bold green] {task.status}")

            if task.status == "completed" and task.result:
                console.print(f"\n[bold]Final Result:[/bold]\n{task.result[:500]}")
            elif task.status == "failed" and task.error:
                console.print(f"\n[bold red]Error:[/bold red]\n{task.error}")

            return ""  # Empty string since we already printed everything

        except (KeyboardInterrupt, asyncio.CancelledError):
            # User pressed Ctrl+C or task was cancelled
            console.print(f"\n[yellow]⏸ Stopped following {task_id}[/yellow]")
            console.print(f"[dim]Task is still running in background[/dim]")
            console.print(f"[cyan]→ /tasks status {task_id}[/cyan]  [dim]Check progress[/dim]")
            console.print(f"[cyan]→ tail -f {task.output_file}[/cyan]  [dim]View log[/dim]")
            return ""
        except Exception as e:
            # Catch any other errors gracefully
            console.print(f"\n[red]Error during follow:[/red] {str(e)}")
            console.print(f"[dim]Task is still running. Check: /tasks status {task_id}[/dim]")
            return ""

    async def _cancel_task(self, task_id: str, task_manager) -> str:
        """
        Cancel running task with confirmation.

        Args:
            task_id: Task ID to cancel
            task_manager: BackgroundTaskManager instance

        Returns:
            Success/failure message
        """
        task = task_manager.get_task(task_id)

        if not task:
            return f"[red]Task not found:[/red] {task_id}\n[dim]Use /tasks list to see available tasks[/dim]"

        if task.status != "running":
            return f"[yellow]Task is not running[/yellow] (status: {task.status})\n[dim]Only running tasks can be cancelled[/dim]"

        # Show confirmation
        task_preview = task.task_content[:60] + "..." if len(task.task_content) > 60 else task.task_content
        confirm_msg = (
            f"⚠️  Confirm cancel task [{task_id}]?\n"
            f"Task: {task_preview}\n"
            f"Status: {task.status}"
        )

        if not Confirm.ask(confirm_msg, default=False):
            return "[dim]Cancellation aborted.[/dim]"

        # Cancel task
        success = await task_manager.cancel_task(task_id)

        if success:
            return f"[green]✓ Task cancelled[/green] [{task_id}]\n[dim]Task will stop shortly[/dim]"
        else:
            return f"[red]Failed to cancel task[/red] [{task_id}]\n[dim]Task may have already completed or failed[/dim]"

    def _help_text(self) -> str:
        """
        Return help text for /tasks command.

        Returns:
            Formatted help text
        """
        return """[bold cyan]/tasks command usage:[/bold cyan]

[bold]Subcommands:[/bold]
  /tasks list              List all background tasks
  /tasks status <task-id>  Show detailed task status and progress
  /tasks follow <task-id>  Follow task output in real-time (Ctrl+C to stop)
  /tasks cancel <task-id>  Cancel a running task

[bold]Examples:[/bold]
  [dim]# List all tasks[/dim]
  /tasks list

  [dim]# Check task status[/dim]
  /tasks status task-001

  [dim]# Follow task output in real-time[/dim]
  /tasks follow task-001

  [dim]# Cancel running task[/dim]
  /tasks cancel task-002
"""
