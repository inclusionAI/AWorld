"""
Session command /messages: view current agent's memory messages (conversation history).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aworld_cli.core.session_commands import register_session_command
from aworld.logs.util import logger
from rich import box
from rich.table import Table

if TYPE_CHECKING:
    from aworld_cli.console import AWorldCLI


def _message_role_or_type(msg: Any) -> str:
    """Get display role/type for a memory message."""
    if hasattr(msg, "metadata") and isinstance(getattr(msg, "metadata", None), dict):
        role = (msg.metadata or {}).get("role")
        if role:
            return str(role)
    return type(msg).__name__.replace("Memory", "").replace("Message", "") or "message"


def _message_content_preview(msg: Any, max_len: int = 80) -> str:
    """Get a short content preview for display."""
    content = getattr(msg, "content", None)
    if content is None:
        return "—"
    if isinstance(content, list):
        return "[multimodal]"
    s = str(content).strip()
    if not s:
        return "—"
    return (s[:max_len] + "…") if len(s) > max_len else s


async def handle_messages(cli: "AWorldCLI", context: Any = None) -> None:
    """
    Handle /messages: show current agent's memory messages (conversation history).

    Uses executor.context.get_memory_messages(last_n, namespace=agent_id).
    context: current executor.context, passed by console (optional).
    """
    agent_id = cli.get_active_agent_id() or "default"
    executor = getattr(cli, "_active_executor", None)
    if not context or not hasattr(context, "get_memory_messages"):
        context = getattr(executor, "context", None) if executor else None
    if (not context or not hasattr(context, "get_memory_messages")) and executor and hasattr(
        executor, "ensure_context"
    ):
        try:
            context = await executor.ensure_context()
        except Exception as e:
            logger.warning(f"⚠️ ensure_context() failed: {e}")
    if not context or not hasattr(context, "get_memory_messages"):
        cli.console.print("[yellow]⚠️ No context available; cannot get memory messages.[/yellow]")
        return
    try:
        messages = context.get_memory_messages(last_n=50, namespace=agent_id)
        if not messages:
            cli.console.print("[dim]📭 No memory messages for this agent yet.[/dim]")
            return
        table = Table(
            title=f"💬 Memory messages (agent: {agent_id}, last {len(messages)})",
            box=box.ROUNDED,
        )
        table.add_column("#", style="dim", width=4)
        table.add_column("Role", style="cyan", width=12)
        table.add_column("Content preview", style="green", no_wrap=False, max_width=72)
        for i, msg in enumerate(messages, 1):
            table.add_row(
                str(i),
                _message_role_or_type(msg),
                _message_content_preview(msg),
            )
        cli.console.print(table)
        cli.console.print(f"[dim]Total: {len(messages)} message(s)[/dim]")
    except Exception as e:
        logger.exception("handle_messages failed")
        cli.console.print(f"[red]❌ Failed to load memory messages: {e}[/red]")


def _register_commands() -> None:
    register_session_command(
        "/messages",
        handle_messages,
        "View current agent memory messages",
    )


_register_commands()
