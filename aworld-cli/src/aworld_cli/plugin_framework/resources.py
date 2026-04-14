from pathlib import Path


class PluginResourceResolver:
    def __init__(self, plugin_root: Path, plugin_id: str):
        self.plugin_root = plugin_root.resolve()
        self.plugin_id = plugin_id

    def resolve_asset(self, relative_path: str) -> Path:
        resolved = (self.plugin_root / relative_path).resolve()
        if self.plugin_root not in resolved.parents and resolved != self.plugin_root:
            raise ValueError(f"asset path escapes plugin root for {self.plugin_id}: {relative_path}")
        return resolved
