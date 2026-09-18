from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from aworld_cli.core.builtin_skills import AWORLD_DEFAULT_SKILL_NAMES, get_builtin_skills_path
from aworld_cli.core.runtime_skill_registry import build_runtime_skill_registry_view
from aworld_cli.core.skill_activation_resolver import SkillActivationResolver, SkillResolverRequest
from aworld_cli.core.skill_state_manager import SkillStateManager


def test_runtime_registry_includes_builtin_filex_without_configured_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "aworld_cli.core.runtime_skill_registry.PluginManager",
        lambda: SimpleNamespace(get_runtime_plugin_roots=lambda: []),
    )
    monkeypatch.setattr(
        "aworld_cli.core.runtime_skill_registry.get_user_skills_paths", lambda: []
    )
    view = build_runtime_skill_registry_view(cwd=tmp_path)
    skills = view.get_all_skills()

    assert set(skills) == {"filex"}
    assert skills["filex"]["default_enabled"] is True
    assert skills["filex"]["active"] is False
    assert Path(skills["filex"]["asset_root"]) == get_builtin_skills_path() / "filex"
    assert "scripts/filex.py" in skills["filex"]["execution_assets"]["relative_paths"]
    assert str(get_builtin_skills_path()) in view.source_paths


@pytest.mark.parametrize(
    "task",
    [
        "Summarize /workspace/input/report.pdf",
        "Read the attached document and extract its tables",
        "Extract text from the scanned invoice",
        "What does the attached spreadsheet contain?",
        "Read /root/workspace/legacy.doc",
        "Inspect /root/workspace/recording.flac",
        "Transcribe the recording at /root/workspace/interview.mp3",
        "读取文档并总结重点",
    ],
)
def test_builtin_filex_activates_for_document_tasks_without_skill_hints(task: str) -> None:
    result = SkillActivationResolver().resolve(
        SkillResolverRequest(plugin_roots=(), runtime_scope="session", task_text=task)
    )

    assert result.active_skill_names == ("filex",)
    assert result.skill_configs["filex"]["active"] is True


@pytest.mark.parametrize(
    "task",
    ["What is 2 + 2?", "Fix password validation", "Create a video", "Explain Python generators"],
)
def test_builtin_filex_is_available_but_not_forced_for_other_tasks(task: str) -> None:
    result = SkillActivationResolver().resolve(
        SkillResolverRequest(plugin_roots=(), runtime_scope="session", task_text=task)
    )

    assert "filex" in result.available_skill_names
    assert result.active_skill_names == ()
    assert result.skill_configs["filex"]["active"] is False


def test_builtin_filex_respects_persisted_user_disable(tmp_path: Path) -> None:
    state = SkillStateManager(tmp_path / "skill-state.json")
    state.disable_skill("filex")
    request = dict(
        plugin_roots=(),
        runtime_scope="session",
        task_text="Read report.pdf",
        disabled_skill_names=state.disabled_skill_names(),
    )
    result = SkillActivationResolver().resolve(SkillResolverRequest(**request))

    assert "filex" not in result.available_skill_names
    assert result.active_skill_names == ()
    with pytest.raises(ValueError, match="Requested skill is not available: filex"):
        SkillActivationResolver().resolve(
            SkillResolverRequest(**request, requested_skill_names=("filex",))
        )


def test_user_filex_definition_takes_precedence_over_builtin(tmp_path: Path) -> None:
    skill_dir = tmp_path / "filex"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: filex\ndescription: User FileX\ndefault_enabled: false\n---\nUser instructions\n",
        encoding="utf-8",
    )
    result = SkillActivationResolver().resolve(
        SkillResolverRequest(
            plugin_roots=(),
            runtime_scope="session",
            task_text="Read report.pdf",
            compatibility_sources=(str(tmp_path),),
        )
    )

    assert "filex" not in result.available_skill_names


def test_builtin_filex_is_independent_of_compatibility_skill_patterns() -> None:
    result = SkillActivationResolver().resolve(
        SkillResolverRequest(
            plugin_roots=(),
            runtime_scope="session",
            task_text="Read report.pdf",
            compatibility_skill_patterns=("browser-use",),
        )
    )

    assert result.active_skill_names == ("filex",)


