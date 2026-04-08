"""
/dispatch command - Submit task to background execution

This is a Tool Command (direct execution, no LLM).
"""
from rich.prompt import Prompt
from aworld_cli.core.command_system import Command, CommandContext, register_command


@register_command
class DispatchCommand(Command):
    """
    Submit task to background execution.

    Type: Tool Command (direct execution)
    Flow: Command → BackgroundTaskManager → Background Task

    User interaction:
    Two modes supported:
    1. Direct: /dispatch <task description>
    2. Interactive: /dispatch (then enter task at prompt)

    The task runs in background, returns task-id immediately.
    Continue working while task executes.

    Examples:
        > /dispatch Run GAIA benchmark 0-50
        ✓ Task submitted [task-000]

        > /dispatch
        📝 Enter task description: Analyze aworld team implementation
        ✓ Task submitted [task-001]
    """

    @property
    def name(self) -> str:
        return "dispatch"

    @property
    def description(self) -> str:
        return "Submit task to background execution (/dispatch or /dispatch <task>)"

    @property
    def command_type(self) -> str:
        return "tool"  # Direct execution, no LLM

    async def execute(self, context: CommandContext) -> str:
        """
        Execute /dispatch command.

        Flow:
        1. Get task description (from args or prompt user)
        2. Get BackgroundTaskManager from context
        3. Submit task to background
        4. Return formatted success message

        Args:
            context: CommandContext with executor reference

        Returns:
            Formatted success message with task-id

        Usage:
            /dispatch                           # Interactive input
            /dispatch Run GAIA benchmark 0-50   # Direct submission
        """
        # 1. Get task description: use user_args if provided, otherwise prompt
        task_content = context.user_args.strip() if context.user_args else None

        if not task_content:
            # No args provided, prompt interactively
            task_content = Prompt.ask("📝 Enter task description")

        if not task_content or not task_content.strip():
            return "[yellow]Task submission cancelled (empty input)[/yellow]"

        # 2. Get BackgroundTaskManager from context
        task_manager = context.background_task_manager
        if not task_manager:
            return "[red]Error: Background task manager not available[/red]\n[dim]Hint: This may occur if executor is not properly initialized[/dim]"

        # 3. Get swarm from executor
        if not context.executor or not hasattr(context.executor, 'swarm'):
            return "[red]Error: Executor or swarm not available[/red]\n[dim]Hint: Executor must have a swarm instance[/dim]"

        swarm = context.executor.swarm
        context_config = getattr(context.executor, 'context_config', None)

        # 4. Submit background task
        try:
            task_id = await task_manager.submit_task(
                agent_name="Aworld",  # Default agent (Aworld dispatches sub-agents based on task)
                task_content=task_content.strip(),
                swarm=swarm,
                context_config=context_config
            )
        except Exception as e:
            return f"[red]Error submitting task:[/red] {str(e)}\n[dim]Check logs for details[/dim]"

        # 5. Return formatted message
        return self._format_success_message(task_id, task_content, task_manager)

    def _format_success_message(self, task_id: str, task_content: str, task_manager) -> str:
        """
        Format success message with task info and usage hints.

        Args:
            task_id: Generated task ID
            task_content: User's task description
            task_manager: BackgroundTaskManager instance

        Returns:
            Formatted success message
        """
        # Truncate task content for display
        display_content = task_content[:60] + "..." if len(task_content) > 60 else task_content

        # Get output file path
        task = task_manager.get_task(task_id)
        output_file_line = ""
        if task and task.output_file:
            output_file_line = f"[bold]Output File:[/bold] [cyan]{task.output_file}[/cyan]\n"

        return f"""[green]✓ Task submitted:[/green] [bold cyan]{task_id}[/bold cyan]

[bold]Task:[/bold] {display_content}
[bold]Agent:[/bold] Aworld (default)
[bold]Status:[/bold] pending
{output_file_line}
[dim]Monitor task:[/dim]
  [cyan]/tasks status {task_id}[/cyan]   Check progress
  [cyan]/tasks follow {task_id}[/cyan]   Follow output (Ctrl+C to exit)
  [cyan]tail -f {task.output_file if task and task.output_file else f'.aworld/tasks/{task_id}.log'}[/cyan]  [dim]# Alternative: system command[/dim]

[dim]Manage tasks:[/dim]
  [cyan]/tasks list[/cyan]              List all tasks
  [cyan]/tasks cancel {task_id}[/cyan]   Cancel this task
"""
