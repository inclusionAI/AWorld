import inspect
from dataclasses import dataclass
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any, Iterable

from .resources import PluginResourceResolver


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


def collect_hud_lines(plugins: Iterable[Any], context: dict[str, Any]) -> list[HudLine]:
    lines: list[HudLine] = []
    for plugin in plugins:
        resolver = PluginResourceResolver(Path(plugin.manifest.plugin_root), plugin.manifest.plugin_id)
        for entrypoint in plugin.manifest.entrypoints.get("hud", ()):
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

            payloads = render_lines(dict(context))
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

    return sorted(
        lines,
        key=lambda item: (
            SECTION_ORDER.get(item.section, len(SECTION_ORDER)),
            item.priority,
            item.provider_id,
            item.text,
        ),
    )
