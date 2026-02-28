"""
Rich-based reusable console UI components for aworld-cli.

This module centralizes common interactive patterns so that:
- The CLI has a consistent look and feel.
- Business logic does not duplicate Rich/Prompt wiring.

All helpers here are thin wrappers around Rich + prompt_toolkit style input.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt


def select_menu_option(
    console: Console,
    title: str,
    options: Sequence[Tuple[str, str]],
    back_label: str = "Back",
) -> Optional[str]:
    """
    Display a simple numbered menu and return the selected key.

    Args:
        console: Rich console instance to render UI.
        title: Menu title displayed inside a panel.
        options: Sequence of (key, label) tuples. Keys are returned on selection.
        back_label: Label for the implicit "back/cancel" option.

    Returns:
        The key of the selected option, or None if the user cancels or input is invalid.
    """
    if not options:
        console.print("[yellow]No options available.[/yellow]")
        return None

    console.print(
        Panel(
            f"[bold]{title}[/bold]",
            border_style="cyan",
        )
    )

    for idx, (_, label) in enumerate(options, start=1):
        console.print(f"  {idx}. {label}")

    back_index = len(options) + 1
    console.print(f"  {back_index}. {back_label}")

    choice_str = Prompt.ask("\nChoice", default=str(back_index))
    try:
        choice = int(choice_str)
    except ValueError:
        console.print("[red]Invalid selection.[/red]")
        return None

    if choice == back_index:
        console.print("[dim]Cancelled.[/dim]")
        return None

    if choice < 1 or choice > len(options):
        console.print("[red]Invalid selection.[/red]")
        return None

    key, _ = options[choice - 1]
    return key


def confirm_action(
    console: Console,
    message: str,
    default: bool = False,
) -> bool:
    """
    Ask the user to confirm an action.

    Args:
        console: Rich console instance.
        message: Prompt message (without trailing '?').
        default: Default answer when user presses Enter directly.

    Returns:
        True if the user confirms, False otherwise.
    """
    suffix = "[Y/n]" if default else "[y/N]"
    return Confirm.ask(f"{message} {suffix}", default=default)


def print_info_panel(console: Console, message: str, title: str = "Info") -> None:
    """
    Print an informational panel.

    Args:
        console: Rich console instance.
        message: Message body to display.
        title: Panel title.
    """
    console.print(
        Panel(
            message,
            title=title,
            border_style="green",
        )
    )


def print_error_panel(console: Console, message: str, title: str = "Error") -> None:
    """
    Print an error panel.

    Args:
        console: Rich console instance.
        message: Error message to display.
        title: Panel title.
    """
    console.print(
        Panel(
            message,
            title=title,
            border_style="red",
        )
    )


__all__ = [
    "select_menu_option",
    "confirm_action",
    "print_info_panel",
    "print_error_panel",
]

