from dataclasses import dataclass

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld_cli.core.top_level_command_system import (
    TopLevelCommand,
    TopLevelCommandContext,
    TopLevelCommandRegistry,
)


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
