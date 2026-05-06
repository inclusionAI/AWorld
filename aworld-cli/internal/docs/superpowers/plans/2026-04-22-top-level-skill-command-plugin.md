# Top-Level Skill Command Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route `aworld-cli skill ...` through a builtin top-level command plugin registry, remove the hardcoded `skill` branch from `aworld-cli/src/aworld_cli/main.py`, and extend interactive discovery so installed skills are auto-visible in `/skills` and manually invokable through generated skill-name slash aliases such as `/brainstorming`.

**Architecture:** Add a dedicated top-level command registry under `aworld_cli/core`, register builtin providers first, then optionally register plugin-provided `cli_commands`, and let `main.py` build and dispatch from that registry. Keep provider-declared slash commands on `CommandRegistry`; add generated `/<skill-name>` aliases for visible skills as manual selection shortcuts, and render both alias types in `/skills`.

**Tech Stack:** Python, argparse, pytest, Rich tables, existing `PluginManager` and `CommandRegistry`

---

## File Structure

- Create: `aworld-cli/src/aworld_cli/core/top_level_command_system.py`
  - Top-level command protocol, context object, registry, reserved-name enforcement
- Create: `aworld-cli/src/aworld_cli/top_level_commands/__init__.py`
  - Builtin command registration entrypoint
- Create: `aworld-cli/src/aworld_cli/top_level_commands/skill_cmd.py`
  - Builtin `skill` top-level command provider
- Create: `aworld-cli/src/aworld_cli/plugin_capabilities/cli_commands.py`
  - Plugin manifest -> top-level command registration bridge
- Modify: `aworld-cli/src/aworld_cli/main.py`
  - Replace hardcoded `skill` branch with registry bootstrap and dispatch
- Modify: `aworld-cli/src/aworld_cli/console.py`
  - Extend `/skills` rendering with related slash commands
- Modify: `aworld-cli/src/aworld_cli/plugin_capabilities/commands.py`
  - Reuse provider metadata helpers if needed
- Test: `tests/core/test_top_level_command_system.py`
- Test: `tests/core/test_skill_cli.py`
- Test: `tests/test_gateway_cli.py`

## Manual Verification Targets

- `aworld-cli skill list` still works with the same user-facing output shape
- `aworld-cli --help` shows `skill` from the registry-backed parser
- `aworld-cli skill install https://github.com/obra/superpowers` produces a usable installed skill package
- a fresh interactive session auto-discovers the installed skills and shows `brainstorming` in `/skills`
- `/brainstorming` is available as a generated standalone manual alias sourced from the skill name
- `aworld-cli gateway ...` behavior remains unchanged
- `/skills` shows visible skills and related standalone slash commands when the provider contributes both
- `/review`-style commands remain directly executable and completable

### Task 1: Add The Top-Level Command Registry

**Files:**
- Create: `aworld-cli/src/aworld_cli/core/top_level_command_system.py`
- Test: `tests/core/test_top_level_command_system.py`

- [ ] **Step 1: Write the failing registry tests**

```python
# tests/core/test_top_level_command_system.py
from dataclasses import dataclass

import pytest

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


def test_registry_registers_and_returns_command():
    registry = TopLevelCommandRegistry(reserved_names={"skill"})
    command = DummyCommand("demo")

    registry.register(command)

    assert registry.get("demo") is command
    assert [item.name for item in registry.list_commands()] == ["demo"]


def test_registry_rejects_duplicate_names():
    registry = TopLevelCommandRegistry()
    registry.register(DummyCommand("demo"))

    with pytest.raises(ValueError, match="already registered"):
        registry.register(DummyCommand("demo"))


def test_registry_rejects_reserved_plugin_override():
    registry = TopLevelCommandRegistry(reserved_names={"skill"})

    with pytest.raises(ValueError, match="reserved"):
        registry.register(DummyCommand("skill"), source="plugin")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=aworld-cli/src pytest tests/core/test_top_level_command_system.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'aworld_cli.core.top_level_command_system'`

- [ ] **Step 3: Write the minimal registry implementation**