def test_local_executor_resolves_builtin_filex_without_agent_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aworld_cli.executors.local import LocalAgentExecutor

    monkeypatch.setattr(
        "aworld_cli.executors.local.PluginManager",
        lambda: SimpleNamespace(
            get_runtime_plugin_roots=lambda: [],
            list_skill_packages=lambda **kwargs: [],
        ),
    )
    monkeypatch.setattr(
        "aworld_cli.core.skill_state_manager.SkillStateManager",
        lambda: SkillStateManager(tmp_path / "skill-state.json"),
    )
    agent = SimpleNamespace(name="Aworld", conf=SimpleNamespace(ext={}, skill_configs={}))
    executor = LocalAgentExecutor.__new__(LocalAgentExecutor)
    executor.swarm = SimpleNamespace(ordered_agents=[agent])

    executor._resolve_swarm_skills(
        SimpleNamespace(task_content="Summarize report.pdf", metadata={})
    )

    assert agent.conf.skill_configs["filex"]["active"] is True
    assert "/skills/filex/scripts/filex.py" in agent.conf.skill_configs["filex"]["usage"]


def test_builtin_manifest_declares_the_canonical_source_and_assets() -> None:
    from aworld_cli.core import builtin_skills

    manifest = json.loads(builtin_skills._MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "aworld.builtin-skills/v1"
    entry = manifest["skills"]["filex"]
    assert entry["source"] == "aworld-skills/filex"
    root = get_builtin_skills_path()
    assert (root / entry["skill_file"]).is_file()
    assert all((root / relative).is_file() for relative in entry["execution_assets"])


def test_aworld_agent_enables_filex_without_task_or_gateway_skill_hints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aworld_cli.builtin_agents.smllc.agents import aworld_agent
    from aworld_cli.executors.local import LocalAgentExecutor

    monkeypatch.setattr(aworld_agent, "_build_aworld_sub_agents", lambda *a, **kw: [])
    monkeypatch.setattr(
        "aworld_cli.executors.local.PluginManager",
        lambda: SimpleNamespace(
            get_runtime_plugin_roots=lambda: [],
            list_skill_packages=lambda **kwargs: [],
        ),
    )
    state = SkillStateManager(tmp_path / "skill-state.json")
    monkeypatch.setattr("aworld_cli.core.skill_state_manager.SkillStateManager", lambda: state)
    monkeypatch.setenv("AWORLD_SKILLS_PATH", str(tmp_path))
    swarm = aworld_agent.build_aworld_agent()
    executor = LocalAgentExecutor.__new__(LocalAgentExecutor)
    executor.swarm = swarm
    root = executor._iter_swarm_agents()[0]
    assert root.conf.ext["skill_resolver_inputs"]["default_skill_names"] == ["filex"]

    task = SimpleNamespace(task_content="Proceed with the task", metadata={})
    executor._resolve_swarm_skills(task)

    assert root.skill_configs["filex"]["active"] is True
    assert root.conf.skill_configs == root.skill_configs
    assert "--layout-format parse-output" in root.skill_configs["filex"]["usage"]

    state.disable_skill("filex")
    executor._resolve_swarm_skills(task)
    assert "filex" not in root.skill_configs


@pytest.mark.parametrize("requested", [(), ("browser-use",)])
def test_aworld_defaults_preserve_automatic_and_explicit_skill_selection(
    tmp_path: Path, requested: tuple[str, ...]
) -> None:
    skill = tmp_path / "browser-use"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: browser-use\ndescription: Browse sites\n"
        "metadata:\n  match_keywords: [browser]\n---\nBrowse the site.\n",
        encoding="utf-8",
    )
    result = SkillActivationResolver().resolve(SkillResolverRequest(
        plugin_roots=(), runtime_scope="session", task_text="Open the browser",
        default_skill_names=AWORLD_DEFAULT_SKILL_NAMES,
        requested_skill_names=requested,
        compatibility_sources=(str(tmp_path),),
    ))

    assert result.active_skill_names == (requested or ("filex", "browser-use"))


def test_agent_defaults_do_not_override_user_skill_definition(tmp_path: Path) -> None:
    skill = tmp_path / "filex"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: filex\ndescription: Custom FileX\ndefault_enabled: false\n---\nUsage\n",
        encoding="utf-8",
    )
    result = SkillActivationResolver().resolve(SkillResolverRequest(
        plugin_roots=(), runtime_scope="session", task_text="Read report.pdf",
        default_skill_names=AWORLD_DEFAULT_SKILL_NAMES,
        compatibility_sources=(str(tmp_path),),
    ))

    assert result.active_skill_names == ()
