import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld_cli.core.installed_skill_manager import InstalledSkillManager
from aworld_cli.core.skill_registry import (
    collect_plugin_and_user_skills,
    get_skill_registry,
    reset_skill_registry,
)
from aworld_cli.builtin_agents.smllc.agents.audio import audio as audio_module
from aworld_cli.builtin_agents.smllc.agents.developer import developer as developer_module
from aworld_cli.builtin_agents.smllc.agents.diffusion import diffusion as diffusion_module
from aworld_cli.builtin_agents.smllc.agents.evaluator import evaluator as evaluator_module
from aworld_cli.builtin_agents.smllc.agents.image import image as image_module


def _write_skill(root: Path, skill_name: str, description: str) -> None:
    skill_dir = root / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        (
            f"---\nname: {skill_name}\ndescription: {description}\n---\n\n"
            f"# {skill_name}\n"
        ),
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    reset_skill_registry()
    yield
    reset_skill_registry()


def test_get_skill_registry_auto_registers_installed_global_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SKILLS_PATH", raising=False)
    monkeypatch.delenv("SKILLS_DIR", raising=False)
    monkeypatch.delenv("SKILLS_CACHE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    manager = InstalledSkillManager()
    global_entry = manager.installed_root / "global-skill-pack"
    project_entry = manager.installed_root / "project-skill-pack"
    _write_skill(global_entry / "skills", "global-only", "global installed version")
    _write_skill(project_entry / "skills", "project-only", "project installed version")
    manager.import_entry(global_entry, scope="global")
    manager.import_entry(project_entry, scope="project")

    registry = get_skill_registry()
    all_skills = registry.get_all_skills()

    assert "global-only" in all_skills
    assert all_skills["global-only"]["description"] == "global installed version"
    assert "project-only" not in all_skills


def test_explicit_skill_path_overrides_installed_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SKILLS_PATH", raising=False)
    monkeypatch.delenv("SKILLS_DIR", raising=False)
    monkeypatch.delenv("SKILLS_CACHE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    manager = InstalledSkillManager()
    installed_entry = manager.installed_root / "installed-shared-pack"
    _write_skill(installed_entry / "skills", "shared-skill", "installed version")
    manager.import_entry(installed_entry, scope="global")

    explicit_source = tmp_path / "explicit-skills"
    _write_skill(explicit_source, "shared-skill", "explicit version")

    registry = get_skill_registry(skill_paths=[str(explicit_source)])
    shared_skill = registry.get_skill("shared-skill")

    assert shared_skill is not None
    assert shared_skill["description"] == "explicit version"


def test_collect_plugin_and_user_skills_merges_global_and_matching_agent_installs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SKILLS_PATH", raising=False)
    monkeypatch.delenv("SKILLS_DIR", raising=False)
    monkeypatch.delenv("SKILLS_CACHE_DIR", raising=False)

    plugin_root = tmp_path / "plugin"
    _write_skill(plugin_root / "skills", "plugin-only", "plugin version")

    manager = InstalledSkillManager()
    global_entry = manager.installed_root / "global-pack"
    matching_agent_entry = manager.installed_root / "developer-pack"
    other_agent_entry = manager.installed_root / "evaluator-pack"
    project_entry = manager.installed_root / "project-pack"
    _write_skill(global_entry / "skills", "global-only", "global version")
    _write_skill(matching_agent_entry / "skills", "developer-only", "developer version")
    _write_skill(other_agent_entry / "skills", "evaluator-only", "evaluator version")
    _write_skill(project_entry / "skills", "project-only", "project version")
    manager.import_entry(global_entry, scope="global")
    manager.import_entry(matching_agent_entry, scope="agent:developer")
    manager.import_entry(other_agent_entry, scope="agent:evaluator")
    manager.import_entry(project_entry, scope="project")

    skills = collect_plugin_and_user_skills(plugin_root, agent_name="developer")

    assert skills["plugin-only"]["description"] == "plugin version"
    assert skills["global-only"]["description"] == "global version"
    assert skills["developer-only"]["description"] == "developer version"
    assert "evaluator-only" not in skills
    assert "project-only" not in skills


def test_collect_plugin_and_user_skills_uses_install_id_order_for_duplicate_installed_skills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SKILLS_PATH", raising=False)
    monkeypatch.delenv("SKILLS_DIR", raising=False)
    monkeypatch.delenv("SKILLS_CACHE_DIR", raising=False)

    plugin_root = tmp_path / "plugin"
    (plugin_root / "skills").mkdir(parents=True, exist_ok=True)

    manager = InstalledSkillManager()
    z_entry = manager.installed_root / "z-pack"
    a_entry = manager.installed_root / "a-pack"
    _write_skill(z_entry / "skills", "shared-skill", "z version")
    _write_skill(a_entry / "skills", "shared-skill", "a version")
    manager.import_entry(z_entry, scope="global")
    manager.import_entry(a_entry, scope="global")

    skills = collect_plugin_and_user_skills(plugin_root, agent_name="developer")

    assert skills["shared-skill"]["description"] == "a version"


def test_disabled_installed_skill_package_is_excluded_from_runtime_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SKILLS_PATH", raising=False)
    monkeypatch.delenv("SKILLS_DIR", raising=False)
    monkeypatch.delenv("SKILLS_CACHE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    manager = InstalledSkillManager()
    install_entry = manager.installed_root / "disabled-pack"
    _write_skill(install_entry / "skills", "disabled-skill", "disabled installed version")
    manager.import_entry(install_entry, scope="global")
    manager.plugin_manager.disable("disabled-pack")

    all_installs = manager.list_installs()
    enabled_installs = manager.list_installs(include_disabled=False)
    assert len(all_installs) == 1
    assert all_installs[0]["install_id"] == "disabled-pack"
    assert all_installs[0]["enabled"] is False
    assert enabled_installs == []

    plugin_root = tmp_path / "plugin"
    (plugin_root / "skills").mkdir(parents=True, exist_ok=True)
    collected = collect_plugin_and_user_skills(plugin_root)
    assert "disabled-skill" not in collected

    registry = get_skill_registry()
    assert registry.get_skill("disabled-skill") is None


@pytest.mark.parametrize(
    ("module", "builder_name", "env_name", "env_value", "agent_class_name", "pass_sandbox"),
    [
        (developer_module, "build_developer_swarm", "DEVELOPER_SKILLS_PATH", "/tmp/dev-skills", "DeveloperAgent", True),
        (evaluator_module, "build_evaluator_swarm", "EVALUATOR_SKILLS_PATH", "/tmp/evaluator-skills", "MultiTaskEvaluatorAgent", False),
        (audio_module, "build_audio_swarm", "SKILLS_PATH", "/tmp/audio-skills", "AudioCreatorAgent", False),
        (image_module, "build_image_swarm", "SKILLS_PATH", "/tmp/image-skills", "ImageCreatorAgent", False),
        (diffusion_module, "build_diffusion_swarm", "SKILLS_PATH", "/tmp/diffusion-skills", "MultiTaskVideoCreatorAgent", False),
    ],
)
def test_smllc_builders_publish_skill_resolver_inputs(
    monkeypatch: pytest.MonkeyPatch,
    module: object,
    builder_name: str,
    env_name: str,
    env_value: str,
    agent_class_name: str,
    pass_sandbox: bool,
) -> None:
    class _DummyAgent:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.conf = kwargs["conf"]

    class _DummySandbox:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.reuse = False

    class _DummySwarm:
        def __init__(self, agent: object) -> None:
            self.ordered_agents = [agent]

    monkeypatch.setenv(env_name, env_value)
    monkeypatch.setattr(module, agent_class_name, _DummyAgent)
    monkeypatch.setattr(module, "Sandbox", _DummySandbox)
    monkeypatch.setattr(module, "Swarm", _DummySwarm)

    builder = getattr(module, builder_name)
    if pass_sandbox:
        swarm = builder(sandbox=_DummySandbox())
    else:
        swarm = builder()

    agent = swarm.ordered_agents[0]
    assert agent.conf.skill_configs == {}
    resolver_inputs = agent.conf.ext["skill_resolver_inputs"]
    assert resolver_inputs["plugin_roots"]
    assert resolver_inputs["compatibility_sources"] == [str(Path(env_value).expanduser().resolve())]
