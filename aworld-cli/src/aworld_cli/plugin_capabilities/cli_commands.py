from __future__ import annotations

from aworld_cli.core.top_level_command_system import TopLevelCommandRegistry


def sync_plugin_cli_commands(registry: TopLevelCommandRegistry, plugins) -> None:
    # Phase 1 only migrates builtin `skill`. Keep the bridge in place so future
    # manifest-declared cli_commands can register through the same bootstrap path.
    for plugin in plugins:
        for entrypoint in plugin.manifest.entrypoints.get("cli_commands", ()):
            command_name = entrypoint.name or entrypoint.entrypoint_id
            if registry.get(command_name) is not None:
                continue
