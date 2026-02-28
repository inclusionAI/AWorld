"""
Session command /skills: list current skills (recommended command).

Also provides: register all loaded skills as slash commands; when user selects
one, call context.active_skill(skill_name, namespace) on the current executor's context.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Dict, Any, Tuple

from aworld_cli.core.session_commands import register_session_command
from aworld_cli.core.skill_registry import get_skill_registry
from aworld.logs.util import logger
from rich.table import Table
from rich.text import Text
from rich.style import Style

if TYPE_CHECKING:
    from aworld_cli.console import AWorldCLI


def _make_skill_activate_handler(skill_name: str):
    """Build an async handler that activates one skill via context.active_skill."""

    async def _handler(cli: "AWorldCLI", context: Any = None) -> None:
        agent_name = getattr(cli, "_session_agent_name", "") or "default"
        # Prefer context passed from console; fallback to executor.context
        if not context or not hasattr(context, "active_skill"):
            executor = getattr(cli, "_session_executor_instance", None)
            context = getattr(executor, "context", None) if executor else None
        if not context or not hasattr(context, "active_skill"):
            cli.console.print("[yellow]⚠️ No context.active_skill available; cannot activate skill.[/yellow]")
            return
        try:
            await context.active_skill(skill_name, namespace=agent_name)
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
    cli._session_executor_instance = executor_instance  # noqa: SLF001
    cli._session_agent_name = agent_name  # noqa: SLF001
    try:
        from aworld_cli.runtime.cli import CliRuntime

        runtime = CliRuntime()
        runtime.cli = cli
        await runtime._load_skills()
    except Exception as e:
        logger.debug(f"Load skills for command registration: {e}")
    registry = get_skill_registry()
    all_skills = registry.get_all_skills()
    for name, data in (all_skills or {}).items():
        cmd = f"/{name}"
        if cmd in session_commands:
            continue
        short_desc = (data.get("description") or data.get("desc") or "").strip()
        desc = f"Activate: {name}" if not short_desc else f"{name} — {short_desc[:50]}"
        session_commands[cmd] = (_make_skill_activate_handler(name), desc)


async def handle_skills(cli: "AWorldCLI", context: Any = None) -> None:
    """
    Handle /skills: load skills from plugins and display current skills list.

    Shows in recommended (/) commands so users can see available skills.
    context: current executor.context, passed by console (optional, unused here).
    """
    try:
        from aworld_cli.runtime.cli import CliRuntime

        runtime = CliRuntime()
        runtime.cli = cli
        loaded_skills = await runtime._load_skills()

        if loaded_skills:
            total_loaded = sum(loaded_skills.values())
            if total_loaded > 0:
                logger.info(
                    f"[green]✅ Loaded {total_loaded} skill(s) from "
                    f"{len([k for k, v in loaded_skills.items() if v > 0])} plugin(s)[/green]"
                )
            else:
                logger.info("[dim]No new skills loaded from plugins.[/dim]")

        registry = get_skill_registry()
        all_skills = registry.get_all_skills()

        if not all_skills:
            cli.console.print("[yellow]📭 No skills available.[/yellow]")
            return

        rows = [(name, data) for name, data in all_skills.items()]
        rows.sort(key=lambda x: x[0])

        table = Table(title="📚 Skills (当前技能列表)", box="rounded")
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
        "List available skills (当前技能列表)",
    )


_register_commands()
