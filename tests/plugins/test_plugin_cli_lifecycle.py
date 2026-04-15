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