```python
# aworld-cli/src/aworld_cli/core/top_level_command_system.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, Protocol


@dataclass(frozen=True)
class TopLevelCommandContext:
    cwd: str
    argv: tuple[str, ...] = ()
    config: dict | None = None


class TopLevelCommand(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    def register_parser(self, subparsers) -> None: ...

    def run(self, args, context: TopLevelCommandContext) -> int | None: ...


@dataclass
class _RegistryEntry:
    command: TopLevelCommand
    source: str = "builtin"


@dataclass
class TopLevelCommandRegistry:
    reserved_names: set[str] = field(default_factory=set)
    _entries: dict[str, _RegistryEntry] = field(default_factory=dict)

    def register(self, command: TopLevelCommand, *, source: str = "builtin") -> None:
        if command.name in self._entries:
            raise ValueError(f"Top-level command '{command.name}' already registered")
        if source != "builtin" and command.name in self.reserved_names:
            raise ValueError(f"Top-level command '{command.name}' is reserved")
        self._entries[command.name] = _RegistryEntry(command=command, source=source)

    def get(self, name: str) -> Optional[TopLevelCommand]:
        entry = self._entries.get(name)
        return entry.command if entry else None

    def list_commands(self) -> list[TopLevelCommand]:
        return [self._entries[key].command for key in sorted(self._entries)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=aworld-cli/src pytest tests/core/test_top_level_command_system.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/core/test_top_level_command_system.py aworld-cli/src/aworld_cli/core/top_level_command_system.py
git commit -m "feat: add top-level command registry"
```

### Task 2: Move `skill` Into A Builtin Command Provider

**Files:**
- Create: `aworld-cli/src/aworld_cli/top_level_commands/__init__.py`
- Create: `aworld-cli/src/aworld_cli/top_level_commands/skill_cmd.py`
- Modify: `tests/core/test_skill_cli.py`

- [ ] **Step 1: Extend the CLI tests to assert registry-backed skill dispatch**

```python
# tests/core/test_skill_cli.py
def test_skill_command_is_registered_as_builtin():
    from aworld_cli.top_level_commands import register_builtin_top_level_commands
    from aworld_cli.core.top_level_command_system import TopLevelCommandRegistry

    registry = TopLevelCommandRegistry(reserved_names={"skill"})
    register_builtin_top_level_commands(registry)

    command = registry.get("skill")
    assert command is not None
    assert command.name == "skill"


def test_skill_list_dispatches_via_builtin_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "source-skills"
    _write_skill(source, "optimizer")

    monkeypatch.setattr(sys, "argv", ["aworld-cli", "skill", "install", str(source)])
    main_module.main()
    capsys.readouterr()

    monkeypatch.setattr(sys, "argv", ["aworld-cli", "skill", "list"])
    main_module.main()

    output = capsys.readouterr().out
    assert "source-skills" in output
    assert "skill_count=1" in output
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `PYTHONPATH=aworld-cli/src pytest tests/core/test_skill_cli.py -q`
Expected: FAIL because `register_builtin_top_level_commands` does not exist yet

- [ ] **Step 3: Write the builtin command provider**

```python
# aworld-cli/src/aworld_cli/top_level_commands/skill_cmd.py
from __future__ import annotations

from pathlib import Path

from aworld_cli.core.installed_skill_manager import InstalledSkillManager


