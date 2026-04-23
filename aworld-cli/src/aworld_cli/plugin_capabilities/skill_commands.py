from __future__ import annotations

import inspect
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from aworld.plugins.resources import PluginResourceResolver


class PluginSkillCommandAdapter:
    def __init__(
        self,
        command,
        *,
        aliases: tuple[str, ...] = (),
    ) -> None:
        self._command = command
        self._aliases = aliases

    @property
    def name(self) -> str:
        return self._command.name

    @property
    def description(self) -> str:
        return self._command.description

    @property
    def aliases(self) -> tuple[str, ...]:
        command_aliases = tuple(getattr(self._command, "aliases", tuple()) or tuple())
        merged: list[str] = []
        for alias in command_aliases + self._aliases:
            normalized = str(alias).strip()
            if normalized and normalized not in merged:
                merged.append(normalized)
        return tuple(merged)

    @property
    def usage(self) -> str:
        usage = str(getattr(self._command, "usage", "") or "").strip()
        if usage:
            return usage
        return f"/skills {self.name}"

    async def run(self, cli, args_text: str, **kwargs):
        result = self._command.run(cli, args_text, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result


def load_plugin_skill_commands(plugins) -> list[PluginSkillCommandAdapter]:
    commands: list[PluginSkillCommandAdapter] = []
    seen: set[str] = set()

    for plugin in plugins:
        for entrypoint in plugin.manifest.entrypoints.get("skill_commands", ()):
            command = _load_plugin_skill_command(plugin, entrypoint)
            names = [command.name, *command.aliases]
            normalized_names = {str(name).strip().lower() for name in names if str(name).strip()}
            if normalized_names & seen:
                continue
            commands.append(command)
            seen.update(normalized_names)

    return sorted(commands, key=lambda item: item.name)


def _load_plugin_skill_command(plugin, entrypoint) -> PluginSkillCommandAdapter:
    if not entrypoint.target:
        raise ValueError(
            f"plugin skill command '{plugin.manifest.plugin_id}:{entrypoint.entrypoint_id}' "
            "is missing a target"
        )

    resolver = PluginResourceResolver(
        Path(plugin.manifest.plugin_root),
        plugin.manifest.plugin_id,
    )
    module_path = resolver.resolve_asset(entrypoint.target)
    spec = spec_from_file_location(
        f"aworld_skill_command_{plugin.manifest.plugin_id}_{entrypoint.entrypoint_id}",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise ImportError(
            f"unable to load plugin skill command module from {module_path}"
        )

    module = module_from_spec(spec)
    spec.loader.exec_module(module)

    factory_name = str(entrypoint.metadata.get("factory", "build_command")).strip()
    factory = getattr(module, factory_name, None)
    if factory is None:
        command = getattr(module, "COMMAND", None)
    else:
        command = factory()

    if command is None:
        raise AttributeError(
            f"plugin skill command '{plugin.manifest.plugin_id}:{entrypoint.entrypoint_id}' "
            f"must expose '{factory_name}()' or COMMAND"
        )
    if not hasattr(command, "name") or not callable(getattr(command, "run", None)):
        raise TypeError(
            f"plugin skill command '{plugin.manifest.plugin_id}:{entrypoint.entrypoint_id}' "
            "did not return a skill-command-compatible object"
        )

    raw_aliases = entrypoint.metadata.get("aliases", ())
    if isinstance(raw_aliases, str):
        aliases = (raw_aliases,)
    elif isinstance(raw_aliases, (list, tuple, set)):
        aliases = tuple(
            str(alias).strip() for alias in raw_aliases if str(alias).strip()
        )
    else:
        aliases = tuple()

    return PluginSkillCommandAdapter(command, aliases=aliases)
