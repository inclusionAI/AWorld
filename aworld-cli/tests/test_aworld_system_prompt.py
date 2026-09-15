from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from aworld_cli.builtin_agents.smllc.agents import aworld_agent
from aworld_cli.builtin_agents.smllc.agents.aworld_agent import (
    _aworld_root_tool_policy,
    render_aworld_system_prompt,
    resolve_aworld_builtin_subagents,
    resolve_aworld_max_completion_tokens,
    resolve_aworld_max_loop_steps,
    resolve_aworld_tool_surface_profile,
)
from aworld.core.tool.surface import ToolLifecycle


def test_render_aworld_system_prompt_injects_beijing_datetime() -> None:
    prompt = render_aworld_system_prompt(
        now=datetime(2026, 5, 10, 1, 8, 7, tzinfo=ZoneInfo("UTC"))
    )

    assert "{{current_date}}" not in prompt
    assert "{{current_datetime}}" not in prompt
    assert "Today is 2026-05-10, 2026-05-10 09:08:07" in prompt
    assert "(Beijing time)" in prompt


def test_render_aworld_system_prompt_uses_runtime_capabilities() -> None:
    prompt = render_aworld_system_prompt(
        available_tools=["terminal", "cron"],
        available_subagents=["developer"],
    )

    assert "Configured tool capabilities: cron, terminal" in prompt
    assert "Available subagents: developer" in prompt
    assert "Capability labels above describe configured providers" in prompt
    assert "Use its exact listed name" in prompt
    assert "one and only one" not in prompt.lower()


def test_render_aworld_system_prompt_disables_unavailable_delegation() -> None:
    prompt = render_aworld_system_prompt(available_tools=["terminal"])

    assert "Available subagents: none" in prompt
    assert "do not attempt delegation" in prompt
    assert "developer" not in prompt


def test_aworld_max_loop_steps_defaults_to_120(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AWORLD_MAX_LOOP_STEPS", raising=False)

    assert resolve_aworld_max_loop_steps() == 120


def test_aworld_max_completion_tokens_defaults_to_16384(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AWORLD_MAX_COMPLETION_TOKENS", raising=False)

    assert resolve_aworld_max_completion_tokens() == 16384


def test_tool_surface_profile_defaults_to_general(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AWORLD_TOOL_SURFACE_PROFILE", raising=False)

    profile = resolve_aworld_tool_surface_profile()

    assert profile.profile_id == "general"
    assert set(profile.allowed_lifecycles) == set(ToolLifecycle)


def test_one_shot_profile_removes_durable_and_background_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWORLD_TOOL_SURFACE_PROFILE", "one_shot")
    monkeypatch.setattr(aworld_agent, "_CAST_TOOLS_AVAILABLE", False)

    profile = resolve_aworld_tool_surface_profile()
    tool_names, black_actions = _aworld_root_tool_policy(
        profile,
        has_subagents=True,
    )

    assert profile.allowed_lifecycles == (ToolLifecycle.IMMEDIATE,)
    assert "cron" not in tool_names
    assert "async_spawn_subagent" in tool_names
    assert black_actions["async_spawn_subagent"] == [
        "spawn_background",
        "check_task",
        "wait_task",
        "cancel_task",
    ]


@pytest.mark.parametrize("value", ["benchmark", "local", "invalid"])
def test_tool_surface_profile_rejects_implicit_or_unknown_modes(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("AWORLD_TOOL_SURFACE_PROFILE", value)

    with pytest.raises(ValueError, match="general.*one_shot"):
        resolve_aworld_tool_surface_profile()


def test_builtin_subagent_allowlist_is_explicit_and_ordered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWORLD_BUILTIN_SUBAGENTS", "image,developer,image")

    assert resolve_aworld_builtin_subagents() == ("developer", "image")


def test_builtin_subagents_can_be_disabled_for_one_shot_runners(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWORLD_BUILTIN_SUBAGENTS", "none")

    assert resolve_aworld_builtin_subagents() == ()


def test_builtin_subagent_allowlist_rejects_unknown_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWORLD_BUILTIN_SUBAGENTS", "developer,unknown")

    with pytest.raises(ValueError, match="unknown names: unknown"):
        resolve_aworld_builtin_subagents()


@pytest.mark.parametrize("value", ["0", "-1", "invalid"])
def test_aworld_max_completion_tokens_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("AWORLD_MAX_COMPLETION_TOKENS", value)

    with pytest.raises(ValueError, match="positive integer"):
        resolve_aworld_max_completion_tokens()


def test_aworld_max_completion_tokens_cannot_exceed_hard_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWORLD_MAX_COMPLETION_TOKENS", "64001")

    with pytest.raises(ValueError, match="hard limit of 64000"):
        resolve_aworld_max_completion_tokens()


@pytest.mark.parametrize("value", ["0", "-1", "invalid"])
def test_aworld_max_loop_steps_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("AWORLD_MAX_LOOP_STEPS", value)

    with pytest.raises(ValueError, match="positive integer"):
        resolve_aworld_max_loop_steps()


def test_aworld_max_loop_steps_cannot_exceed_hard_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWORLD_MAX_LOOP_STEPS", "241")

    with pytest.raises(ValueError, match="hard limit of 240"):
        resolve_aworld_max_loop_steps()


def test_optional_subagent_failures_do_not_hide_healthy_subagents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeAgent:
        def __init__(self, name: str) -> None:
            self._name = name

        def name(self) -> str:
            return self._name

    seen_sandboxes = []

    def successful_builder(name: str):
        def build(*, sandbox):
            seen_sandboxes.append(sandbox)
            return name

        return build

    def failing_builder(*, sandbox):
        seen_sandboxes.append(sandbox)
        raise RuntimeError("not configured")

    monkeypatch.setattr(aworld_agent, "_CAST_TOOLS_AVAILABLE", False)
    monkeypatch.setattr(
        aworld_agent,
        "extract_agents_from_swarm",
        lambda swarm: [FakeAgent(swarm)],
    )
    monkeypatch.setattr(
        aworld_agent,
        "build_diffusion_swarm",
        successful_builder("diffusion"),
    )
    monkeypatch.setattr(
        aworld_agent,
        "build_avatar_swarm",
        failing_builder,
    )
    monkeypatch.setattr(
        aworld_agent,
        "build_audio_swarm",
        successful_builder("audio"),
    )
    monkeypatch.setattr(
        aworld_agent,
        "build_image_swarm",
        successful_builder("image"),
    )

    shared_sandbox = object()
    agents = aworld_agent._build_aworld_sub_agents(sandbox=shared_sandbox)

    assert aworld_agent._subagent_names(agents) == ["audio", "diffusion", "image"]
    assert seen_sandboxes == [shared_sandbox] * 4
