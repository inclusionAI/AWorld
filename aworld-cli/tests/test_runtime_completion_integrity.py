import hashlib
import json
import sys
import time
from types import SimpleNamespace

import pytest

from aworld.core.common import ActionModel
from aworld.core.context.base import Context
from aworld.core.context.execution_state import record_execution_state
from aworld.core.context.compiler import ArtifactRequirement, CompletionContract, CompletionMode, ValidationCommand
from aworld.core.event.base import Constants, Message, TopicType
from aworld.core.task import Task, TaskResponse
from aworld.runners.handler.agent import DefaultAgentHandler
from aworld.runners.handler.task import DefaultTaskHandler
from aworld_cli import main as main_module
from aworld_cli.atif import build_atif_trajectory
from aworld_cli.core.runtime_completion import configure_runtime_completion, resolve_runtime_completion_evidence
from aworld_cli.executors.continuous import ContinuousExecutor
from aworld_cli.run_outcome import DirectRunOutcome, DirectRunStatus


def test_explicit_outputs_can_be_observed_without_enforcement(monkeypatch, tmp_path):
    monkeypatch.setenv("AWORLD_COMPLETION_MODE", "observe")
    monkeypatch.setenv("AWORLD_REQUIRED_ARTIFACTS_JSON", '["result.json"]')
    monkeypatch.setenv("AWORLD_INFER_REQUIRED_ARTIFACTS", "true")
    context = Context(task_id="explicit")
    contract = configure_runtime_completion(context, request="Maybe write optional.csv if needed", workspace_path=tmp_path)
    assert context.completion_mode is CompletionMode.OBSERVE
    assert context.context_info["completion_enforcement_explicit"] is False
    assert [r.path for r in contract.required_artifacts] == [str(tmp_path / "result.json")]


