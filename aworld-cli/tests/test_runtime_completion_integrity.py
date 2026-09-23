import hashlib
import json
import sys

import pytest

from aworld.core.context.base import Context
from aworld.core.context.compiler import ArtifactRequirement, CompletionContract, CompletionMode, ValidationCommand
from aworld_cli.core.runtime_completion import configure_runtime_completion, resolve_runtime_completion_evidence
from aworld_cli.executors.continuous import ContinuousExecutor
from aworld_cli.run_outcome import DirectRunOutcome
from types import SimpleNamespace


def test_explicit_outputs_enforce_without_natural_language_guessing(monkeypatch, tmp_path):
    monkeypatch.delenv("AWORLD_COMPLETION_MODE", raising=False)
    monkeypatch.setenv("AWORLD_REQUIRED_ARTIFACTS_JSON", '["result.json"]')
    monkeypatch.setenv("AWORLD_INFER_REQUIRED_ARTIFACTS", "true")
    context = Context(task_id="explicit")
    contract = configure_runtime_completion(context, request="Maybe write optional.csv if needed", workspace_path=tmp_path)
    assert context.completion_mode is CompletionMode.ENFORCE
    assert [r.path for r in contract.required_artifacts] == [str(tmp_path / "result.json")]


@pytest.mark.asyncio
async def test_text_saying_pass_does_not_override_real_validation_failure(monkeypatch, tmp_path):
    monkeypatch.delenv("AWORLD_COMPLETION_MODE", raising=False)
    monkeypatch.delenv("AWORLD_REQUIRED_ARTIFACTS_JSON", raising=False)
    monkeypatch.setenv("AWORLD_VALIDATION_COMMANDS_JSON", json.dumps([
        {"command_id":"check", "argv":[sys.executable, "-c", "print('ALL TESTS PASSED'); raise SystemExit(2)"]}
    ]))
    context = Context(task_id="false-pass")
    contract = configure_runtime_completion(context, request="validate work", workspace_path=tmp_path)
    context.record_completion_final_evidence("agent_final_response")
    await context.resolve_completion_evidence()
    assessment = context.assess_completion_contract(agent_claimed_finished=True)
    assert "self_check_failed" in assessment.reason_codes
    assert context._completion_self_checks[-1].exit_code == 2
    assert contract.validation_commands[0].cwd == str(tmp_path)


@pytest.mark.asyncio
async def test_runtime_checks_expected_hash_and_drains_large_validation_output(tmp_path):
    path = tmp_path / "result.txt"
    path.write_text("wrong bytes")
    contract = CompletionContract(
        required_artifacts=(ArtifactRequirement("result", str(path), expected_hash="sha256:" + "0" * 64),),
        immutable_inputs=(), validation_commands=(ValidationCommand("check", (sys.executable, "-c", "import sys; sys.stdout.write('x' * 2000000)")),),
        max_evidence_age_seconds=30, required_final_evidence=(),
    )
    context = Context(task_id="hash")
    context.configure_completion_contract(contract, mode=CompletionMode.ENFORCE)
    await resolve_runtime_completion_evidence(context, contract)
    result = context.assess_completion_contract(agent_claimed_finished=True)
    assert "artifact_hash_mismatch" in result.reason_codes
    assert context._completion_self_checks[-1].output_hash == "sha256:" + hashlib.sha256(b"x" * 2000000).hexdigest()


@pytest.mark.asyncio
async def test_validation_timeout_is_failure_not_success(tmp_path):
    contract = CompletionContract(required_artifacts=(), immutable_inputs=(),
        validation_commands=(ValidationCommand("slow", (sys.executable, "-c", "import time; time.sleep(30)"), timeout_seconds=1),),
        max_evidence_age_seconds=30, required_final_evidence=())
    context = Context(task_id="timeout")
    await resolve_runtime_completion_evidence(context, contract)
    assert context._completion_self_checks[-1].exit_code == 124


@pytest.mark.parametrize("status", ["incomplete", "budget_exhausted"])
def test_task_response_status_survives_cli_projection(status):
    response = SimpleNamespace(semantic_status=status, status=status,
        completion_reason="model_output_truncated", recoverable=True, failure_origin="task")
    result = ContinuousExecutor._attach_task_response_evidence({"success":False}, response)
    assert result["semantic_status"] == status
    outcome = DirectRunOutcome.from_summary({"results":[result]}, status="task_failed",
        failure_record={"stage":"agent_execution", "error_code":"agent_" + status})
    assert not outcome.succeeded
    assert outcome.process_exit_code != 0
    assert outcome.to_dict()["semantic_status"] == "task_failed"
    assert outcome.to_dict()["failure"]["error_code"] == "agent_" + status
    assert result["recoverable"] is True


