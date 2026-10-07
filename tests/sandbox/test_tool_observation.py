from types import SimpleNamespace

from aworld.core.common import ActionResult
from aworld.sandbox.tool_observation import (
    SandboxToolObservationRuntime,
    canonical_tool_identity,
    classify_tool_effect,
)


def _context():
    return SimpleNamespace(task_id="task", task_epoch=1, session_id="session")


def test_internal_mcp_dispatcher_has_one_canonical_capability_identity() -> None:
    action = {
        "tool_name": "mcp",
        "action_name": "terminal__run_code",
        "params": {"code": "pwd"},
    }

    assert canonical_tool_identity(action) == ("terminal", "run_code")
    effect = classify_tool_effect(action)
    assert effect.identity == "terminal.run_code"
    assert effect.effect == "read_only"


def test_shell_classifier_fails_closed_for_pipeline_and_dynamic_code() -> None:
    for code in (
        "cat /app/a.py | head",
        "cat $TARGET",
        "python -c 'print(1)'",
    ):
        effect = classify_tool_effect(
            {
                "tool_name": "terminal",
                "action_name": "run_code",
                "params": {"code": code},
            }
        )
        assert effect.effect == "unknown"
        assert effect.cacheable is False


def test_known_mutation_advances_generation_but_unknown_does_not_claim_progress() -> None:
    runtime = SandboxToolObservationRuntime()
    context = _context()
    mutation = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "params": {"code": "cp source target"},
    }
    unknown = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "params": {"code": "python build.py"},
    }

    mutated = runtime.record(
        mutation,
        ActionResult(success=True, content="ok"),
        context=context,
    )
    uncertain = runtime.record(
        unknown,
        ActionResult(success=True, content="ok"),
        context=context,
    )

    assert mutated.metadata["sandbox_observation"]["workspace_mutated"] is True
    assert mutated.metadata["sandbox_observation"]["workspace_generation"] == 1
    assert uncertain.metadata["sandbox_observation"]["effect"] == "unknown"
    assert uncertain.metadata["sandbox_observation"]["workspace_mutated"] is None
    assert uncertain.metadata["sandbox_observation"]["workspace_generation"] == 2
