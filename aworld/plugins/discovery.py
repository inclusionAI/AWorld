from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, List

from .manifest import load_plugin_manifest
from .models import PluginEntrypoint, PluginManifest


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

        legacy_skill_items: dict[str, PluginEntrypoint] = {}
        skills_dir = root / "skills"
        if skills_dir.exists():
            for skill_dir in sorted(
                (item for item in skills_dir.iterdir() if item.is_dir()),
                key=lambda item: item.name,
            ):
                skill_md = skill_dir / "SKILL.md"
                if not skill_md.is_file():
                    continue
                legacy_skill_items[skill_dir.name] = PluginEntrypoint(
                    entrypoint_id=skill_dir.name,
                    entrypoint_type="skills",
                    name=skill_dir.name,
                    target=str(skill_md.relative_to(root)),
                    scope="workspace",
                    visibility="public",
                    metadata=MappingProxyType({"legacy": True}),
                    permissions=MappingProxyType({}),
                )

        if root.exists():
            for skill_dir in sorted(
                (
                    item
                    for item in root.iterdir()
                    if item.is_dir()
                    and item.name not in {"agents", "skills", ".aworld-plugin"}
                    and not item.name.startswith(".")
                ),
                key=lambda item: item.name,
            ):
                skill_md = skill_dir / "SKILL.md"
                if not skill_md.is_file():
                    continue
                legacy_skill_items.setdefault(
                    skill_dir.name,
                    PluginEntrypoint(
                        entrypoint_id=skill_dir.name,
                        entrypoint_type="skills",
                        name=skill_dir.name,
                        target=str(skill_md.relative_to(root)),
                        scope="workspace",
                        visibility="public",
                        metadata=MappingProxyType({"legacy": True}),
                        permissions=MappingProxyType({}),
                    ),
                )

        legacy_skill_entrypoints = tuple(
            legacy_skill_items[key] for key in sorted(legacy_skill_items)
        )

        capabilities = []
        if (root / "agents").exists():
            capabilities.append("agents")
        if legacy_skill_entrypoints:
            capabilities.append("skills")
        if not capabilities:
            continue

        entrypoints = {
            "agents": tuple(),
            "skills": legacy_skill_entrypoints,
        }
        if "agents" not in capabilities:
            entrypoints.pop("agents")
        if "skills" not in capabilities:
            entrypoints.pop("skills")
        entrypoints_proxy = MappingProxyType(entrypoints)

        manifest = PluginManifest(
            plugin_id=root.name,
            name=root.name,
            version="0.0.0-legacy",
            activation_scope="workspace",
            source=MappingProxyType({"type": "legacy"}),
            policy=MappingProxyType({}),
            dependencies=tuple(),
            conflicts=tuple(),
            lifecycle=("discover", "validate", "resolve", "load", "activate", "deactivate", "unload"),
            capabilities=frozenset(entrypoints_proxy.keys()),
            entrypoints=entrypoints_proxy,
            plugin_root=str(root.resolve()),
        )
        discovered.append(DiscoveredPlugin(manifest=manifest, source="legacy"))
    return discovered
