from __future__ import annotations

from pathlib import Path

import pytest

from aworld.tools.run_acp_phase1_validation import (
    ValidationStepResult,
    build_phase1_validation_steps,
    build_summary_payload,
)


def test_build_phase1_validation_steps_uses_expected_gate_order() -> None:
    steps = build_phase1_validation_steps(Path("/repo"))

    assert [step.id for step in steps] == [
        "pytest_acp",
        "acp_self_test",
        "validate_stdio_host",
    ]
    assert "tests/acp" in steps[0].command
    assert steps[1].command[-2:] == ["acp", "self-test"]
    assert "validate-stdio-host" in steps[2].command
    assert "--command" in steps[2].command


def test_build_summary_payload_marks_run_failed_when_any_step_fails() -> None:
    payload = build_summary_payload(
        [
            ValidationStepResult(
                id="pytest_acp",
                ok=True,
                command=["python", "-m", "pytest", "tests/acp", "-q"],
                returncode=0,
                stdout="127 passed",
                stderr="",
            ),
            ValidationStepResult(
                id="acp_self_test",
                ok=False,
                command=["python", "-m", "aworld_cli.main", "--no-banner", "acp", "self-test"],
                returncode=1,
                stdout='{"ok": false}',
                stderr="traceback",
            ),
        ]
    )

    assert payload["ok"] is False
    assert payload["summary"] == {"passed": 1, "failed": 1, "total": 2}
    assert payload["steps"][1]["stderr"] == "traceback"


@pytest.mark.parametrize("step_id", ["pytest_acp", "acp_self_test", "validate_stdio_host"])
def test_step_ids_are_stable(step_id: str) -> None:
    payload = build_summary_payload(
        [
            ValidationStepResult(
                id=step_id,
                ok=True,
                command=["echo", "ok"],
                returncode=0,
                stdout="ok",
                stderr="",
            )
        ]
    )

    assert payload["steps"][0]["id"] == step_id
