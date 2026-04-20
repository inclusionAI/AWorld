from pathlib import Path

from aworld_cli.core.command_system import CommandRegistry
from aworld_cli.core.plugin_manager import (
    PluginManager,
    get_builtin_agent_bundle_roots,
    get_builtin_plugin_roots,
)
from aworld.plugins.discovery import discover_plugins
from aworld_cli.console import AWorldCLI
from aworld_cli.plugin_capabilities.commands import register_plugin_commands
from aworld_cli.runtime.base import BaseCliRuntime
from aworld_cli.runtime.cli import CliRuntime


def _set_isolated_plugin_dir(monkeypatch, tmp_path: Path) -> Path:
    plugin_dir = tmp_path / ".aworld" / "plugins"
    monkeypatch.setattr("aworld_cli.core.plugin_manager.DEFAULT_PLUGIN_DIR", plugin_dir)
    return plugin_dir


def test_code_review_like_plugin_registers_command_and_assets(tmp_path):
    manager = PluginManager(plugin_dir=tmp_path / "plugins")
    plugin_root = Path("tests/fixtures/plugins/code_review_like").resolve()

    assert manager.install("code-review-like", local_path=str(plugin_root))

    discovered = discover_plugins(manager.get_plugin_roots())

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands(discovered)

        assert CommandRegistry.get("code-review") is not None
    finally:
        CommandRegistry.restore(snapshot)


def test_runtime_initialization_registers_enabled_plugin_commands(tmp_path):
    manager = PluginManager(plugin_dir=tmp_path / "plugins")
    plugin_root = Path("tests/fixtures/plugins/code_review_like").resolve()

    assert manager.install("code-review-like", local_path=str(plugin_root))

    class FakeRuntime(BaseCliRuntime):
        def __init__(self, plugin_dirs):
            self.plugin_dirs = plugin_dirs
            super().__init__(agent_name="Aworld")

        async def _load_agents(self):
            return []

        async def _create_executor(self, agent):
            return None

        def _get_source_type(self) -> str:
            return "TEST"

        def _get_source_location(self) -> str:
            return "test"

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        runtime = FakeRuntime(manager.get_plugin_roots())
        runtime._initialize_plugin_framework()

        assert CommandRegistry.get("code-review") is not None
    finally:
        CommandRegistry.restore(snapshot)


def test_builtin_agent_bundle_namespace_moves_to_builtin_agents_package():
    from aworld_cli.builtin_agents.smllc.agents.aworld_agent import load_aworld_system_prompt
    from aworld_cli.builtin_agents.smllc.agents.avatar.avatar import build_avatar_swarm

    assert "cron" in load_aworld_system_prompt()
    assert callable(build_avatar_swarm)


def test_get_builtin_plugin_roots_prefers_builtin_plugins_for_aworld_hud():
    roots = get_builtin_plugin_roots()

    assert any(root.parent.name == "builtin_plugins" and root.name == "aworld_hud" for root in roots)
    assert not any(root.name == "smllc" for root in roots)


def test_get_builtin_agent_bundle_roots_includes_smllc_only_as_agent_bundle():
    roots = get_builtin_agent_bundle_roots()

    assert any(root.parent.name == "builtin_agents" and root.name == "smllc" for root in roots)
    assert not any(root.name == "aworld_hud" for root in roots)


def test_cli_runtime_separates_framework_plugins_from_builtin_agent_bundles(monkeypatch, tmp_path):
    _set_isolated_plugin_dir(monkeypatch, tmp_path)
    runtime = CliRuntime(local_dirs=[], remote_backends=[])

    assert any(path.parent.name == "builtin_plugins" and path.name == "aworld_hud" for path in runtime.plugin_dirs)
    assert any(path.parent.name == "builtin_agents" and path.name == "smllc" for path in runtime.builtin_agent_dirs)


def test_cli_runtime_excludes_disabled_builtin_hud_plugin(monkeypatch, tmp_path):
    plugin_dir = _set_isolated_plugin_dir(monkeypatch, tmp_path)
    manager = PluginManager(plugin_dir=plugin_dir)

    manager.disable("aworld-hud")

    runtime = CliRuntime(local_dirs=[], remote_backends=[])

    assert not any(path.name == "aworld_hud" for path in runtime.plugin_dirs)


def test_cli_runtime_includes_enabled_builtin_hud_plugin(monkeypatch, tmp_path):
    plugin_dir = _set_isolated_plugin_dir(monkeypatch, tmp_path)
    manager = PluginManager(plugin_dir=plugin_dir)

    manager.disable("aworld-hud")
    manager.enable("aworld-hud")

    runtime = CliRuntime(local_dirs=[], remote_backends=[])

    assert any(path.name == "aworld_hud" for path in runtime.plugin_dirs)


