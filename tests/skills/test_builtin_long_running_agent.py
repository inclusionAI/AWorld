from __future__ import annotations

from pathlib import Path

from aworld_cli.core.builtin_skills import (
    AWORLD_DEFAULT_SKILL_NAMES,
    get_builtin_skills_path,
)
from aworld_cli.core.skill_activation_resolver import (
    SkillActivationResolver,
    SkillResolverRequest,
)
from aworld.skills.filesystem_provider import FilesystemSkillProvider


def test_long_running_agent_is_bundled_and_enabled_by_agent_defaults() -> None:
    root = get_builtin_skills_path()
    skill = root / "long-running-agent" / "SKILL.md"
    assert skill.is_file()

    result = SkillActivationResolver().resolve(
        SkillResolverRequest(
            plugin_roots=(),
            runtime_scope="session",
            task_text="Handle the request",
            default_skill_names=AWORLD_DEFAULT_SKILL_NAMES,
        )
    )

    assert "long-running-agent" in result.active_skill_names
    assert result.skill_configs["long-running-agent"]["active"] is True


def test_long_running_agent_uses_normal_persisted_disable_surface() -> None:
    result = SkillActivationResolver().resolve(
        SkillResolverRequest(
            plugin_roots=(),
            runtime_scope="session",
            task_text="Handle the request",
            default_skill_names=AWORLD_DEFAULT_SKILL_NAMES,
            disabled_skill_names=("long-running-agent",),
        )
    )

    assert "long-running-agent" not in result.available_skill_names
    assert "long-running-agent" not in result.active_skill_names


def test_long_running_agent_skill_is_domain_and_benchmark_independent() -> None:
    skill_root = get_builtin_skills_path() / "long-running-agent"
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(skill_root.rglob("*.md"))
    ).lower()

    assert "terminal-bench" not in text
    assert "case id" not in text
    assert "hidden verifier" not in text
    assert "workbench" not in text
    assert "rolling plan" in text
    assert "fail open" in text or "fail-open" in text
    assert "replace the only copy of" in text
    assert "important input or evidence" in text
    assert "described as a read may still trigger" in text
    assert Path(skill_root / "references" / "execution-semantics.md").is_file()


def test_long_running_agent_stages_its_referenced_guidance() -> None:
    provider = FilesystemSkillProvider("builtin-test", get_builtin_skills_path())
    descriptor = next(
        item
        for item in provider.list_descriptors()
        if item.skill_name == "long-running-agent"
    )

    assert set(descriptor.execution_assets["relative_paths"]) == {
        "references/checkpoints-and-review.md",
        "references/execution-semantics.md",
    }


def test_builtin_skill_requests_profile_only_with_a_real_tool_call() -> None:
    skill = (get_builtin_skills_path() / "long-running-agent" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "__aworld_execution_profile" in skill
    assert "same real tool call" in skill
    assert "Do not make a separate Tool call" in skill
