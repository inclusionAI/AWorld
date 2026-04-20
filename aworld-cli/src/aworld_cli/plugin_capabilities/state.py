import hashlib
import json
from pathlib import Path


class PluginStateHandle:
    def __init__(self, path: Path):
        self.path = path

    def read(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def write(self, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("plugin state payload must be a mapping")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return dict(payload)

    def update(self, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("plugin state payload must be a mapping")
        current = self.read()
        current.update(payload)
        return self.write(current)

    def clear(self) -> dict:
        return self.write({})


class PluginStateStore:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir.resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _safe_component(self, value: str) -> str:
        return hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]

    def _scope_dir(self, scope: str, plugin_id: str, key: str) -> Path:
        plugin_component = self._safe_component(plugin_id)
        key_component = self._safe_component(key)
        path = self.base_dir / scope / plugin_component / key_component
        resolved = path.resolve()
        if self.base_dir not in resolved.parents and resolved != self.base_dir:
            raise ValueError(f"state path escapes base_dir for plugin {plugin_id}")
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved

    def session_state(self, plugin_id: str, session_id: str) -> Path:
        return self._scope_dir("session", plugin_id, session_id) / "state.json"

    def workspace_state(self, plugin_id: str, workspace_path: str) -> Path:
        return self._scope_dir("workspace", plugin_id, workspace_path) / "state.json"

    def global_state(self, plugin_id: str) -> Path:
        return self._scope_dir("global", plugin_id, plugin_id) / "state.json"

    def handle(self, path: Path) -> PluginStateHandle:
        resolved = path.resolve()
        if self.base_dir not in resolved.parents:
            raise ValueError("state handle path escapes base_dir")
        return PluginStateHandle(resolved)
