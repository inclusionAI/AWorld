"""Exercise production workbench actions and completion against synthetic files."""

import json
import sys
from types import SimpleNamespace

import pytest

from aworld.config import ToolConfig
from aworld.core.common import ActionModel
from aworld.core.context.base import Context
from aworld.core.context.compiler.completion import CompletionStatus
from aworld.core.task_workspace.session import (
    bind_task_workspace,
    get_task_workspace,
    goal_workspace_identity,
)
from aworld.tools.workbench_tool import WORKBENCH, WORKBENCH_SCHEMA_IDS, WorkbenchTool
from aworld_cli.core.runtime_completion import configure_runtime_completion


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "task"
    root.mkdir()
    monkeypatch.setenv("AWORLD_TASK_WORKSPACE_ROOT", str(tmp_path / "stores"))
    monkeypatch.setenv("AWORLD_COMPLETION_MODE", "enforce")
    for key in ("AWORLD_REQUIRED_ARTIFACTS_JSON", "AWORLD_VALIDATION_COMMANDS_JSON"):
        monkeypatch.delenv(key, raising=False)
    return root


def context_for(workspace, request, scope=None):
    context = Context()
    bind_task_workspace(
        context, workspace, scope or {"session_id": "session", "task_id": "task"}
    )
    configure_runtime_completion(context, request=request, workspace_path=workspace)
    return context


async def invoke(context, name, **params):
    tool = WorkbenchTool(ToolConfig(name=WORKBENCH))
    observation, _, done, truncated, _ = await tool.do_step(
        [ActionModel(tool_name=WORKBENCH, action_name=name, params=params)],
        SimpleNamespace(context=context),
    )
    assert not done and not truncated
    result = observation.action_result[0]
    assert result.success, result.error
    return json.loads(result.content)


@pytest.mark.asyncio
async def test_native_delivery_rejects_corrupt_and_later_changed_artifact(workspace):
    context = context_for(workspace, "Write the final result to result.json.")
    context.record_completion_final_evidence("agent_final_response")
    await context.resolve_completion_evidence()
    assert (
        context.assess_completion_contract(agent_claimed_finished=True).status
        != CompletionStatus.SATISFIED
    )
    target = workspace / "result.json"
    target.write_text("{broken")
    await context.resolve_completion_evidence()
    assert (
        context.assess_completion_contract(agent_claimed_finished=True).status
        != CompletionStatus.SATISFIED
    )
    target.write_text('{"value": 3}')
    saved = await invoke(
        context, "save_candidate", files={"result.json": "result.json"}
    )
    receipt = await invoke(
        context, "validate_candidate", candidate_id=saved["candidate_id"]
    )
    assert receipt["eligible"]
    promoted = await invoke(
        context,
        "promote_candidate",
        candidate_id=saved["candidate_id"],
        receipt_id=receipt["receipt_id"],
    )
    assert promoted["promoted"]
    await context.resolve_completion_evidence()
    assert (
        context.assess_completion_contract(agent_claimed_finished=True).status
        == CompletionStatus.SATISFIED
    )
    target.write_text('{"value": 4}')  # still valid JSON; old receipt is stale
    await context.resolve_completion_evidence()
    assert (
        context.assess_completion_contract(agent_claimed_finished=True).status
        != CompletionStatus.SATISFIED
    )


@pytest.mark.asyncio
async def test_goal_continuation_keeps_original_sources_and_declared_requirement(
    workspace,
):
    source = workspace / "source.csv"
    source.write_text("id,value\n1,3\n2,5\n")
    request = "Read source.csv and write the final result to result.csv."
    state = {"workspace_id": "goal-id", "objective": request, "started_at": "date"}
    scope = {"session_id": "session", "goal_id": goal_workspace_identity(state)}
    first = context_for(workspace, request, scope)
    before = await invoke(first, "inspect")
    snapshot_id = before["store"]["input_snapshot_id"]
    assert snapshot_id
    source.unlink()
    second = context_for(workspace, request, scope)
    resumed = await invoke(second, "inspect")
    assert resumed["store"]["input_snapshot_id"] == snapshot_id
    restored = await invoke(second, "restore_inputs", snapshot_id=snapshot_id)
    assert restored["restored"] == [str(source)]
    assert source.read_text() == "id,value\n1,3\n2,5\n"
    source.write_text("new user content")
    with pytest.raises(AssertionError, match="changed"):
        await invoke(second, "restore_inputs", snapshot_id=snapshot_id)


@pytest.mark.asyncio
async def test_public_checks_cannot_be_weakened_by_tool_arguments(workspace):
    context = context_for(workspace, "Write final output to result.json.")
    (workspace / "result.json").write_text("{}")
    saved = await invoke(
        context, "save_candidate", files={"result.json": "result.json"}
    )
    check = next(
        c for c in get_task_workspace(context)._checks() if c["kind"] == "json"
    )
    with pytest.raises(AssertionError, match="cannot replace"):
        await invoke(
            context,
            "validate_candidate",
            candidate_id=saved["candidate_id"],
            checks=[{**check, "kind": "exists"}],
        )


