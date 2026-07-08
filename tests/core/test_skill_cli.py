import sys
import json
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld_cli import main as main_module
from aworld_cli.console import AWorldCLI
from aworld_cli.core.command_system import Command, CommandContext, CommandRegistry
from aworld_cli.core.installed_skill_manager import InstalledSkillManager
from aworld_cli.core.plugin_manager import PluginManager
from aworld_cli.core.skill_state_manager import SkillStateManager
from aworld_cli.core.skill_registry import resolve_repo_aworld_skills_path
from aworld_cli.core.skill_activation_resolver import (
    SkillActivationResolver,
    SkillResolverRequest,
)
from aworld_cli.core.top_level_command_system import TopLevelCommandRegistry
from aworld_cli.executors.continuous import ContinuousExecutor
from aworld_cli.models import AgentInfo
from aworld_cli.plugin_capabilities.commands import register_plugin_commands
from aworld_cli.plugin_capabilities.state import PluginStateStore
from aworld_cli.top_level_commands import register_builtin_top_level_commands
from aworld_cli.top_level_commands.run_cmd import RunTopLevelCommand
from aworld.plugins.discovery import discover_plugins


def _write_skill(root: Path, skill_name: str) -> None:
    skill_dir = root / skill_name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: demo\n---\n\n# Demo\n",
        encoding="utf-8",
    )


def _get_builtin_goal_plugin_root() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "aworld-cli"
        / "src"
        / "aworld_cli"
        / "builtin_plugins"
        / "goal_session"
    )


def test_skill_install_and_list_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "source-skills"
    _write_skill(source, "optimizer")

    monkeypatch.setattr(sys, "argv", ["aworld-cli", "skill", "install", str(source)])
    main_module.main()

    install_output = capsys.readouterr().out
    assert "installed successfully" in install_output

    monkeypatch.setattr(sys, "argv", ["aworld-cli", "skill", "list"])
    main_module.main()

    list_output = capsys.readouterr().out
    assert "source-skills" in list_output
    assert "global" in list_output
    assert "skill_count=1" in list_output


def test_skill_import_and_remove_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    manual_entry = tmp_path / ".aworld" / "skills" / "installed" / "manual-demo"
    _write_skill(manual_entry, "agent-browser")

    monkeypatch.setattr(
        sys, "argv", ["aworld-cli", "skill", "import", str(manual_entry)]
    )
    main_module.main()

    import_output = capsys.readouterr().out
    assert "imported successfully" in import_output

    monkeypatch.setattr(sys, "argv", ["aworld-cli", "skill", "remove", "manual-demo"])
    main_module.main()

    remove_output = capsys.readouterr().out
    assert "removed successfully" in remove_output


def test_skill_update_cli_rejects_non_git_install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "source-skills"
    _write_skill(source, "optimizer")

    monkeypatch.setattr(sys, "argv", ["aworld-cli", "skill", "install", str(source)])
    main_module.main()

    install_id = InstalledSkillManager().list_installs()[0]["install_id"]

    monkeypatch.setattr(sys, "argv", ["aworld-cli", "skill", "update", install_id])
    with pytest.raises(SystemExit) as exc_info:
        main_module.main()
    assert exc_info.value.code == 1
    output = capsys.readouterr().out
    assert "Only git-backed installed skill entries can be updated" in output


def test_skill_install_cli_rejects_unsupported_at_source_spec_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    monkeypatch.setattr(
        sys,
        "argv",
        ["aworld-cli", "skill", "install", "obsidian@obsidian-skills"],
    )
    with pytest.raises(SystemExit) as exc_info:
        main_module.main()

    assert exc_info.value.code == 1
    output = capsys.readouterr().out
    assert "Unsupported skill source" in output
    assert "Git URL or local skill directory" in output


def test_skill_install_accepts_agent_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "developer-skills"
    _write_skill(source, "optimizer")

    monkeypatch.setattr(
        sys,
        "argv",
        ["aworld-cli", "skill", "install", str(source), "--scope", "agent:developer"],
    )
    main_module.main()
    capsys.readouterr()

    monkeypatch.setattr(sys, "argv", ["aworld-cli", "skill", "list"])
    main_module.main()
    output = capsys.readouterr().out

    assert "agent:developer" in output


def test_skill_disable_and_enable_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "toggle-skills"
    _write_skill(source, "optimizer")

    monkeypatch.setattr(sys, "argv", ["aworld-cli", "skill", "install", str(source)])
    main_module.main()
    capsys.readouterr()

    monkeypatch.setattr(sys, "argv", ["aworld-cli", "skill", "disable", "toggle-skills"])
    main_module.main()
    disable_output = capsys.readouterr().out
    assert "disabled successfully" in disable_output

    installs = InstalledSkillManager().list_installs()
    assert installs[0]["enabled"] is False

    monkeypatch.setattr(sys, "argv", ["aworld-cli", "skill", "enable", "toggle-skills"])
    main_module.main()
    enable_output = capsys.readouterr().out
    assert "enabled successfully" in enable_output

    installs = InstalledSkillManager().list_installs()
    assert installs[0]["enabled"] is True