def test_cli_runtime_reports_discovered_plugin_count_not_raw_runtime_roots(monkeypatch, tmp_path):
    valid_root = tmp_path / "valid-plugin"
    cache_root = tmp_path / "__pycache__"
    valid_root.mkdir()
    cache_root.mkdir()

    class FakePluginManager:
        def get_runtime_plugin_roots(self):
            return [valid_root, cache_root]

    discovered_plugin = type(
        "Plugin",
        (),
        {
            "source": "manifest",
            "manifest": type("Manifest", (), {"plugin_root": str(valid_root)})(),
        },
    )()

    printed = []
    runtime = CliRuntime(local_dirs=[], remote_backends=[])
    runtime.cli = type(
        "Cli",
        (),
        {
            "console": type("Console", (), {"print": lambda self, message: printed.append(message)})(),
        },
    )()

    monkeypatch.setattr("aworld_cli.core.plugin_manager.PluginManager", FakePluginManager)
    monkeypatch.setattr("aworld.plugins.discovery.discover_plugins", lambda roots: [discovered_plugin])

    resolved = runtime._get_plugin_dirs()

    assert resolved == [valid_root]
    assert printed == ["📦 Found 1 active plugin(s)"]


def test_cli_runtime_reports_only_manifest_plugins_in_active_plugin_count(monkeypatch, tmp_path):
    manifest_root = tmp_path / "manifest-plugin"
    legacy_root = tmp_path / "legacy-plugin"
    manifest_root.mkdir()
    legacy_root.mkdir()

    class FakePluginManager:
        def get_runtime_plugin_roots(self):
            return [manifest_root, legacy_root]

    manifest_plugin = type(
        "Plugin",
        (),
        {
            "source": "manifest",
            "manifest": type("Manifest", (), {"plugin_root": str(manifest_root)})(),
        },
    )()
    legacy_plugin = type(
        "Plugin",
        (),
        {
            "source": "legacy",
            "manifest": type("Manifest", (), {"plugin_root": str(legacy_root)})(),
        },
    )()

    printed = []
    runtime = CliRuntime(local_dirs=[], remote_backends=[])
    runtime.cli = type(
        "Cli",
        (),
        {
            "console": type("Console", (), {"print": lambda self, message: printed.append(message)})(),
        },
    )()

    monkeypatch.setattr("aworld_cli.core.plugin_manager.PluginManager", FakePluginManager)
    monkeypatch.setattr(
        "aworld.plugins.discovery.discover_plugins",
        lambda roots: [manifest_plugin, legacy_plugin],
    )

    resolved = runtime._get_plugin_dirs()

    assert resolved == [manifest_root, legacy_root]
    assert printed == ["📦 Found 1 active plugin(s)"]


def test_runtime_initializes_framework_registry_and_context_handlers():
    class DummyRuntime(BaseCliRuntime):
        def __init__(self, plugin_dirs):
            super().__init__()
            self.plugin_dirs = plugin_dirs

        async def _load_agents(self):
            return []

        async def _create_executor(self, agent):
            return None

        def _get_source_type(self):
            return "TEST"

        def _get_source_location(self):
            return "test://plugins"

    runtime = DummyRuntime([Path("tests/fixtures/plugins/context_like").resolve()])
    runtime._initialize_plugin_framework()

    assert runtime._plugin_registry is not None
    assert runtime._plugin_registry.capabilities() == ("contexts",)
    assert [adapter.entrypoint_id for adapter in runtime.get_context_phase_handlers("schema")] == [
        "workspace-memory"
    ]


def test_runtime_tracks_active_plugins_by_capability(tmp_path):
    plugin_root = tmp_path / "runtime_capability"
    plugin_root.mkdir()
    (plugin_root / ".aworld-plugin").mkdir()
    (plugin_root / ".aworld-plugin" / "plugin.json").write_text(
        '''
        {
          "id": "runtime-capability",
          "name": "runtime-capability",
          "version": "1.0.0",
          "entrypoints": {
            "tools": [{"id": "tool", "target": "tools/tool.py"}],
            "runners": [{"id": "runner", "target": "runners/runner.py"}],
            "hud": [{"id": "hud", "target": "hud/status.py"}]
          }
        }
        ''',
        encoding="utf-8",
    )

    class DummyRuntime(BaseCliRuntime):
        def __init__(self, plugin_dirs):
            super().__init__()
            self.plugin_dirs = plugin_dirs

        async def _load_agents(self):
            return []

        async def _create_executor(self, agent):
            return None

        def _get_source_type(self):
            return "TEST"

        def _get_source_location(self):
            return "test://plugins"

    runtime = DummyRuntime([plugin_root])
    runtime._initialize_plugin_framework()

    assert runtime.active_plugin_capabilities() == ("hud", "runners", "tools")
    assert [plugin.manifest.plugin_id for plugin in runtime.get_active_plugins("tools")] == ["runtime-capability"]
    assert [entry.entrypoint.entrypoint_id for entry in runtime.get_active_entrypoints("runners")] == ["runner"]


