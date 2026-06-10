from __future__ import annotations

import pytest

from aworld.core.task import TaskResponse
from aworld.evaluations.execution import EvalExecutionMode, EvalExecutionSpec
from aworld.evaluations.execution_adapters import resolve_execution_adapter
from aworld.evaluations.eval_targets.agent_eval import AworldAgentEvalTarget
from aworld.evaluations.substrate import EvalCaseDef


async def _demo_program(case, spec, target):
    return {
        "status": "success",
        "answer": f"ran:{case.input['query']}",
        "completion": [{"role": "assistant", "content": "final"}],
        "trajectory": [{"role": "assistant", "content": "step"}],
        "usage": {"total_tokens": 7},
    }


@pytest.mark.asyncio
async def test_program_execution_adapter_normalizes_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "aworld.evaluations.execution_adapters.load_program_callable",
        lambda ref: _demo_program,
    )
    spec = EvalExecutionSpec(mode=EvalExecutionMode.PROGRAM, target_ref="pkg.module:run_case")
    adapter = resolve_execution_adapter(spec)

    state = await adapter.execute(
        case=EvalCaseDef(case_id="case-1", input={"query": "demo"}),
        target={"target_kind": "directory"},
        spec=spec,
    )

    assert state.case_id == "case-1"
    assert state.answer == "ran:demo"
    assert state.completion[0]["content"] == "final"
    assert state.trajectory[0]["content"] == "step"
    assert state.usage["total_tokens"] == 7


@pytest.mark.asyncio
async def test_program_execution_adapter_does_not_copy_case_input_into_state_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "aworld.evaluations.execution_adapters.load_program_callable",
        lambda ref: _demo_program,
    )
    spec = EvalExecutionSpec(mode=EvalExecutionMode.PROGRAM, target_ref="pkg.module:run_case")

    state = await resolve_execution_adapter(spec).execute(
        case=EvalCaseDef(case_id="case-1", input={"query": "demo", "large_blob": "x" * 1000}),
        target={"target_kind": "directory"},
        spec=spec,
    )

    assert state.metadata["_execution_mode"] == "program"
    assert state.metadata["_target"] == {"target_kind": "directory"}
    assert "query" not in state.metadata
    assert "large_blob" not in state.metadata


@pytest.mark.asyncio
async def test_task_execution_adapter_does_not_copy_case_input_into_state_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DemoTask:
        id = "task-1"

    async def fake_run_task(*, task):
        return TaskResponse(success=True, answer="ok")

    monkeypatch.setattr("aworld.evaluations.execution_adapters.Runners.run_task", fake_run_task)
    spec = EvalExecutionSpec(mode=EvalExecutionMode.TASK, target_config={"task": DemoTask()})

    state = await resolve_execution_adapter(spec).execute(
        case=EvalCaseDef(case_id="case-1", input={"query": "demo", "large_blob": "x" * 1000}),
        target={"target_kind": "task"},
        spec=spec,
    )

    assert state.metadata["_target"] == {"target_kind": "task"}
    assert "query" not in state.metadata
    assert "large_blob" not in state.metadata


@pytest.mark.asyncio
async def test_agent_eval_target_reuses_resolved_execution_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_specs = []

    class FakeAdapter:
        def __init__(self, expected_spec):
            self.expected_spec = expected_spec

        async def execute(self, *, case, target, spec):
            assert spec is self.expected_spec
            return TaskResponse(success=True, answer="ok")

    def fake_resolve(spec):
        seen_specs.append(spec)
        return FakeAdapter(spec)

    monkeypatch.setattr("aworld.evaluations.eval_targets.agent_eval.resolve_execution_adapter", fake_resolve)

    result = await AworldAgentEvalTarget(agent=object()).predict(0, {"query": "hello"})

    assert result["answer"] == "ok"
    assert len(seen_specs) == 1


def test_resolve_execution_adapter_rejects_missing_program_ref() -> None:
    with pytest.raises(ValueError, match="target_ref"):
        resolve_execution_adapter(EvalExecutionSpec(mode=EvalExecutionMode.PROGRAM))


def test_resolve_execution_adapter_rejects_command_style_program_ref() -> None:
    with pytest.raises(ValueError, match="importable callable"):
        resolve_execution_adapter(
            EvalExecutionSpec(mode=EvalExecutionMode.PROGRAM, target_ref="python script.py")
        )


@pytest.mark.parametrize(
    "spec",
    [
        EvalExecutionSpec(
            mode=EvalExecutionMode.PROGRAM,
            target_ref="pkg.module:run_case",
            runner_method="shell",
        ),
        EvalExecutionSpec(
            mode=EvalExecutionMode.PROGRAM,
            target_ref="pkg.module:run_case",
            target_config={"command": "python script.py"},
        ),
        EvalExecutionSpec(
            mode=EvalExecutionMode.PROGRAM,
            target_ref="pkg.module:run_case",
            target_config={"workflow": "external"},
        ),
    ],
)
def test_resolve_execution_adapter_rejects_unsupported_program_runtime_config(spec: EvalExecutionSpec) -> None:
    with pytest.raises(ValueError, match="unsupported program execution configuration"):
        resolve_execution_adapter(spec)


@pytest.mark.parametrize(
    "target_ref",
    [
        "script.py",
        "./script.py",
        "scripts/run.py",
        "script.py:main",
        "scripts.run.py:main",
    ],
)
def test_resolve_execution_adapter_rejects_path_style_program_ref(target_ref: str) -> None:
    with pytest.raises(ValueError, match="importable callable"):
        resolve_execution_adapter(
            EvalExecutionSpec(mode=EvalExecutionMode.PROGRAM, target_ref=target_ref)
        )
