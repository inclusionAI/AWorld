import json
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .models import PluginEntrypoint, PluginManifest

DEFAULT_LIFECYCLE = ("discover", "validate", "resolve", "load", "activate", "deactivate", "unload")


def load_plugin_manifest(plugin_root: Path) -> PluginManifest:
    resolved_root = plugin_root.resolve()
    manifest_path = resolved_root / ".aworld-plugin" / "plugin.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if "id" not in raw:
        raise ValueError("missing required field: id")
    if "version" not in raw:
        raise ValueError("missing required field: version")

    if "entrypoints" in raw:
        entrypoints_raw = raw["entrypoints"]
        if not isinstance(entrypoints_raw, Mapping):
            raise ValueError("entrypoints must be a mapping")
    else:
        entrypoints_raw = {}

    source = raw.get("source") or {}
    if not isinstance(source, Mapping):
        raise ValueError("source must be a mapping")

    policy = raw.get("policy") or {}
    if not isinstance(policy, Mapping):
        raise ValueError("policy must be a mapping")

    dependencies = raw.get("dependencies") or []
    if not isinstance(dependencies, list):
        raise ValueError("dependencies must be a list")
    conflicts = raw.get("conflicts") or []
    if not isinstance(conflicts, list):
        raise ValueError("conflicts must be a list")

    entrypoints = {}
    capabilities = set()

    for entrypoint_type, items in entrypoints_raw.items():
        seen_ids = set()
        parsed_items = []
        for item in items:
            if not isinstance(item, Mapping):
                raise ValueError("entrypoint must be an object")
            entrypoint_id = item["id"]
            if entrypoint_id in seen_ids:
                raise ValueError(f"duplicate entrypoint id: {entrypoint_type}:{entrypoint_id}")
            seen_ids.add(entrypoint_id)
            metadata = item.get("metadata")
            if metadata is None:
                metadata = {}
            if not isinstance(metadata, Mapping):
                raise ValueError("metadata must be a mapping")
            permissions = item.get("permissions")
            if permissions is None:
                permissions = {}
            if not isinstance(permissions, Mapping):
                raise ValueError("permissions must be a mapping")
            parsed_items.append(
                PluginEntrypoint(
                    entrypoint_id=entrypoint_id,
                    entrypoint_type=entrypoint_type,
                    name=item.get("name"),
                    target=item.get("target"),
                    scope=item.get("scope", "workspace"),
                    visibility=item.get("visibility", "public"),
                    description=item.get("description"),
                    metadata=MappingProxyType(dict(metadata)),
                    permissions=MappingProxyType(dict(permissions)),
                )
            )
        entrypoints[entrypoint_type] = tuple(parsed_items)
        capabilities.add(entrypoint_type)

    return PluginManifest(
        plugin_id=raw["id"],
        name=raw.get("name", raw["id"]),
        version=raw["version"],
        activation_scope=str(raw.get("activation_scope", "workspace")).strip().lower() or "workspace",
        source=MappingProxyType(dict(source)),
        policy=MappingProxyType(dict(policy)),
        dependencies=tuple(str(item) for item in dependencies),
        conflicts=tuple(str(item) for item in conflicts),
        lifecycle=tuple(str(item) for item in raw.get("lifecycle", DEFAULT_LIFECYCLE)),
        capabilities=frozenset(capabilities),
        entrypoints=MappingProxyType(entrypoints),
        plugin_root=str(resolved_root),
    )