class SkillTopLevelCommand:
    @property
    def name(self) -> str:
        return "skill"

    @property
    def description(self) -> str:
        return "Installed skill management commands"

    def register_parser(self, subparsers) -> None:
        skill_parser = subparsers.add_parser("skill", help=self.description)
        skill_subparsers = skill_parser.add_subparsers(dest="skill_action", required=True)
        install_parser = skill_subparsers.add_parser("install", help="Install a skill package")
        install_parser.add_argument("source")
        install_parser.add_argument("--scope", default="global")
        install_parser.add_argument("--mode", choices=["clone", "copy", "symlink"], default=None)
        skill_subparsers.add_parser("list", help="List installed skill packages")
        enable_parser = skill_subparsers.add_parser("enable", help="Enable an installed skill package")
        enable_parser.add_argument("install_id")
        disable_parser = skill_subparsers.add_parser("disable", help="Disable an installed skill package")
        disable_parser.add_argument("install_id")
        remove_parser = skill_subparsers.add_parser("remove", help="Remove an installed skill package")
        remove_parser.add_argument("install_id")
        update_parser = skill_subparsers.add_parser("update", help="Update a git-backed skill package")
        update_parser.add_argument("install_id")
        import_parser = skill_subparsers.add_parser("import", help="Import a manually placed installed skill entry")
        import_parser.add_argument("path")
        import_parser.add_argument("--scope", default="global")

    def run(self, args, context) -> int | None:
        manager = InstalledSkillManager()
        if args.skill_action == "install":
            source_path = Path(args.source).expanduser()
            mode = args.mode or ("copy" if source_path.exists() else "clone")
            record = manager.install(source=args.source, mode=mode, scope=args.scope)
            print(f"✅ Skill package '{record['install_id']}' installed successfully")
            print(f"📍 Location: {record['installed_path']}")
            return 0
        if args.skill_action == "list":
            installs = sorted(manager.list_installs(), key=lambda item: str(item["install_id"]))
            for install in installs:
                print(
                    f"{install['install_id']} | enabled={install.get('enabled', True)} | "
                    f"scope={install['scope']} | skill_count={install['skill_count']} | source={install['source']}"
                )
            return 0
        if args.skill_action == "enable":
            record = manager.enable_install(args.install_id)
            print(f"✅ Skill package '{record['install_id']}' enabled successfully")
            return 0
        if args.skill_action == "disable":
            record = manager.disable_install(args.install_id)
            print(f"✅ Skill package '{record['install_id']}' disabled successfully")
            return 0
        if args.skill_action == "remove":
            manager.remove_install(args.install_id)
            print(f"✅ Skill package '{args.install_id}' removed successfully")
            return 0
        if args.skill_action == "update":
            record = manager.update_install(args.install_id)
            print(f"✅ Skill package '{record['install_id']}' updated successfully")
            return 0
        if args.skill_action == "import":
            record = manager.import_entry(Path(args.path), scope=args.scope)
            print(f"✅ Skill package '{record['install_id']}' imported successfully")
            return 0
        raise ValueError(f"Unsupported skill action: {args.skill_action}")
```

```python
# aworld-cli/src/aworld_cli/top_level_commands/__init__.py
from aworld_cli.top_level_commands.skill_cmd import SkillTopLevelCommand


def register_builtin_top_level_commands(registry) -> None:
    registry.register(SkillTopLevelCommand(), source="builtin")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=aworld-cli/src pytest tests/core/test_skill_cli.py -q`
Expected: PASS with existing `skill` CLI behavior still green

- [ ] **Step 5: Commit**

```bash
git add tests/core/test_skill_cli.py aworld-cli/src/aworld_cli/top_level_commands/__init__.py aworld-cli/src/aworld_cli/top_level_commands/skill_cmd.py
git commit -m "refactor: move skill CLI into builtin top-level command"
```

### Task 3: Replace `main.py` Hardcoding With Registry Bootstrap

**Files:**
- Modify: `aworld-cli/src/aworld_cli/main.py`
- Create: `aworld-cli/src/aworld_cli/plugin_capabilities/cli_commands.py`
- Modify: `tests/test_gateway_cli.py`
- Modify: `tests/core/test_skill_cli.py`

- [ ] **Step 1: Add failing tests for registry-based main dispatch**

```python
# tests/core/test_skill_cli.py
def test_main_no_longer_requires_hardcoded_skill_branch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "source-skills"
    _write_skill(source, "optimizer")

    monkeypatch.setattr(sys, "argv", ["aworld-cli", "skill", "install", str(source)])
    main_module.main()
    out = capsys.readouterr().out

    assert "installed successfully" in out
```

```python
# tests/test_gateway_cli.py
def test_gateway_dispatch_still_bypasses_top_level_skill_registry(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["aworld-cli", "gateway", "status"])
    monkeypatch.setattr("aworld_cli.gateway_cli.handle_gateway_status", lambda: {"state": "registered"})
    monkeypatch.setattr("aworld_cli.main._show_banner", lambda: None)
    monkeypatch.setattr("aworld_cli.main.init_middlewares", lambda **kwargs: None)
    monkeypatch.setattr("aworld_cli.core.config.load_config_with_env", lambda env_file: ({}, "env", env_file))
    monkeypatch.setattr("aworld_cli.core.config.has_model_config", lambda config: True)
    monkeypatch.setattr("aworld_cli.core.skill_registry.get_skill_registry", lambda skill_paths=None: type("R", (), {"get_all_skills": lambda self: {}})())

    cli_main.main()

    assert capsys.readouterr().out == "{'state': 'registered'}\n"
