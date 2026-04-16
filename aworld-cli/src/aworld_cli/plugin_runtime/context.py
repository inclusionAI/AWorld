import inspect
from dataclasses import dataclass
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any, Iterable, Mapping

from aworld.plugins.resources import PluginResourceResolver


CONTEXT_PHASES = ("schema", "bootstrap", "enrich", "propagate", "persist")
PHASE_FUNCTIONS = {
    "schema": "register_schema",
    "bootstrap": "bootstrap_context",
    "enrich": "enrich_context",
    "propagate": "propagate_context",
    "persist": "persist_context",
}


@dataclass(frozen=True)
class PluginContextAdapter:
    plugin: Any
    entrypoint: Any

    @property
    def plugin_id(self) -> str:
        return self.plugin.manifest.plugin_id

    @property
    def entrypoint_id(self) -> str:
        return self.entrypoint.entrypoint_id

    @property
    def scope(self) -> str:
        return self.entrypoint.scope

    @property
    def phases(self) -> tuple[str, ...]:
        declared = self.entrypoint.metadata.get("phases", CONTEXT_PHASES)
        if not isinstance(declared, (list, tuple)):
            raise ValueError(
                f"context entrypoint '{self.plugin_id}:{self.entrypoint_id}' metadata.phases must be a list"
            )

        normalized = []
        for phase in declared:
            normalized_phase = str(phase).strip().lower()
            if normalized_phase not in CONTEXT_PHASES:
                raise ValueError(
                    f"unsupported context phase '{normalized_phase}' "
                    f"for '{self.plugin_id}:{self.entrypoint_id}'"
                )
            normalized.append(normalized_phase)
        return tuple(normalized)

    def _load_module(self) -> Any:
        if not self.entrypoint.target:
            raise ValueError(f"context entrypoint '{self.plugin_id}:{self.entrypoint_id}' is missing a target")

        resolver = PluginResourceResolver(Path(self.plugin.manifest.plugin_root), self.plugin_id)
        module_path = resolver.resolve_asset(self.entrypoint.target)
        spec = spec_from_file_location(
            f"aworld_context_{self.plugin_id}_{self.entrypoint_id}",
            module_path,
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"unable to load plugin context module from {module_path}")

        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def supports_phase(self, phase: str) -> bool:
        return phase in self.phases

    async def run(
        self,
        phase: str,
        context: Mapping[str, Any],
        state: Mapping[str, Any],
        target: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        normalized_phase = str(phase).strip().lower()
        if normalized_phase not in CONTEXT_PHASES:
            raise ValueError(f"unsupported context phase: {phase}")
        if normalized_phase not in self.phases:
            return {}

        module = self._load_module()
        handler_name = PHASE_FUNCTIONS[normalized_phase]
        handler = getattr(module, handler_name, None)
        if handler is None:
            return {}

        if normalized_phase == "propagate":
            payload = handler(dict(context), dict(state), dict(target or {}))
        else:
            payload = handler(dict(context), dict(state))

        if inspect.isawaitable(payload):
            payload = await payload

        if payload is None:
            return {}
        if not isinstance(payload, Mapping):
            raise ValueError(
                f"context phase '{normalized_phase}' for '{self.plugin_id}:{self.entrypoint_id}' "
                "must return a mapping"
            )
        return dict(payload)


def load_plugin_contexts(plugins: Iterable[Any]) -> dict[str, tuple[PluginContextAdapter, ...]]:
    loaded: dict[str, list[PluginContextAdapter]] = {phase: [] for phase in CONTEXT_PHASES}

    for plugin in plugins:
        for entrypoint in plugin.manifest.entrypoints.get("contexts", ()):
            adapter = PluginContextAdapter(plugin=plugin, entrypoint=entrypoint)
            for phase in adapter.phases:
                loaded[phase].append(adapter)

    for phase in CONTEXT_PHASES:
        loaded[phase].sort(key=lambda item: (item.plugin_id, item.entrypoint_id))

    return {phase: tuple(loaded[phase]) for phase in CONTEXT_PHASES}


def run_context_phase(
    phase: str,
    adapters: Iterable[PluginContextAdapter],
    *,
    context: Mapping[str, Any],
    state: Mapping[str, Any],
    target: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_phase = str(phase).strip().lower()
    if normalized_phase not in CONTEXT_PHASES:
        raise ValueError(f"unsupported context phase: {phase}")

    async def _run() -> dict[str, Any]:
        merged: dict[str, Any] = {}
        for adapter in adapters:
            payload = await adapter.run(
                normalized_phase,
                context=context,
                state=state,
                target=target,
            )
            if payload:
                merged[f"{adapter.plugin_id}.{adapter.entrypoint_id}"] = payload
        return merged

    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_run())

    raise RuntimeError(
        "run_context_phase() cannot be called from an active event loop; "
        "await PluginContextAdapter.run(...) directly instead"
    ) from None
