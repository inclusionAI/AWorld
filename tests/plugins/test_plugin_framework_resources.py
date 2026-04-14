from pathlib import Path

from aworld_cli.plugin_framework.manifest import load_plugin_manifest
from aworld_cli.plugin_framework.resources import PluginResourceResolver


def test_resolve_packaged_asset_within_plugin_root():
    plugin_root = Path("tests/fixtures/plugins/framework_minimal").resolve()
    manifest = load_plugin_manifest(plugin_root)
    resolver = PluginResourceResolver(plugin_root=plugin_root, plugin_id=manifest.plugin_id)

    resolved = resolver.resolve_asset("commands/echo.md")

    assert resolved == plugin_root / "commands" / "echo.md"


def test_resolve_packaged_asset_rejects_escape(tmp_path):
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    resolver = PluginResourceResolver(plugin_root=plugin_root, plugin_id="escape-test")

    try:
        resolver.resolve_asset("../outside.txt")
    except ValueError as exc:
        assert "plugin root" in str(exc).lower()
    else:
        raise AssertionError("expected path traversal to fail")
