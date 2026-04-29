from __future__ import annotations

import shlex

from aworld_cli.core.command_system import CommandContext
from aworld_cli.memory.durable import (
    DEFAULT_DURABLE_MEMORY_TYPE,
    DURABLE_MEMORY_TYPES,
    normalize_durable_memory_type,
)
from aworld_cli.memory.provider import CliDurableMemoryProvider
from aworld_cli.plugin_capabilities.commands import PluginBoundCommand


class RememberCommand(PluginBoundCommand):
    @property
    def command_type(self) -> str:
        return "tool"

    async def execute(self, context: CommandContext) -> str:
        parsed = _parse_remember_args(context.user_args or "")
        if isinstance(parsed, str):
            return parsed

        memory_type, guidance = parsed
        result = CliDurableMemoryProvider().append_durable_memory_record(
            context.cwd,
            text=guidance,
            memory_type=memory_type,
            source="remember_command",
        )

        label = "durable memory" if memory_type == DEFAULT_DURABLE_MEMORY_TYPE else f"{memory_type} durable memory"
        lines = []
        if result.record_created:
            lines.append(f"Saved {label} to {result.record_path}")
        else:
            lines.append(f"{label.capitalize()} already recorded in {result.record_path}")
        if result.instruction_target is not None:
            if result.instruction_updated:
                lines.append(f"Mirrored into workspace instructions: {result.instruction_target}")
            else:
                lines.append(
                    f"Workspace instructions already contain this guidance: {result.instruction_target}"
                )
        return "\n".join(lines)


def _parse_remember_args(user_args: str) -> tuple[str, str] | str:
    try:
        tokens = shlex.split(user_args)
    except ValueError as exc:
        return f"Unable to parse /remember arguments: {exc}"

    if not tokens:
        return _usage()

    memory_type = DEFAULT_DURABLE_MEMORY_TYPE
    guidance_tokens: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--type":
            if index + 1 >= len(tokens):
                return _usage()
            memory_type = tokens[index + 1]
            index += 2
            continue
        if token.startswith("--type="):
            memory_type = token.split("=", 1)[1]
            index += 1
            continue
        guidance_tokens.append(token)
        index += 1

    guidance = " ".join(guidance_tokens).strip()
    if not guidance:
        return _usage()

    try:
        normalized_type = normalize_durable_memory_type(memory_type)
    except ValueError as exc:
        return str(exc)

    return normalized_type, guidance


def _usage() -> str:
    valid_types = "|".join(DURABLE_MEMORY_TYPES)
    return f"Usage: /remember [--type {valid_types}] <durable guidance>"


def build_command(plugin, entrypoint):
    return RememberCommand(plugin, entrypoint)
