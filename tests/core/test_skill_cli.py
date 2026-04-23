import sys
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld_cli import main as main_module
from aworld_cli.console import AWorldCLI
from aworld_cli.core.installed_skill_manager import InstalledSkillManager
from aworld_cli.core.plugin_manager import PluginManager
from aworld_cli.core.skill_state_manager import SkillStateManager
from aworld_cli.core.skill_registry import resolve_repo_aworld_skills_path
from aworld_cli.core.skill_activation_resolver import (
    SkillActivationResolver,
    SkillResolverRequest,
)
from aworld_cli.core.top_level_command_system import TopLevelCommandRegistry
from aworld_cli.models import AgentInfo
from aworld_cli.top_level_commands import register_builtin_top_level_commands


def _write_skill(root: Path, skill_name: str) -> None:
    skill_dir = root / skill_name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: demo\n---\n\n# Demo\n",
        encoding="utf-8",
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
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "source-skills"
    _write_skill(source, "optimizer")

    monkeypatch.setattr(sys, "argv", ["aworld-cli", "skill", "install", str(source)])
    main_module.main()

    install_id = InstalledSkillManager().list_installs()[0]["install_id"]

    monkeypatch.setattr(sys, "argv", ["aworld-cli", "skill", "update", install_id])
    with pytest.raises(
        ValueError, match="Only git-backed installed skill entries can be updated"
    ):
        main_module.main()


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
