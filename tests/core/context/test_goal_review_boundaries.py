import sys

import pytest

from aworld.core.context.base import Context
from aworld.core.context.compiler import CompletionContract, CompletionMode, ValidationCommand, CompletionStatus
from aworld.core.context.compiler.work_state import advance_adaptive_work_state, build_adaptive_work_state_entry
from aworld.core.context.work_progress import record_budget_handoff
from aworld_cli.core.runtime_completion import configure_goal_completion, resolve_runtime_completion_evidence


@pytest.mark.asyncio
async def test_goal_validation_cannot_delete_a_failing_caller_check_by_id_prefix(tmp_path):
    context=Context(task_id="validation")
    caller_check=ValidationCommand(
        command_id="goal-verify-1", argv=(sys.executable,"-c","raise SystemExit(4)"), cwd=str(tmp_path),
    )
    context.configure_completion_contract(
        CompletionContract(required_artifacts=(), immutable_inputs=(),
                           validation_commands=(caller_check,), max_evidence_age_seconds=None,
                           required_final_evidence=("agent_final_response",)), mode=CompletionMode.ENFORCE,
        evidence_resolver=resolve_runtime_completion_evidence,
    )
    contract=configure_goal_completion(context,verification_commands=["true"],workspace_path=tmp_path)
    assert caller_check in contract.validation_commands
    assert len({c.command_id for c in contract.validation_commands}) == 2
    await context.resolve_completion_evidence()
    context.record_completion_final_evidence("agent_final_response")
    assert context.assess_completion_contract(agent_claimed_finished=True).status is not CompletionStatus.SATISFIED
    # Replacing this goal's own checks still retains the original caller check.
    changed=configure_goal_completion(context,verification_commands=["printf updated"],workspace_path=tmp_path)
    assert caller_check in changed.validation_commands
    assert len(changed.validation_commands) == 2


@pytest.mark.parametrize("code", [
    "echo $(touch result.txt)",
    "echo `touch result.txt`",
    "rg --pre 'python mutate.py' pattern input.txt",
    "rg --pre=./mutate pattern input.txt",
    "sed -n 's/a/b/w output.txt' input.txt",
    "sed -n '1e touch result.txt' input.txt",
    "echo unchanged\ntouch result.txt",
    "cat missing.txt || touch result.txt",
    "echo unchanged >& result.txt",
])
def test_side_effect_capable_shell_is_never_read_repetition_evidence(code):
    context=Context(task_id="work")
    state={"scope":{"task_id":"work","task_epoch":context.task_epoch}}
    entry=build_adaptive_work_state_entry(
        tool_name="terminal",
        actions=[{"tool_name":"terminal","action_name":"run_code","params":{"code":code}}],
        observation={"action_result":[{"tool_name":"terminal","success":True,"content":"same stdout"}]},
    )
    for _ in range(3):
        state=advance_adaptive_work_state(state,entry)
    assert not state.get("repeated_read_evidence")
    context.context_info["adaptive_work_state:agent"]=state
    assert record_budget_handoff(context,"agent") is True
