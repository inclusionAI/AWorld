from dataclasses import dataclass, field
from typing import FrozenSet, Mapping, Optional, Tuple


@dataclass(frozen=True)
class PluginEntrypoint:
    entrypoint_id: str
    entrypoint_type: str
    name: Optional[str]
    target: Optional[str]
    scope: str = "workspace"
    visibility: str = "public"
    description: Optional[str] = None
    metadata: Mapping[str, object] = field(default_factory=dict)
    permissions: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PluginManifest:
    plugin_id: str
    name: str
    version: str
    activation_scope: str
    source: Mapping[str, object]
    policy: Mapping[str, object]
    dependencies: Tuple[str, ...]
    conflicts: Tuple[str, ...]
    lifecycle: Tuple[str, ...]
    capabilities: FrozenSet[str]
    entrypoints: Mapping[str, Tuple[PluginEntrypoint, ...]]
    plugin_root: str
