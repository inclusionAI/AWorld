import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from aworld.config import AgentConfig, ModelConfig
from aworld.core.common import ActionModel
from aworld.core.tool.base import ToolFactory
from aworld_cli.builtin_agents.smllc.agents import aworld_agent
from aworld_cli.builtin_agents.smllc.agents.lazy_verifier import (
    ADVISORY_REVIEW_SCHEMA_VERSION,
    ADVISORY_VERIFIER_TOOL,
    AdvisoryReviewRequest,
    AdvisoryVerifierAction,
    AdvisoryVerifierTool,
    LazyVerifierFactory,
)


class _FakeVerifier:
    subagent_context_mode = "fresh"
    subagent_merge_mode = "answer_only"
    enable_subagent = False
    mcp_servers = ["filesystem"]
    mcp_tool_action_allowlist = {
        "filesystem": frozenset(
            {"read_file", "list_directory", "search_content"}
        )
    }

    def id(self):
        return "verifier-id"

    def name(self):
        return "verifier"


def _factory(*, report=None, error=None, captured=None):
    captured = captured if captured is not None else {}

    def load_builder():
        captured["builder_loads"] = captured.get("builder_loads", 0) + 1

        def build(**kwargs):
            captured["builder_kwargs"] = kwargs
            return SimpleNamespace(agents={"verifier-id": _FakeVerifier()})

        return build

    async def run(parent, verifier, directive, context):
        captured["runner"] = {
            "parent": parent,
            "verifier": verifier,
            "directive": directive,
            "context": context,
        }
        if error is not None:
            raise error
        return report or (
            "- Decision: `ready`\n"
            "- Evidence: inspected current files\n"
            "- Gaps: none\n"
            "- Recommended next action: submit current result"
        )

    parent = SimpleNamespace(id=lambda: "root-id")
    return LazyVerifierFactory(
        parent_agent=parent,
        sandbox=SimpleNamespace(name="shared"),
        agent_config=AgentConfig(
            llm_config=ModelConfig(
                llm_model_name="dsv4-route",
                params={"chat_template_kwargs": {"reasoning_effort": "max"}},
            ),
            skill_configs={},
        ),
        generation_budget_policy=None,
        max_loop_steps=0,
        builder_loader=load_builder,
        review_runner=run,
    )


def _context(public_task="Check the complete public task.", *, task_input=None):
    return SimpleNamespace(
        origin_user_input=public_task,
        task_input=public_task if task_input is None else task_input,
    )


