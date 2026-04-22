import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld_cli import main as main_module
from aworld_cli.console import AWorldCLI
from aworld_cli.core.installed_skill_manager import InstalledSkillManager
from aworld_cli.core.plugin_manager import PluginManager


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


def test_main_accepts_repeated_skill_flag() -> None:
    parsed = main_module.build_parser().parse_args(
        ["interactive", "--skill", "browser-use", "--skill", "code-review"]
    )

    assert parsed.skill == ["browser-use", "code-review"]


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
