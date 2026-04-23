from __future__ import annotations

from pathlib import Path

from aworld.plugins.discovery import discover_plugins
from aworld_cli.plugin_capabilities.skill_commands import load_plugin_skill_commands


def test_load_plugin_skill_commands_reads_plugin_entrypoints(tmp_path: Path) -> None:
    plugin_root = tmp_path / "skill_plugin"
    manifest_dir = plugin_root / ".aworld-plugin"
    skill_commands_dir = plugin_root / "skill_commands"
    manifest_dir.mkdir(parents=True)
    skill_commands_dir.mkdir(parents=True)

    (manifest_dir / "plugin.json").write_text(
        """
{
  "id": "skill-command-like",
  "name": "skill-command-like",
  "version": "1.0.0",
  "entrypoints": {
    "skill_commands": [
      {
        "id": "pin",
        "name": "pin",
        "target": "skill_commands/pin.py",
        "scope": "workspace",
        "metadata": {
          "factory": "build_command",
          "aliases": ["select"]
        }
      }
    ]
  }
}
""".strip(),
        encoding="utf-8",
    )
    (skill_commands_dir / "pin.py").write_text(
        """
class PinSkillCommand:
    @property
    def name(self):
        return "pin"

    @property
    def description(self):
        return "Pin a skill for the next task"

    async def run(self, cli, args_text, **kwargs):
        return True


def build_command():
    return PinSkillCommand()
""".strip(),
        encoding="utf-8",
    )

    plugins = discover_plugins([plugin_root])
    commands = load_plugin_skill_commands(plugins)

    assert len(commands) == 1
    assert commands[0].name == "pin"
    assert commands[0].description == "Pin a skill for the next task"
    assert commands[0].aliases == ("select",)


def test_builtin_skill_cli_plugin_owns_its_skill_command_implementations() -> None:
    plugin_root = (
        Path(__file__).resolve().parents[2]
        / "aworld-cli"
        / "src"
        / "aworld_cli"
        / "builtin_plugins"
        / "skill_cli"
        / "skill_commands"
    )

    for module_name in ("clear.py", "disable.py", "enable.py", "use.py"):
        module_path = plugin_root / module_name
        content = module_path.read_text(encoding="utf-8")
        assert "aworld_cli.skill_commands" not in content