@pytest.mark.asyncio
async def test_text_saying_pass_does_not_override_real_validation_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("AWORLD_COMPLETION_MODE", "observe")
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
@pytest.mark.parametrize(
    "semantic_status,completion_reason,expected_outcome",
    [
        (
            "incomplete",
            "model_output_truncated",
            DirectRunStatus.INCOMPLETE,
        ),
        (
            "budget_exhausted",
            "long_horizon_generation_budget_exhausted",
            DirectRunStatus.BUDGET_EXHAUSTED,
        ),
    ],
)
async def test_team_handler_preserves_recoverable_stop_through_direct_outcome(
    monkeypatch,
    semantic_status,
    completion_reason,
    expected_outcome,
):
    task = Task(id="recoverable", name="recoverable", input="finish work")
    # Event processing may clone or fan in contexts. The terminal execution
    # state is written on the event context, while TaskHandler owns the runner
    # context. This is the shape that exposed truncated responses as success in
    # a real direct run.
    class NonMergingContext(Context):
        def merge_context(self, other_context):
            # Completion truth must survive independently from best-effort
            # merging of the broader data plane.
            return None

    context = NonMergingContext(task_id=task.id)
    context.set_task(task)
    event_context = Context(task_id=task.id)
    event_context.set_task(task)

    class RootAgent:
        finished = False

        @staticmethod
        def id():
            return "root-agent"

    root = RootAgent()
    swarm = SimpleNamespace(
        agents={root.id(): root},
        agent_graph=SimpleNamespace(root_agent=root),
        communicate_agent=root,
        min_call_num=0,
        max_steps=100,
        cur_step=1,
        finished=False,
    )
    agent_runner = SimpleNamespace(
        swarm=swarm,
        endless_threshold=3,
        task=task,
    )

    class TaskRunner:
        def __init__(self):
            self.task = task
            self.context = context
            self.start_time = time.time()
            self._task_response = None
            self.stopped = False

        async def stop(self):
            self.stopped = True

    async def route_through_handlers() -> TaskResponse:
        agent_handler = DefaultAgentHandler(agent_runner)
        agent_handler.agent_calls.append(root.id())
        record_execution_state(
            event_context,
            root.id(),
            semantic_status,
            completion_reason,
            recoverable=True,
        )
        source = Message(
            category=Constants.AGENT,
            sender=root.id(),
            receiver=root.id(),
            headers={"context": event_context},
        )
        routed = [
            event
            async for event in agent_handler._team_stop_check(
                ActionModel(
                    agent_name=root.id(),
                    policy_info="Work remains incomplete.",
                ),
                source,
            )
        ]
        assert len(routed) == 1
        assert routed[0].topic == TopicType.FINISHED

        task_runner = TaskRunner()
        task_handler = DefaultTaskHandler(task_runner)
        task_events = [
            event async for event in task_handler._do_handle(routed[0])
        ]
        response = task_events[-1].payload
        assert isinstance(response, TaskResponse)
        assert task_runner.stopped is True
        return response

    class BridgeExecutor:
        def __init__(self):
            self.session_id = "recoverable-session"
            self.context = context
            self.last_task_response = None
            self.last_task_interrupted = False
            self.last_skill_activation_evidence = ()
            self.last_llm_usage = None
            self.chat_count = 0

        async def chat(self, *args, **kwargs):
            self.chat_count += 1
            response = await route_through_handlers()
            response.llm_calls = [{"request_id": "provider-1"}]
            self.last_task_response = response
            return response.answer

    bridge = BridgeExecutor()

    class Runtime:
        def __init__(self, *args, **kwargs):
            self._scheduler = None

        async def _load_agents(self):
            return [SimpleNamespace(name="Aworld")]

        async def _create_executor(self, agent):
            return bridge

        def _bind_scheduler_default_agent(self, name):
            return None

        def _restore_executor_session(self, *args, **kwargs):
            return None

    monkeypatch.setattr(main_module, "CliRuntime", Runtime)
    monkeypatch.setattr("aworld.core.scheduler.get_scheduler", lambda: object())

    outcome = await main_module._run_direct_mode(
        prompt="finish work",
        agent_name="Aworld",
        non_interactive=True,
    )

    response = bridge.last_task_response
    assert isinstance(response, TaskResponse)
    assert response.success is False
    assert response.status == semantic_status
    assert response.semantic_status == semantic_status
    assert response.failure_origin == "task"
    assert response.failure_code == completion_reason
    assert response.recoverable is True
    assert bridge.chat_count == 1
    assert outcome.status is expected_outcome
    assert outcome.succeeded is False
    assert outcome.process_exit_code == 0
    assert outcome.failure_record is None
    assert "failure" not in outcome.to_dict()
    projected = outcome.summary["results"][-1]
    assert projected["semantic_status"] == semantic_status
    assert projected["completion_reason"] == completion_reason
    assert projected["recoverable"] is True
    serialized_summary = json.dumps(outcome.summary, default=str)
    assert "runtime_exception" not in serialized_summary
    assert "infrastructure_failed" not in serialized_summary
    atif = build_atif_trajectory(
        projected,
        prompt="finish work",
        agent_name="Aworld",
        agent_version="test",
        run_outcome=outcome.to_dict(),
    )
    assert atif["extra"]["aworld"]["completion_state"] == "incomplete"
    assert (
        atif["extra"]["aworld"]["run_outcome"]["semantic_status"]
        == expected_outcome.value
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("foreign_status", "runner_status", "expected_status", "expected_success"),
    (
        ("succeeded", "incomplete", "incomplete", False),
        ("incomplete", "succeeded", "succeeded", True),
    ),
)
async def test_task_handler_rejects_foreign_terminal_execution_state(
    foreign_status,
    runner_status,
    expected_status,
    expected_success,
):
    task = Task(id="runner-task", name="runner-task", input="work")
    runner_context = Context(task_id=task.id, task_epoch=3)
    runner_context.set_task(task)
    record_execution_state(
        runner_context,
        "root-agent",
        runner_status,
        "runner-state",
        recoverable=runner_status != "succeeded",
    )
    foreign_context = Context(task_id="other-task", task_epoch=3)
    foreign_context.set_task(
        Task(id="other-task", name="other-task", input="other")
    )
    record_execution_state(
        foreign_context,
        "root-agent",
        foreign_status,
        "foreign-state",
        recoverable=foreign_status != "succeeded",
    )

    class TaskRunner:
        def __init__(self):
            self.task = task
            self.context = runner_context
            self.start_time = time.time()
            self._task_response = None

        async def stop(self):
            return None

    handler = DefaultTaskHandler(TaskRunner())
    events = [
        event
        async for event in handler._do_handle(
            Message(
                category=Constants.TASK,
                payload="foreign terminal answer",
                headers={"context": foreign_context},
                topic=TopicType.FINISHED,
            )
        )
    ]
    response = events[-1].payload
    assert response.success is expected_success
    assert response.semantic_status == expected_status
    assert response.completion_reason == (
        "runner-state" if expected_status != "succeeded" else None
    )


@pytest.mark.asyncio
async def test_task_handler_rejects_wrong_epoch_terminal_execution_state():
    task = Task(id="runner-task", name="runner-task", input="work")
    runner_context = Context(task_id=task.id, task_epoch=7)
    runner_context.set_task(task)
    record_execution_state(
        runner_context,
        "root-agent",
        "incomplete",
        "runner-state",
        recoverable=True,
    )
    stale_context = Context(task_id=task.id, task_epoch=6)
    stale_context.set_task(task)
    record_execution_state(
        stale_context,
        "root-agent",
        "succeeded",
        "stale-success",
        recoverable=False,
    )

    class TaskRunner:
        def __init__(self):
            self.task = task
            self.context = runner_context
            self.start_time = time.time()
            self._task_response = None

        async def stop(self):
            return None

    handler = DefaultTaskHandler(TaskRunner())
    events = [
        event
        async for event in handler._do_handle(
            Message(
                category=Constants.TASK,
                payload="stale terminal answer",
                headers={"context": stale_context},
                topic=TopicType.FINISHED,
            )
        )
    ]
    response = events[-1].payload
    assert response.success is False
    assert response.semantic_status == "incomplete"
    assert response.completion_reason == "runner-state"


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
    assert payload["semantic_status"] == semantic
    assert payload["process_exit_code"] == 0
    assert "failure" not in payload
    assert 'AWORLD_AGENT_TERMINATION=' in capsys.readouterr().err
