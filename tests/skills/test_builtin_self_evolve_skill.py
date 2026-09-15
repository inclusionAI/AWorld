from pathlib import Path

from aworld.utils.skill_loader import collect_skill_docs
from aworld_cli.core.skill_registry import resolve_repo_aworld_skills_path
from aworld_cli.core.skill_activation_resolver import (
    SkillActivationResolver,
    SkillResolverRequest,
)


DEFAULT_RUNTIME_SKILLS = {
    "agent-browser",
    "filex",
    "media_comprehension",
}


def test_builtin_self_evolve_skill_is_discoverable_and_operational() -> None:
    skills_root = resolve_repo_aworld_skills_path()
    assert skills_root is not None

    skill_path = Path(skills_root) / "self_evolve" / "SKILL.md"
    skill_text = skill_path.read_text(encoding="utf-8")
    docs = collect_skill_docs(skills_root)
    skill = docs["self_evolve"]

    assert "name: self_evolve" in skill_text
    assert "framework-gated self-evolve" in skill["description"]
    assert "aworld-cli optimize" in skill["description"]
    assert "optimizer" not in skill["description"].lower().split()
    assert "aworld.self_evolve" in skill["usage"]
    assert "Do not bypass" in skill["usage"]
    assert "Available" in skill["usage"]
    assert "Roadmap" in skill["usage"]
    assert "CLI fallback" in skill["usage"]
    assert "aworld-cli optimize" in skill["usage"]
    assert "CandidateOptimizer" in skill["usage"]
    assert "references/plan.md" in skill["usage"]
    assert "Tool descriptions - Roadmap" in skill["usage"]
    assert "Prompt sections - Roadmap" in skill["usage"]
    assert "Agent config - Roadmap" in skill["usage"]


def test_builtin_self_evolve_plan_reference_exists_and_defines_boundaries() -> None:
    skills_root = resolve_repo_aworld_skills_path()
    assert skills_root is not None

    plan_path = Path(skills_root) / "self_evolve" / "references" / "plan.md"
    plan = plan_path.read_text(encoding="utf-8")

    assert "## Vision" in plan
    assert "## What Can Be Improved" in plan
    assert "## Architecture" in plan
    assert "## Optimization Loop" in plan
    assert "## AWorld Integration Points" in plan
    assert "## Safety Gates" in plan
    assert "## Invocation Forms" in plan
    assert "## Phases" in plan
    assert "## Non-goals" in plan
    assert "Available" in plan
    assert "Conditional" in plan
    assert "Roadmap" in plan


def test_builtin_self_evolve_skill_defines_runtime_only_candidate_boundary() -> None:
    skills_root = resolve_repo_aworld_skills_path()
    assert skills_root is not None

    skill_path = Path(skills_root) / "self_evolve" / "SKILL.md"
    skill_text = skill_path.read_text(encoding="utf-8")

    assert "Target skills express task behavior only" in skill_text
    assert "must not encode" in skill_text
    assert "self-evolve framework control flow" in skill_text
    assert "released `SKILL.md` should contain only runtime" in skill_text
    assert "Internal self-evolve context belongs in report artifacts" in skill_text


def test_repo_skill_catalog_exposes_only_general_skills_by_default() -> None:
    skills_root = resolve_repo_aworld_skills_path()
    assert skills_root is not None

    resolver = SkillActivationResolver()
    default_view = resolver.resolve(
        SkillResolverRequest(
            plugin_roots=(),
            runtime_scope="session",
            compatibility_sources=(str(skills_root),),
        )
    )
    management_view = resolver.resolve(
        SkillResolverRequest(
            plugin_roots=(),
            runtime_scope="session",
            compatibility_sources=(str(skills_root),),
            include_default_disabled=True,
        )
    )

    assert set(default_view.available_skill_names) == DEFAULT_RUNTIME_SKILLS
    assert len(management_view.available_skill_names) == 17
    assert {
        name
        for name, config in management_view.skill_configs.items()
        if config.get("default_enabled", True) is False
    } == set(management_view.available_skill_names) - DEFAULT_RUNTIME_SKILLS