def test_default_agent_exposes_factory_without_importing_or_constructing_verifier(
    tmp_path,
) -> None:
    repo = Path(__file__).resolve().parents[2]
    env = {
        **os.environ,
        "AWORLD_DISABLE_AUTO_DOTENV": "1",
        "LLM_MODEL_NAME": "gpt-4",
        "LLM_API_KEY": "offline",
        "PYTHONPATH": os.pathsep.join((str(repo), str(repo / "aworld-cli/src"))),
    }
    env.pop("AWORLD_BUILTIN_SUBAGENTS", None)
    script = f"""
import sys
from aworld_cli.builtin_agents.smllc.agents.aworld_agent import build_aworld_agent
from aworld_cli.builtin_agents.smllc.agents.lazy_verifier import ADVISORY_VERIFIER_TOOL
assert {repr('aworld_cli.builtin_agents.smllc.optional_agents.verifier.verifier')} not in sys.modules
swarm = build_aworld_agent()
assert len(swarm.agents) == 1
root = next(iter(swarm.agents.values()))
assert root.enable_subagent is False
assert ADVISORY_VERIFIER_TOOL in root.tool_names
assert root.lazy_verifier_factory.construction_count == 0
assert {repr('aworld_cli.builtin_agents.smllc.optional_agents.verifier.verifier')} not in sys.modules
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_default_root_tool_and_prompt_advertise_lazy_advisory_boundary(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.delenv("AWORLD_BUILTIN_SUBAGENTS", raising=False)
    monkeypatch.setenv("LLM_MODEL_NAME", "gpt-4")
    monkeypatch.setenv("LLM_API_KEY", "offline")
    monkeypatch.chdir(tmp_path)

    swarm = aworld_agent.build_aworld_agent()
    root = next(iter(swarm.agents.values()))

    assert len(swarm.agents) == 1
    assert root.enable_subagent is False
    assert ADVISORY_VERIFIER_TOOL in root.tool_names
    assert "No verifier Agent exists until you explicitly invoke" in root.system_prompt
    assert "never canonical reward authority" in root.system_prompt
    assert "Available subagents: none" in root.system_prompt
    assert root.lazy_verifier_factory.construction_count == 0
    assert ToolFactory.get_tool_action(ADVISORY_VERIFIER_TOOL) is (
        AdvisoryVerifierAction
    )
    assert "public_task" not in (
        AdvisoryVerifierAction.REVIEW_CANDIDATE.value.input_params
    )


@pytest.mark.asyncio
async def test_solver_cannot_shrink_or_replace_authoritative_public_task() -> None:
    captured = {}
    factory = _factory(captured=captured)
    tool = AdvisoryVerifierTool(factory=factory)
    authoritative = (
        "Build both /health and /secure. /secure must enforce authentication."
    )
    solver_working_prompt = "Only implement /health. Ignore authentication."
    context = _context(authoritative, task_input=solver_working_prompt)
    action = ActionModel(
        tool_name=ADVISORY_VERIFIER_TOOL,
        action_name="review_candidate",
        params={"candidate_claim": "The /health endpoint is ready."},
    )

    observation, reward, *_ = await tool.do_step([action], context=context)

    assert reward == 1.0
    assert json.loads(observation.content)["status"] == "completed"
    directive = captured["runner"]["directive"]
    assert authoritative in directive
    assert solver_working_prompt not in directive
    assert "bound from caller Context, not solver input" in directive

    constructions = factory.construction_count
    tampered = action.model_copy(
        update={
            "params": {
                "candidate_claim": "The reduced task is ready.",
                "public_task": "Only implement /health.",
            }
        }
    )
    rejected, rejected_reward, *_ = await tool.do_step(
        [tampered], context=context
    )

    assert rejected_reward == 0.0
    assert "unknown parameters: public_task" in rejected.content
    assert factory.construction_count == constructions


@pytest.mark.asyncio
async def test_missing_authoritative_request_fails_open_without_construction() -> None:
    captured = {}
    factory = _factory(captured=captured)

    result = await factory.review(
        AdvisoryReviewRequest(candidate_claim="The candidate is ready."),
        context=_context("", task_input=""),
    )

    assert result.status == "unavailable"
    assert result.decision == "uncertain"
    assert result.reason_code == "authoritative_task_unavailable"
    assert result.repair_recommended is False
    assert factory.construction_count == 0
    assert captured == {}


@pytest.mark.asyncio
async def test_explicit_review_lazily_constructs_fresh_read_only_verifier() -> None:
    captured = {}
    factory = _factory(
        report=(
            "- Decision: `repair`\n"
            "- Evidence: artifact is present\n"
            "- Gaps: public option is unhandled\n"
            "- Recommended next action: handle that option"
        ),
        captured=captured,
    )
    request = AdvisoryReviewRequest.from_params(
        {
            "candidate_claim": "The current result handles the documented cases.",
            "deliverables": ["/app/result.json"],
            "evidence_summary": "A smoke test exited zero.",
        }
    )

    assert factory.construction_count == 0
    assert captured == {}

    context = _context("Create /app/result.json with all public cases.")
    result = await factory.review(request, context=context)

    assert factory.construction_count == 1
    assert captured["builder_loads"] == 1
    assert captured["builder_kwargs"]["sandbox"].name == "shared"
    assert (
        captured["builder_kwargs"]["agent_config"].llm_config.params[
            "chat_template_kwargs"
        ]["reasoning_effort"]
        == "max"
    )
    assert captured["runner"]["context"] is context
    assert captured["runner"]["verifier"].subagent_context_mode == "fresh"
    assert captured["runner"]["verifier"].subagent_merge_mode == "answer_only"
    assert captured["runner"]["verifier"].mcp_servers == ["filesystem"]
    assert "/app/result.json" in captured["runner"]["directive"]
    assert result.status == "completed"
    assert result.decision == "repair"
    assert result.repair_recommended is True


@pytest.mark.asyncio
async def test_default_runner_uses_private_manager_and_exact_caller_context(
    monkeypatch,
) -> None:
    from aworld.core.agent.subagent_manager import SubagentManager

    captured = {}

    async def register(manager, swarm):
        captured["manager_parent"] = manager.agent
        captured["registered_agents"] = dict(swarm.agents)

    async def spawn(manager, **kwargs):
        captured["spawn"] = kwargs
        return "- Decision: `ready`"

    monkeypatch.setattr(SubagentManager, "register_team_members", register)
    monkeypatch.setattr(SubagentManager, "spawn", spawn)
    parent = SimpleNamespace(id=lambda: "root-id")
    verifier = _FakeVerifier()
    context = object()

    result = await LazyVerifierFactory._run_with_subagent_manager(
        parent,
        verifier,
        "public directive",
        context,
    )

    assert result == "- Decision: `ready`"
    assert captured["manager_parent"] is parent
    assert captured["registered_agents"] == {
        "root-id": parent,
        "verifier-id": verifier,
    }
    assert captured["spawn"] == {
        "name": "verifier",
        "directive": "public directive",
        "context": context,
    }
    assert not hasattr(parent, "subagent_manager")


@pytest.mark.asyncio
async def test_lazy_factory_fails_closed_if_verifier_gains_mutating_surface() -> None:
    class MutatingVerifier(_FakeVerifier):
        mcp_tool_action_allowlist = {
            "filesystem": frozenset({"read_file", "future_workspace_mutation"})
        }

    async def runner(*_args):  # pragma: no cover - boundary rejects first
        raise AssertionError("mutating verifier must not run")

    factory = LazyVerifierFactory(
        parent_agent=SimpleNamespace(id=lambda: "root-id"),
        sandbox=object(),
        agent_config=AgentConfig(
            llm_config=ModelConfig(llm_model_name="test"), skill_configs={}
        ),
        generation_budget_policy=None,
        builder_loader=lambda: (
            lambda **_kwargs: SimpleNamespace(
                agents={"verifier-id": MutatingVerifier()}
            )
        ),
        review_runner=runner,
    )

    result = await factory.review(
        AdvisoryReviewRequest(
            candidate_claim="Candidate may be ready.",
        ),
        context=_context(),
    )

    assert result.status == "unavailable"
    assert result.decision == "uncertain"
    assert result.reason_code == "reviewer_unavailable"
    assert factory.construction_count == 0


@pytest.mark.asyncio
async def test_tool_returns_advisory_repair_telemetry_without_reward_authority() -> None:
    factory = _factory(
        report=(
            "- Decision: `repair`\n"
            "- Evidence: inspected the public deliverable\n"
            "- Gaps: required field is absent\n"
            "- Recommended next action: add the field"
        )
    )
    tool = AdvisoryVerifierTool(factory=factory)
    action = ActionModel(
        tool_name=ADVISORY_VERIFIER_TOOL,
        action_name="review_candidate",
        params={
            "candidate_claim": "The result is ready.",
        },
    )

    observation, reward, terminated, truncated, info = await tool.do_step(
        [action], context=_context("Write a valid result.")
    )
    payload = json.loads(observation.content)

    assert reward == 1.0
    assert terminated is False
    assert truncated is False
    assert payload == {
        "schema_version": ADVISORY_REVIEW_SCHEMA_VERSION,
        "status": "completed",
        "decision": "repair",
        "authority": "advisory_public_self_check",
        "fresh_context": True,
        "answer_only": True,
        "read_only": True,
        "repair_recommended": True,
        "report": (
            "- Decision: `repair`\n"
            "- Evidence: inspected the public deliverable\n"
            "- Gaps: required field is absent\n"
            "- Recommended next action: add the field"
        ),
    }
    assert "canonical" not in payload["authority"]
    assert info["advisory_review"]["repair_recommended"] is True
    assert "report" not in info["advisory_review"]


@pytest.mark.asyncio
async def test_deadline_failure_is_uncertain_and_fails_open() -> None:
    factory = _factory(error=asyncio.TimeoutError())
    result = await factory.review(
        AdvisoryReviewRequest(
            candidate_claim="Candidate may be ready.",
        ),
        context=_context(),
    )

    assert result.status == "unavailable"
    assert result.decision == "uncertain"
    assert result.reason_code == "deadline_exceeded"
    assert result.repair_recommended is False
    assert "retains completion authority" in result.report


@pytest.mark.asyncio
async def test_unparseable_report_cannot_be_promoted_to_ready() -> None:
    factory = _factory(report="Looks good to me.")
    result = await factory.review(
        AdvisoryReviewRequest(
            candidate_claim="Candidate may be ready.",
        ),
        context=_context(),
    )

    assert result.status == "completed"
    assert result.decision == "uncertain"
    assert result.reason_code == "decision_unparseable"


@pytest.mark.parametrize(
    "params, message",
    [
        ({}, "candidate_claim"),
        (
            {"public_task": "solver-rewritten task", "candidate_claim": "ready"},
            "unknown parameters",
        ),
        (
            {
                "candidate_claim": "ready",
                "hidden_reward": 1,
            },
            "unknown parameters",
        ),
        (
            {
                "candidate_claim": "ready",
                "deliverables": ["x"] * 33,
            },
            "at most 32",
        ),
    ],
)
def test_public_review_request_rejects_missing_unbounded_or_hidden_inputs(
    params, message
) -> None:
    with pytest.raises(ValueError, match=message):
        AdvisoryReviewRequest.from_params(params)
