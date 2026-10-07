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
        "python -c \"__import__('os').remove('/app/a.py')\"",
        "python -c 'print(1)'\nrm /app/a.py",
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


def test_shell_classifier_accepts_provably_read_only_inline_python() -> None:
    for code in (
        "python -c 'print(1)'",
        "cd /app && python3 -c \"from pathlib import Path; print(Path('a').read_text())\"",
        "cd /app && python -c \"\nimport cv2, numpy as np\ncap = cv2.VideoCapture('example.mp4')\nprint(np.array([cap.get(1)]).max())\n\"",
        "cd /app && python -c \"print(1 > 0)\" 2>&1 | head -20",
        "cd /app && python -c \"print(1)\" 2>/dev/null",
    ):
        effect = classify_tool_effect(
            {
                "tool_name": "terminal",
                "action_name": "run_code",
                "params": {"code": code},
            }
        )
        assert effect.effect == "read_only"
        assert effect.cacheable is True


def test_shell_classifier_rejects_inline_python_file_mutation() -> None:
    for code in (
        "python -c \"open('/app/a.py', 'w').write('x')\"",
        "python -c \"from pathlib import Path; Path('/app/a.py').write_text('x')\"",
        "python -c \"import os; os.remove('/app/a.py')\"",
        "python -c \"import numpy as np; np.save('/app/a.npy', np.array([1]))\"",
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


def test_shell_classifier_does_not_treat_fd_redirection_as_file_mutation() -> None:
    effect = classify_tool_effect(
        {
            "tool_name": "terminal",
            "action_name": "run_code",
            "params": {"code": "python inspect.py 2>&1 | head -20"},
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
