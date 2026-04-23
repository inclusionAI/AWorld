from __future__ import annotations

import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_cli.acp.stdio_harness import AcpStdioHarness, local_server_env
from aworld_cli.acp import validation as validation_module
from aworld_cli.acp.validation import (
    REQUIRED_PHASE1_CASE_IDS,
    build_summary,
    run_phase1_validation_against_stdio_command,
    run_phase1_validation_cases,
)
from aworld_cli.acp.validation_profiles import SELF_TEST_PHASE1_PROFILE


@pytest.mark.asyncio
async def test_run_phase1_validation_cases_matches_required_case_contract() -> None:
    harness = AcpStdioHarness.for_local_server(extra_env={"AWORLD_ACP_SELF_TEST_BRIDGE": "1"})

    async with harness:
        cases = await run_phase1_validation_cases(
            harness,
            profile=SELF_TEST_PHASE1_PROFILE,
            session_params={"cwd": ".", "mcpServers": []},
        )

    assert [case["id"] for case in cases] == list(REQUIRED_PHASE1_CASE_IDS)
    assert build_summary(cases)["ok"] is True
    turn_error_case = next(case for case in cases if case["id"] == "turn_error_terminal")
    assert turn_error_case["detail"]["result"]["error"]["message"] == "AWORLD_ACP_REQUIRES_HUMAN"
    assert turn_error_case["detail"]["end"]["params"]["update"]["status"] == "failed"
    assert turn_error_case["detail"]["result"]["error"]["data"]["message"] == "Human approval/input flow is not bridged in phase 1."
    suppression_case = next(case for case in cases if case["id"] == "turn_error_suppresses_followup_events")
    assert suppression_case["detail"]["reason"] == "timed_out_waiting_for_followup_event"
    continuity_case = next(case for case in cases if case["id"] == "post_turn_error_session_continues")
    assert continuity_case["detail"]["notification"]["params"]["update"]["content"]["text"] == "self-test"
    assert continuity_case["detail"]["result"]["result"]["status"] == "completed"


@pytest.mark.asyncio
async def test_run_phase1_validation_against_stdio_command_returns_summary() -> None:
    payload = await run_phase1_validation_against_stdio_command(
        command=[sys.executable, "-m", "aworld_cli.main", "--no-banner", "acp"],
        cwd=Path(__file__).resolve().parents[2],
        env=local_server_env(extra_env={"AWORLD_ACP_SELF_TEST_BRIDGE": "1"}),
        profile=SELF_TEST_PHASE1_PROFILE,
        session_params={"cwd": ".", "mcpServers": []},
    )

    assert payload["ok"] is True
    assert payload["summary"]["passed"] == len(REQUIRED_PHASE1_CASE_IDS)


@pytest.mark.asyncio
async def test_run_phase1_validation_against_stdio_command_retries_startup_once(monkeypatch) -> None:
    attempts: list[int] = []

    class FakeHarness:
        def __init__(self, *, command, cwd, env) -> None:
            self.command = command
            self.cwd = cwd
            self.env = env
            self.stdout_lines = []
            self.stderr_text = ""
            self.attempt = len(attempts) + 1

        async def __aenter__(self):
            attempts.append(self.attempt)
            if self.attempt == 1:
                raise RuntimeError("startup timeout")
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def fake_run_cases(harness, *, profile, session_params=None, startup_timeout_seconds=None):
        return [{"id": "initialize_handshake", "ok": True}]

    monkeypatch.setattr(validation_module, "AcpStdioHarness", FakeHarness)
    monkeypatch.setattr(validation_module, "run_phase1_validation_cases", fake_run_cases)

    payload = await run_phase1_validation_against_stdio_command(
        command=["python", "-m", "demo_host"],
        cwd=Path(__file__).resolve().parents[2],
        env={},
        profile=SELF_TEST_PHASE1_PROFILE,
        startup_timeout_seconds=0.2,
        startup_retries=1,
    )

    assert payload["ok"] is True
    assert attempts == [1, 2]