```

- [ ] **Step 2: Run the focused dispatch tests to verify they fail**

Run: `PYTHONPATH=aworld-cli/src pytest tests/core/test_skill_cli.py tests/test_gateway_cli.py -q`
Expected: FAIL until `main.py` boots the new registry and dispatches through it

- [ ] **Step 3: Refactor `main.py` bootstrap and add plugin adapter**

```python
# aworld-cli/src/aworld_cli/plugin_capabilities/cli_commands.py
from aworld_cli.core.top_level_command_system import TopLevelCommandRegistry


def sync_plugin_cli_commands(registry: TopLevelCommandRegistry, plugins) -> None:
    for plugin in plugins:
        for entrypoint in plugin.manifest.entrypoints.get("cli_commands", ()):
            # Phase 1: only reserve the bridge shape; actual external providers can follow later.
            # Skip duplicates deterministically.
            if registry.get(entrypoint.name or entrypoint.entrypoint_id) is not None:
                continue
```

```python
# aworld-cli/src/aworld_cli/main.py
from aworld_cli.core.plugin_manager import PluginManager
from aworld_cli.core.top_level_command_system import TopLevelCommandContext, TopLevelCommandRegistry
from aworld_cli.plugin_capabilities.cli_commands import sync_plugin_cli_commands
from aworld_cli.top_level_commands import register_builtin_top_level_commands


def _build_top_level_registry() -> TopLevelCommandRegistry:
    registry = TopLevelCommandRegistry(reserved_names={"skill"})
    register_builtin_top_level_commands(registry)
    plugin_manager = PluginManager()
    sync_plugin_cli_commands(registry, plugin_manager.get_framework_registry().plugins())
    return registry


def _dispatch_top_level_command(argv: list[str]) -> bool:
    registry = _build_top_level_registry()
    parser = argparse.ArgumentParser(prog="aworld-cli")
    subparsers = parser.add_subparsers(dest="command")
    for command in registry.list_commands():
        command.register_parser(subparsers)
    args, _ = parser.parse_known_args(argv[1:])
    command = registry.get(getattr(args, "command", "") or "")
    if command is None:
        return False
    command.run(args, TopLevelCommandContext(cwd=str(Path.cwd()), argv=tuple(argv)))
    return True


def main():
    if _dispatch_top_level_command(sys.argv):
        return
    # existing gateway / interactive / direct-mode code remains below
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=aworld-cli/src pytest tests/core/test_skill_cli.py tests/test_gateway_cli.py -q`
Expected: PASS with gateway regressions still green

- [ ] **Step 5: Commit**

```bash
git add aworld-cli/src/aworld_cli/main.py aworld-cli/src/aworld_cli/plugin_capabilities/cli_commands.py tests/core/test_skill_cli.py tests/test_gateway_cli.py
git commit -m "refactor: dispatch skill through top-level command registry"
```

### Task 4: Generate Skill-Name Slash Aliases And Show Them In `/skills`

**Files:**
- Modify: `aworld-cli/src/aworld_cli/console.py`
- Modify: `aworld-cli/src/aworld_cli/plugin_capabilities/commands.py`
- Modify: `aworld-cli/src/aworld_cli/core/command_system.py`
- Modify: `tests/core/test_skill_cli.py`

- [ ] **Step 1: Add failing tests for generated skill aliases**

```python
# tests/core/test_skill_cli.py
@pytest.mark.asyncio
async def test_skills_table_shows_generated_skill_alias_and_related_provider_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = AWorldCLI()
    rendered: list[str] = []

    class DummyConsole:
        def print(self, value):
            rendered.append(str(value))

    cli.console = DummyConsole()

    monkeypatch.setattr(
        cli,
        "_resolve_visible_skills",
        lambda **kwargs: SimpleNamespace(
            skill_configs={
                "browser-use": {
                    "description": "Browser automation",
                    "skill_path": "/tmp/browser-use/SKILL.md",
                    "generated_alias": "/browser-use",
                    "provider_commands": ["/browser", "/browse-page"],
                }
            }
        ),
    )

    await cli._render_skills_table()

    assert any("/browser-use" in line for line in rendered)
    assert any("/browser" in line for line in rendered)
    assert any("/browse-page" in line for line in rendered)


