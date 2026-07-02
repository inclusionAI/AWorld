import inspect
from dataclasses import dataclass
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any, Iterable

from aworld.logs.util import logger
from aworld.plugins.resources import PluginResourceResolver


SECTION_ORDER = {
    "identity": 0,
    "session": 1,
    "context": 2,
    "activity": 3,
    "tasks": 4,
    "custom": 5,
}

_HUD_RENDER_CACHE: dict[str, Any] = {}


@dataclass(frozen=True)
class HudLine:
    section: str
    priority: int
    text: str
    provider_id: str
    segments: tuple[str, ...] = ()


def _call_render_lines(
    render_lines,
    context: dict[str, Any],
    plugin_state: dict[str, Any],
):
    signature = inspect.signature(render_lines)
    parameters = list(signature.parameters.values())
    positional = [
        parameter
        for parameter in parameters
        if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    accepts_varargs = any(parameter.kind == inspect.Parameter.VAR_POSITIONAL for parameter in parameters)
    plugin_state_parameter = signature.parameters.get("plugin_state")

    context_payload = dict(context)
    plugin_state_payload = dict(plugin_state)
    if accepts_varargs or len(positional) >= 2:
        return render_lines(context_payload, plugin_state_payload)
    if plugin_state_parameter is not None:
        if plugin_state_parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            return render_lines(context_payload, plugin_state_payload)
        return render_lines(context_payload, plugin_state=plugin_state_payload)
    return render_lines(context_payload)


def collect_hud_lines(
    plugins: Iterable[Any],
    context: dict[str, Any],
    plugin_state_provider=None,
) -> list[HudLine]:
    lines: list[HudLine] = []
    for plugin in plugins:
        resolver = PluginResourceResolver(Path(plugin.manifest.plugin_root), plugin.manifest.plugin_id)
        for entrypoint in plugin.manifest.entrypoints.get("hud", ()):
            try:
                if not entrypoint.target:
                    raise ValueError(
                        f"plugin hud entrypoint '{plugin.manifest.plugin_id}:{entrypoint.entrypoint_id}' "
                        "is missing a target"
                    )
                module_path = resolver.resolve_asset(entrypoint.target)
                cache_key = str(module_path)
                render_lines = _HUD_RENDER_CACHE.get(cache_key)
                if render_lines is None:
                    spec = spec_from_file_location(
                        f"hud_{plugin.manifest.plugin_id}_{entrypoint.entrypoint_id}",
                        module_path,
                    )
                    if spec is None or spec.loader is None:
                        raise ImportError(f"unable to load plugin hud module from {module_path}")

                    module = module_from_spec(spec)
                    spec.loader.exec_module(module)

                    render_lines = getattr(module, "render_lines", None)
                    if render_lines is None:
                        raise AttributeError(
                            f"plugin hud entrypoint '{entrypoint.entrypoint_id}' must define render_lines(context)"
                        )
                    _HUD_RENDER_CACHE[cache_key] = render_lines

                plugin_state = {}
                if plugin_state_provider is not None:
                    plugin_state = plugin_state_provider(
                        plugin.manifest.plugin_id,
                        getattr(entrypoint, "scope", "workspace"),
                        dict(context),
                    ) or {}

                payloads = _call_render_lines(render_lines, context, plugin_state)
                if inspect.isawaitable(payloads):
                    raise ValueError("plugin hud render_lines must be synchronous")

                for payload in payloads:
                    section = str(payload["section"]).strip().lower()
                    raw_segments = payload.get("segments", ())
                    if raw_segments is None:
                        raw_segments = ()
                    elif isinstance(raw_segments, str):
                        raw_segments = (raw_segments,)
                    elif isinstance(raw_segments, dict):
                        raw_segments = (raw_segments,)
                    else:
                        try:
                            raw_segments = tuple(raw_segments)
                        except TypeError:
                            raw_segments = (raw_segments,)

                    segments = tuple(
                        str(item) for item in raw_segments if str(item).strip()
                    ) if raw_segments else ()
                    text = payload.get("text")
                    text = str(text) if text is not None else " | ".join(segments)
                    lines.append(
                        HudLine(
                            section=section,
                            priority=int(payload.get("priority", 100)),
                            text=text,
                            provider_id=entrypoint.entrypoint_id,
                            segments=segments,
                        )
                    )
            except Exception as exc:
                logger.warning(
                    f"HUD provider '{plugin.manifest.plugin_id}:{entrypoint.entrypoint_id}' failed: {exc}"
                )
                continue

    return sorted(
        lines,
        key=lambda item: (
            SECTION_ORDER.get(item.section, len(SECTION_ORDER)),
            item.priority,
            item.provider_id,
            item.text,
        ),
    )
