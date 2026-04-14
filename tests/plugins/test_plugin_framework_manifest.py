from pathlib import Path
from types import MappingProxyType

from aworld_cli.plugin_framework.manifest import load_plugin_manifest
from aworld_cli.plugin_framework.models import PluginEntrypoint


def test_load_plugin_manifest_exposes_typed_entrypoints():
    plugin_root = Path("tests/fixtures/plugins/framework_minimal")

    manifest = load_plugin_manifest(plugin_root)

    assert manifest.plugin_id == "framework-minimal"
    assert manifest.capabilities == {"commands", "hud"}
    assert manifest.plugin_root == str(plugin_root.resolve())
    assert isinstance(manifest.entrypoints, MappingProxyType)
    command_entrypoint = manifest.entrypoints["commands"][0]
    assert isinstance(manifest.entrypoints["commands"], tuple)
    assert isinstance(command_entrypoint, PluginEntrypoint)
    assert isinstance(command_entrypoint.metadata, MappingProxyType)
    assert isinstance(command_entrypoint.permissions, MappingProxyType)
    assert command_entrypoint.entrypoint_id == "echo"
    assert command_entrypoint.target == "commands/echo.md"


def test_invalid_duplicate_entrypoint_ids_raise_value_error(tmp_path):
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    (plugin_root / ".aworld-plugin").mkdir()
    (plugin_root / ".aworld-plugin" / "plugin.json").write_text(
        '''
        {
          "id": "dup-plugin",
          "name": "dup-plugin",
          "version": "1.0.0",
          "entrypoints": {
            "commands": [
              {"id": "dup", "name": "dup", "target": "commands/one.md"},
              {"id": "dup", "name": "dup2", "target": "commands/two.md"}
            ]
          }
        }
        ''',
        encoding="utf-8",
    )

    try:
        load_plugin_manifest(plugin_root)
    except ValueError as exc:
        assert "duplicate entrypoint id" in str(exc).lower()
    else:
        raise AssertionError("expected duplicate entrypoint ids to fail")


def test_missing_required_id_fails(tmp_path):
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    (plugin_root / ".aworld-plugin").mkdir()
    (plugin_root / ".aworld-plugin" / "plugin.json").write_text(
        '''
        {
          "name": "missing-id",
          "version": "1.0.0",
          "entrypoints": {}
        }
        ''',
        encoding="utf-8",
    )

    try:
        load_plugin_manifest(plugin_root)
    except ValueError as exc:
        assert "missing required" in str(exc).lower()
    else:
        raise AssertionError("expected missing id to fail")


def test_missing_required_version_fails(tmp_path):
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    (plugin_root / ".aworld-plugin").mkdir()
    (plugin_root / ".aworld-plugin" / "plugin.json").write_text(
        '''
        {
          "id": "missing-version",
          "name": "missing-version",
          "entrypoints": {}
        }
        ''',
        encoding="utf-8",
    )

    try:
        load_plugin_manifest(plugin_root)
    except ValueError as exc:
        assert "missing required" in str(exc).lower()
    else:
        raise AssertionError("expected missing version to fail")


def test_non_mapping_entrypoints_fails(tmp_path):
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    (plugin_root / ".aworld-plugin").mkdir()
    (plugin_root / ".aworld-plugin" / "plugin.json").write_text(
        '''
        {
          "id": "bad-entrypoints",
          "name": "bad-entrypoints",
          "version": "1.0.0",
          "entrypoints": []
        }
        ''',
        encoding="utf-8",
    )

    try:
        load_plugin_manifest(plugin_root)
    except ValueError as exc:
        assert "entrypoints must be a mapping" in str(exc).lower()
    else:
        raise AssertionError("expected non-mapping entrypoints to fail")


def test_entrypoint_item_must_be_object(tmp_path):
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    (plugin_root / ".aworld-plugin").mkdir()
    (plugin_root / ".aworld-plugin" / "plugin.json").write_text(
        '''
        {
          "id": "bad-entrypoint",
          "name": "bad-entrypoint",
          "version": "1.0.0",
          "entrypoints": {
            "commands": ["not-a-dict"]
          }
        }
        ''',
        encoding="utf-8",
    )

    try:
        load_plugin_manifest(plugin_root)
    except ValueError as exc:
        assert "entrypoint must be an object" in str(exc).lower()
    else:
        raise AssertionError("expected non-object entrypoint to fail")


def test_entrypoint_metadata_must_be_mapping(tmp_path):
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    (plugin_root / ".aworld-plugin").mkdir()
    (plugin_root / ".aworld-plugin" / "plugin.json").write_text(
        '''
        {
          "id": "bad-metadata",
          "name": "bad-metadata",
          "version": "1.0.0",
          "entrypoints": {
            "commands": [
              {"id": "cmd", "name": "cmd", "metadata": ["nope"]}
            ]
          }
        }
        ''',
        encoding="utf-8",
    )

    try:
        load_plugin_manifest(plugin_root)
    except ValueError as exc:
        assert "metadata must be a mapping" in str(exc).lower()
    else:
        raise AssertionError("expected non-mapping metadata to fail")


def test_entrypoint_permissions_must_be_mapping(tmp_path):
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    (plugin_root / ".aworld-plugin").mkdir()
    (plugin_root / ".aworld-plugin" / "plugin.json").write_text(
        '''
        {
          "id": "bad-permissions",
          "name": "bad-permissions",
          "version": "1.0.0",
          "entrypoints": {
            "commands": [
              {"id": "cmd", "name": "cmd", "permissions": "nope"}
            ]
          }
        }
        ''',
        encoding="utf-8",
    )

    try:
        load_plugin_manifest(plugin_root)
    except ValueError as exc:
        assert "permissions must be a mapping" in str(exc).lower()
    else:
        raise AssertionError("expected non-mapping permissions to fail")