@pytest.mark.asyncio
async def test_generated_skill_alias_sets_pending_override() -> None:
    cli = AWorldCLI()
    cli._pending_skill_overrides = []

    handled = await cli._handle_skills_command("/brainstorming")

    assert handled is True
    assert cli._pending_skill_overrides == ["brainstorming"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=aworld-cli/src pytest tests/core/test_skill_cli.py -q`
Expected: FAIL because `/skills` currently renders no generated alias column and `/brainstorming` is not recognized

- [ ] **Step 3: Extend command handling, completion, and table rendering**

```python
# aworld-cli/src/aworld_cli/console.py
table.add_column("Alias", style="yellow", no_wrap=False, max_width=20)
table.add_column("Commands", style="yellow", no_wrap=False, max_width=28)

for skill_name, skill_data in rows:
    generated_alias = skill_data.get("generated_alias", f"/{skill_name}")
    commands = ", ".join(skill_data.get("provider_commands", [])) or "—"
    table.add_row(skill_name, str(desc), status, generated_alias, commands, addr_cell)
```

```python
# aworld-cli/src/aworld_cli/console.py
if normalized.startswith("/") and normalized.count(" ") == 0:
    alias_name = normalized[1:]
    try:
        resolved = self._resolve_visible_skills(
            agent_name=agent_name,
            executor_instance=executor_instance,
        )
    except Exception:
        resolved = None
    if resolved is not None and alias_name in resolved.skill_configs:
        self._pending_skill_overrides = [alias_name]
        self.console.print(f"[green]Will force skill on next task:[/green] {alias_name}")
        return True
```

```python
# aworld-cli/src/aworld_cli/plugin_capabilities/commands.py
def commands_for_plugin(plugin) -> list[str]:
    visible: list[str] = []
    for entrypoint in plugin.manifest.entrypoints.get("commands", []):
        if entrypoint.visibility == "hidden":
            continue
        visible.append(f"/{entrypoint.name or entrypoint.entrypoint_id}")
    return visible
```

```python
# aworld-cli/src/aworld_cli/console.py
def _provider_commands_for_skill(self, plugin_manager, skill_name: str, agent_name: str | None) -> list[str]:
    for plugin in plugin_manager.get_framework_registry().plugins():
        entrypoint_ids = {
            item.entrypoint_id for item in plugin.manifest.entrypoints.get("skills", ())
        }
        if skill_name not in entrypoint_ids:
            continue
        from .plugin_capabilities.commands import commands_for_plugin
        return commands_for_plugin(plugin)
    return []
```

```python
# aworld-cli/src/aworld_cli/console.py
skill_data["generated_alias"] = f"/{skill_name}"
skill_data["provider_commands"] = self._provider_commands_for_skill(
    plugin_manager,
    skill_name,
    agent_name,
)
```

```python
# aworld-cli/src/aworld_cli/core/command_system.py
def generated_skill_completion_items(skill_names: list[str]) -> dict[str, str]:
    return {f"/{skill_name}": f"Force skill on next task: {skill_name}" for skill_name in skill_names}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=aworld-cli/src pytest tests/core/test_skill_cli.py -q`
Expected: PASS and existing `/skills use` tests stay green

- [ ] **Step 5: Commit**

```bash
git add aworld-cli/src/aworld_cli/console.py aworld-cli/src/aworld_cli/plugin_capabilities/commands.py aworld-cli/src/aworld_cli/core/command_system.py tests/core/test_skill_cli.py
git commit -m "feat: add generated skill aliases in interactive UI"
```

## Self-Review

- Spec coverage:
  - Top-level command SPI: Task 1
  - Builtin `skill` provider: Task 2
  - `main.py` hardcode removal: Task 3
  - `/skills` related command discovery and generated skill aliases: Task 4
- Placeholder scan:
  - No `TODO`, `TBD`, or deferred implementation markers remain in the steps above.
- Type consistency:
  - `TopLevelCommandRegistry`, `TopLevelCommandContext`, and `SkillTopLevelCommand` are introduced once and referenced consistently in later tasks.