@pytest.mark.asyncio
async def test_semantic_self_check_retained_and_independent_input_used(workspace):
    (workspace / "source.csv").write_text("id,value\n1,3\n2,5\n")
    context = context_for(
        workspace, "Read source.csv and write the result to result.csv."
    )
    (workspace / "result.csv").write_text("id,value\n1,3\n")
    saved = await invoke(context, "save_candidate", files={"result.csv": "result.csv"})
    receipt = await invoke(
        context,
        "validate_candidate",
        candidate_id=saved["candidate_id"],
        checks=[
            {
                "id": "preserve-values",
                "kind": "preserve",
                "path": "result.csv",
                "input": "source.csv",
                "format": "csv",
                "columns": ["value"],
                "primary_key": ["id"],
            },
        ],
    )
    assert receipt["eligible"] is False
    context.record_completion_final_evidence("agent_final_response")
    await context.resolve_completion_evidence()
    assert (
        context.assess_completion_contract(agent_claimed_finished=True).status
        != CompletionStatus.SATISFIED
    )
    (workspace / "result.csv").write_text("id,value\n2,5\n1,3\n")
    await context.resolve_completion_evidence()
    assert (
        context.assess_completion_contract(agent_claimed_finished=True).status
        == CompletionStatus.SATISFIED
    )


@pytest.mark.asyncio
async def test_tool_probes_actual_task_interpreter_and_rejects_unbound_context(
    workspace,
):
    context = context_for(workspace, "Explain the installed Python version.")
    result = await invoke(
        context,
        "probe_api",
        interpreter=sys.executable,
        module="json",
        object_path="loads",
        call={"args": ['{"ok":true}'], "kwargs": {}, "result": "json"},
    )
    assert result["success"] is True
    assert result["task_correctness"] == "not_assessed"
    with pytest.raises(AssertionError, match="no local filesystem authority"):
        await invoke(Context(), "inspect")


def test_actual_generated_tool_catalog_and_one_shot_policy_include_workbench():
    from aworld.agents.llm_agent import get_tool_desc, tool_desc_transform
    from aworld_cli.builtin_agents.smllc.agents.aworld_agent import (
        _aworld_root_tool_policy,
    )
    from aworld.core.tool.surface import ToolSurfaceProfile, ToolLifecycle

    schemas = tool_desc_transform(get_tool_desc(), tools=[WORKBENCH])
    assert {s["function"]["name"] for s in schemas} == set(WORKBENCH_SCHEMA_IDS)
    for schema in schemas:
        assert not {"scope", "root", "policy", "success", "metrics"} & set(
            schema["function"]["parameters"]["properties"]
        )
    names, _ = _aworld_root_tool_policy(
        ToolSurfaceProfile(
            profile_id="one_shot", allowed_lifecycles=(ToolLifecycle.IMMEDIATE,)
        ),
        has_subagents=False,
    )
    assert WORKBENCH in names


@pytest.mark.asyncio
async def test_mistaken_agent_self_check_can_be_revised_with_history(workspace):
    context = context_for(workspace, "Write the final result to result.json.")
    (workspace / "result.json").write_text('{"value":3}')
    saved = await invoke(
        context, "save_candidate", files={"result.json": "result.json"}
    )
    with pytest.raises(AssertionError, match="unsupported check kind"):
        await invoke(
            context,
            "validate_candidate",
            candidate_id=saved["candidate_id"],
            checks=[{"id": "agent-json", "kind": "jsoon", "path": "result.json"}],
        )
    receipt = await invoke(
        context,
        "validate_candidate",
        candidate_id=saved["candidate_id"],
        checks=[
            {
                "id": "agent-json",
                "kind": "json",
                "path": "result.json",
                "root_type": "array",
            }
        ],
    )
    assert receipt["eligible"] is False
    await invoke(
        context,
        "revise_checks",
        checks=[
            {
                "id": "agent-json",
                "kind": "json",
                "path": "result.json",
                "root_type": "object",
            }
        ],
        reason="The public output is a named object, not an array.",
    )
    receipt = await invoke(
        context, "validate_candidate", candidate_id=saved["candidate_id"]
    )
    assert receipt["eligible"] is True
    assert (await invoke(context, "inspect"))["self_check_history"]
    public_id = get_task_workspace(context)._public_checks()[0]["id"]
    with pytest.raises(AssertionError, match="public checks cannot be removed"):
        await invoke(
            context, "revise_checks", remove_ids=[public_id], reason="Skip validation"
        )


@pytest.mark.asyncio
async def test_probe_excludes_private_runtime_environment(workspace, monkeypatch):
    monkeypatch.setenv("PRIVATE_RUNTIME_TEST_TOKEN", "secret-fixture")
    (workspace / "probe_example.py").write_text(
        'import os\ndef inspect_env():\n return "PRIVATE_RUNTIME_TEST_TOKEN" in os.environ\n'
    )
    context = context_for(workspace, "Inspect a local installed API.")
    result = await invoke(
        context,
        "probe_api",
        interpreter=sys.executable,
        module="probe_example",
        object_path="inspect_env",
        call={"args": [], "kwargs": {}, "result": "json"},
    )
    assert result["success"] is True
    assert "secret-fixture" not in json.dumps(result)
    # Inspect the subprocess-returned JSON, not a parent-process import.
    assert result["report"]["json_result"] is False


