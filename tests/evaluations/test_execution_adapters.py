from __future__ import annotations

import pytest

from aworld.evaluations.execution import EvalExecutionMode, EvalExecutionSpec
from aworld.evaluations.execution_adapters import resolve_execution_adapter
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
