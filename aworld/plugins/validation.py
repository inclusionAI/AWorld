from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aworld.plugins.manifest import load_plugin_manifest
from aworld.plugins.resources import PluginResourceResolver


def get_plugin_manifest_schema_path() -> Path:
    return Path(__file__).resolve().parent / "plugin.schema.json"


def validate_plugin_path(plugin_path: Path) -> dict[str, Any]:
    root = Path(plugin_path).resolve()
    manifest_path = root / ".aworld-plugin" / "plugin.json"
    if manifest_path.exists():
        manifest = load_plugin_manifest(root)
        resolver = PluginResourceResolver(root, manifest.plugin_id)
        for entrypoints in manifest.entrypoints.values():
            for entrypoint in entrypoints:
                if entrypoint.target:
                    resolver.resolve_asset(entrypoint.target)

        return {
            "valid": True,
            "plugin_id": manifest.plugin_id,
            "name": manifest.name,
            "framework_source": "manifest",
            "capabilities": sorted(manifest.capabilities),
            "path": str(root),
            "schema_path": str(get_plugin_manifest_schema_path()),
        }

    agents_dir = root / "agents"
    skills_dir = root / "skills"
    if agents_dir.exists() or skills_dir.exists():
        capabilities = []
        if agents_dir.exists():
            capabilities.append("agents")
        if skills_dir.exists():
            capabilities.append("skills")
        return {
            "valid": True,
            "plugin_id": root.name,
            "name": root.name,
            "framework_source": "legacy",
            "capabilities": capabilities,
            "path": str(root),
            "schema_path": str(get_plugin_manifest_schema_path()),
        }

    raise ValueError(
        "plugin validation requires a valid .aworld-plugin/plugin.json "
        "or a legacy plugin directory with agents/ or skills/"
    )


def load_plugin_manifest_schema() -> dict[str, Any]:
    return json.loads(get_plugin_manifest_schema_path().read_text(encoding="utf-8"))
