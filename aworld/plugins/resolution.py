from __future__ import annotations

from typing import Any, Iterable


def resolve_plugin_activation(plugins: Iterable[Any]) -> tuple[list[Any], dict[str, str]]:
    accepted: list[Any] = []
    accepted_ids: set[str] = set()
    accepted_conflicts: dict[str, tuple[str, ...]] = {}
    skipped: dict[str, str] = {}

    for plugin in plugins:
        plugin_id = plugin.manifest.plugin_id
        dependencies = tuple(plugin.manifest.dependencies)
        conflicts = tuple(plugin.manifest.conflicts)

        missing = [item for item in dependencies if item not in accepted_ids]
        if missing:
            skipped[plugin_id] = f"missing dependencies: {', '.join(missing)}"
            continue

        conflicting = [item for item in conflicts if item in accepted_ids]
        if conflicting:
            skipped[plugin_id] = f"conflicts with active plugins: {', '.join(conflicting)}"
            continue

        reverse_conflicts = [
            active_id
            for active_id, active_conflicts in accepted_conflicts.items()
            if plugin_id in active_conflicts
        ]
        if reverse_conflicts:
            skipped[plugin_id] = f"blocked by active plugin conflicts: {', '.join(reverse_conflicts)}"
            continue

        accepted.append(plugin)
        accepted_ids.add(plugin_id)
        accepted_conflicts[plugin_id] = conflicts

    return accepted, skipped