def test_skill_disable_and_enable_runtime_skill_cli(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SKILLS_DIR", raising=False)
    runtime_source = tmp_path / "runtime-skills"
    _write_skill(runtime_source, "youtube_search")
    monkeypatch.setenv("SKILLS_PATH", str(runtime_source))

    monkeypatch.setattr(sys, "argv", ["aworld-cli", "skill", "disable", "youtube_search"])
    main_module.main()
    disable_output = capsys.readouterr().out
    assert "disabled successfully" in disable_output
    assert SkillStateManager().is_enabled("youtube_search") is False

    monkeypatch.setattr(sys, "argv", ["aworld-cli", "skill", "enable", "youtube_search"])
    main_module.main()
    enable_output = capsys.readouterr().out
    assert "enabled successfully" in enable_output
    assert SkillStateManager().is_enabled("youtube_search") is True


def test_skill_list_cli_shows_enabled_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "stateful-skills"
    _write_skill(source, "optimizer")

    monkeypatch.setattr(sys, "argv", ["aworld-cli", "skill", "install", str(source)])
    main_module.main()
    capsys.readouterr()

    monkeypatch.setattr(sys, "argv", ["aworld-cli", "skill", "disable", "stateful-skills"])
    main_module.main()
    capsys.readouterr()

    monkeypatch.setattr(sys, "argv", ["aworld-cli", "skill", "list"])
    main_module.main()
    list_output = capsys.readouterr().out

    assert "enabled=False" in list_output


def test_skill_list_cli_shows_runtime_aworld_skills_source_without_installs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_aworld_skills = resolve_repo_aworld_skills_path()
    if repo_aworld_skills is None:
        pytest.skip("repo aworld-skills source is not available in this checkout")

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SKILLS_PATH", raising=False)
    monkeypatch.delenv("SKILLS_DIR", raising=False)

    monkeypatch.setattr(sys, "argv", ["aworld-cli", "skill", "list"])
    main_module.main()

    list_output = capsys.readouterr().out

    assert "No installed skill packages" in list_output
    assert str(repo_aworld_skills) in list_output


def test_skill_list_cli_shows_runtime_skill_names_without_installs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SKILLS_DIR", raising=False)
    runtime_source = tmp_path / "runtime-skills"
    _write_skill(runtime_source, "youtube_search")
    monkeypatch.setenv("SKILLS_PATH", str(runtime_source))

    monkeypatch.setattr(sys, "argv", ["aworld-cli", "skill", "list"])
    main_module.main()

    list_output = capsys.readouterr().out

    assert "Runtime skills:" in list_output
    assert "youtube_search" in list_output


def test_skill_list_cli_shows_disabled_runtime_skill_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SKILLS_DIR", raising=False)
    runtime_source = tmp_path / "runtime-skills"
    _write_skill(runtime_source, "youtube_search")
    monkeypatch.setenv("SKILLS_PATH", str(runtime_source))

    monkeypatch.setattr(sys, "argv", ["aworld-cli", "skill", "disable", "youtube_search"])
    main_module.main()
    capsys.readouterr()

    monkeypatch.setattr(sys, "argv", ["aworld-cli", "skill", "list"])
    main_module.main()
    list_output = capsys.readouterr().out

    assert "youtube_search | enabled=False" in list_output


def test_skill_remove_cli_removes_runtime_skill_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SKILLS_DIR", raising=False)
    runtime_source = tmp_path / "runtime-skills"
    _write_skill(runtime_source, "web-content-grounding")
    monkeypatch.setenv("SKILLS_PATH", str(runtime_source))

    monkeypatch.setattr(
        sys, "argv", ["aworld-cli", "skill", "remove", "web-content-grounding"]
    )
    main_module.main()

    remove_output = capsys.readouterr().out

    assert "Runtime skill 'web-content-grounding' removed successfully" in remove_output
    assert (runtime_source / "web-content-grounding").exists() is False


def test_skill_remove_cli_prefers_installed_package_over_runtime_skill(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SKILLS_DIR", raising=False)
    runtime_source = tmp_path / "runtime-skills"
    _write_skill(runtime_source, "web-content-grounding")
    monkeypatch.setenv("SKILLS_PATH", str(runtime_source))

    source = tmp_path / "source-skills"
    _write_skill(source, "web-content-grounding")
    InstalledSkillManager().install(
        source=source,
        mode="copy",
        scope="global",
        install_id="web-content-grounding",
    )

    monkeypatch.setattr(
        sys, "argv", ["aworld-cli", "skill", "remove", "web-content-grounding"]
    )
    main_module.main()

    remove_output = capsys.readouterr().out

    assert "Skill package 'web-content-grounding' removed successfully" in remove_output
    assert (runtime_source / "web-content-grounding").exists() is True
    assert InstalledSkillManager().list_installs() == []


def test_skill_install_creates_plugin_managed_skill_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "source-skills"
    _write_skill(source, "optimizer")

    monkeypatch.setattr(sys, "argv", ["aworld-cli", "skill", "install", str(source)])
    main_module.main()

    plugins = PluginManager(plugin_dir=tmp_path / ".aworld" / "plugins").list_plugins()
    skill_plugin = next(plugin for plugin in plugins if plugin["name"] == "source-skills")

    assert skill_plugin["package_kind"] == "skill"
    assert skill_plugin["managed_by"] == "skill"
    assert skill_plugin["activation_scope"] == "global"


def test_installed_skill_package_is_visible_and_generates_alias(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "source-skills"
    _write_skill(source / "skills", "brainstorming")

    InstalledSkillManager().install(source=source, mode="copy", scope="global")

    cli = AWorldCLI()
    resolved = cli._resolve_visible_skills()
    aliases = cli._generated_skill_alias_map()

    assert "brainstorming" in resolved.available_skill_names
    assert aliases["/brainstorming"] == "brainstorming"


def test_resolver_auto_activates_installed_skill_package_from_task_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "source-skills"
    _write_skill(source / "skills", "brainstorming")

    InstalledSkillManager().install(source=source, mode="copy", scope="global")

    plugin_manager = PluginManager(plugin_dir=tmp_path / ".aworld" / "plugins")
    resolver = SkillActivationResolver()
    resolved = resolver.resolve(
        SkillResolverRequest(
            plugin_roots=tuple(
                plugin_manager.get_runtime_plugin_roots()
                + plugin_manager.get_skill_package_roots()
            ),
            runtime_scope="session",
            task_text="Please help me brainstorming the rollout plan.",
        )
    )

    assert resolved.active_skill_names == ("brainstorming",)
    assert resolved.skill_configs["brainstorming"]["active"] is True


def test_main_accepts_repeated_skill_flag() -> None:
    parsed = main_module.build_parser().parse_args(
        ["interactive", "--skill", "browser-use", "--skill", "code-review"]
    )

    assert parsed.skill == ["browser-use", "code-review"]


def test_main_accepts_evolve_modes() -> None:
    parser = main_module.build_parser()

    assert parser.parse_args(["--evolve"]).evolve == "shadow"
    assert parser.parse_args(["--evolve=online"]).evolve == "online"
    assert parser.parse_args(["--evolve", "off"]).evolve == "off"
    parsed = parser.parse_args(["--evolve=online", "--judge-agent", "agent.md"])
    assert parsed.evolve == "online"
    assert parsed.judge_agent == "agent.md"


def test_cli_evolve_mode_maps_to_self_evolve_config() -> None:
    shadow = main_module._self_evolve_config_from_cli_mode("shadow")
    online = main_module._self_evolve_config_from_cli_mode("online")
    off = main_module._self_evolve_config_from_cli_mode("off")

    assert shadow.mode == "shadow"
    assert shadow.apply_policy == "proposal"
    assert online.mode == "online"
    assert online.apply_policy == "auto_verified"
    assert off.mode == "off"
    assert off.apply_policy == "proposal"


def test_cli_evolve_mode_maps_judge_agent_to_config() -> None:
    config = main_module._self_evolve_config_from_cli_mode(
        "online",
        judge_agent="agent.md",
    )

    assert config.mode == "online"
    assert config.apply_policy == "auto_verified"
    assert config.judge_config.mode == "agent_md"
    assert config.judge_config.agent_path == "agent.md"


def test_skill_command_is_registered_via_plugin_registry() -> None:
    registry = TopLevelCommandRegistry()

    register_builtin_top_level_commands(registry)

    assert registry.get("skill") is None

    plugin_registry = main_module._build_top_level_command_registry()

    command = plugin_registry.get("skill")
    assert command is not None
    assert command.name == "skill"


@pytest.mark.asyncio
async def test_run_direct_mode_passes_requested_skill_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class DummyRuntime:
        def __init__(self, *args, **kwargs) -> None:
            self._scheduler = None

        async def _load_agents(self):
            return [SimpleNamespace(name="Aworld")]

        def _bind_scheduler_default_agent(self, agent_name: str) -> None:
            captured["bound_agent"] = agent_name

        async def _create_executor(self, _agent):
            return SimpleNamespace(console=None)

        def _restore_executor_session(self, executor, current_agent_name=None):
            return None

    class DummyContinuousExecutor:
        def __init__(self, agent_executor, console=None) -> None:
            self.agent_executor = agent_executor
            self.console = console

        async def run_continuous(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(main_module, "CliRuntime", DummyRuntime)
    monkeypatch.setattr(main_module, "ContinuousExecutor", DummyContinuousExecutor)
    monkeypatch.setattr(
        "aworld.core.scheduler.get_scheduler",
        lambda: SimpleNamespace(),
    )

    await main_module._run_direct_mode(
        prompt="use browser",
        agent_name="Aworld",
        requested_skill_names=["browser-use", "code-review"],
    )

    assert captured["requested_skill_names"] == ["browser-use", "code-review"]


@pytest.mark.asyncio
async def test_run_direct_mode_passes_self_evolve_config_to_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class DummyRuntime:
        def __init__(self, *args, **kwargs) -> None:
            self._scheduler = None
            captured["self_evolve_config"] = kwargs.get("self_evolve_config")

        async def _load_agents(self):
            return [SimpleNamespace(name="Aworld")]

        def _bind_scheduler_default_agent(self, agent_name: str) -> None:
            pass

        async def _create_executor(self, _agent):
            return SimpleNamespace(console=None)

        def _restore_executor_session(self, executor, current_agent_name=None):
            return None

    class DummyContinuousExecutor:
        def __init__(self, agent_executor, console=None) -> None:
            pass

        async def run_continuous(self, **kwargs) -> None:
            return None

    monkeypatch.setattr(main_module, "CliRuntime", DummyRuntime)
    monkeypatch.setattr(main_module, "ContinuousExecutor", DummyContinuousExecutor)
    monkeypatch.setattr(
        "aworld.core.scheduler.get_scheduler",
        lambda: SimpleNamespace(),
    )

    await main_module._run_direct_mode(
        prompt="use browser",
        agent_name="Aworld",
        self_evolve_config=main_module._self_evolve_config_from_cli_mode("online"),
    )

    config = captured["self_evolve_config"]
    assert config.mode == "online"
    assert config.apply_policy == "auto_verified"


@pytest.mark.asyncio
async def test_run_direct_mode_binds_runtime_to_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class DummyExecutor(SimpleNamespace):
        pass

    class DummyRuntime:
        def __init__(self, *args, **kwargs) -> None:
            self._scheduler = None

        async def _load_agents(self):
            return [SimpleNamespace(name="Aworld")]

        def _bind_scheduler_default_agent(self, agent_name: str) -> None:
            captured["bound_agent"] = agent_name

        async def _create_executor(self, _agent):
            executor = DummyExecutor(console=None)
            captured["executor"] = executor
            return executor

        def _restore_executor_session(self, executor, current_agent_name=None):
            return None

    class DummyContinuousExecutor:
        def __init__(self, agent_executor, console=None) -> None:
            self.agent_executor = agent_executor
            self.console = console

        async def run_continuous(self, **kwargs) -> None:
            captured["runtime_on_executor"] = getattr(self.agent_executor, "_base_runtime", None)

    monkeypatch.setattr(main_module, "CliRuntime", DummyRuntime)
    monkeypatch.setattr(main_module, "ContinuousExecutor", DummyContinuousExecutor)
    monkeypatch.setattr(
        "aworld.core.scheduler.get_scheduler",
        lambda: SimpleNamespace(),
    )

    await main_module._run_direct_mode(
        prompt="use browser",
        agent_name="Aworld",
    )

    assert captured["runtime_on_executor"] is not None


@pytest.mark.asyncio
async def test_run_direct_mode_returns_replayable_trajectory_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DummyRuntime:
        def __init__(self, *args, **kwargs) -> None:
            self._scheduler = None

        async def _load_agents(self):
            return [SimpleNamespace(name="Aworld")]

        def _bind_scheduler_default_agent(self, agent_name: str) -> None:
            pass

        async def _create_executor(self, _agent):
            return SimpleNamespace(console=None)

        def _restore_executor_session(self, executor, current_agent_name=None):
            return None

    class DummyContinuousExecutor:
        def __init__(self, agent_executor, console=None) -> None:
            self.agent_executor = agent_executor
            self.console = console

        async def run_continuous(self, **kwargs) -> dict:
            return {
                "total_runs": 1,
                "successful_runs": 1,
                "total_cost": 0.0,
                "results": [
                    {
                        "iteration": 1,
                        "response": "Replay completed.",
                        "completed": True,
                        "success": True,
                    }
                ],
            }

    monkeypatch.setattr(main_module, "CliRuntime", DummyRuntime)
    monkeypatch.setattr(main_module, "ContinuousExecutor", DummyContinuousExecutor)
    monkeypatch.setattr(
        "aworld.core.scheduler.get_scheduler",
        lambda: SimpleNamespace(),
    )

    summary = await main_module._run_direct_mode(
        prompt="Replay this task",
        agent_name="Aworld",
    )

    trajectory = main_module._trajectory_from_direct_run_summary(
        summary,
        prompt="Replay this task",
        agent_name="Aworld",
    )

    assert trajectory == [
        {
            "meta": {"step": 1, "agent_id": "Aworld", "pre_agent": "runner"},
            "state": {"input": {"content": "Replay this task"}},
            "action": {
                "content": "Replay completed.",
                "is_agent_finished": "True",
                "tool_calls": [],
            },
            "reward": {"status": "ok"},
        }
    ]


def test_run_top_level_command_emits_machine_readable_trajectory(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fake_run_direct_mode(**kwargs):
        return {
            "results": [
                {
                    "iteration": 1,
                    "response": "Replay completed.",
                    "completed": True,
                    "success": True,
                }
            ]
        }

    monkeypatch.setattr(
        "aworld_cli.top_level_commands.run_cmd.bootstrap_runtime",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        main_module,
        "_resolve_agent_dirs",
        lambda agent_dirs: [],
    )
    monkeypatch.setattr(main_module, "_run_direct_mode", fake_run_direct_mode)

    args = SimpleNamespace(
        task="Replay this task",
        agent="Aworld",
        skill=None,
        max_runs=1,
        max_cost=None,
        max_duration=None,
        completion_signal=None,
        completion_threshold=3,
        non_interactive=True,
        session_id=None,
        remote_backend=None,
        agent_dir=None,
        agent_file=None,
        skill_path=None,
        env_file=".env",
        emit_trajectory=True,
    )
    context = SimpleNamespace(argv=["aworld-cli", "run", "--emit-trajectory"])

    assert RunTopLevelCommand().run(args, context) == 0

    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["trajectory"][0]["action"]["content"] == "Replay completed."
    assert payload["trajectory"][0]["state"]["input"]["content"] == "Replay this task"


def test_run_top_level_command_dispatches_global_evolve_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    async def fake_run_direct_mode(**kwargs):
        captured.update(kwargs)
        return {"results": []}

    monkeypatch.setattr(
        "aworld_cli.top_level_commands.run_cmd.bootstrap_runtime",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(main_module, "_resolve_agent_dirs", lambda agent_dirs: [])
    monkeypatch.setattr(main_module, "_run_direct_mode", fake_run_direct_mode)

    args = SimpleNamespace(
        task="Replay this task",
        agent="Aworld",
        skill=None,
        max_runs=1,
        max_cost=None,
        max_duration=None,
        completion_signal=None,
        completion_threshold=3,
        non_interactive=True,
        session_id=None,
        remote_backend=None,
        agent_dir=None,
        agent_file=None,
        skill_path=None,
        env_file=".env",
        emit_trajectory=False,
    )
    context = SimpleNamespace(
        argv=[
            "aworld-cli",
            "--evolve=online",
            "--judge-agent",
            "agent.md",
            "run",
            "--task",
            "Replay this task",
        ]
    )

    assert RunTopLevelCommand().run(args, context) == 0

    config = captured["self_evolve_config"]
    assert config.mode == "online"
    assert config.apply_policy == "auto_verified"
    assert config.judge_config.mode == "agent_md"
    assert config.judge_config.agent_path == "agent.md"


def test_run_top_level_command_prefers_task_response_trajectory(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    full_trajectory = [
        {
            "id": "step-1",
            "meta": {"step": 1, "agent_id": "Aworld", "pre_agent": "runner"},
            "state": {
                "input": {"content": "Replay this task"},
                "messages": [{"role": "assistant", "content": "tool evidence"}],
            },
            "action": {
                "content": "Replay completed.",
                "is_agent_finished": "True",
                "tool_calls": [{"name": "browser", "arguments": {"url": "https://example.com"}}],
            },
            "reward": {"status": "ok"},
        }
    ]

    async def fake_run_direct_mode(**kwargs):
        return {
            "results": [
                {
                    "iteration": 1,
                    "response": "Synthetic fallback should not be used.",
                    "completed": True,
                    "success": True,
                    "trajectory": full_trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            ]
        }

    monkeypatch.setattr(
        "aworld_cli.top_level_commands.run_cmd.bootstrap_runtime",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        main_module,
        "_resolve_agent_dirs",
        lambda agent_dirs: [],
    )
    monkeypatch.setattr(main_module, "_run_direct_mode", fake_run_direct_mode)

    args = SimpleNamespace(
        task="Replay this task",
        agent="Aworld",
        skill=None,
        max_runs=1,
        max_cost=None,
        max_duration=None,
        completion_signal=None,
        completion_threshold=3,
        non_interactive=True,
        session_id=None,
        remote_backend=None,
        agent_dir=None,
        agent_file=None,
        skill_path=None,
        env_file=".env",
        emit_trajectory=True,
    )
    context = SimpleNamespace(argv=["aworld-cli", "run", "--emit-trajectory"])

    assert RunTopLevelCommand().run(args, context) == 0

    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["trajectory_capture_mode"] == "task_response"
    assert payload["trajectory"] == full_trajectory
    assert payload["trajectory"][0]["action"]["tool_calls"][0]["name"] == "browser"


@pytest.mark.asyncio
async def test_run_direct_mode_prints_restored_transcript_before_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = StringIO()
    test_console = Console(file=output, force_terminal=False, width=120)
    captured: dict[str, object] = {}

    class DummyExecutor(SimpleNamespace):
        pass

    class DummyRuntime:
        def __init__(self, *args, **kwargs) -> None:
            self._scheduler = None

        async def _load_agents(self):
            return [SimpleNamespace(name="Aworld")]

        def _bind_scheduler_default_agent(self, agent_name: str) -> None:
            captured["bound_agent"] = agent_name

        async def _create_executor(self, _agent):
            return DummyExecutor(console=None)

        def _restore_executor_session(self, executor, current_agent_name=None):
            executor._aworld_cli_restored_transcript = SimpleNamespace(
                rendered_text="── Previous session transcript ──\nYou: old prompt\n\nAworld:\nold answer"
            )

    class DummyContinuousExecutor:
        def __init__(self, agent_executor, console=None) -> None:
            self.agent_executor = agent_executor
            self.console = console

        async def run_continuous(self, **kwargs) -> None:
            captured["output_before_run"] = output.getvalue()

    monkeypatch.setattr("aworld_cli._globals.console", test_console)
    monkeypatch.setattr(main_module, "CliRuntime", DummyRuntime)
    monkeypatch.setattr(main_module, "ContinuousExecutor", DummyContinuousExecutor)
    monkeypatch.setattr(
        "aworld.core.scheduler.get_scheduler",
        lambda: SimpleNamespace(),
    )

    await main_module._run_direct_mode(
        prompt="new prompt",
        agent_name="Aworld",
        session_id="session_test",
        session_mode="interactive",
        resume_record=SimpleNamespace(session_id="session_test"),
        session_store=SimpleNamespace(),
    )

    assert "Previous session transcript" in captured["output_before_run"]
    assert "You: old prompt" in captured["output_before_run"]
    assert "old answer" in captured["output_before_run"]


@pytest.mark.asyncio
async def test_continuous_executor_can_render_prompt_as_terminal_turn() -> None:
    output = StringIO()
    test_console = Console(file=output, force_terminal=False, width=120)

    class DummyAgentExecutor(SimpleNamespace):
        async def chat(self, prompt, **kwargs):
            return "done"

    executor = ContinuousExecutor(DummyAgentExecutor(session_id="session_test"), console=test_console)

    await executor.run_continuous(
        prompt="next prompt",
        agent_name="Aworld",
        max_runs=1,
        show_start_banner=False,
        show_iteration_header=False,
        echo_prompt_as_turn=True,
    )

    rendered = output.getvalue()
    assert "You: next prompt" in rendered
    assert "Starting" not in rendered
    assert "Continuous Execution" not in rendered


@pytest.mark.asyncio
async def test_console_skills_use_sets_pending_override() -> None:
    cli = AWorldCLI()
    cli._pending_skill_overrides = []

    handled = await cli._handle_skills_command("/skills use browser-use")

    assert handled is True
    assert cli._pending_skill_overrides == ["browser-use"]


@pytest.mark.asyncio
async def test_console_generated_skill_alias_sets_pending_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = AWorldCLI()
    cli._pending_skill_overrides = []
    monkeypatch.setattr(
        cli,
        "_generated_skill_alias_map",
        lambda **kwargs: {"/brainstorming": "brainstorming"},
    )

    handled = await cli._handle_skills_command("/brainstorming")

    assert handled is True
    assert cli._pending_skill_overrides == ["brainstorming"]


def test_generated_skill_alias_match_is_case_insensitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = AWorldCLI()
    monkeypatch.setattr(
        cli,
        "_generated_skill_alias_map",
        lambda **kwargs: {"/OpenClaw": "OpenClaw"},
    )

    assert cli._match_generated_skill_alias("/openclaw") == "OpenClaw"
    assert cli._match_generated_skill_alias("/OpenClaw") == "OpenClaw"


def test_rewrite_generated_skill_alias_with_prompt_sets_override_and_rewrites_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = AWorldCLI()
    monkeypatch.setattr(
        cli,
        "_generated_skill_alias_map",
        lambda **kwargs: {"/ad_image_create_skill": "ad_image_create_skill"},
    )

    handled, rewritten = cli._rewrite_generated_skill_alias_input(
        "/ad_image_create_skill 帮我创建一张萌娃的照片"
    )

    assert handled is True
    assert rewritten == "帮我创建一张萌娃的照片"
    assert cli._pending_skill_overrides == ["ad_image_create_skill"]


@pytest.mark.asyncio
async def test_skills_table_shows_generated_skill_alias_and_provider_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = AWorldCLI()
    output = StringIO()
    cli.console = Console(file=output, force_terminal=False, color_system=None, width=160)
    monkeypatch.setattr(
        cli,
        "_resolve_visible_skills",
        lambda **kwargs: SimpleNamespace(
            skill_configs={
                "brainstorming": {
                    "description": "Design before implementation",
                    "skill_path": "/tmp/brainstorming/SKILL.md",
                }
            }
        ),
    )
    monkeypatch.setattr(
        cli,
        "_generated_skill_alias_map",
        lambda **kwargs: {"/brainstorming": "brainstorming"},
    )
    monkeypatch.setattr(
        cli,
        "_provider_commands_by_skill",
        lambda **kwargs: {"brainstorming": ["/review"]},
    )

    await cli._render_skills_table()

    rendered = output.getvalue()

    assert "/brainstorming" in rendered
    assert "/review" in rendered


@pytest.mark.asyncio
async def test_skills_table_shows_disabled_skill_without_loading_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = AWorldCLI()
    output = StringIO()
    cli.console = Console(file=output, force_terminal=False, color_system=None, width=160)

    def fake_resolve_visible_skills(**kwargs):
        apply_disabled_filter = kwargs.get("apply_disabled_filter", True)
        if apply_disabled_filter:
            return SimpleNamespace(skill_configs={}, active_skill_names=(), available_skill_names=())
        return SimpleNamespace(
            skill_configs={
                "youtube_search": {
                    "description": "Search YouTube",
                    "skill_path": "/tmp/youtube_search/SKILL.md",
                }
            },
            active_skill_names=(),
            available_skill_names=("youtube_search",),
        )

    monkeypatch.setattr(cli, "_resolve_visible_skills", fake_resolve_visible_skills)
    monkeypatch.setattr(
        "aworld_cli.core.skill_state_manager.SkillStateManager.disabled_skill_names",
        lambda self: ("youtube_search",),
    )
    monkeypatch.setattr(
        cli,
        "_generated_skill_alias_map",
        lambda **kwargs: {},
    )
    monkeypatch.setattr(
        cli,
        "_provider_commands_by_skill",
        lambda **kwargs: {},
    )

    await cli._render_skills_table()

    rendered = output.getvalue()

    assert "youtube_search" in rendered
    assert "disabled" in rendered


def test_completion_entries_include_generated_skill_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = AWorldCLI()
    monkeypatch.setattr(
        cli,
        "_generated_skill_alias_map",
        lambda **kwargs: {"/brainstorming": "brainstorming"},
    )

    words, meta = cli._build_completion_entries(agent_names=["Aworld"])

    assert "/brainstorming" in words
    assert meta["/brainstorming"] == "Force skill on next task: brainstorming"


def test_completion_entries_include_dynamic_skill_subcommands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = AWorldCLI()

    class FakeSkillCommand:
        name = "pin"
        description = "Pin a skill for the next task"
        aliases = ("select",)

        async def run(self, cli_instance, args_text: str, **kwargs):
            return True

    monkeypatch.setattr(
        cli,
        "_load_skill_commands",
        lambda **kwargs: [FakeSkillCommand()],
    )

    words, meta = cli._build_completion_entries(agent_names=["Aworld"])

    assert "/skills pin" in words
    assert "/skills select" in words
    assert meta["/skills pin"] == "Pin a skill for the next task"


@pytest.mark.asyncio
async def test_console_dispatches_dynamic_skill_subcommand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = AWorldCLI()
    captured: dict[str, object] = {}

    class FakeSkillCommand:
        name = "pin"
        description = "Pin a skill for the next task"
        aliases = ("select",)
        usage = "/skills pin <name>"

        async def run(self, cli_instance, args_text: str, **kwargs):
            captured["args_text"] = args_text
            cli_instance._pending_skill_overrides = [args_text]
            return True

    monkeypatch.setattr(
        cli,
        "_load_skill_commands",
        lambda **kwargs: [FakeSkillCommand()],
    )

    handled = await cli._handle_skills_command("/skills pin brainstorming")

    assert handled is True
    assert captured["args_text"] == "brainstorming"
    assert cli._pending_skill_overrides == ["brainstorming"]


@pytest.mark.asyncio
async def test_console_skills_disable_and_enable_by_skill_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SKILLS_DIR", raising=False)
    source = tmp_path / "runtime-skills"
    _write_skill(source, "youtube_search")
    monkeypatch.setenv("SKILLS_PATH", str(source))

    cli = AWorldCLI()
    await cli._handle_skills_command("/skills disable youtube_search")

    assert SkillStateManager().is_enabled("youtube_search") is False

    await cli._handle_skills_command("/skills enable youtube_search")

    assert SkillStateManager().is_enabled("youtube_search") is True


@pytest.mark.asyncio
async def test_run_chat_session_rewrites_generated_skill_alias_before_command_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = AWorldCLI()
    output = StringIO()
    cli.console = Console(file=output, force_terminal=False, color_system=None, width=160)
    captured: dict[str, object] = {}
    prompts = iter(["/brainstorming draft rollout", "/exit"])

    async def fake_executor(prompt: str, requested_skill_names=None):
        captured["prompt"] = prompt
        captured["requested_skill_names"] = requested_skill_names
        return "ok"

    async def fake_apply_user_input_hooks(user_input: str, executor_instance=None):
        return True, user_input

    async def fake_stop_notification_poller():
        return None

    monkeypatch.setattr(
        cli,
        "_generated_skill_alias_map",
        lambda **kwargs: {"/brainstorming": "brainstorming"},
    )
    monkeypatch.setattr(cli, "_apply_user_input_hooks", fake_apply_user_input_hooks)
    monkeypatch.setattr(cli, "_stop_notification_poller", fake_stop_notification_poller)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda: next(prompts))

    result = await cli.run_chat_session(
        "Aworld",
        fake_executor,
        available_agents=[AgentInfo(name="Aworld")],
    )

    assert result is False
    assert captured["prompt"] == "draft rollout"
    assert captured["requested_skill_names"] == ["brainstorming"]


@pytest.mark.asyncio
async def test_run_chat_session_reports_disabled_skill_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = AWorldCLI()
    output = StringIO()
    cli.console = Console(file=output, force_terminal=False, color_system=None, width=160)
    prompts = iter(["/youtube_search test", "/exit"])
    executed: dict[str, object] = {"called": False}

    async def fake_executor(prompt: str, requested_skill_names=None):
        executed["called"] = True
        return "ok"

    async def fake_apply_user_input_hooks(user_input: str, executor_instance=None):
        return True, user_input

    async def fake_stop_notification_poller():
        return None

    def fake_resolve_visible_skills(**kwargs):
        apply_disabled_filter = kwargs.get("apply_disabled_filter", True)
        if apply_disabled_filter:
            return SimpleNamespace(skill_configs={}, active_skill_names=(), available_skill_names=())
        return SimpleNamespace(
            skill_configs={
                "youtube_search": {
                    "description": "Search YouTube",
                    "skill_path": "/tmp/youtube_search/SKILL.md",
                }
            },
            active_skill_names=(),
            available_skill_names=("youtube_search",),
        )

    monkeypatch.setattr(cli, "_resolve_visible_skills", fake_resolve_visible_skills)
    monkeypatch.setattr(cli, "_apply_user_input_hooks", fake_apply_user_input_hooks)
    monkeypatch.setattr(cli, "_stop_notification_poller", fake_stop_notification_poller)
    monkeypatch.setattr(
        "aworld_cli.core.skill_state_manager.SkillStateManager.disabled_skill_names",
        lambda self: ("youtube_search",),
    )
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda: next(prompts))

    result = await cli.run_chat_session(
        "Aworld",
        fake_executor,
        available_agents=[AgentInfo(name="Aworld")],
    )

    rendered = output.getvalue()

    assert result is False
    assert executed["called"] is False
    assert "disabled" in rendered.lower()
    assert "/skills enable youtube_search" in rendered


@pytest.mark.asyncio
async def test_run_chat_session_treats_absolute_path_text_as_plain_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = AWorldCLI()
    output = StringIO()
    cli.console = Console(file=output, force_terminal=False, color_system=None, width=160)
    captured: dict[str, object] = {}
    prompts = iter(
        [
            "/Users/manwu/Documents/health/2026-05-25/icloud，你看看这个里面有今天的健康数据吗？",
            "/exit",
        ]
    )

    async def fake_executor(prompt: str, requested_skill_names=None):
        captured["prompt"] = prompt
        captured["requested_skill_names"] = requested_skill_names
        return "ok"

    async def fake_apply_user_input_hooks(user_input: str, executor_instance=None):
        return True, user_input

    async def fake_stop_notification_poller():
        return None

    monkeypatch.setattr(cli, "_apply_user_input_hooks", fake_apply_user_input_hooks)
    monkeypatch.setattr(cli, "_stop_notification_poller", fake_stop_notification_poller)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda: next(prompts))

    result = await cli.run_chat_session(
        "Aworld",
        fake_executor,
        available_agents=[AgentInfo(name="Aworld")],
    )

    rendered = output.getvalue()

    assert result is False
    assert (
        captured["prompt"]
        == "/Users/manwu/Documents/health/2026-05-25/icloud，你看看这个里面有今天的健康数据吗？"
    )
    assert "Unknown command:" not in rendered


@pytest.mark.asyncio
async def test_run_chat_session_routes_prompt_commands_through_active_steering_in_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = AWorldCLI()
    output = StringIO()
    cli.console = Console(file=output, force_terminal=False, color_system=None, width=160)
    captured: dict[str, object] = {}
    prompts = iter(["/steercheck now", "/exit"])

    class DummyPromptCommand(Command):
        @property
        def name(self) -> str:
            return "steercheck"

        @property
        def description(self) -> str:
            return "exercise active steering path"

        async def get_prompt(self, context: CommandContext) -> str:
            captured["user_args"] = context.user_args
            return "generated prompt"

    class FakePromptSession:
        def prompt(self, *_args, **_kwargs):
            return next(prompts)

    async def fake_executor(prompt: str, requested_skill_names=None):
        captured["executor_prompt"] = prompt
        captured["requested_skill_names"] = requested_skill_names
        return "ok"

    async def fake_apply_user_input_hooks(user_input: str, executor_instance=None):
        return True, user_input

    async def fake_ensure_notification_poller(_runtime):
        return None

    async def fake_stop_notification_poller():
        return None

    async def fake_run_executor_with_active_steering(**kwargs):
        captured["steering_kwargs"] = kwargs
        return "ok"

    def fake_create_prompt_session(_completer, **_kwargs):
        session = FakePromptSession()
        cli._active_prompt_session = session
        return session

    monkeypatch.setattr(cli, "_apply_user_input_hooks", fake_apply_user_input_hooks)
    monkeypatch.setattr(cli, "_ensure_notification_poller", fake_ensure_notification_poller)
    monkeypatch.setattr(cli, "_stop_notification_poller", fake_stop_notification_poller)
    monkeypatch.setattr(cli, "_build_session_completer", lambda **_kwargs: object())
    monkeypatch.setattr(cli, "_create_prompt_session", fake_create_prompt_session)
    monkeypatch.setattr(cli, "_run_executor_with_active_steering", fake_run_executor_with_active_steering)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    executor_instance = SimpleNamespace(
        session_id="sess-1",
        swarm=SimpleNamespace(tools=[]),
        _base_runtime=SimpleNamespace(),
    )
    command = DummyPromptCommand()
    CommandRegistry.register(command)
    try:
        result = await cli.run_chat_session(
            "Aworld",
            fake_executor,
            available_agents=[AgentInfo(name="Aworld")],
            executor_instance=executor_instance,
        )
    finally:
        CommandRegistry.unregister(command.name)

    assert result is False
    assert captured["user_args"] == "now"
    assert captured["steering_kwargs"]["prompt"] == "generated prompt"
    assert captured["steering_kwargs"]["executor_instance"] is executor_instance
    assert captured["steering_kwargs"]["is_terminal"] is True
    assert "executor_prompt" not in captured


@pytest.mark.asyncio
async def test_run_chat_session_starts_goal_prompt_in_a_new_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cli = AWorldCLI()
    output = StringIO()
    cli.console = Console(file=output, force_terminal=False, color_system=None, width=160)
    monkeypatch.chdir(tmp_path)
    prompts = iter(
        [
            '/goal "Build a REST API" --verify "pytest tests/api -q" --completion-promise "COMPLETE"',
            "/exit",
        ]
    )
    captured: dict[str, object] = {}

    class DummyRuntime:
        def __init__(self, state_root: Path):
            self._plugin_state_store = PluginStateStore(state_root)

        def _resolve_plugin_state_path(
            self,
            plugin_id: str,
            scope: str,
            session_id: str | None,
            workspace_path: str | None,
        ):
            if scope == "global":
                return self._plugin_state_store.global_state(plugin_id)
            if scope == "session" and session_id:
                return self._plugin_state_store.session_state(plugin_id, session_id)
            if workspace_path:
                return self._plugin_state_store.workspace_state(plugin_id, workspace_path)
            return None

    class GoalExecutor:
        def __init__(self, runtime):
            self.session_id = "sess-1"
            self._base_runtime = runtime
            self.swarm = SimpleNamespace(tools=[])
            self.created_sessions: list[str] = []

        def new_session(self) -> str:
            self.created_sessions.append(self.session_id)
            self.session_id = "sess-2"
            return self.session_id

    class FakePromptSession:
        def prompt(self, *_args, **_kwargs):
            return next(prompts)

    async def fake_executor(prompt: str, requested_skill_names=None):
        captured["executor_prompt"] = prompt
        captured["requested_skill_names"] = requested_skill_names
        return "ok"

    async def fake_apply_user_input_hooks(user_input: str, executor_instance=None):
        return True, user_input

    async def fake_ensure_notification_poller(_runtime):
        return None

    async def fake_stop_notification_poller():
        return None

    async def fake_run_executor_with_active_steering(**kwargs):
        captured["steering_kwargs"] = kwargs
        return "ok"

    def fake_create_prompt_session(_completer, **_kwargs):
        session = FakePromptSession()
        cli._active_prompt_session = session
        return session

    monkeypatch.setattr(cli, "_apply_user_input_hooks", fake_apply_user_input_hooks)
    monkeypatch.setattr(cli, "_ensure_notification_poller", fake_ensure_notification_poller)
    monkeypatch.setattr(cli, "_stop_notification_poller", fake_stop_notification_poller)
    monkeypatch.setattr(cli, "_build_session_completer", lambda **_kwargs: object())
    monkeypatch.setattr(cli, "_create_prompt_session", fake_create_prompt_session)
    monkeypatch.setattr(cli, "_run_executor_with_active_steering", fake_run_executor_with_active_steering)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    runtime = DummyRuntime(tmp_path / "state")
    executor_instance = GoalExecutor(runtime)
    plugin = discover_plugins([_get_builtin_goal_plugin_root()])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])

        result = await cli.run_chat_session(
            "Aworld",
            fake_executor,
            available_agents=[AgentInfo(name="Aworld")],
            executor_instance=executor_instance,
        )
    finally:
        CommandRegistry.restore(snapshot)

    new_session_state_path = runtime._resolve_plugin_state_path(
        plugin_id="goal-session",
        scope="session",
        session_id="sess-2",
        workspace_path=str(tmp_path),
    )
    old_session_state_path = runtime._resolve_plugin_state_path(
        plugin_id="goal-session",
        scope="session",
        session_id="sess-1",
        workspace_path=str(tmp_path),
    )

    assert result is False
    assert executor_instance.created_sessions == ["sess-1"]
    assert executor_instance.session_id == "sess-2"
    assert "steering_kwargs" in captured
    assert "executor_prompt" not in captured
    assert "Objective: Build a REST API" in str(captured["steering_kwargs"]["prompt"])
    assert (
        runtime._plugin_state_store.handle(new_session_state_path).read()["objective"]
        == "Build a REST API"
    )
    assert runtime._plugin_state_store.handle(old_session_state_path).read() == {}