def test_runtime_initialization_skips_invalid_manifest_plugin_and_keeps_valid_plugin(tmp_path):
    invalid_root = tmp_path / "broken"
    (invalid_root / ".aworld-plugin").mkdir(parents=True)
    (invalid_root / ".aworld-plugin" / "plugin.json").write_text(
        '{"name": "broken", "version": "1.0.0"}',
        encoding="utf-8",
    )

    class DummyRuntime(BaseCliRuntime):
        def __init__(self, plugin_dirs):
            super().__init__()
            self.plugin_dirs = plugin_dirs

        async def _load_agents(self):
            return []

        async def _create_executor(self, agent):
            return None

        def _get_source_type(self):
            return "TEST"

        def _get_source_location(self):
            return "test://plugins"

    runtime = DummyRuntime(
        [
            invalid_root,
            Path("tests/fixtures/plugins/context_like").resolve(),
        ]
    )
    runtime._initialize_plugin_framework()

    assert runtime._plugin_registry is not None
    assert [plugin.manifest.plugin_id for plugin in runtime._plugins] == ["context-like"]
    assert runtime.active_plugin_capabilities() == ("contexts",)


def test_runtime_initialization_skips_duplicate_plugin_ids(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"

    for root, name in ((first, "first"), (second, "second")):
        (root / ".aworld-plugin").mkdir(parents=True)
        (root / ".aworld-plugin" / "plugin.json").write_text(
            (
                "{"
                "\"id\": \"duplicate-plugin\", "
                f"\"name\": \"{name}\", "
                "\"version\": \"1.0.0\", "
                "\"entrypoints\": {"
                "\"commands\": ["
                "{"
                "\"id\": \"hello\", "
                "\"target\": \"commands/hello.md\""
                "}"
                "]"
                "}"
                "}"
            ),
            encoding="utf-8",
        )

    class DummyRuntime(BaseCliRuntime):
        def __init__(self, plugin_dirs):
            super().__init__()
            self.plugin_dirs = plugin_dirs

        async def _load_agents(self):
            return []

        async def _create_executor(self, agent):
            return None

        def _get_source_type(self):
            return "TEST"

        def _get_source_location(self):
            return "test://plugins"

    runtime = DummyRuntime([first, second])
    runtime._initialize_plugin_framework()

    assert runtime._plugin_registry is not None
    assert [plugin.manifest.name for plugin in runtime._plugins] == ["first"]
    assert runtime.active_plugin_capabilities() == ("commands",)


def test_runtime_initialization_skips_plugins_with_missing_dependencies_and_conflicts(tmp_path):
    def write_plugin(root: Path, plugin_id: str, *, dependencies=(), conflicts=()):
        (root / ".aworld-plugin").mkdir(parents=True)
        dependencies_json = "[" + ", ".join(f'"{item}"' for item in dependencies) + "]"
        conflicts_json = "[" + ", ".join(f'"{item}"' for item in conflicts) + "]"
        (root / ".aworld-plugin" / "plugin.json").write_text(
            (
                "{"
                f"\"id\": \"{plugin_id}\", "
                f"\"name\": \"{plugin_id}\", "
                "\"version\": \"1.0.0\", "
                f"\"dependencies\": {dependencies_json}, "
                f"\"conflicts\": {conflicts_json}, "
                "\"entrypoints\": {"
                "\"commands\": ["
                "{"
                "\"id\": \"hello\", "
                "\"target\": \"commands/hello.md\""
                "}"
                "]"
                "}"
                "}"
            ),
            encoding="utf-8",
        )

    base_root = tmp_path / "base"
    dependent_root = tmp_path / "dependent"
    missing_dep_root = tmp_path / "missing-dep"
    conflicting_root = tmp_path / "conflicting"

    write_plugin(base_root, "base-tools")
    write_plugin(dependent_root, "dependent-tools", dependencies=("base-tools",))
    write_plugin(missing_dep_root, "missing-dependent", dependencies=("not-installed",))
    write_plugin(conflicting_root, "conflicting-tools", conflicts=("base-tools",))

    class DummyRuntime(BaseCliRuntime):
        def __init__(self, plugin_dirs):
            super().__init__()
            self.plugin_dirs = plugin_dirs

        async def _load_agents(self):
            return []

        async def _create_executor(self, agent):
            return None

        def _get_source_type(self):
            return "TEST"

        def _get_source_location(self):
            return "test://plugins"

    runtime = DummyRuntime([base_root, dependent_root, missing_dep_root, conflicting_root])
    runtime._initialize_plugin_framework()

    assert runtime._plugin_registry is not None
    assert [plugin.manifest.plugin_id for plugin in runtime._plugins] == ["base-tools", "dependent-tools"]
    assert runtime.active_plugin_capabilities() == ("commands",)


async def test_runtime_renders_third_party_hud_plugin_from_hook_driven_state(tmp_path):
    plugin_root = Path("tests/fixtures/plugins/hud_stateful_like").resolve()

    class DummyRuntime(BaseCliRuntime):
        def __init__(self, plugin_dirs):
            super().__init__(agent_name="Aworld")
            self.plugin_dirs = plugin_dirs

        async def _load_agents(self):
            return []

        async def _create_executor(self, agent):
            return None

        def _get_source_type(self):
            return "TEST"

        def _get_source_location(self):
            return "test://hud-stateful-like"

    runtime = DummyRuntime([plugin_root])
    runtime._initialize_plugin_framework()
    runtime._plugin_state_store = type(runtime._plugin_state_store)(tmp_path / "plugin-state")

    executor_instance = type(
        "Executor",
        (),
        {
            "session_id": "session-1",
            "context": type("Context", (), {"workspace_path": str(tmp_path), "task_id": "task-1"})(),
        },
    )()

    await runtime.run_plugin_hooks(
        "task_started",
        event={"task_id": "task-1", "session_id": "session-1"},
        executor_instance=executor_instance,
    )
    runtime.update_hud_snapshot(
        session={"session_id": "session-1", "elapsed_seconds": 12.5},
        task={"current_task_id": "task-1", "status": "running"},
        usage={"input_tokens": 1200, "output_tokens": 300},
    )

    cli = AWorldCLI()
    text = cli._build_status_bar_text(runtime, agent_name="Aworld", mode="Chat", max_width=160)

    assert "PluginStatus: started" in text
    assert "Observed Task: task-1" in text
    assert "Usage: in 1.2k out 300" in text
    assert "Elapsed: 12.5s" in text


async def test_runtime_renders_builtin_aworld_hud_from_hook_driven_state(tmp_path):
    plugin_root = next(
        root for root in get_builtin_plugin_roots() if root.name == "aworld_hud"
    )

    class DummyRuntime(BaseCliRuntime):
        def __init__(self, plugin_dirs):
            super().__init__(agent_name="Aworld")
            self.plugin_dirs = plugin_dirs

        async def _load_agents(self):
            return []

        async def _create_executor(self, agent):
            return None

        def _get_source_type(self):
            return "TEST"

        def _get_source_location(self):
            return "test://builtin-aworld-hud"

    runtime = DummyRuntime([plugin_root])
    runtime._initialize_plugin_framework()
    runtime._plugin_state_store = type(runtime._plugin_state_store)(tmp_path / "plugin-state")

    executor_instance = type(
        "Executor",
        (),
        {
            "session_id": "session-1",
            "context": type("Context", (), {"workspace_path": str(tmp_path), "task_id": "task-1"})(),
        },
    )()

    await runtime.run_plugin_hooks(
        "task_started",
        event={"task_id": "task-1", "session_id": "session-1", "message": "hello"},
        executor_instance=executor_instance,
    )
    await runtime.run_plugin_hooks(
        "task_progress",
        event={
            "task_id": "task-1",
            "session_id": "session-1",
            "elapsed_seconds": 12.5,
            "usage": {
                "input_tokens": 1200,
                "output_tokens": 300,
                "context_percent": 34,
                "model": "gpt-5",
            },
        },
        executor_instance=executor_instance,
    )

    runtime.update_hud_snapshot(
        session={"session_id": "session-1", "model": "stale-model", "elapsed_seconds": 1.0},
        task={"current_task_id": "stale-task", "status": "idle"},
        usage={"input_tokens": 1, "output_tokens": 2, "context_percent": 1},
    )

    cli = AWorldCLI()
    text = cli._build_status_bar_text(runtime, agent_name="Aworld", mode="Chat", max_width=160)

    assert "Model: gpt-5" in text
    assert "Task: task-1 (running)" in text
    assert "Tokens: in 1.2k out 300" in text
    assert "Ctx: 34%" in text
    assert "Elapsed: 12.5s" in text
