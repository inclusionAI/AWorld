from pathlib import Path
from importlib.util import module_from_spec, spec_from_file_location

from aworld.plugins.resources import PluginResourceResolver
from aworld_cli.core.command_system import Command, CommandContext, CommandRegistry


class PluginBoundCommand(Command):
    def __init__(self, plugin, entrypoint):
        self._plugin = plugin
        self._entrypoint = entrypoint
        self._resolver = PluginResourceResolver(Path(plugin.manifest.plugin_root), plugin.manifest.plugin_id)
        self._aworld_plugin_command = True

    @property
    def name(self) -> str:
        return self._entrypoint.name or self._entrypoint.entrypoint_id

    @property
    def description(self) -> str:
        return self._entrypoint.description or ""

    @property
    def allowed_tools(self) -> list[str]:
        tools = self._entrypoint.permissions.get("allowed_tools", [])
        return [str(item) for item in tools]

    def resolve_state_path(self, context: CommandContext):
        runtime = getattr(context, "runtime", None)
        if runtime is None or not hasattr(runtime, "_resolve_plugin_state_path"):
            return None

        session_id = getattr(context, "session_id", None) or getattr(runtime, "session_id", None)
        return runtime._resolve_plugin_state_path(
            plugin_id=self._plugin.manifest.plugin_id,
            scope=self._entrypoint.scope,
            session_id=session_id,
            workspace_path=context.cwd,
        )

    def get_state_handle(self, context: CommandContext):
        runtime = getattr(context, "runtime", None)
        state_path = self.resolve_state_path(context)
        if runtime is None or state_path is None:
            return None
        store = getattr(runtime, "_plugin_state_store", None)
        if store is None:
            return None
        return store.handle(state_path)


class PluginPromptCommand(PluginBoundCommand):

    async def get_prompt(self, context: CommandContext) -> str:
        prompt_path = self._resolver.resolve_asset(self._entrypoint.target)
        prompt = prompt_path.read_text(encoding="utf-8")
        return f"{prompt}\n\nUser args: {context.user_args}".strip()


def _load_module_command(plugin, entrypoint) -> Command:
    resolver = PluginResourceResolver(Path(plugin.manifest.plugin_root), plugin.manifest.plugin_id)
    module_path = resolver.resolve_asset(entrypoint.target)
    spec = spec_from_file_location(
        f"plugin_command_{plugin.manifest.plugin_id}_{entrypoint.entrypoint_id}",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"unable to load plugin command module from {module_path}")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)

    factory_name = str(entrypoint.metadata.get("factory", "build_command"))
    factory = getattr(module, factory_name, None)
    if factory is None:
        raise AttributeError(
            f"plugin command '{plugin.manifest.plugin_id}:{entrypoint.entrypoint_id}' "
            f"must define {factory_name}(plugin, entrypoint)"
        )

    command = factory(plugin, entrypoint)
    if not isinstance(command, Command):
        raise TypeError(
            f"plugin command '{plugin.manifest.plugin_id}:{entrypoint.entrypoint_id}' "
            "factory must return a Command instance"
        )
    setattr(command, "_aworld_plugin_command", True)
    return command


def _build_plugin_command(plugin, entrypoint) -> Command:
    target = str(entrypoint.target or "")
    if target.endswith(".py"):
        return _load_module_command(plugin, entrypoint)
    return PluginPromptCommand(plugin, entrypoint)


def register_plugin_commands(plugins) -> None:
    for plugin in plugins:
        for entrypoint in plugin.manifest.entrypoints.get("commands", []):
            if entrypoint.visibility == "hidden":
                continue
            command_name = entrypoint.name or entrypoint.entrypoint_id
            if CommandRegistry.get(command_name) is not None:
                continue
            CommandRegistry.register(_build_plugin_command(plugin, entrypoint))


def sync_plugin_commands(plugins) -> None:
    for command in list(CommandRegistry.list_commands()):
        if getattr(command, "_aworld_plugin_command", False):
            CommandRegistry.unregister(command.name)

    register_plugin_commands(plugins)


def commands_for_plugin(plugin) -> list[str]:
    visible: list[str] = []
    for entrypoint in plugin.manifest.entrypoints.get("commands", []):
        if entrypoint.visibility == "hidden":
            continue
        visible.append(f"/{entrypoint.name or entrypoint.entrypoint_id}")
    return visible
