"""Explicit plugin-facing HUD formatting helpers."""

from aworld_cli.executors.stats import (
    format_context_bar_hud,
    format_elapsed,
    format_tokens,
)


def format_hud_tokens(value: int) -> str:
    return format_tokens(value)


def format_hud_elapsed(value: float) -> str:
    return format_elapsed(value)


def format_hud_context_bar(used_tokens: int, max_tokens: int, bar_width: int = 10) -> str:
    return format_context_bar_hud(used_tokens, max_tokens, bar_width=bar_width)
