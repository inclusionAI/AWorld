from pathlib import Path

from aworld.utils.skill_loader import collect_skill_docs
from aworld_cli.core.skill_registry import resolve_repo_aworld_skills_path


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
