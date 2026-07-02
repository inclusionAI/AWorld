from dataclasses import dataclass

import sys
import json
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld.plugins.discovery import discover_plugins
from aworld_cli import main as main_module
from aworld_cli.core.top_level_command_system import (
    TopLevelCommand,
    TopLevelCommandContext,
    TopLevelCommandRegistry,
)
from aworld_cli.plugin_capabilities.cli_commands import sync_plugin_cli_commands


@dataclass
class DummyCommand(TopLevelCommand):
    command_name: str

    @property
    def name(self) -> str:
        return self.command_name

    @property
    def description(self) -> str:
        return f"{self.command_name} help"

    def register_parser(self, subparsers) -> None:
        subparsers.add_parser(self.name, help=self.description)

    def run(self, args, context: TopLevelCommandContext) -> int:
        return 0


def test_registry_registers_and_returns_command() -> None:
    registry = TopLevelCommandRegistry(reserved_names={"skill"})
    command = DummyCommand("demo")

    registry.register(command)

    assert registry.get("demo") is command
    assert [item.name for item in registry.list_commands()] == ["demo"]


def test_registry_rejects_duplicate_names() -> None:
    registry = TopLevelCommandRegistry()
    registry.register(DummyCommand("demo"))

    with pytest.raises(ValueError, match="already registered"):
        registry.register(DummyCommand("demo"))


def test_registry_rejects_reserved_plugin_override() -> None:
    registry = TopLevelCommandRegistry(reserved_names={"skill"})

    with pytest.raises(ValueError, match="reserved"):
        registry.register(DummyCommand("skill"), source="plugin")


def _write_cli_command_plugin(
    plugin_root: Path,
    command_name: str = "demo",
    *,
    aliases: tuple[str, ...] = (),
    visibility: str = "public",
) -> None:
    manifest_dir = plugin_root / ".aworld-plugin"
    handlers_dir = plugin_root / "cli_commands"
    manifest_dir.mkdir(parents=True)
    handlers_dir.mkdir(parents=True)
    (manifest_dir / "plugin.json").write_text(
        json.dumps(
            {
                "id": f"{command_name}-plugin",
                "name": f"{command_name}-plugin",
                "version": "1.0.0",
                "entrypoints": {
                    "cli_commands": [
                        {
                            "id": command_name,
                            "name": command_name,
                            "target": f"cli_commands/{command_name}.py",
                            "visibility": visibility,
                            "metadata": {"aliases": list(aliases)},
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    (handlers_dir / f"{command_name}.py").write_text(
        "from aworld_cli.core.top_level_command_system import TopLevelCommand\n"
        "\n"
        "class DemoCommand(TopLevelCommand):\n"
        "    @property\n"
        "    def name(self):\n"
        f"        return {command_name!r}\n"
        "\n"
        "    @property\n"
        "    def description(self):\n"
        "        return 'demo plugin command'\n"
        "\n"
        "    def register_parser(self, subparsers):\n"
        f"        parser = subparsers.add_parser({command_name!r}, help=self.description)\n"
        "        parser.add_argument('--value', default='default')\n"
        "\n"
        "    def run(self, args, context):\n"
        "        print(f'PLUGIN:{args.value}:{context.cwd}')\n"
        "        return 0\n"
        "\n"
        "def build_command():\n"
        "    return DemoCommand()\n",
        encoding="utf-8",
    )


def test_sync_plugin_cli_commands_registers_manifest_declared_command(tmp_path: Path) -> None:
    plugin_root = tmp_path / "demo-plugin"
    _write_cli_command_plugin(plugin_root)
    registry = TopLevelCommandRegistry()

    sync_plugin_cli_commands(registry, discover_plugins([plugin_root]))

    command = registry.get("demo")

    assert command is not None
    assert command.name == "demo"


def test_maybe_dispatch_top_level_command_runs_manifest_declared_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_dir = tmp_path / ".aworld" / "plugins"
    plugin_root = plugin_dir / "demo-plugin"
    _write_cli_command_plugin(plugin_root)
    monkeypatch.setattr("aworld_cli.core.plugin_manager.DEFAULT_PLUGIN_DIR", plugin_dir)

    handled = main_module._maybe_dispatch_top_level_command(
        ["aworld-cli", "demo", "--value", "works"]
    )

    output = capsys.readouterr().out

    assert handled is True
    assert "PLUGIN:works:" in output


def test_sync_plugin_cli_commands_registers_manifest_declared_aliases(
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "demo-plugin"
    _write_cli_command_plugin(
        plugin_root,
        aliases=("demo-alias", "demo-short"),
    )
    registry = TopLevelCommandRegistry()

    sync_plugin_cli_commands(registry, discover_plugins([plugin_root]))

    command = registry.get("demo")

    assert command is not None
    assert registry.get("demo-alias") is command
    assert registry.get("demo-short") is command


def test_sync_plugin_cli_commands_registers_hidden_command_for_internal_lookup(
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "demo-plugin"
    _write_cli_command_plugin(
        plugin_root,
        command_name="hidden-demo",
        visibility="hidden",
    )
    registry = TopLevelCommandRegistry()

    sync_plugin_cli_commands(registry, discover_plugins([plugin_root]))

    command = registry.get("hidden-demo")

    assert command is not None
    assert [item.name for item in registry.list_commands(include_hidden=False)] == []


def test_maybe_dispatch_top_level_command_accepts_manifest_declared_alias(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_dir = tmp_path / ".aworld" / "plugins"
    plugin_root = plugin_dir / "demo-plugin"
    _write_cli_command_plugin(
        plugin_root,
        aliases=("demo-alias",),
    )
    monkeypatch.setattr("aworld_cli.core.plugin_manager.DEFAULT_PLUGIN_DIR", plugin_dir)

    handled = main_module._maybe_dispatch_top_level_command(
        ["aworld-cli", "demo-alias", "--value", "alias-works"]
    )

    output = capsys.readouterr().out

    assert handled is True
    assert "PLUGIN:alias-works:" in output


def test_build_top_level_command_registry_omits_disabled_builtin_cli_plugin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin_dir = tmp_path / ".aworld" / "plugins"
    monkeypatch.setattr("aworld_cli.core.plugin_manager.DEFAULT_PLUGIN_DIR", plugin_dir)

    from aworld_cli.core.plugin_manager import PluginManager

    PluginManager(plugin_dir=plugin_dir).disable("aworld-skill-cli")

    registry = main_module._build_top_level_command_registry()

    assert registry.get("skill") is None


def test_maybe_dispatch_top_level_command_accepts_command_after_global_options(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_dir = tmp_path / ".aworld" / "plugins"
    plugin_root = plugin_dir / "demo-plugin"
    _write_cli_command_plugin(plugin_root)
    monkeypatch.setattr("aworld_cli.core.plugin_manager.DEFAULT_PLUGIN_DIR", plugin_dir)

    handled = main_module._maybe_dispatch_top_level_command(
        ["aworld-cli", "--env-file", ".env", "demo", "--value", "late-works"]
    )

    output = capsys.readouterr().out

    assert handled is True
    assert "PLUGIN:late-works:" in output
