from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from aworld.plugins.resources import PluginResourceResolver
from aworld_cli.core.top_level_command_system import TopLevelCommandRegistry


class PluginTopLevelCommandAdapter:
    def __init__(self, command, *, aliases: tuple[str, ...]) -> None:
        self._command = command
        self._aliases = aliases

    @property
    def name(self):
        return self._command.name

    @property
    def description(self):
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

    def register_parser(self, subparsers) -> None:
        return self._command.register_parser(subparsers)

    def run(self, args, context):
        return self._command.run(args, context)


def sync_plugin_cli_commands(
    registry: TopLevelCommandRegistry,
    plugins,
    *,
    builtin_plugin_roots: tuple[Path, ...] = (),
) -> None:
    builtin_roots = {Path(root).resolve() for root in builtin_plugin_roots}
    for plugin in plugins:
        registration_source = "plugin"
        if Path(plugin.manifest.plugin_root).resolve() in builtin_roots:
            registration_source = "builtin"
        for entrypoint in plugin.manifest.entrypoints.get("cli_commands", ()):
            if entrypoint.visibility == "hidden":
                continue
            command_name = entrypoint.name or entrypoint.entrypoint_id
            if registry.get(command_name) is not None:
                continue
            registry.register(
                _load_plugin_cli_command(plugin, entrypoint),
                source=registration_source,
            )


def _load_plugin_cli_command(plugin, entrypoint):
    if not entrypoint.target:
        raise ValueError(
            f"plugin cli command '{plugin.manifest.plugin_id}:{entrypoint.entrypoint_id}' "
            "is missing a target"
        )

    resolver = PluginResourceResolver(
        Path(plugin.manifest.plugin_root),
        plugin.manifest.plugin_id,
    )
    module_path = resolver.resolve_asset(entrypoint.target)
    spec = spec_from_file_location(
        f"aworld_cli_command_{plugin.manifest.plugin_id}_{entrypoint.entrypoint_id}",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"unable to load plugin cli command module from {module_path}")

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
            f"plugin cli command '{plugin.manifest.plugin_id}:{entrypoint.entrypoint_id}' "
            f"must expose '{factory_name}()' or COMMAND"
        )

    if not hasattr(command, "name") or not callable(getattr(command, "register_parser", None)):
        raise TypeError(
            f"plugin cli command '{plugin.manifest.plugin_id}:{entrypoint.entrypoint_id}' "
            "did not return a TopLevelCommand-compatible object"
        )

    raw_aliases = entrypoint.metadata.get("aliases", ())
    if isinstance(raw_aliases, str):
        aliases = (raw_aliases,)
    elif isinstance(raw_aliases, (list, tuple, set)):
        aliases = tuple(str(alias).strip() for alias in raw_aliases if str(alias).strip())
    else:
        aliases = tuple()

    return PluginTopLevelCommandAdapter(command, aliases=aliases)
