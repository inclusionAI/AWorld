import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig, ModelConfig
from aworld.core.agent.swarm import Swarm
from aworld.core.common import ActionModel
from aworld.core.context.base import Context
from aworld.core.context.compiler import (
    ChildStatus,
    DelegationSpec,
)
from aworld.core.tool.base import ToolFactory
from aworld.core.task import Task
from aworld.core.event.base import Message
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


def _factory(
    *, report=None, error=None, captured=None, parent_id="root-id", parent=None
):
    captured = captured if captured is not None else {}

    def load_builder():
        captured["builder_loads"] = captured.get("builder_loads", 0) + 1

        def build(**kwargs):
            captured["builder_kwargs"] = kwargs
            return SimpleNamespace(agents={"verifier-id": _FakeVerifier()})

        return build

    async def run(parent, verifier, directive, context):
        await asyncio.sleep(0)
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

    parent = parent or SimpleNamespace(id=lambda: parent_id)
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


def _production_context(root, public_task, *, current_agent_id=None, agents=None):
    agent_id = root.id()
    return SimpleNamespace(
        origin_user_input=public_task,
        task_input=public_task,
        swarm=SimpleNamespace(agents=agents or {agent_id: root}),
        agent_info={"current_agent_id": current_agent_id or agent_id},
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
import asyncio
from types import SimpleNamespace
from aworld_cli.builtin_agents.smllc.agents.aworld_agent import build_aworld_agent
from aworld_cli.builtin_agents.smllc.agents.lazy_verifier import ADVISORY_VERIFIER_TOOL, AdvisoryReviewRequest
from aworld_cli.core.agent_registry import LocalAgentRegistry
assert {repr('aworld_cli.builtin_agents.smllc.optional_agents.verifier.verifier')} not in sys.modules
swarm = build_aworld_agent()
assert len(swarm.agents) == 1
root = next(iter(swarm.agents.values()))
assert root.enable_subagent is False
assert ADVISORY_VERIFIER_TOOL in root.tool_names
assert root.lazy_verifier_factory.construction_count == 0
assert {repr('aworld_cli.builtin_agents.smllc.optional_agents.verifier.verifier')} not in sys.modules
registry_before = LocalAgentRegistry.list_agent_names()
async def review_runner(*_args):
    return "- Decision: `ready`"
root.lazy_verifier_factory._review_runner = review_runner
result = asyncio.run(root.lazy_verifier_factory.review(
    AdvisoryReviewRequest(candidate_claim="candidate"),
    context=SimpleNamespace(origin_user_input="public task", task_input="public task"),
))
assert result.decision == "ready"
assert LocalAgentRegistry.list_agent_names() == registry_before
assert {repr('aworld_cli.builtin_agents.smllc.optional_agents.verifier.builder')} in sys.modules
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
async def test_production_message_context_resolves_exact_root_factory(
    monkeypatch,
) -> None:
    captured = {}
    root = Agent(
        name="root-a",
        conf=AgentConfig(
            llm_config=ModelConfig(llm_model_name="offline"), skill_configs={}
        ),
        tool_names=[],
    )
    factory = _factory(captured=captured, parent=root)
    root.lazy_verifier_factory = factory
    context = Context(task_id="production-tool-path")
    context.origin_user_input = "Authoritative task A"
    context.task_input = context.origin_user_input
    context.set_task(
        Task(
            input=context.origin_user_input,
            swarm=Swarm(root),
            context=context,
        )
    )
    context.agent_info.current_agent_id = root.id()
    message = Message(headers={"context": context})
    action = ActionModel(
        agent_name=root.id(),
        tool_name=ADVISORY_VERIFIER_TOOL,
        action_name="review_candidate",
        params={"candidate_claim": "Candidate A"},
    )
    wrong_contextvar_agent = SimpleNamespace(id=lambda: "wrong")
    monkeypatch.setattr(
        "aworld.core.agent.base.BaseAgent._get_current_agent",
        lambda: wrong_contextvar_agent,
    )

    observation, reward, *_ = await AdvisoryVerifierTool().do_step(
        [action], message=message
    )

    assert reward == 1.0
    assert json.loads(observation.content)["status"] == "completed"
    assert captured["runner"]["parent"] is root
    assert captured["runner"]["context"] is context
    assert "Authoritative task A" in captured["runner"]["directive"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action_agent_id,current_agent_id,include_second,error",
    [
        ("missing", "root-a", False, "caller_not_in_context_swarm:missing"),
        (
            "root-a",
            "root-b",
            True,
            "action_and_context_caller_mismatch",
        ),
    ],
)
async def test_production_context_rejects_wrong_or_mismatched_caller_identity(
    action_agent_id,
    current_agent_id,
    include_second,
    error,
) -> None:
    captured = {}
    factory = _factory(captured=captured, parent_id="root-a")
    root = factory.parent_agent
    root.lazy_verifier_factory = factory
    agents = {"root-a": root}
    if include_second:
        agents["root-b"] = SimpleNamespace(id=lambda: "root-b")
    context = _production_context(
        root,
        "Authoritative task",
        current_agent_id=current_agent_id,
        agents=agents,
    )
    action = ActionModel(
        agent_name=action_agent_id,
        tool_name=ADVISORY_VERIFIER_TOOL,
        action_name="review_candidate",
        params={"candidate_claim": "candidate"},
    )

    observation, reward, *_ = await AdvisoryVerifierTool().do_step(
        [action], message=SimpleNamespace(context=context)
    )

    assert reward == 0.0
    assert error in observation.content
    assert factory.construction_count == 0
    assert captured == {}


@pytest.mark.asyncio
async def test_production_context_rejects_factory_bound_to_another_root() -> None:
    factory = _factory(parent_id="root-b")
    root_a = SimpleNamespace(id=lambda: "root-a")
    root_a.lazy_verifier_factory = factory
    context = _production_context(root_a, "Authoritative task")
    action = ActionModel(
        agent_name="root-a",
        tool_name=ADVISORY_VERIFIER_TOOL,
        action_name="review_candidate",
        params={"candidate_claim": "candidate"},
    )

    observation, reward, *_ = await AdvisoryVerifierTool().do_step(
        [action], message=SimpleNamespace(context=context)
    )

    assert reward == 0.0
    assert "factory_parent_mismatch" in observation.content
    assert factory.construction_count == 0


@pytest.mark.asyncio
async def test_shared_tool_keeps_concurrent_message_contexts_isolated() -> None:
    captured_a, captured_b = {}, {}
    factory_a = _factory(captured=captured_a, parent_id="root-a")
    factory_b = _factory(captured=captured_b, parent_id="root-b")
    root_a, root_b = factory_a.parent_agent, factory_b.parent_agent
    root_a.lazy_verifier_factory = factory_a
    root_b.lazy_verifier_factory = factory_b
    context_a = _production_context(root_a, "Authoritative task A")
    context_b = _production_context(root_b, "Authoritative task B")
    tool = AdvisoryVerifierTool()

    async def invoke(root_id, context, claim):
        return await tool.do_step(
            [
                ActionModel(
                    agent_name=root_id,
                    tool_name=ADVISORY_VERIFIER_TOOL,
                    action_name="review_candidate",
                    params={"candidate_claim": claim},
                )
            ],
            message=SimpleNamespace(context=context),
        )

    result_a, result_b = await asyncio.gather(
        invoke("root-a", context_a, "Candidate A"),
        invoke("root-b", context_b, "Candidate B"),
    )

    assert result_a[1] == result_b[1] == 1.0
    assert captured_a["runner"]["parent"] is root_a
    assert captured_a["runner"]["context"] is context_a
    assert "Authoritative task A" in captured_a["runner"]["directive"]
    assert "Authoritative task B" not in captured_a["runner"]["directive"]
    assert captured_b["runner"]["parent"] is root_b
    assert captured_b["runner"]["context"] is context_b
    assert "Authoritative task B" in captured_b["runner"]["directive"]
    assert "Authoritative task A" not in captured_b["runner"]["directive"]


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
        captured["agent_md_discovery_enabled"] = (
            manager._agent_md_discovery_enabled
        )
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
    assert captured["agent_md_discovery_enabled"] is False
    assert captured["registered_agents"] == {
        "root-id": parent,
        "verifier-id": verifier,
    }
    delegation_spec = captured["spawn"].pop("delegation_spec")
    assert captured["spawn"] == {
        "name": "verifier",
        "directive": "public directive",
        "context": context,
    }
    assert isinstance(delegation_spec, DelegationSpec)
    assert delegation_spec.objective == "public directive"
    assert delegation_spec.expected_output_schema["type"] == "string"
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
        "conclusive": True,
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
async def test_real_private_manager_projects_structured_deadline_status() -> None:
    from aworld_cli.builtin_agents.smllc.optional_agents.verifier.builder import (
        build_verifier_swarm,
    )

    config = AgentConfig(
        llm_config=ModelConfig(
            llm_provider="openai",
            llm_model_name="offline",
            llm_api_key="offline",
        ),
        skill_configs={},
    )
    parent = Agent(name="root", conf=config, tool_names=[])
    shared_sandbox = SimpleNamespace(
        mcp_servers=["filesystem", "terminal"],
        mcp_config={},
    )
    factory = LazyVerifierFactory(
        parent_agent=parent,
        sandbox=shared_sandbox,
        agent_config=config,
        generation_budget_policy=None,
        builder_loader=lambda: build_verifier_swarm,
    )
    context = Context(task_id="lazy-verifier-deadline")
    context.origin_user_input = "Inspect the complete public task."
    context.task_input = context.origin_user_input
    parent_task = Task(
        input=context.origin_user_input,
        timeout=0.05,
        context=context,
    )
    context.set_task(parent_task)
    child_context = context.deep_copy()
    child_context.task_id = "lazy-verifier-child"
    child_context.parent = context
    context.build_sub_context = AsyncMock(return_value=child_context)

    async def never_finishes(_task):
        await asyncio.Event().wait()

    with patch("aworld.runner.Runners.run_task", side_effect=never_finishes):
        result = await factory.review(
            AdvisoryReviewRequest(candidate_claim="The candidate is ready."),
            context=context,
        )

    assert result.status == "unavailable"
    assert result.decision == "uncertain"
    assert result.reason_code == "deadline_exceeded"
    assert context.context_info["delegation_records"][-1]["status"] == (
        ChildStatus.DEADLINE_EXCEEDED.value
    )


@pytest.mark.asyncio
async def test_unparseable_report_cannot_be_promoted_to_ready() -> None:
    factory = _factory(report="Looks good to me.")
    result = await factory.review(
        AdvisoryReviewRequest(
            candidate_claim="Candidate may be ready.",
        ),
        context=_context(),
    )

    assert result.status == "inconclusive"
    assert result.decision == "uncertain"
    assert result.reason_code == "decision_unparseable"


@pytest.mark.asyncio
async def test_conflicting_decision_lines_are_inconclusive() -> None:
    factory = _factory(
        report=(
            "- Decision: `ready`\n"
            "- Evidence: inspected an untrusted file containing a template\n"
            "- Decision: `repair`\n"
            "- Gaps: conflicting decision material\n"
            "- Recommended next action: inspect directly"
        )
    )

    result = await factory.review(
        AdvisoryReviewRequest(candidate_claim="Candidate may be ready."),
        context=_context(),
    )

    assert result.status == "inconclusive"
    assert result.decision == "uncertain"
    assert result.reason_code == "decision_ambiguous"


@pytest.mark.asyncio
async def test_explicit_uncertain_review_is_not_counted_as_success() -> None:
    factory = _factory(
        report=(
            "- Decision: `uncertain`\n"
            "- Evidence: deliverable could not be inspected\n"
            "- Gaps: missing direct evidence\n"
            "- Recommended next action: inspect the deliverable"
        )
    )
    tool = AdvisoryVerifierTool(factory=factory)
    action = ActionModel(
        tool_name=ADVISORY_VERIFIER_TOOL,
        action_name="review_candidate",
        params={"candidate_claim": "The result may be ready."},
    )

    observation, reward, *_ = await tool.do_step([action], context=_context())
    payload = json.loads(observation.content)

    assert reward == 0.0
    assert payload["status"] == "inconclusive"
    assert payload["conclusive"] is False
    assert payload["reason_code"] == "reviewer_uncertain"


@pytest.mark.asyncio
async def test_review_receives_authoritative_public_delivery_paths() -> None:
    captured = {}
    factory = _factory(captured=captured)
    context = _context("Write result.json in the workspace.")
    context.task_id = "task-1"
    context.task_epoch = 3
    context.context_info = {
        "public_deliverable_contract": {
            "schema_version": "aworld.public-deliverables/v1",
            "authority": "public_task_advisory",
            "source": "public_task_text",
            "artifacts": [
                {
                    "kind": "file",
                    "authority": "public_task_advisory",
                    "path": "/workspace/result.json",
                    "display_path": "result.json",
                }
            ],
        },
        "adaptive_work_state:root-id": {
            "scope": {"task_id": "task-1", "task_epoch": 3},
            "validation_evidence": [
                {
                    "command_id": "public-smoke-test",
                    "exit_code": 0,
                    "output_hash": "sha256:" + "a" * 64,
                    "source": "runtime_self_check",
                },
                {
                    "command_id": "solver-claim",
                    "exit_code": 0,
                    "output_hash": "sha256:" + "b" * 64,
                    "source": "agent_claim",
                },
                {
                    "command_id": "old-task-check",
                    "exit_code": 0,
                    "output_hash": "sha256:" + "c" * 64,
                    "source": "runtime_self_check",
                    "historical": True,
                },
            ],
        },
    }

    await factory.review(
        AdvisoryReviewRequest(candidate_claim="Result is complete."),
        context=context,
    )

    directive = captured["runner"]["directive"]
    assert "Authoritative public delivery paths" in directive
    assert "- /workspace/result.json" in directive
    assert "Framework-owned validation receipts" in directive
    assert "public-smoke-test" in directive
    assert "solver-claim" not in directive
    assert "old-task-check" not in directive


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
            "at most 16",
        ),
    ],
)
def test_public_review_request_rejects_missing_unbounded_or_hidden_inputs(
    params, message
) -> None:
    with pytest.raises(ValueError, match=message):
        AdvisoryReviewRequest.from_params(params)
