from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Optional

from aworld.logs.util import logger
from aworld_cli.runtime.hud_snapshot import HudSnapshotStore


class AcpPluginRuntime:
    """ACP-local plugin runtime surface for executor hook reuse."""

    def __init__(
        self,
        *,
        workspace_path: str,
        plugin_roots: Iterable[Path],
        bootstrap: dict[str, Any] | None = None,
    ) -> None:
        self.workspace_path = str(Path(workspace_path).expanduser().resolve())
        self.plugin_roots = [Path(path).expanduser().resolve() for path in plugin_roots]
        self.bootstrap = dict(bootstrap or {})
        self._plugins: list[Any] = []
        self._plugin_registry = None
        self._plugin_hooks: dict[str, tuple[Any, ...]] = {}
        self._plugin_state_store = None
        self._hud_snapshot_store = HudSnapshotStore()
        self._initialize_plugin_framework()

    def _initialize_plugin_framework(self) -> None:
        if not self.plugin_roots:
            self._plugins = []
            self._plugin_registry = None
            self._plugin_hooks = {}
            self._plugin_state_store = None
            return

        try:
            from aworld.plugins.discovery import discover_plugins
            from aworld.plugins.registry import PluginCapabilityRegistry
            from aworld.plugins.resolution import resolve_plugin_activation
            from aworld_cli.plugin_capabilities.hooks import load_plugin_hooks
            from aworld_cli.plugin_capabilities.state import PluginStateStore

            discovered_plugins = []
            registry = PluginCapabilityRegistry()
            loaded_plugins = []
            loaded_hooks = defaultdict(list)

            for plugin_root in self.plugin_roots:
                try:
                    discovered = discover_plugins([plugin_root])
                except Exception as exc:
                    logger.warning(f"Skipping ACP plugin root '{plugin_root}': {exc}")
                    continue
                discovered_plugins.extend(discovered)

            active_plugins, skipped_plugins = resolve_plugin_activation(discovered_plugins)
            for plugin_id, reason in skipped_plugins.items():
                logger.warning(f"Skipping ACP plugin '{plugin_id}': {reason}")

            for plugin in active_plugins:
                try:
                    plugin_hooks = load_plugin_hooks([plugin])
                    registry.register(plugin)
                except Exception as exc:
                    logger.warning(
                        f"Skipping ACP plugin "
                        f"({getattr(plugin.manifest, 'plugin_id', 'unknown')}): {exc}"
                    )
                    continue

                loaded_plugins.append(plugin)
                for hook_point, hooks in plugin_hooks.items():
                    loaded_hooks[hook_point].extend(hooks)

            self._plugins = loaded_plugins
            self._plugin_registry = registry
            self._plugin_hooks = {
                hook_point: tuple(
                    sorted(
                        hooks,
                        key=lambda hook: (hook.priority, hook.plugin_id, hook.entrypoint_id),
                    )
                )
                for hook_point, hooks in loaded_hooks.items()
            }
            self._plugin_state_store = PluginStateStore(
                Path(self.workspace_path) / ".aworld" / "plugin_state"
            )
        except Exception as exc:
            logger.warning(f"Failed to initialize ACP plugin runtime: {exc}")
            self._plugins = []
            self._plugin_registry = None
            self._plugin_hooks = {}
            self._plugin_state_store = None

    def get_plugin_hooks(self, hook_point: str) -> list[Any]:
        normalized = (hook_point or "").strip().lower()
        return list(self._plugin_hooks.get(normalized, ()))

    def active_plugin_capabilities(self) -> tuple[str, ...]:
        if self._plugin_registry is None:
            return tuple()
        return self._plugin_registry.capabilities()

    def update_hud_snapshot(self, **sections: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return self._hud_snapshot_store.update(**sections)

    def settle_hud_snapshot(self, task_status: str = "idle") -> dict[str, dict[str, Any]]:
        return self._hud_snapshot_store.settle(task_status=task_status)

    def get_hud_snapshot(self) -> dict[str, dict[str, Any]]:
        return self._hud_snapshot_store.snapshot()

    def build_plugin_hook_state(
        self,
        plugin_id: str,
        scope: str,
        executor_instance: Any = None,
    ) -> dict[str, Any]:
        context = getattr(executor_instance, "context", None) if executor_instance else None
        workspace_path = getattr(context, "workspace_path", None) or self.workspace_path
        session_id = getattr(executor_instance, "session_id", None)
        if not session_id and context is not None:
            session_id = getattr(context, "session_id", None)
        task_id = getattr(context, "task_id", None) if context is not None else None

        return self._build_plugin_state(
            plugin_id=plugin_id,
            scope=scope,
            session_id=session_id,
            workspace_path=workspace_path,
            task_id=task_id,
            include_handle=True,
        )

    def _build_plugin_state(
        self,
        plugin_id: str,
        scope: str,
        session_id: Optional[str],
        workspace_path: Optional[str],
        task_id: Optional[str],
        *,
        include_handle: bool,
    ) -> dict[str, Any]:
        state: dict[str, Any] = {}
        handle = None
        state_path = self._resolve_plugin_state_path(
            plugin_id=plugin_id,
            scope=scope,
            session_id=session_id,
            workspace_path=workspace_path,
        )
        if state_path is not None and self._plugin_state_store is not None:
            try:
                handle = self._plugin_state_store.handle(state_path)
                state.update(handle.read())
            except Exception as exc:
                logger.warning(f"Failed to read ACP plugin state for {plugin_id}: {exc}")

        if session_id:
            state.setdefault("session_id", session_id)
        if workspace_path:
            state.setdefault("workspace_path", workspace_path)
        if task_id:
            state.setdefault("task_id", task_id)
        if include_handle and handle is not None:
            state["__plugin_state__"] = handle

        return state

    async def run_plugin_hooks(
        self,
        hook_point: str,
        event: dict[str, Any],
        executor_instance: Any = None,
    ) -> list[tuple[Any, Any]]:
        normalized = (hook_point or "").strip().lower()
        results = []
        for hook in self.get_plugin_hooks(normalized):
            try:
                state = self.build_plugin_hook_state(hook.plugin_id, hook.scope, executor_instance)
                results.append((hook, await hook.run(event=dict(event), state=state)))
            except Exception as exc:
                logger.warning(
                    f"ACP plugin hook '{getattr(hook, 'entrypoint_id', 'unknown')}' failed "
                    f"at '{normalized}': {exc}"
                )
        return results

    def _resolve_plugin_state_path(
        self,
        plugin_id: str,
        scope: str,
        session_id: Optional[str],
        workspace_path: Optional[str],
    ) -> Optional[Path]:
        if self._plugin_state_store is None:
            return None
        if scope == "global":
            return self._plugin_state_store.global_state(plugin_id)
        if scope == "session" and session_id:
            return self._plugin_state_store.session_state(plugin_id, session_id)
        if workspace_path:
            return self._plugin_state_store.workspace_state(plugin_id, workspace_path)
        return None
