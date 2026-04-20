from pathlib import Path

import pytest

from aworld_cli.core.plugin_manager import PluginManager
from aworld_cli.runtime.cli import CliRuntime


def test_enable_disable_reload_framework_plugin(tmp_path):
    manager = PluginManager(plugin_dir=tmp_path / "plugins")
    plugin_root = Path("tests/fixtures/plugins/code_review_like").resolve()

    assert manager.install("code-review-like", local_path=str(plugin_root))

    listed = manager.list()
    assert listed["code-review-like"]["plugin_id"] == "code-review-like"
    assert listed["code-review-like"]["framework_source"] == "manifest"
    assert listed["code-review-like"]["capabilities"] == ["commands"]

    manager.disable("code-review-like")
    disabled = manager.list()
    assert disabled["code-review-like"]["enabled"] is False
    assert manager.get_plugin_roots() == []

    manager.enable("code-review-like")
    enabled = manager.list()
    assert enabled["code-review-like"]["enabled"] is True

    reloaded = manager.reload("code-review-like")
    assert reloaded["enabled"] is True


def test_framework_registry_filters_disabled_plugins(tmp_path):
    manager = PluginManager(plugin_dir=tmp_path / "plugins")
    review_root = Path("tests/fixtures/plugins/code_review_like").resolve()
    context_root = Path("tests/fixtures/plugins/context_like").resolve()

    assert manager.install("code-review-like", local_path=str(review_root))
    assert manager.install("context-like", local_path=str(context_root))
    manager.disable("context-like")

    registry = manager.get_framework_registry()

    assert registry.get_plugin("code-review-like") is not None
    assert registry.get_plugin("context-like") is None

    listed = manager.list()
    assert listed["code-review-like"]["lifecycle_phase"] == "activate"
    assert listed["context-like"]["lifecycle_phase"] == "disabled"


def test_builtin_framework_plugin_can_be_disabled_and_reenabled(tmp_path):
    manager = PluginManager(plugin_dir=tmp_path / "plugins")

    disabled = manager.disable("aworld-hud")
    assert disabled["enabled"] is False
    assert disabled["plugin_id"] == "aworld-hud"
    assert disabled["lifecycle_phase"] == "disabled"
    assert all(path.name != "aworld_hud" for path in manager.get_runtime_plugin_roots())

    enabled = manager.enable("aworld-hud")
    assert enabled["enabled"] is True
    assert enabled["plugin_id"] == "aworld-hud"
    assert enabled["lifecycle_phase"] == "activate"
    assert any(path.name == "aworld_hud" for path in manager.get_runtime_plugin_roots())

    reloaded = manager.reload("aworld-hud")
    assert reloaded["enabled"] is True


def test_builtin_framework_plugin_disable_recovers_from_stale_builtin_path(tmp_path):
    manager = PluginManager(plugin_dir=tmp_path / "plugins")
    manager._manifest = {
        "aworld-hud": {
            "name": "aworld-hud",
            "path": str((Path(__file__).resolve().parents[2] / "aworld-cli" / "src" / "aworld_cli" / "plugins" / "aworld_hud").resolve()),
            "source": "built-in",
            "enabled": True,
        }
    }
    manager._save_manifest()
    manager._manifest = manager._load_manifest()

    disabled = manager.disable("aworld-hud")

    assert disabled["enabled"] is False
    assert Path(str(disabled["path"])).parent.name == "builtin_plugins"
    assert disabled["plugin_id"] == "aworld-hud"


def test_cli_runtime_honors_disabled_builtin_framework_plugin(tmp_path, monkeypatch):
    manager = PluginManager(plugin_dir=tmp_path / "plugins")
    manager.disable("aworld-hud")

    class FakePluginManager:
        def __init__(self):
            self.plugin_dir = manager.plugin_dir

        def get_runtime_plugin_roots(self):
            return manager.get_runtime_plugin_roots()

        def get_plugin_roots(self):
            return manager.get_plugin_roots()

    monkeypatch.setattr("aworld_cli.core.plugin_manager.PluginManager", FakePluginManager)

    runtime = CliRuntime(local_dirs=[], remote_backends=[])

    assert all(path.name != "aworld_hud" for path in runtime.plugin_dirs)


def test_install_rejects_empty_plugin_directory(tmp_path):
    manager = PluginManager(plugin_dir=tmp_path / "plugins")
    empty_root = tmp_path / "empty-plugin"
    empty_root.mkdir()

    with pytest.raises(ValueError, match="manifest|legacy plugin"):
        manager.install("empty-plugin", local_path=str(empty_root))

    assert "empty-plugin" not in manager.list()


def test_install_rejects_invalid_plugin_manifest(tmp_path):
    manager = PluginManager(plugin_dir=tmp_path / "plugins")
    plugin_root = tmp_path / "broken-plugin"
    (plugin_root / ".aworld-plugin").mkdir(parents=True)
    (plugin_root / ".aworld-plugin" / "plugin.json").write_text(
        '{"name": "broken-plugin", "version": "1.0.0"}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing required field: id"):
        manager.install("broken-plugin", local_path=str(plugin_root))

    assert "broken-plugin" not in manager.list()
