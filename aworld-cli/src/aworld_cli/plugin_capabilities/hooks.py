import asyncio
import inspect
from dataclasses import dataclass, field
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from aworld.plugins.resources import PluginResourceResolver

DEFAULT_PLUGIN_HOOK_TIMEOUT_SECONDS = 5.0
_HOOK_HANDLER_CACHE: dict[str, Any] = {}


def _normalize_hook_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    field_mapping = {
        "systemMessage": "system_message",
        "stopReason": "stop_reason",
        "updatedInput": "updated_input",
        "followUpPrompt": "follow_up_prompt",
        "hookSpecificOutput": "hook_specific_output",
    }
    normalized = {}
    for key, value in payload.items():
        normalized[field_mapping.get(key, key)] = value
    return normalized


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        content = value.get("content")
        if isinstance(content, str):
            return content
    return str(value)


@dataclass(frozen=True)
class PluginHookResult:
    action: str = "allow"
    reason: str | None = None
    updated_input: Any = None
    follow_up_prompt: str | None = None
    system_message: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None) -> "PluginHookResult":
        if payload is None:
            return cls()
        if not isinstance(payload, Mapping):
            raise ValueError("plugin hook payload must be a mapping")

        normalized = _normalize_hook_payload(payload)
        action = cls._resolve_action(normalized)
        reason = _coerce_text(
            normalized.get("reason")
            or normalized.get("stop_reason")
            or normalized.get("permission_decision_reason")
        )
        updated_input = normalized.get("updated_input")
        follow_up_prompt = _coerce_text(normalized.get("follow_up_prompt"))
        if action == "block_and_continue" and follow_up_prompt is None:
            follow_up_prompt = _coerce_text(normalized.get("reason")) or _coerce_text(updated_input)
        system_message = _coerce_text(normalized.get("system_message"))

        metadata = normalized.get("metadata")
        if metadata is None:
            metadata = normalized.get("hook_specific_output")
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, Mapping):
            metadata = {"value": metadata}

        return cls(
            action=action,
            reason=reason,
            updated_input=updated_input,
            follow_up_prompt=follow_up_prompt,
            system_message=system_message,
            metadata=MappingProxyType(dict(metadata)),
        )

    @staticmethod
    def _resolve_action(payload: Mapping[str, Any]) -> str:
        raw_action = payload.get("action")
        if raw_action is None:
            decision = payload.get("decision")
            if decision is not None:
                raw_action = {
                    "allow": "allow",
                    "deny": "deny",
                    "stop": "deny",
                    "block": "block_and_continue",
                }.get(str(decision).strip().lower())

        if raw_action is None:
            permission_decision = payload.get("permission_decision")
            if permission_decision in {"deny", "ask"}:
                raw_action = "deny"

        if raw_action is None and payload.get("prevent_continuation"):
            raw_action = "deny"

        if raw_action is None and payload.get("continue") is False:
            raw_action = "deny"

        action = str(raw_action or "allow").strip().lower()
        if action not in {"allow", "deny", "block_and_continue"}:
            raise ValueError(f"unsupported plugin hook action: {action}")
        return action


class PluginHookAdapter:
    def __init__(self, plugin, entrypoint):
        self._plugin = plugin
        self._entrypoint = entrypoint
        self._resolver = PluginResourceResolver(
            Path(plugin.manifest.plugin_root),
            plugin.manifest.plugin_id,
        )

    @property
    def entrypoint_id(self) -> str:
        return self._entrypoint.entrypoint_id

    @property
    def plugin_id(self) -> str:
        return self._plugin.manifest.plugin_id

    @property
    def scope(self) -> str:
        return self._entrypoint.scope

    @property
    def priority(self) -> int:
        return int(self._entrypoint.metadata.get("priority", 100))

    @property
    def hook_point(self) -> str:
        return str(self._entrypoint.metadata["hook_point"]).strip().lower()

    @property
    def timeout_seconds(self) -> float:
        raw_value = self._entrypoint.metadata.get("timeout_seconds", DEFAULT_PLUGIN_HOOK_TIMEOUT_SECONDS)
        try:
            timeout = float(raw_value)
        except (TypeError, ValueError):
            return DEFAULT_PLUGIN_HOOK_TIMEOUT_SECONDS
        return timeout if timeout > 0 else DEFAULT_PLUGIN_HOOK_TIMEOUT_SECONDS

    def _load_handler(self) -> Any:
        if not self._entrypoint.target:
            raise ValueError(f"plugin hook '{self.entrypoint_id}' is missing a target")

        hook_path = self._resolver.resolve_asset(self._entrypoint.target)
        cache_key = str(hook_path)
        cached = _HOOK_HANDLER_CACHE.get(cache_key)
        if cached is not None:
            return cached
        spec = spec_from_file_location(
            f"aworld_plugin_{self.plugin_id}_{self.entrypoint_id}",
            hook_path,
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"unable to load plugin hook module from {hook_path}")

        module = module_from_spec(spec)
        spec.loader.exec_module(module)

        handler = getattr(module, "handle_event", None)
        if handler is None:
            raise AttributeError(
                f"plugin hook '{self.entrypoint_id}' must define handle_event(event, state)"
            )
        _HOOK_HANDLER_CACHE[cache_key] = handler
        return handler

    async def _invoke_handler(self, event: Mapping[str, Any], state: Mapping[str, Any]) -> Any:
        handler = self._load_handler()
        if inspect.iscoroutinefunction(handler):
            payload = handler(event=dict(event), state=dict(state))
            if inspect.isawaitable(payload):
                return await payload
            return payload

        payload = await asyncio.to_thread(handler, event=dict(event), state=dict(state))
        if inspect.isawaitable(payload):
            return await payload
        return payload

    async def run(self, event: Mapping[str, Any], state: Mapping[str, Any]) -> PluginHookResult:
        try:
            payload = await asyncio.wait_for(
                self._invoke_handler(event, state),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"plugin hook '{self.plugin_id}:{self.entrypoint_id}' timed out "
                f"after {self.timeout_seconds:.2f}s"
            ) from exc
        return PluginHookResult.from_payload(payload)


def load_plugin_hooks(plugins: Iterable[Any]) -> dict[str, tuple[PluginHookAdapter, ...]]:
    loaded: dict[str, list[PluginHookAdapter]] = {}
    for plugin in plugins:
        for entrypoint in plugin.manifest.entrypoints.get("hooks", ()):
            hook_point = str(entrypoint.metadata.get("hook_point", "")).strip().lower()
            if not hook_point:
                raise ValueError(
                    f"plugin hook '{plugin.manifest.plugin_id}:{entrypoint.entrypoint_id}' "
                    "is missing metadata.hook_point"
                )
            loaded.setdefault(hook_point, []).append(PluginHookAdapter(plugin, entrypoint))

    ordered: dict[str, tuple[PluginHookAdapter, ...]] = {}
    for hook_point, hooks in loaded.items():
        ordered[hook_point] = tuple(
            sorted(
                hooks,
                key=lambda hook: (hook.priority, hook.plugin_id, hook.entrypoint_id),
            )
        )
    return ordered
