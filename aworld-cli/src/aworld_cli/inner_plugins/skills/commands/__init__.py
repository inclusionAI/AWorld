"""
Session command /skills: list current skills (recommended command).

Also provides: register all loaded skills as slash commands; when user selects
one, call context.active_skill(skill_name, namespace) on the current executor's context.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Dict, Any, Tuple

from aworld_cli.core.session_commands import (
    register_session_command,
    register_session_command_dynamic_provider,
)
from aworld_cli.core.skill_registry import get_skill_registry
from aworld.logs.util import logger
from rich import box
from rich.table import Table
from rich.text import Text
from rich.style import Style

if TYPE_CHECKING:
    from aworld_cli.console import AWorldCLI


def _make_skill_activate_handler(skill_name: str):
    """Build an async handler that activates one skill via context.active_skill."""

    async def _handler(cli: "AWorldCLI", context: Any = None) -> None:
        agent_id = cli.get_active_agent_id() or "default"
        # Prefer context passed from console; fallback to executor.context
        if not context or not hasattr(context, "active_skill"):
            executor = getattr(cli, "_active_executor", None)
            context = getattr(executor, "context", None) if executor else None
            if (not context or not hasattr(context, "active_skill")) and executor and hasattr(executor, "ensure_context"):
                try:
                    context = await executor.ensure_context()
                except Exception as e:
                    logger.warning(f"⚠️ ensure_context() failed: {e}")
        if not context or not hasattr(context, "active_skill"):
            cli.console.print("[yellow]⚠️ No context.active_skill available; cannot activate skill.[/yellow]")
            return
        try:
            await context.active_skill(skill_name, namespace=agent_id)
            cli.console.print(f"[green]✅ Activated skill: {skill_name}[/green]")
        except Exception as e:
            logger.exception(f"active_skill({skill_name}) failed")
            cli.console.print(f"[red]❌ Failed to activate skill '{skill_name}': {e}[/red]")

    return _handler


async def register_skill_commands_into(
    session_commands: Dict[str, Tuple[Any, str]],
    cli: "AWorldCLI",
    executor_instance: Any,
    agent_name: str,
) -> None:
    """
    Load skills, set session refs on cli, and add one session command per skill.

    Each command is "/<skill_name>"; when selected, calls context.active_skill(skill_name, namespace).
    Mutates session_commands in place.
    """
    all_skills = cli.get_all_skills()
    for name, data in (all_skills or {}).items():
        cmd = f"/{name}"
        if cmd in session_commands:
            continue
        short_desc = (data.get("description") or data.get("desc") or "").strip()
        desc = f"Activate: {name}" if not short_desc else f"{name} — {short_desc[:50]}"
        session_commands[cmd] = (_make_skill_activate_handler(name), desc)


async def handle_skills(cli: "AWorldCLI", context: Any = None) -> None:
    """
    Handle /skills: display current skills list (skills are loaded once at runtime.start()).

    Shows in recommended (/) commands so users can see available skills.
    context: current executor.context, passed by console (optional, unused here).
    """
    try:
        all_skills = cli.get_all_skills()
        if not all_skills:
            cli.console.print("[yellow]📭 No skills available.[/yellow]")
            return

        rows = [(name, data) for name, data in all_skills.items()]
        rows.sort(key=lambda x: x[0])

        table = Table(title="📚 Skills", box=box.ROUNDED)
        table.add_column("Name", style="magenta")
        table.add_column("Description", style="green")
        _addr_max = 48
        table.add_column("Address", style="dim", no_wrap=False, max_width=_addr_max)

        for skill_name, skill_data in rows:
            raw_desc = (
                skill_data.get("description")
                or skill_data.get("desc")
                or "No description"
            )
            # Ensure str: skill metadata may expose template-like objects; Rich expects string cells
            desc = raw_desc if isinstance(raw_desc, str) else str(raw_desc)
            skill_name = skill_name if isinstance(skill_name, str) else str(skill_name)
            address = skill_data.get("skill_path", "") or "—"
            if not isinstance(address, str):
                address = str(address)
            if address == "—":
                addr_cell = Text("—", style="dim")
            else:
                p = Path(address)
                link_target = p.parent if p.suffix else p
                try:
                    link_url = link_target.resolve().as_uri()
                except (OSError, RuntimeError):
                    link_url = ""
                if link_url:
                    addr_display = (
                        address[: _addr_max - 3] + "..."
                        if len(address) > _addr_max
                        else address
                    )
                    addr_cell = Text(addr_display, style=Style(dim=True, link=link_url))
                else:
                    addr_cell = Text(
                        address[: _addr_max - 3] + "..."
                        if len(address) > _addr_max
                        else address,
                        style="dim",
                    )
            table.add_row(skill_name, desc, addr_cell)

        cli.console.print(table)
        cli.console.print(f"[dim]Total: {len(all_skills)} skill(s)[/dim]")

    except Exception as e:
        logger.exception("Error loading skills")
        cli.console.print(f"[red]❌ Error loading skills: {e}[/red]")


def _register_commands() -> None:
    register_session_command(
        "/skills",
        handle_skills,
        "List available skills",
    )
    # Auto-load: when session starts, merge_dynamic_session_commands runs and invokes
    # this provider to add /<skill_name> commands.
    register_session_command_dynamic_provider(register_skill_commands_into)


_register_commands()
