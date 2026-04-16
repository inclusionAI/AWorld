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
from aworld_cli.inner_plugins.smllc.agents.audio import audio as audio_module
from aworld_cli.inner_plugins.smllc.agents.developer import developer as developer_module
from aworld_cli.inner_plugins.smllc.agents.diffusion import diffusion as diffusion_module
from aworld_cli.inner_plugins.smllc.agents.evaluator import evaluator as evaluator_module
from aworld_cli.inner_plugins.smllc.agents.image import image as image_module


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


@pytest.mark.parametrize(
    ("module", "builder_name", "agent_name", "agent_class_name", "pass_sandbox"),
    [
        (developer_module, "build_developer_swarm", "developer", "DeveloperAgent", True),
        (evaluator_module, "build_evaluator_swarm", "evaluator", "MultiTaskEvaluatorAgent", False),
        (audio_module, "build_audio_swarm", "audio_generator", "AudioCreatorAgent", False),
        (image_module, "build_image_swarm", "image_generator", "ImageCreatorAgent", False),
        (diffusion_module, "build_diffusion_swarm", "video_diffusion", "MultiTaskVideoCreatorAgent", False),
    ],
)
def test_smllc_builders_pass_agent_name_to_skill_collection(
    monkeypatch: pytest.MonkeyPatch,
    module: object,
    builder_name: str,
    agent_name: str,
    agent_class_name: str,
    pass_sandbox: bool,
) -> None:
    captured: dict[str, str | None] = {}

    def _fake_collect(
        plugin_base_dir: Path,
        user_dir: Path | str | None = None,
        agent_name: str | None = None,
    ) -> dict[str, object]:
        captured["agent_name"] = agent_name
        return {}

    class _DummyAgent:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

    class _DummySandbox:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.reuse = False

    class _DummySwarm:
        def __init__(self, agent: object) -> None:
            self.agent = agent

    monkeypatch.setattr(module, "collect_plugin_and_user_skills", _fake_collect)
    monkeypatch.setattr(module, agent_class_name, _DummyAgent)
    monkeypatch.setattr(module, "Sandbox", _DummySandbox)
    monkeypatch.setattr(module, "Swarm", _DummySwarm)

    builder = getattr(module, builder_name)
    if pass_sandbox:
        builder(sandbox=_DummySandbox())
    else:
        builder()

    assert captured["agent_name"] == agent_name