@pytest.mark.asyncio
async def test_repeated_prose_is_not_completion():
    async def chat(*args, **kwargs):
        return "I need to continue fixing this."
    executor = ContinuousExecutor(SimpleNamespace(chat=chat))
    first = await executor.run_iteration(1, "work", non_interactive=True)
    second = await executor.run_iteration(2, "work", non_interactive=True)
    assert first["completed"] is False
    assert second["completed"] is False


@pytest.mark.asyncio
async def test_batch_counts_typed_incomplete_as_failure():
    import asyncio
    from aworld_cli.plugins.batch.executor import BatchExecutor
    async def chat(*args, **kwargs):
        return "Task complete!"
    executor = SimpleNamespace(chat=chat, last_task_response=SimpleNamespace(
        success=False, semantic_status="budget_exhausted", completion_reason="agent_loop_budget_exhausted", recoverable=True))
    async def create(*args):
        return executor
    batch = BatchExecutor()
    batch._extract_usage_metrics = lambda response, executor: (0, 0)
    result = await batch._execute_single_task(semaphore=asyncio.Semaphore(1), record={},
        builder=SimpleNamespace(build_task=lambda record: {"record_id":"one", "prompt":"work"}),
        config=SimpleNamespace(execution=SimpleNamespace(timeout_per_task=None), agent=SimpleNamespace(name="agent")),
        agent_info=object(), runtime=SimpleNamespace(_create_executor=create))
    assert result["success"] is False
    assert result["semantic_status"] == "budget_exhausted"


@pytest.mark.asyncio
async def test_goal_verify_pipefail_blocks_claimed_completion(tmp_path):
    import shlex
    from aworld_cli.core.runtime_completion import configure_goal_completion
    context = Context(task_id="goal-verify")
    command = shlex.join([sys.executable, "-c", "print('PASS'); raise SystemExit(3)"]) + " | tail -1"
    contract = configure_goal_completion(context, verification_commands=[command], workspace_path=tmp_path)
    context.record_completion_final_evidence("agent_final_response")
    await context.resolve_completion_evidence()
    assert context.completion_mode is CompletionMode.ENFORCE
    assert context._completion_self_checks[-1].exit_code == 3
    assert "self_check_failed" in context.assess_completion_contract(agent_claimed_finished=True).reason_codes


@pytest.mark.asyncio
@pytest.mark.parametrize("semantic", ["incomplete", "budget_exhausted"])
async def test_direct_cli_returns_incomplete_attempt_to_verifier(
    monkeypatch, capsys, semantic
):
    from aworld_cli import main as main_module
    class Runtime:
        def __init__(self, *args, **kwargs): pass
        async def _load_agents(self): return [SimpleNamespace(name="Aworld")]
        async def _create_executor(self, agent): return SimpleNamespace()
        def _bind_scheduler_default_agent(self, name): pass
        def _restore_executor_session(self, *args, **kwargs): pass
    class Continuous:
        def __init__(self, *args, **kwargs): pass
        async def run_continuous(self, **kwargs):
            return {"results":[{"success":False, "semantic_status":semantic,
                "recoverable":True, "failure_origin":"task", "llm_calls":[{"request_id":"one"}],
                "trajectory":[{"meta":{"step":1}}]}]}
    monkeypatch.setattr(main_module, "CliRuntime", Runtime)
    monkeypatch.setattr(main_module, "ContinuousExecutor", Continuous)
    monkeypatch.setattr("aworld.core.scheduler.get_scheduler", lambda: object())
    result = await main_module._run_direct_mode(prompt="work", agent_name="Aworld", non_interactive=True)
    payload = result.to_dict()
    assert result.summary["results"][0]["semantic_status"] == semantic
    assert payload["semantic_status"] == "succeeded"
    assert payload["process_exit_code"] == 0
    assert "failure" not in payload
    assert 'AWORLD_AGENT_TERMINATION=' in capsys.readouterr().err
