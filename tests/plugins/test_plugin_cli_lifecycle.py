from pathlib import Path

from aworld_cli.core.plugin_manager import PluginManager


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
