from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, List

from .manifest import load_plugin_manifest
from .models import PluginManifest


@dataclass(frozen=True)
class DiscoveredPlugin:
    manifest: PluginManifest
    source: str


def discover_plugins(roots: Iterable[Path]) -> List[DiscoveredPlugin]:
    discovered: List[DiscoveredPlugin] = []
    for root in roots:
        manifest_path = root / ".aworld-plugin" / "plugin.json"
        if manifest_path.exists():
            discovered.append(
                DiscoveredPlugin(
                    manifest=load_plugin_manifest(root),
                    source="manifest",
                )
            )
            continue

        capabilities = []
        if (root / "agents").exists():
            capabilities.append("agents")
        if (root / "skills").exists():
            capabilities.append("skills")
        if not capabilities:
            continue

        entrypoints = MappingProxyType({capability: tuple() for capability in capabilities})

        manifest = PluginManifest(
            plugin_id=root.name,
            name=root.name,
            version="0.0.0-legacy",
            capabilities=frozenset(entrypoints.keys()),
            entrypoints=entrypoints,
            plugin_root=str(root.resolve()),
        )
        discovered.append(DiscoveredPlugin(manifest=manifest, source="legacy"))
    return discovered
