import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
import subprocess
import sys
import time
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
    ArtifactRequirement,
    ChildStatus,
    CompletionContract,
    DelegationSpec,
)
from aworld.core.execution_protocol import (
    ExecutionProtocolPolicy,
    ExecutionProtocolStore,
    ProtocolPhase,
)
from aworld.core.tool.base import ToolFactory
from aworld.core.task import Task
from aworld.core.event.base import Message
from aworld.runners.execution_protocol import (
    configure_execution_protocol,
    execution_protocol_policy,
    store_candidate_fallback,
)
from aworld_cli.builtin_agents.smllc.agents import aworld_agent
from aworld_cli.builtin_agents.smllc.agents import lazy_verifier as lazy_verifier_module
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


def _candidate_gate(context, agent_id, candidate_fingerprint):
    return {
        "schema_version": "aworld.mutation-gate/v4",
        "scope_hash": lazy_verifier_module.semantic_fingerprint(
            {
                "task_id": getattr(context, "task_id", None),
                "task_epoch": getattr(context, "task_epoch", None),
                "agent_id": agent_id,
            }
        ),
        "agent_id": agent_id,
        "candidate_fingerprint": candidate_fingerprint,
    }


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
    candidate_fingerprint = "sha256:" + "c" * 64
    context.write_task_runtime_state(
        root.id(),
        "execution_protocol_mutation_gate",
        _candidate_gate(context, root.id(), candidate_fingerprint),
    )
    context.write_task_runtime_state(
        root.id(),
        "adaptive_work_state",
        {
            "scope": {"task_id": context.task_id, "task_epoch": context.task_epoch},
            "validation_evidence": [
                {
                    "command_id": "public-check",
                    "exit_code": 0,
                    "output_hash": "sha256:" + "d" * 64,
                    "candidate_fingerprint": candidate_fingerprint,
                    "source": "runtime_self_check",
                }
            ],
        },
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
    assert captured["builder_kwargs"]["llm_max_attempts"] == 1
    assert captured["builder_kwargs"]["llm_retry_delay"] == 0.0
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
    assert delegation_spec.token_budget == 32_768
    assert delegation_spec.max_output_tokens == 4_096
    assert delegation_spec.max_turns == 2
    assert "read_file" in delegation_spec.allowed_tools
    assert "filesystem__read_file" in delegation_spec.allowed_tools
    assert "mcp__filesystem__read_file" in delegation_spec.allowed_tools
    assert not any(
        operation in tool_name
        for tool_name in delegation_spec.allowed_tools
        for operation in ("write", "edit", "delete", "execute")
    )
    assert delegation_spec.may_use_parent_finalization_reserve is False
    assert delegation_spec.deadline is not None
    assert delegation_spec.deadline.tzinfo is not None
    assert delegation_spec.deadline > datetime.now(timezone.utc)
    assert not hasattr(parent, "subagent_manager")


async def _capture_reserve_authorization(monkeypatch, phase: ProtocolPhase) -> bool:
    from aworld.core.agent.subagent_manager import SubagentManager

    captured = {}

    async def register(_manager, _swarm):
        return None

    async def spawn(_manager, **kwargs):
        captured["spec"] = kwargs["delegation_spec"]
        return (
            '{"decision":"ready","evidence":"public check passed",'
            '"gaps":"none","recommended_next_action":"submit current result"}'
        )

    monkeypatch.setattr(SubagentManager, "register_team_members", register)
    monkeypatch.setattr(SubagentManager, "spawn", spawn)
    root = Agent(
        name=f"root-{phase.value}",
        conf=AgentConfig(
            llm_config=ModelConfig(llm_model_name="offline"),
            skill_configs={},
        ),
        tool_names=[],
    )
    swarm = Swarm(root)
    context = Context(task_id=f"reserve-{phase.value}")
    context.origin_user_input = "Review the public candidate."
    context.task_input = context.origin_user_input
    context.agent_info.current_agent_id = root.id()
    context.set_task(
        Task(
            input=context.origin_user_input,
            swarm=swarm,
            timeout=60,
            completion_reserve_seconds=1,
            context=context,
        )
    )
    policy = ExecutionProtocolPolicy(
        mode="observe",
        terminal_review_gate_enabled=True,
        finalization_reserve_seconds=4,
    )
    configure_execution_protocol(context, root.id(), policy)
    store_candidate_fallback(
        context,
        root.id(),
        [ActionModel(agent_name=root.id(), policy_info="canonical candidate")],
    )
    candidate_fingerprint = "sha256:" + "e" * 64
    context.write_task_runtime_state(
        root.id(),
        "execution_protocol_mutation_gate",
        _candidate_gate(context, root.id(), candidate_fingerprint),
    )
    context.write_task_runtime_state(
        root.id(),
        "adaptive_work_state",
        {
            "scope": {"task_id": context.task_id, "task_epoch": context.task_epoch},
            "validation_evidence": [
                {
                    "command_id": "public-check",
                    "exit_code": 0,
                    "output_hash": "sha256:" + "f" * 64,
                    "candidate_fingerprint": candidate_fingerprint,
                    "source": "runtime_self_check",
                }
            ],
        },
    )
    configured_policy = execution_protocol_policy(context, root.id())
    store = ExecutionProtocolStore(context, root.id(), configured_policy)
    state = store.load()
    if phase is not ProtocolPhase.EXECUTE:
        store.save(
            replace(
                state,
                phase=phase,
                review_pending=phase is ProtocolPhase.REVIEW,
                finalization_entered=True,
            )
        )
    factory = LazyVerifierFactory(
        parent_agent=root,
        sandbox=SimpleNamespace(name="shared"),
        agent_config=root.conf,
        generation_budget_policy=None,
        builder_loader=lambda: (
            lambda **_kwargs: SimpleNamespace(
                agents={"verifier-id": _FakeVerifier()}
            )
        ),
    )

    result = await factory.review(
        AdvisoryReviewRequest(candidate_claim="Candidate is ready."),
        context=context,
    )

    assert result.decision == "ready"
    return captured["spec"].may_use_parent_finalization_reserve


@pytest.mark.asyncio
async def test_execute_phase_advisory_review_cannot_use_finalization_reserve(
    monkeypatch,
) -> None:
    assert (
        await _capture_reserve_authorization(
            monkeypatch,
            ProtocolPhase.EXECUTE,
        )
        is False
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", [ProtocolPhase.REVIEW, ProtocolPhase.FINALIZE])
async def test_terminal_candidate_review_can_use_finalization_reserve(
    monkeypatch,
    phase,
) -> None:
    assert await _capture_reserve_authorization(monkeypatch, phase) is True


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
async def test_injected_sync_review_runner_has_absolute_deadline(monkeypatch) -> None:
    factory = _factory()

    def blocks_past_deadline(*_args):
        time.sleep(0.2)
        return "- Decision: `ready`"

    factory._review_runner = blocks_past_deadline
    monkeypatch.setattr(
        lazy_verifier_module,
        "_REVIEW_WALL_CLOCK_SECONDS",
        0.02,
    )
    started = asyncio.get_running_loop().time()

    result = await factory.review(
        AdvisoryReviewRequest(candidate_claim="Candidate may be ready."),
        context=_context(),
    )

    elapsed = asyncio.get_running_loop().time() - started
    assert elapsed < 0.15
    assert result.status == "unavailable"
    assert result.reason_code == "deadline_exceeded"


@pytest.mark.asyncio
async def test_duplicate_candidate_review_is_bounded_without_second_child() -> None:
    captured = {}
    factory = _factory(captured=captured)
    request = AdvisoryReviewRequest(
        candidate_claim="The same candidate is ready.",
        deliverables=("/app/result.json",),
    )
    context = _context()

    first = await factory.review(request, context=context)
    second = await factory.review(request, context=context)

    assert first.status == "completed"
    assert second.status == "unavailable"
    assert second.decision == "uncertain"
    assert second.reason_code == "duplicate_candidate_review"
    assert captured["builder_loads"] == 1


@pytest.mark.asyncio
async def test_changed_candidate_does_not_reopen_review_budget() -> None:
    captured = {}
    factory = _factory(captured=captured)
    request = AdvisoryReviewRequest(candidate_claim="The candidate is ready.")
    context = _context()
    first_fingerprint = "sha256:" + "a" * 64
    second_fingerprint = "sha256:" + "b" * 64
    context.context_info = {
        "execution_protocol_mutation_gate:root-id": _candidate_gate(
            context, "root-id", first_fingerprint
        )
    }

    first = await factory.review(request, context=context)
    context.context_info["execution_protocol_mutation_gate:root-id"] = (
        _candidate_gate(context, "root-id", second_fingerprint)
    )
    second = await factory.review(request, context=context)

    assert first.status == "completed"
    assert second.status == "unavailable"
    assert second.reason_code == "review_attempt_limit"
    assert captured["builder_loads"] == 1


@pytest.mark.asyncio
async def test_real_private_manager_projects_structured_deadline_status(
    monkeypatch,
) -> None:
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
    fingerprint = "sha256:" + "a" * 64
    context.write_task_runtime_state(
        parent.id(),
        "execution_protocol_mutation_gate",
        _candidate_gate(context, parent.id(), fingerprint),
    )
    monkeypatch.setattr(lazy_verifier_module, "_REVIEW_MIN_START_SECONDS", 0.0)
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
async def test_strict_json_review_envelope_is_parsed_without_regex_inference() -> None:
    factory = _factory(
        report=json.dumps(
            {
                "decision": "repair",
                "evidence": "the current response omits the required field",
                "gaps": "required field is absent",
                "recommended_next_action": "add the field and revalidate once",
            }
        )
    )

    result = await factory.review(
        AdvisoryReviewRequest(candidate_claim="Candidate may be ready."),
        context=_context(),
    )

    assert result.status == "completed"
    assert result.decision == "repair"
    assert result.reason_code is None
    assert json.loads(result.report)["decision"] == "repair"


@pytest.mark.asyncio
async def test_single_strict_json_object_is_accepted_under_bounded_wrapping() -> None:
    report = (
        "Review result follows:\n```json\n"
        + json.dumps(
            {
                "decision": "repair",
                "evidence": "the public field is absent",
                "gaps": "one public obligation remains",
                "recommended_next_action": "add the field and validate once",
            }
        )
        + "\n```\nEnd of review."
    )

    result = await _factory(report=report).review(
        AdvisoryReviewRequest(candidate_claim="Candidate may be ready."),
        context=_context(),
    )

    assert result.status == "completed"
    assert result.decision == "repair"
    assert json.loads(result.report)["decision"] == "repair"


@pytest.mark.asyncio
async def test_multiple_strict_json_objects_remain_ambiguous() -> None:
    ready = json.dumps(
        {
            "decision": "ready",
            "evidence": "candidate exists",
            "gaps": "none",
            "recommended_next_action": "submit current result",
        }
    )
    repair = json.dumps(
        {
            "decision": "repair",
            "evidence": "field missing",
            "gaps": "one field",
            "recommended_next_action": "add the field",
        }
    )

    result = await _factory(report=ready + "\n" + repair).review(
        AdvisoryReviewRequest(candidate_claim="Candidate may be ready."),
        context=_context(),
    )

    assert result.status == "inconclusive"
    assert result.decision == "uncertain"
    assert result.reason_code == "decision_ambiguous"


@pytest.mark.asyncio
async def test_ready_requires_framework_owned_direct_evidence() -> None:
    context = Context(task_id="ready-without-evidence")
    public_task = "Configure the service."
    context.origin_user_input = public_task
    context.set_task(Task(id="ready-without-evidence", input=public_task))
    fingerprint = "sha256:" + "a" * 64
    context.write_task_runtime_state(
        "root-id",
        "execution_protocol_mutation_gate",
        _candidate_gate(context, "root-id", fingerprint),
    )
    factory = _factory(
        report=json.dumps(
            {
                "decision": "ready",
                "evidence": "solver says the service is ready",
                "gaps": "none",
                "recommended_next_action": "submit current result",
            }
        )
    )

    result = await factory.review(
        AdvisoryReviewRequest(candidate_claim="Service is ready."),
        context=context,
    )

    assert result.status == "inconclusive"
    assert result.decision == "uncertain"
    assert result.reason_code == "ready_evidence_missing"
    assert result.to_payload()["recommended_framework_action"] == (
        "task_derived_validation_once"
    )
    assert result.to_payload()["review_retry_allowed"] is False


@pytest.mark.asyncio
async def test_review_does_not_start_inside_persistence_window() -> None:
    context = Context(task_id="review-window-closed")
    public_task = "Configure the service."
    context.origin_user_input = public_task
    task = Task(
        id="review-window-closed",
        input=public_task,
        completion_reserve_seconds=10,
    )
    task.remaining_seconds = lambda: 25.0
    context.set_task(task)
    fingerprint = "sha256:" + "b" * 64
    context.write_task_runtime_state(
        "root-id",
        "execution_protocol_mutation_gate",
        _candidate_gate(context, "root-id", fingerprint),
    )
    captured = {}
    factory = _factory(captured=captured)

    result = await factory.review(
        AdvisoryReviewRequest(candidate_claim="Service is ready."),
        context=context,
    )

    assert result.status == "unavailable"
    assert result.reason_code == "review_start_window_closed"
    assert captured.get("builder_loads", 0) == 0


@pytest.mark.asyncio
async def test_review_budget_allows_exactly_one_candidate_review() -> None:
    captured = {}
    factory = _factory(
        captured=captured,
        report=(
            '{"decision":"repair","evidence":"direct check failed",'
            '"gaps":"one defect","recommended_next_action":"repair once"}'
        ),
    )

    first = await factory.review(
        AdvisoryReviewRequest(candidate_claim="candidate one"), context=_context()
    )
    second = await factory.review(
        AdvisoryReviewRequest(candidate_claim="candidate two"), context=_context()
    )
    third = await factory.review(
        AdvisoryReviewRequest(candidate_claim="candidate three"), context=_context()
    )

    assert first.status == "completed"
    assert second.status == third.status == "unavailable"
    assert second.reason_code == third.reason_code == "review_attempt_limit"
    assert captured["builder_loads"] == 1


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
async def test_repeated_identical_decision_lines_are_not_ambiguous() -> None:
    factory = _factory(
        report=(
            "Decision: ready.\n"
            "- Evidence: inspected the current deliverable\n"
            "- Gaps: none\n"
            "- Recommended next action: submit current result\n"
            "Decision: ready\n"
        )
    )

    result = await factory.review(
        AdvisoryReviewRequest(candidate_claim="Candidate is ready."),
        context=_context(),
    )

    assert result.status == "completed"
    assert result.decision == "ready"
    assert result.reason_code is None


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
async def test_uncertain_review_projects_only_public_validation_scope() -> None:
    factory = _factory(
        report=json.dumps(
            {
                "decision": "uncertain",
                "evidence": "the artifact exists",
                "gaps": "the public size threshold is not independently checked",
                "recommended_next_action": "validate every listed obligation",
            }
        )
    )
    tool = AdvisoryVerifierTool(factory=factory)
    context = _context(
        "Write result.json. The resulting file must be at most 2500 bytes."
    )
    action = ActionModel(
        tool_name=ADVISORY_VERIFIER_TOOL,
        action_name="review_candidate",
        params={"candidate_claim": "result.json is ready."},
    )

    observation, reward, *_ = await tool.do_step([action], context=context)
    payload = json.loads(observation.content)

    assert reward == 0.0
    scope = payload["task_derived_validation_scope"]
    assert {item["kind"] for item in scope} == {
        "deliverable_file",
        "numeric_threshold",
    }
    assert all(item["obligation_id"].startswith("public-obligation-") for item in scope)
    assert all(item["source_fingerprint"].startswith("sha256:") for item in scope)
    serialized = json.dumps(scope, sort_keys=True)
    assert "verifier" not in serialized.lower()
    assert "reward" not in serialized.lower()


@pytest.mark.asyncio
async def test_review_receives_authoritative_public_delivery_paths() -> None:
    captured = {}
    factory = _factory(captured=captured)
    context = _context("Write result.json in the workspace.")
    context.task_id = "task-1"
    context.task_epoch = 3
    candidate_fingerprint = "sha256:" + "d" * 64
    context.context_info = {
        "execution_protocol_mutation_gate:root-id": _candidate_gate(
            context, "root-id", candidate_fingerprint
        ),
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
                    "observed_at": "2026-10-11T00:00:00+00:00",
                    "candidate_fingerprint": candidate_fingerprint,
                    "source": "runtime_self_check",
                },
                {
                    "command_id": "solver-claim",
                    "exit_code": 0,
                    "output_hash": "sha256:" + "b" * 64,
                    "candidate_fingerprint": candidate_fingerprint,
                    "source": "agent_claim",
                },
                {
                    "command_id": "old-task-check",
                    "exit_code": 0,
                    "output_hash": "sha256:" + "c" * 64,
                    "candidate_fingerprint": candidate_fingerprint,
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


@pytest.mark.asyncio
async def test_in_progress_public_candidate_and_contract_can_be_reviewed_without_gate(
    monkeypatch, tmp_path
) -> None:
    captured = {}
    factory = _factory(
        report=json.dumps(
            {
                "decision": "ready",
                "evidence": "captured current declared JSON with the value field",
                "gaps": "none",
                "recommended_next_action": "submit current result",
            }
        ),
        captured=captured,
    )
    declared = tmp_path / "result.json"
    declared.write_text('{"value":42}', encoding="utf-8")
    monkeypatch.setenv("ARTIFACT_DIRECTORY", str(tmp_path))
    public_task = "Write result.json with the required value field."
    context = _context(public_task)
    context.task_id = "in-progress-public-candidate"
    context.task_epoch = 2
    context.context_info = {
        "public_deliverable_contract": {
            "schema_version": "aworld.public-deliverables/v1",
            "authority": "public_task_advisory",
            "source": "public_task_text",
            "artifacts": [
                {
                    "kind": "file",
                    "authority": "public_task_advisory",
                    "path": str(declared),
                    "display_path": "result.json",
                }
            ],
        },
        "public_task_contract": {
            "schema_version": "aworld.public-task-contract/v1",
            "authority": "public_task_advisory",
            "source": "public_task_text",
            "request_fingerprint": "sha256:"
            + hashlib.sha256(public_task.encode()).hexdigest(),
            "obligations": [
                {
                    "obligation_id": "public-obligation-1",
                    "kind": "structured_field",
                    "subject": "value",
                    "expectation": {"required": True, "declared_type": "int"},
                    "source_excerpt": "required value field",
                    "source_fingerprint": "sha256:" + "b" * 64,
                    "authority": "public_task_advisory",
                    "source": "public_task_text",
                }
            ],
        },
    }

    result = await factory.review(
        AdvisoryReviewRequest(candidate_claim="Result is complete."),
        context=context,
    )

    assert result.status == "completed"
    assert result.decision == "ready"
    directive = captured["runner"]["directive"]
    assert '"authentication":"current_public_capture"' in directive
    assert '\\"value\\":42' in directive
    assert "Public task contract ledger" in directive
    assert '"obligation_id":"public-obligation-1"' in directive
    assert "hidden" not in directive.lower()


@pytest.mark.asyncio
async def test_review_reads_public_contracts_from_task_owner_context(
    monkeypatch, tmp_path
) -> None:
    captured = {}
    factory = _factory(
        report=json.dumps(
            {
                "decision": "ready",
                "evidence": "captured the owner-bound public artifact",
                "gaps": "none",
                "recommended_next_action": "submit current result",
            }
        ),
        captured=captured,
    )
    declared = tmp_path / "run.py"
    declared.write_text("async def run_tasks(tasks, max_concurrent):\n    return None\n")
    monkeypatch.setenv("ARTIFACT_DIRECTORY", str(tmp_path))
    public_task = "Write run.py with async run_tasks(tasks, max_concurrent)."
    request_fingerprint = "sha256:" + hashlib.sha256(
        public_task.encode()
    ).hexdigest()
    owner = SimpleNamespace(
        context_info={
            "public_deliverable_contract": {
                "schema_version": "aworld.public-deliverables/v1",
                "authority": "public_task_advisory",
                "source": "public_task_text",
                "artifacts": [
                    {
                        "kind": "file",
                        "authority": "public_task_advisory",
                        "path": str(declared),
                        "display_path": "run.py",
                    }
                ],
            },
            "public_task_contract": {
                "schema_version": "aworld.public-task-contract/v1",
                "authority": "public_task_advisory",
                "source": "public_task_text",
                "request_fingerprint": request_fingerprint,
                "obligations": [
                    {
                        "obligation_id": "public-obligation-1",
                        "kind": "structured_field",
                        "subject": "run_tasks",
                        "expectation": {"required": True},
                        "source_fingerprint": "sha256:" + "c" * 64,
                        "authority": "public_task_advisory",
                        "source": "public_task_text",
                    }
                ],
            },
        }
    )
    transport = _context(public_task)
    transport.task_id = "transport-owner-contract"
    transport.task_epoch = 1
    transport.context_info = {}
    transport.event_manager = SimpleNamespace(context=owner)

    result = await factory.review(
        AdvisoryReviewRequest(candidate_claim="run.py is ready."),
        context=transport,
    )

    assert result.status == "completed"
    assert result.decision == "ready"
    directive = captured["runner"]["directive"]
    assert '"authentication":"current_public_capture"' in directive
    assert "async def run_tasks" in directive
    assert '"obligation_id":"public-obligation-1"' in directive


@pytest.mark.asyncio
async def test_review_rederives_public_contract_when_transport_projection_is_missing(
    monkeypatch, tmp_path
) -> None:
    captured = {}
    factory = _factory(
        report=json.dumps(
            {
                "decision": "ready",
                "evidence": "captured the declared public file",
                "gaps": "none",
                "recommended_next_action": "submit current result",
            }
        ),
        captured=captured,
    )
    declared = tmp_path / "result.json"
    declared.write_text('{"status":"ready"}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARTIFACT_DIRECTORY", str(tmp_path))
    public_task = f"Write a file called `{declared}` containing the result."
    context = _context(public_task)
    context.task_id = "transport-contract-rederivation"
    context.task_epoch = 1
    context.context_info = {}

    result = await factory.review(
        AdvisoryReviewRequest(candidate_claim="The result file is ready."),
        context=context,
    )

    assert result.status == "completed"
    assert result.decision == "ready"
    directive = captured["runner"]["directive"]
    assert '"authentication":"current_public_capture"' in directive
    assert str(declared) in directive
    assert '"kind":"deliverable_file"' in directive


@pytest.mark.asyncio
async def test_executable_obligation_requires_public_validation_receipt_for_ready(
    monkeypatch, tmp_path
) -> None:
    factory = _factory(
        report=json.dumps(
            {
                "decision": "ready",
                "evidence": "the function source was captured",
                "gaps": "none",
                "recommended_next_action": "submit current result",
            }
        )
    )
    declared = tmp_path / "run.py"
    declared.write_text("async def run_tasks(tasks, max_concurrent):\n    return None\n")
    monkeypatch.setenv("ARTIFACT_DIRECTORY", str(tmp_path))
    public_task = "Write run.py with async run_tasks(tasks, max_concurrent)."
    context = _context(public_task)
    context.task_id = "behavior-needs-validation"
    context.task_epoch = 1
    context.context_info = {
        "public_deliverable_contract": {
            "schema_version": "aworld.public-deliverables/v1",
            "authority": "public_task_advisory",
            "source": "public_task_text",
            "artifacts": [
                {
                    "kind": "file",
                    "authority": "public_task_advisory",
                    "path": str(declared),
                    "display_path": "run.py",
                }
            ],
        },
        "public_task_contract": {
            "schema_version": "aworld.public-task-contract/v1",
            "authority": "public_task_advisory",
            "source": "public_task_text",
            "request_fingerprint": "sha256:"
            + hashlib.sha256(public_task.encode()).hexdigest(),
            "obligations": [
                {
                    "obligation_id": "public-obligation-1",
                    "kind": "function_signature",
                    "subject": "run_tasks",
                    "expectation": {
                        "signature": "async run_tasks(tasks, max_concurrent)"
                    },
                    "source_fingerprint": "sha256:" + "d" * 64,
                    "authority": "public_task_advisory",
                    "source": "public_task_text",
                }
            ],
        },
    }

    result = await factory.review(
        AdvisoryReviewRequest(candidate_claim="run.py is ready."),
        context=context,
    )

    assert result.status == "inconclusive"
    assert result.decision == "uncertain"
    assert result.reason_code == "ready_evidence_missing"
    assert result.to_payload()["recommended_framework_action"] == (
        "task_derived_validation_once"
    )


@pytest.mark.asyncio
async def test_public_contract_with_wrong_request_fingerprint_is_not_projected(
    monkeypatch, tmp_path
) -> None:
    captured = {}
    factory = _factory(
        report=json.dumps(
            {
                "decision": "repair",
                "evidence": "public candidate inspected",
                "gaps": "needs validation",
                "recommended_next_action": "run one public check",
            }
        ),
        captured=captured,
    )
    declared = tmp_path / "result.json"
    declared.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("ARTIFACT_DIRECTORY", str(tmp_path))
    context = _context("Write result.json.")
    context.task_id = "wrong-ledger-fingerprint"
    context.task_epoch = 1
    context.context_info = {
        "public_deliverable_contract": {
            "schema_version": "aworld.public-deliverables/v1",
            "authority": "public_task_advisory",
            "source": "public_task_text",
            "artifacts": [
                {
                    "kind": "file",
                    "authority": "public_task_advisory",
                    "path": str(declared),
                    "display_path": "result.json",
                }
            ],
        },
        "public_task_contract": {
            "schema_version": "aworld.public-task-contract/v1",
            "authority": "public_task_advisory",
            "source": "public_task_text",
            "request_fingerprint": "sha256:" + "f" * 64,
            "obligations": [
                {
                    "obligation_id": "must-not-project",
                    "kind": "structured_field",
                    "subject": "hidden_field",
                    "expectation": {"required": True},
                    "source_excerpt": "untrusted",
                    "source_fingerprint": "sha256:" + "e" * 64,
                    "authority": "public_task_advisory",
                    "source": "public_task_text",
                }
            ],
        },
    }

    await factory.review(
        AdvisoryReviewRequest(candidate_claim="Result needs review."),
        context=context,
    )

    directive = captured["runner"]["directive"]
    assert "must-not-project" not in directive
    assert "hidden_field" not in directive


@pytest.mark.asyncio
async def test_review_receives_bounded_declared_artifact_snapshot(
    monkeypatch, tmp_path
) -> None:
    captured = {}
    factory = _factory(captured=captured)
    monkeypatch.setattr(
        lazy_verifier_module,
        "_declared_snapshot_authentication_status",
        lambda *_args, **_kwargs: {
            "authenticated": True,
            "reason": "current_complete_projection",
            "artifact_hashes": {
                str(declared): "sha256:"
                + hashlib.sha256(declared.read_bytes()).hexdigest()
            },
        },
    )
    declared = tmp_path / "artifacts" / "result.json"
    undeclared = tmp_path / "private.txt"
    declared.parent.mkdir()
    declared.write_text('{"status":"ready","rows":[1,2,3]}', encoding="utf-8")
    undeclared.write_text("must not be projected", encoding="utf-8")
    monkeypatch.setenv("ARTIFACT_DIRECTORY", str(declared.parent))
    context = _context("Write the declared result artifact.")
    context.context_info = {
        "public_deliverable_contract": {
            "schema_version": "aworld.public-deliverables/v1",
            "authority": "public_task_advisory",
            "source": "public_task_text",
            "artifacts": [
                {
                    "kind": "file",
                    "authority": "public_task_advisory",
                    "path": str(declared),
                    "display_path": "result.json",
                }
            ],
        }
    }

    await factory.review(
        AdvisoryReviewRequest(candidate_claim="Result is complete."),
        context=context,
    )

    directive = captured["runner"]["directive"]
    assert "aworld.declared-artifact-snapshot/v1" in directive
    assert '"status":"captured"' in directive
    assert '\\"rows\\":[1,2,3]' in directive
    assert "must not be projected" not in directive


def test_review_snapshot_is_bounded_and_does_not_follow_symlinks(
    monkeypatch, tmp_path
) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("sensitive-marker", encoding="utf-8")
    link = tmp_path / "declared.txt"
    link.symlink_to(secret)
    monkeypatch.setenv("ARTIFACT_DIRECTORY", str(tmp_path))
    snapshot = lazy_verifier_module._authoritative_delivery_snapshot(
        None,
        authoritative_deliverables=(str(link),),
        authentication_status={
            "authenticated": True,
            "reason": "current_complete_projection",
            "artifact_hashes": {
                str(link): "sha256:"
                + hashlib.sha256(secret.read_bytes()).hexdigest()
            },
        },
    )

    assert snapshot["items"][0]["status"] == "unavailable"
    assert "sensitive-marker" not in json.dumps(snapshot)


def test_direct_public_capture_does_not_follow_symlinks(monkeypatch, tmp_path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("private-marker", encoding="utf-8")
    link = tmp_path / "result.txt"
    link.symlink_to(secret)
    monkeypatch.setenv("ARTIFACT_DIRECTORY", str(tmp_path))

    status = lazy_verifier_module._direct_public_snapshot_authentication_status(
        None,
        authoritative_deliverables=(str(link),),
    )

    assert status["authenticated"] is False
    assert status["reason"] == "public_candidate_unavailable"
    assert status["artifact_hashes"] == {}


def test_review_snapshot_truncates_declared_text_with_fixed_budget(
    monkeypatch, tmp_path
) -> None:
    declared = tmp_path / "large.md"
    declared.write_text("A" * 20_000 + "tail-marker", encoding="utf-8")
    monkeypatch.setenv("ARTIFACT_DIRECTORY", str(tmp_path))
    snapshot = lazy_verifier_module._authoritative_delivery_snapshot(
        None,
        authoritative_deliverables=(str(declared),),
        authentication_status={
            "authenticated": True,
            "reason": "current_complete_projection",
            "artifact_hashes": {
                str(declared): "sha256:"
                + hashlib.sha256(declared.read_bytes()).hexdigest()
            },
        },
    )

    item = snapshot["items"][0]
    assert item["captured_bytes"] == 8192
    assert item["truncated"] is True
    assert "tail-marker" not in item["content_utf8"]


def test_review_snapshot_refuses_uncheckpointed_declared_bytes(tmp_path) -> None:
    declared = tmp_path / "result.json"
    declared.write_text('{"secret":"not-checkpointed"}', encoding="utf-8")
    snapshot = lazy_verifier_module._authoritative_delivery_snapshot(
        None,
        authoritative_deliverables=(str(declared),),
        authentication_status={
            "authenticated": False,
            "reason": "candidate_checkpoint_unavailable",
        },
    )

    assert snapshot["items"][0]["status"] == "unauthenticated"
    assert "not-checkpointed" not in json.dumps(snapshot)


def test_review_snapshot_rejects_mutation_between_authentication_and_read(
    monkeypatch, tmp_path
) -> None:
    declared = tmp_path / "result.json"
    declared.write_text('{"version":1}', encoding="utf-8")
    expected = "sha256:" + hashlib.sha256(declared.read_bytes()).hexdigest()
    monkeypatch.setenv("ARTIFACT_DIRECTORY", str(tmp_path))
    real_open = lazy_verifier_module._open_confined_snapshot_file
    mutated = False

    def mutate_before_open(*, trusted_root, path):
        nonlocal mutated
        if path == str(declared) and not mutated:
            mutated = True
            declared.write_text('{"version":2}', encoding="utf-8")
        return real_open(trusted_root=trusted_root, path=path)

    monkeypatch.setattr(
        lazy_verifier_module,
        "_open_confined_snapshot_file",
        mutate_before_open,
    )

    snapshot = lazy_verifier_module._authoritative_delivery_snapshot(
        None,
        authoritative_deliverables=(str(declared),),
        authentication_status={
            "authenticated": True,
            "reason": "current_complete_projection",
            "artifact_hashes": {str(declared): expected},
        },
    )

    assert snapshot["items"][0]["status"] == "authentication_changed"
    assert "content_utf8" not in snapshot["items"][0]


def test_review_snapshot_rejects_nested_symlink_escape(monkeypatch, tmp_path) -> None:
    trusted = tmp_path / "workspace"
    trusted.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("outside-secret", encoding="utf-8")
    (trusted / "linked").symlink_to(outside, target_is_directory=True)
    escaped = trusted / "linked" / "secret.txt"
    monkeypatch.setenv("ARTIFACT_DIRECTORY", str(trusted))

    snapshot = lazy_verifier_module._authoritative_delivery_snapshot(
        None,
        authoritative_deliverables=(str(escaped),),
        authentication_status={
            "authenticated": True,
            "reason": "current_complete_projection",
            "artifact_hashes": {
                str(escaped): "sha256:"
                + hashlib.sha256(secret.read_bytes()).hexdigest()
            },
        },
    )

    assert snapshot["items"][0]["status"] == "unavailable"
    assert "outside-secret" not in json.dumps(snapshot)


def test_review_snapshot_rejects_arbitrary_absolute_path(monkeypatch, tmp_path) -> None:
    trusted = tmp_path / "workspace"
    trusted.mkdir()
    secret = tmp_path / "outside" / "secret.txt"
    secret.parent.mkdir()
    secret.write_text("must-not-inline", encoding="utf-8")
    monkeypatch.setenv("ARTIFACT_DIRECTORY", str(trusted))
    monkeypatch.chdir(trusted)

    snapshot = lazy_verifier_module._authoritative_delivery_snapshot(
        SimpleNamespace(completion_contract=None),
        authoritative_deliverables=(str(secret),),
        authentication_status={
            "authenticated": True,
            "reason": "current_complete_projection",
            "artifact_hashes": {
                str(secret): "sha256:"
                + hashlib.sha256(secret.read_bytes()).hexdigest()
            },
        },
    )

    assert snapshot["items"][0]["status"] == "untrusted_path"
    assert "must-not-inline" not in json.dumps(snapshot)


def test_review_snapshot_allows_exact_trusted_completion_artifact(
    monkeypatch, tmp_path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    artifact = tmp_path / "external-artifacts" / "result.json"
    artifact.parent.mkdir()
    artifact.write_text('{"status":"ready"}', encoding="utf-8")
    digest = "sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest()
    monkeypatch.setenv("ARTIFACT_DIRECTORY", str(workspace))
    monkeypatch.chdir(workspace)
    contract = CompletionContract(
        required_artifacts=(ArtifactRequirement("result", str(artifact)),),
        immutable_inputs=(),
        validation_commands=(),
        max_evidence_age_seconds=None,
        required_final_evidence=(),
    )

    snapshot = lazy_verifier_module._authoritative_delivery_snapshot(
        SimpleNamespace(completion_contract=contract),
        authoritative_deliverables=(str(artifact),),
        authentication_status={
            "authenticated": True,
            "reason": "current_complete_projection",
            "artifact_hashes": {str(artifact): digest},
        },
    )

    assert snapshot["items"][0]["status"] == "captured"
    assert snapshot["items"][0]["content_utf8"] == '{"status":"ready"}'


@pytest.mark.asyncio
async def test_review_omits_stale_unbound_and_wrong_agent_receipts() -> None:
    captured = {}
    factory = _factory(captured=captured)
    context = _context("Write result.json in the workspace.")
    context.task_id = "receipt-filter"
    context.task_epoch = 4
    current = "sha256:" + "1" * 64
    stale = "sha256:" + "2" * 64
    context.context_info = {
        "execution_protocol_mutation_gate:other": _candidate_gate(
            context, "other", stale
        ),
        "execution_protocol_mutation_gate:root-id": _candidate_gate(
            context, "root-id", current
        ),
        "adaptive_work_state:root-id": {
            "scope": {"task_id": "receipt-filter", "task_epoch": 4},
            "validation_evidence": [
                {
                    "command_id": "matching",
                    "exit_code": 0,
                    "output_hash": "sha256:" + "3" * 64,
                    "observed_at": "2026-10-11T00:00:00+00:00",
                    "candidate_fingerprint": current,
                    "source": "runtime_self_check",
                },
                {
                    "command_id": "stale",
                    "exit_code": 0,
                    "output_hash": "sha256:" + "4" * 64,
                    "candidate_fingerprint": stale,
                    "source": "runtime_self_check",
                },
                {
                    "command_id": "unbound",
                    "exit_code": 0,
                    "output_hash": "sha256:" + "5" * 64,
                    "source": "runtime_self_check",
                },
            ],
        },
    }

    await factory.review(
        AdvisoryReviewRequest(candidate_claim="Result is complete."),
        context=context,
    )

    directive = captured["runner"]["directive"]
    assert "matching" in directive
    assert '"command_id":"stale"' not in directive
    assert "unbound" not in directive


@pytest.mark.asyncio
async def test_long_bounded_report_parses_decision_before_public_truncation() -> None:
    report = (
        "Evidence detail. " * 220
        + "\n- Decision: `ready`\n"
        + "- Evidence: inspected the current deliverable\n"
        + "- Gaps: none\n"
        + "- Recommended next action: submit current result"
    )
    assert 2_048 < len(report) < 8_192

    result = await _factory(report=report).review(
        AdvisoryReviewRequest(candidate_claim="Result is complete."),
        context=_context(),
    )

    assert result.status == "completed"
    assert result.decision == "ready"
    assert len(result.report) == 2_048


@pytest.mark.asyncio
async def test_malformed_candidate_fingerprint_cannot_mint_ready() -> None:
    captured = {}
    factory = _factory(captured=captured)
    context = _context("Write result.json in the workspace.")
    context.task_id = "invalid-candidate-fingerprint"
    context.task_epoch = 1
    malformed = "sha256:" + "z" * 64
    context.context_info = {
        "execution_protocol_mutation_gate:root-id": _candidate_gate(
            context, "root-id", malformed
        ),
        "adaptive_work_state:root-id": {
            "scope": {
                "task_id": "invalid-candidate-fingerprint",
                "task_epoch": 1,
            },
            "validation_evidence": [
                {
                    "command_id": "must-not-project",
                    "exit_code": 0,
                    "output_hash": "sha256:" + "a" * 64,
                    "candidate_fingerprint": malformed,
                    "source": "runtime_self_check",
                }
            ],
        },
    }

    result = await factory.review(
        AdvisoryReviewRequest(candidate_claim="Result is complete."),
        context=context,
    )

    assert result.status == "inconclusive"
    assert result.decision == "uncertain"
    assert result.reason_code == "ready_evidence_missing"
    assert "runner" in captured
    assert "must-not-project" not in captured["runner"]["directive"]


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
            "at most 8",
        ),
    ],
)
def test_public_review_request_rejects_missing_unbounded_or_hidden_inputs(
    params, message
) -> None:
    with pytest.raises(ValueError, match=message):
        AdvisoryReviewRequest.from_params(params)
