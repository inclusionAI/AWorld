"""
/history command - View and search tool call history

Usage:
  /history              - Show recent 10 tool calls
  /history 20           - Show recent 20 tool calls
  /history bash         - Show recent bash tool calls
  /history --failed     - Show recent failed calls
  /history --full <id>  - Show full output of a specific call
"""
from typing import List, Optional
from rich.console import Console
from rich.table import Table
from ..core.command_system import Command, CommandContext, register_command


@register_command
class HistoryCommand(Command):
    """View tool call history for debugging and analysis."""

    @property
    def name(self) -> str:
        return "history"

    @property
    def description(self) -> str:
        return "View tool call history"

    @property
    def command_type(self) -> str:
        return "tool"  # Direct execution, no agent needed

    @property
    def allowed_tools(self) -> Optional[List[str]]:
        return None  # Not applicable for tool commands

    async def execute(self, context: CommandContext) -> str:
        """
        Execute history command.

        Args:
            context: Command execution context

        Returns:
            Formatted history output
        """
        from ..executors.tool_logger import get_tool_logger

        logger = get_tool_logger()
        console = Console()

        # Parse arguments
        args = context.user_args.strip().split()
        show_failed = "--failed" in args
        show_full = "--full" in args

        # Remove flags from args
        args = [a for a in args if not a.startswith("--")]

        # Determine what to show
        if show_full:
            # Show full output of a specific call
            if not args:
                return "[yellow]Usage: /history --full <call_number>[/yellow]"

            call_number = int(args[0])
            return self._show_full_output(logger, call_number)

        elif show_failed:
            # Show failed calls
            failed_calls = logger.get_failed_calls(limit=20)
            return self._format_calls_table(failed_calls, "Recent Failed Calls", console)

        else:
            # Show recent calls, optionally filtered by tool name
            limit = 10
            tool_filter = None

            if args:
                # First arg could be limit or tool name
                try:
                    limit = int(args[0])
                except ValueError:
                    tool_filter = args[0]
                    if len(args) > 1:
                        try:
                            limit = int(args[1])
                        except ValueError:
                            pass

            calls = logger.search_calls(tool_name=tool_filter, limit=limit)
            title = f"Recent {limit} Tool Calls"
            if tool_filter:
                title += f" (filter: {tool_filter})"

            return self._format_calls_table(calls, title, console)

    def _format_calls_table(self, calls: List[dict], title: str, console: Console) -> str:
        """Format tool calls as a table."""
        if not calls:
            return "[dim]No tool calls found.[/dim]"

        table = Table(title=title, show_header=True, header_style="bold cyan")
        table.add_column("#", style="dim", width=4)
        table.add_column("Time", style="dim", width=16)
        table.add_column("Tool", style="cyan", width=25)
        table.add_column("Status", width=8)
        table.add_column("Duration", justify="right", width=10)
        table.add_column("Output", width=40)

        for i, call in enumerate(calls, 1):
            # Format timestamp
            timestamp = call.get('timestamp', '')[:19].replace('T', ' ')

            # Format tool name
            tool_name = call.get('tool_name', 'Unknown')
            if len(tool_name) > 25:
                tool_name = tool_name[:22] + "..."

            # Format status with color
            status = call.get('status', 'unknown')
            if status == "success":
                status_display = "[green]✓[/green]"
            elif status == "error":
                status_display = "[red]✗[/red]"
            else:
                status_display = "[yellow]?[/yellow]"

            # Format duration
            duration = call.get('duration_seconds', 0)
            duration_display = f"{duration:.2f}s"

            # Format output preview
            output = call.get('output', '')
            if not output or output == "None":
                output_preview = "[dim]no output[/dim]"
            else:
                # Take first line, truncate to 40 chars
                first_line = output.split('\n')[0].strip()
                if len(first_line) > 40:
                    output_preview = first_line[:37] + "..."
                else:
                    output_preview = first_line

                # Add line count if truncated
                stats = call.get('output_stats', {})
                if stats.get('truncated'):
                    total_lines = stats.get('lines', 0)
                    output_preview += f" [dim]({total_lines} lines)[/dim]"

            table.add_row(
                str(call.get('_call_number', i)),
                timestamp,
                tool_name,
                status_display,
                duration_display,
                output_preview
            )

        # Render table to string
        from io import StringIO
        string_buffer = StringIO()
        temp_console = Console(file=string_buffer, force_terminal=False, color_system=None)
        temp_console.print(table)

        output = string_buffer.getvalue()

        # Add usage hints
        hints = [
            "",
            "[dim]Tip: Use /history --full <#> to see full output[/dim]",
            "[dim]     Use /history --failed to see errors[/dim]",
            "[dim]     Use /history bash 20 to filter by tool name[/dim]"
        ]

        return output + "\n".join(hints)

    def _show_full_output(self, logger, call_number: int) -> str:
        """Show full output of a specific call."""
        calls = logger.search_calls(limit=100)  # Search recent calls

        # Find call by number
        target_call = None
        for call in calls:
            if call.get('_call_number') == call_number:
                target_call = call
                break

        if not target_call:
            return f"[yellow]Call #{call_number} not found in recent history.[/yellow]"

        # Format full output
        lines = [
            f"[bold]Tool Call #{call_number}[/bold]",
            "",
            f"[dim]Tool:[/dim] {target_call.get('tool_name')}",
            f"[dim]Time:[/dim] {target_call.get('timestamp')}",
            f"[dim]Duration:[/dim] {target_call.get('duration_seconds'):.3f}s",
            f"[dim]Status:[/dim] {target_call.get('status')}",
            ""
        ]

        # Show args
        args = target_call.get('args', {})
        if args:
            lines.append("[dim]Arguments:[/dim]")
            for key, value in args.items():
                lines.append(f"  {key}: {value}")
            lines.append("")

        # Show full output or point to file
        if target_call.get('metadata', {}).get('output_file'):
            output_file = target_call['metadata']['output_file']
            lines.extend([
                "",
                f"[cyan]💾 Full output saved to:[/cyan]",
                f"  [green]{output_file}[/green]",
                "",
                f"[dim]View with:[/dim] [yellow]cat {output_file}[/yellow]"
            ])
        else:
            output = target_call.get('output', '')
            if output:
                lines.extend([
                    "[dim]Output:[/dim]",
                    "```",
                    output,
                    "```"
                ])
            else:
                lines.append("[dim italic]No output[/dim italic]")

        # Show error if present
        error = target_call.get('error')
        if error:
            lines.extend([
                "",
                "[red]Error:[/red]",
                error
            ])

        return "\n".join(lines)
