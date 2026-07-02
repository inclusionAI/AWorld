from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class TopLevelCommandContext:
    cwd: str
    argv: tuple[str, ...] = ()
    config: dict[str, Any] | None = None


class TopLevelCommand:
    @property
    def name(self) -> str:
        raise NotImplementedError

    @property
    def description(self) -> str:
        raise NotImplementedError

    @property
    def aliases(self) -> tuple[str, ...]:
        return tuple()

    @property
    def visible_in_help(self) -> bool:
        return True

    def register_parser(self, subparsers) -> None:
        raise NotImplementedError

    def run(self, args, context: TopLevelCommandContext) -> int | None:
        raise NotImplementedError


@dataclass(frozen=True)
class RegisteredTopLevelCommand:
    command: TopLevelCommand
    source: str = "builtin"


@dataclass
class TopLevelCommandRegistry:
    reserved_names: set[str] = field(default_factory=set)
    _commands: dict[str, RegisteredTopLevelCommand] = field(default_factory=dict)
    _aliases: dict[str, str] = field(default_factory=dict)

    def register(self, command: TopLevelCommand, *, source: str = "builtin") -> None:
        aliases = tuple(getattr(command, "aliases", tuple()) or tuple())
        names = [command.name, *aliases]
        if source != "builtin":
            for name in names:
                if name in self.reserved_names:
                    raise ValueError(f"Top-level command '{name}' is reserved")

        for name in names:
            if name in self._commands or name in self._aliases:
                raise ValueError(f"Top-level command '{name}' already registered")

        self._commands[command.name] = RegisteredTopLevelCommand(command=command, source=source)
        for alias in aliases:
            self._aliases[alias] = command.name

    def unregister(self, name: str) -> None:
        canonical = self.canonical_name(name)
        if canonical is None:
            return
        command = self._commands.pop(canonical).command
        for alias in tuple(getattr(command, "aliases", tuple()) or tuple()):
            self._aliases.pop(alias, None)

    def canonical_name(self, name: str) -> Optional[str]:
        if name in self._commands:
            return name
        return self._aliases.get(name)

    def get(self, name: str) -> Optional[TopLevelCommand]:
        canonical = self.canonical_name(name)
        if canonical is None:
            return None
        return self._commands[canonical].command

    def list_commands(self, *, include_hidden: bool = True) -> list[TopLevelCommand]:
        commands = [self._commands[key].command for key in sorted(self._commands)]
        if include_hidden:
            return commands
        return [item for item in commands if getattr(item, "visible_in_help", True)]

    def snapshot(self) -> tuple[dict[str, RegisteredTopLevelCommand], dict[str, str]]:
        return dict(self._commands), dict(self._aliases)

    def restore(
        self,
        snapshot: tuple[dict[str, RegisteredTopLevelCommand], dict[str, str]],
    ) -> None:
        commands, aliases = snapshot
        self._commands = dict(commands)
        self._aliases = dict(aliases)