@pytest.mark.asyncio
async def test_default_local_executor_prepares_before_model_and_goal_does_not_rebaseline(
    workspace, monkeypatch
):
    from unittest.mock import AsyncMock, Mock
    from aworld.core.context.session import Session
    from aworld_cli.executors.local import LocalAgentExecutor
    from aworld_cli.builtin_agents.smllc.agents.aworld_agent import build_aworld_agent

    monkeypatch.chdir(workspace)
    monkeypatch.setenv("AWORLD_BUILTIN_SUBAGENTS", "none")
    monkeypatch.setenv("LLM_MODEL_NAME", "offline")
    monkeypatch.setenv("LLM_API_KEY", "offline")
    swarm = build_aworld_agent()
    assert any(
        getattr(a, "_task_workspace_local_path", None) == str(workspace)
        for a in swarm.agents.values()
    )
    executor = object.__new__(LocalAgentExecutor)
    executor._base_runtime = None
    executor.session_id = "cli-native"
    executor.swarm = swarm
    executor.context_config = SimpleNamespace()
    executor._execute_hooks = AsyncMock(return_value=None)
    executor._create_workspace = AsyncMock(return_value=None)
    executor._resolve_swarm_skills = Mock()
    goal = {
        "active": True,
        "workspace_id": "durable-goal",
        "objective": "Read source.csv and write result.json.",
    }
    monkeypatch.setattr(executor, "_goal_session_state", lambda: goal)

    async def from_input(task_input, **kwargs):
        context = Context(
            task_id=task_input.task_id, session=Session(session_id="cli-native")
        )
        context.user_id = "user"
        context.get_config = lambda: SimpleNamespace(debug_mode=False)
        context.init_swarm_state = AsyncMock()
        return context

    monkeypatch.setattr(
        "aworld_cli.executors.local.ApplicationContext.from_input", from_input
    )
    source = workspace / "source.csv"
    source.write_text("id,value\n1,3\n")
    first = await executor._build_task(goal["objective"], task_id="first")
    assert first.timeout is None and first.deadline_epoch_seconds is None
    store = get_task_workspace(first.context).store
    original = store.current_input_snapshot_id
    assert original
    source.write_text("changed\n")
    second = await executor._build_task("Continue the work.", task_id="second")
    assert (
        get_task_workspace(second.context).store.current_input_snapshot_id == original
    )
    assert (
        first.context.context_info["delivery_contract"]
        == second.context.context_info["delivery_contract"]
    )
    goal["active"] = False
    third = await executor._build_task("Write other.json.", task_id="third")
    assert get_task_workspace(third.context).store.scope_id != store.scope_id


@pytest.mark.asyncio
async def test_final_gate_checks_public_policy_without_candidate_publication(workspace):
    context = Context()
    context.context_info["task_workspace_contract"] = {
        "outputs": ["result.bin"],
        "policy": {"hard_constraints": [{"artifact": "result.bin", "max_bytes": 10}]},
    }
    bind_task_workspace(context, workspace, {"task_id": "direct-final"})
    configure_runtime_completion(
        context, request="Produce the requested output.", workspace_path=workspace
    )
    context.record_completion_final_evidence("agent_final_response")
    target = workspace / "result.bin"
    target.write_bytes(b"x" * 20)
    await context.resolve_completion_evidence()
    assert (
        context.assess_completion_contract(agent_claimed_finished=True).status
        != CompletionStatus.SATISFIED
    )
    assert context.context_info["delivery_validation"]["receipt"]["policy_violations"]
    target.write_bytes(b"x" * 5)
    await context.resolve_completion_evidence()
    assert (
        context.assess_completion_contract(agent_claimed_finished=True).status
        == CompletionStatus.SATISFIED
    )


@pytest.mark.asyncio
async def test_dynamic_artifact_self_check_participates_in_default_completion(
    workspace,
):
    context = context_for(workspace, "Create a report.")
    context.record_completion_final_evidence("agent_final_response")
    await context.resolve_completion_evidence()
    assert (
        context.assess_completion_contract(agent_claimed_finished=True).status
        == CompletionStatus.SATISFIED
    )
    target = workspace / "chosen.json"
    target.write_text("not json")
    saved = await invoke(
        context, "save_candidate", files={"chosen.json": "chosen.json"}
    )
    receipt = await invoke(
        context,
        "validate_candidate",
        candidate_id=saved["candidate_id"],
        checks=[{"id": "agent-json", "kind": "json", "path": "chosen.json"}],
    )
    assert receipt["eligible"] is False
    await context.resolve_completion_evidence()
    assert (
        context.assess_completion_contract(agent_claimed_finished=True).status
        != CompletionStatus.SATISFIED
    )
    target.write_text('{"value": 3}')
    await context.resolve_completion_evidence()
    assert (
        context.assess_completion_contract(agent_claimed_finished=True).status
        == CompletionStatus.SATISFIED
    )
