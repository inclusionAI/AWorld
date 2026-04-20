from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class RegisteredEntrypoint:
    plugin: Any
    entrypoint: Any


class PluginCapabilityRegistry:
    def __init__(self, plugins: Iterable[Any] = ()):
        self._plugins: dict[str, Any] = {}
        self._capabilities: dict[str, list[Any]] = {}
        self._entrypoints: dict[str, list[RegisteredEntrypoint]] = {}
        self._lifecycle: dict[str, str] = {}
        self._lifecycle_history: dict[str, tuple[str, ...]] = {}

        for plugin in plugins:
            self.register(plugin)

    def register(self, plugin: Any) -> None:
        plugin_id = plugin.manifest.plugin_id
        if plugin_id in self._plugins:
            raise ValueError(f"duplicate plugin id: {plugin_id}")

        self._plugins[plugin_id] = plugin
        history = tuple(str(phase) for phase in plugin.manifest.lifecycle)
        self._lifecycle_history[plugin_id] = history
        self._lifecycle[plugin_id] = "activate" if "activate" in history else (history[-1] if history else "discover")

        for capability in sorted(plugin.manifest.capabilities):
            self._capabilities.setdefault(capability, []).append(plugin)
            for entrypoint in plugin.manifest.entrypoints.get(capability, ()):
                self._entrypoints.setdefault(capability, []).append(
                    RegisteredEntrypoint(plugin=plugin, entrypoint=entrypoint)
                )

        for capability in tuple(self._capabilities):
            self._capabilities[capability] = sorted(
                self._capabilities[capability],
                key=lambda item: item.manifest.plugin_id,
            )

        for capability in tuple(self._entrypoints):
            self._entrypoints[capability] = sorted(
                self._entrypoints[capability],
                key=lambda item: (item.plugin.manifest.plugin_id, item.entrypoint.entrypoint_id),
            )

    def get_plugin(self, plugin_id: str) -> Any | None:
        return self._plugins.get(plugin_id)

    def get_plugins(self, capability: str) -> tuple[Any, ...]:
        return tuple(self._capabilities.get(capability, ()))

    def get_entrypoints(self, capability: str) -> tuple[RegisteredEntrypoint, ...]:
        return tuple(self._entrypoints.get(capability, ()))

    def capabilities(self) -> tuple[str, ...]:
        return tuple(sorted(self._capabilities))

    def plugins(self) -> tuple[Any, ...]:
        return tuple(self._plugins[plugin_id] for plugin_id in sorted(self._plugins))

    def lifecycle_phase(self, plugin_id: str) -> str | None:
        return self._lifecycle.get(plugin_id)

    def lifecycle_history(self, plugin_id: str) -> tuple[str, ...]:
        return self._lifecycle_history.get(plugin_id, tuple())

    def deactivate(self, plugin_id: str) -> None:
        if plugin_id not in self._plugins:
            raise KeyError(plugin_id)
        self._lifecycle[plugin_id] = "deactivate"

    def unload(self, plugin_id: str) -> None:
        if plugin_id not in self._plugins:
            raise KeyError(plugin_id)
        self._lifecycle[plugin_id] = "unload"
