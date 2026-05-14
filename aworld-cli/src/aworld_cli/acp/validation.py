from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .errors import AWORLD_ACP_SESSION_BUSY
from .stdio_harness import AcpStdioHarness
from ..steering import STEERING_CAPTURED_ACK


REQUIRED_PHASE1_CASE_IDS = (
    "initialize_handshake",
    "new_session_usable",
    "prompt_visible_text",
    "cancel_idle_noop",
    "tool_lifecycle_closes",
    "turn_error_pauses_for_steering",
    "turn_error_suppresses_followup_events",
    "post_turn_error_session_continues",
    "final_text_fallback",
    "prompt_busy_queued",
    "cancel_active_terminal",
    "stdout_protocol_only",
    "stderr_diagnostics_only",
)


class AcpValidationHarness(Protocol):
    stdout_lines: list[str]
    stderr_text: str

    async def send(self, message: dict[str, Any]) -> None: ...

    async def send_request(self, request_id: int, method: str, params: dict[str, Any]) -> None: ...

    async def read_response(self, request_id: int) -> dict[str, Any]: ...

    async def read_notification(self, method: str | None = None) -> dict[str, Any]: ...

    async def read_responses(self, request_ids: set[int]) -> dict[int, dict[str, Any]]: ...


@dataclass(frozen=True)
class Phase1ValidationProfile:
    visible_text_prompt: str
    visible_text_expected: str
    tool_prompt: str
    turn_error_prompt: str
    final_text_prompt: str
    final_text_expected: str
    slow_prompt: str
    competing_prompt: str = "competing-prompt"


def build_summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    passed = sum(1 for case in cases if case["ok"])
    failed = sum(1 for case in cases if not case["ok"])
    return {
        "ok": failed == 0,
        "summary": {"passed": passed, "failed": failed, "skipped": 0},
        "cases": cases,
    }


async def run_phase1_validation_against_stdio_command(
    *,
    command: list[str],
    cwd: str | Path,
    env: dict[str, str],
    profile: Phase1ValidationProfile,
    session_params: dict[str, Any] | None = None,
    startup_timeout_seconds: float | None = None,
    startup_retries: int = 0,
) -> dict[str, Any]:
    last_error: Exception | None = None
    total_attempts = max(1, startup_retries + 1)

    for attempt in range(1, total_attempts + 1):
        harness = AcpStdioHarness(command=command, cwd=str(cwd), env=env)
        try:
            async with harness:
                cases = await run_phase1_validation_cases(
                    harness,
                    profile=profile,
                    session_params=session_params,
                    startup_timeout_seconds=startup_timeout_seconds,
                )
            return build_summary(cases)
        except Exception as exc:
            last_error = exc
            if attempt >= total_attempts:
                break

    assert last_error is not None
    raise RuntimeError(
        f"ACP phase-1 validation failed during startup after {total_attempts} attempt(s): {last_error}"
    ) from last_error


async def run_phase1_validation_cases(
    harness: AcpValidationHarness,
    *,
    profile: Phase1ValidationProfile,
    session_params: dict[str, Any] | None = None,
    startup_timeout_seconds: float | None = None,
) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    session_id: str | None = None

    await harness.send_request(1, "initialize", {})
    initialize = await harness.read_response(1, timeout_seconds=startup_timeout_seconds)
    cases.append(
        {
            "id": "initialize_handshake",
            "ok": initialize.get("result", {}).get("serverInfo", {}).get("name") == "aworld-cli",
            "detail": initialize,
        }
    )

    await harness.send_request(2, "newSession", session_params or {"cwd": ".", "mcpServers": []})
    new_session = await harness.read_response(2, timeout_seconds=startup_timeout_seconds)
    session_id = new_session.get("result", {}).get("sessionId")
    cases.append(
        {
            "id": "new_session_usable",
            "ok": bool(session_id),
            "detail": new_session,
        }
    )

    if session_id:
        await harness.send(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "prompt",
                "params": {
                    "sessionId": session_id,
                    "prompt": {"content": [{"type": "text", "text": profile.visible_text_prompt}]},
                },
            }
        )
        notification = await harness.read_notification("sessionUpdate")
        prompt_result = await harness.read_response(3)
        cases.append(
            {
                "id": "prompt_visible_text",
                "ok": (
                    notification.get("method") == "sessionUpdate"
                    and notification.get("params", {})
                    .get("update", {})
                    .get("content", {})
                    .get("text")
                    == profile.visible_text_expected
                    and prompt_result.get("result", {}).get("status") == "completed"
                ),
                "detail": {
                    "notification": notification,
                    "result": prompt_result,
                },
            }
        )

        await harness.send(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "cancel",
                "params": {"sessionId": session_id},
            }
        )
        cancel_result = await harness.read_response(4)
        cases.append(
            {
                "id": "cancel_idle_noop",
                "ok": (
                    cancel_result.get("result", {}).get("ok") is True
                    and cancel_result.get("result", {}).get("status") == "noop"
                ),
                "detail": cancel_result,
            }
        )

        await harness.send(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "prompt",
                "params": {
                    "sessionId": session_id,
                    "prompt": {"content": [{"type": "text", "text": profile.tool_prompt}]},
                },
            }
        )
        tool_start = await harness.read_notification("sessionUpdate")
        tool_end = await harness.read_notification("sessionUpdate")
        tool_result = await harness.read_response(5)
        cases.append(
            {
                "id": "tool_lifecycle_closes",
                "ok": (
                    tool_start.get("method") == "sessionUpdate"
                    and tool_start.get("params", {}).get("update", {}).get("sessionUpdate") == "tool_call"
                    and tool_end.get("method") == "sessionUpdate"
                    and tool_end.get("params", {}).get("update", {}).get("sessionUpdate")
                    == "tool_call_update"
                    and tool_start.get("params", {}).get("update", {}).get("toolCallId")
                    == tool_end.get("params", {}).get("update", {}).get("toolCallId")
                    and tool_end.get("params", {}).get("update", {}).get("status") == "completed"
                    and tool_result.get("result", {}).get("status") == "completed"
                ),
                "detail": {
                    "start": tool_start,
                    "end": tool_end,
                    "result": tool_result,
                },
            }
        )

        await harness.send(
            {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "prompt",
                "params": {
                    "sessionId": session_id,
                    "prompt": {"content": [{"type": "text", "text": profile.turn_error_prompt}]},
                },
            }
        )
        turn_error_start = await harness.read_notification("sessionUpdate")
        turn_error_end = await harness.read_notification("sessionUpdate")
        pause_notice = await harness.read_notification("sessionUpdate")
        turn_error_result = await harness.read_response(10)
        cases.append(
            {
                "id": "turn_error_pauses_for_steering",
                "ok": (
                    turn_error_start.get("method") == "sessionUpdate"
                    and turn_error_start.get("params", {}).get("update", {}).get("sessionUpdate")
                    == "tool_call"
                    and turn_error_end.get("method") == "sessionUpdate"
                    and turn_error_end.get("params", {}).get("update", {}).get("sessionUpdate")
                    == "tool_call_update"
                    and turn_error_end.get("params", {}).get("update", {}).get("status") == "failed"
                    and turn_error_start.get("params", {}).get("update", {}).get("toolCallId")
                    == turn_error_end.get("params", {}).get("update", {}).get("toolCallId")
                    and turn_error_end.get("params", {})
                    .get("update", {})
                    .get("content", {})
                    .get("code")
                    == "AWORLD_ACP_REQUIRES_HUMAN"
                    and pause_notice.get("params", {}).get("update", {}).get("sessionUpdate")
                    == "agent_message_chunk"
                    and "Execution paused."
                    in pause_notice.get("params", {}).get("update", {}).get("content", {}).get("text", "")
                    and turn_error_result.get("result", {}).get("status") == "completed"
                ),
                "detail": {
                    "start": turn_error_start,
                    "end": turn_error_end,
                    "pause": pause_notice,
                    "result": turn_error_result,
                },
            }
        )
        try:
            unexpected_followup = await harness.read_notification(
                "sessionUpdate",
                timeout_seconds=0.1,
            )
        except RuntimeError as exc:
            cases.append(
                {
                    "id": "turn_error_suppresses_followup_events",
                    "ok": "timed out waiting for a JSON line" in str(exc),
                    "detail": {"reason": "timed_out_waiting_for_followup_event"},
                }
            )
        else:
            cases.append(
                {
                    "id": "turn_error_suppresses_followup_events",
                    "ok": False,
                    "detail": {"unexpected_notification": unexpected_followup},
                }
            )

        await harness.send(
            {
                "jsonrpc": "2.0",
                "id": 11,
                "method": "prompt",
                "params": {
                    "sessionId": session_id,
                    "prompt": {"content": [{"type": "text", "text": profile.visible_text_prompt}]},
                },
            }
        )
        post_error_notification = await harness.read_notification("sessionUpdate")
        post_error_result = await harness.read_response(11)
        cases.append(
            {
                "id": "post_turn_error_session_continues",
                "ok": (
                    post_error_notification.get("method") == "sessionUpdate"
                    and post_error_notification.get("params", {})
                    .get("update", {})
                    .get("content", {})
                    .get("text")
                    is not None
                    and profile.visible_text_prompt
                    in post_error_notification.get("params", {})
                    .get("update", {})
                    .get("content", {})
                    .get("text", "")
                    and post_error_result.get("result", {}).get("status") == "completed"
                ),
                "detail": {
                    "notification": post_error_notification,
                    "result": post_error_result,
                },
            }
        )

        await harness.send(
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "prompt",
                "params": {
                    "sessionId": session_id,
                    "prompt": {"content": [{"type": "text", "text": profile.final_text_prompt}]},
                },
            }
        )
        final_only_notification = await harness.read_notification("sessionUpdate")
        final_only_result = await harness.read_response(6)
        cases.append(
            {
                "id": "final_text_fallback",
                "ok": (
                    final_only_notification.get("method") == "sessionUpdate"
                    and final_only_notification.get("params", {})
                    .get("update", {})
                    .get("sessionUpdate")
                    == "agent_message_chunk"
                    and final_only_notification.get("params", {})
                    .get("update", {})
                    .get("content", {})
                    .get("text")
                    == profile.final_text_expected
                    and final_only_result.get("result", {}).get("status") == "completed"
                ),
                "detail": {
                    "notification": final_only_notification,
                    "result": final_only_result,
                },
            }
        )

        await harness.send(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "prompt",
                "params": {
                    "sessionId": session_id,
                    "prompt": {"content": [{"type": "text", "text": profile.slow_prompt}]},
                },
            }
        )
        await asyncio.sleep(0.05)
        await harness.send(
            {
                "jsonrpc": "2.0",
                "id": 8,
                "method": "prompt",
                "params": {
                    "sessionId": session_id,
                    "prompt": {"content": [{"type": "text", "text": profile.competing_prompt}]},
                },
            }
        )
        await harness.send(
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "cancel",
                "params": {"sessionId": session_id},
            }
        )
        seen_by_id = await harness.read_responses({7, 8, 9})
        busy_session_update = await harness.read_notification("sessionUpdate")

        cases.append(
            {
                "id": "prompt_busy_queued",
                "ok": (
                    seen_by_id[8].get("result", {}).get("status") == "queued"
                    and busy_session_update.get("params", {}).get("update", {}).get("sessionUpdate")
                    == "agent_message_chunk"
                    and busy_session_update.get("params", {}).get("update", {}).get("content", {}).get("text")
                    == STEERING_CAPTURED_ACK
                ),
                "detail": {
                    "response": seen_by_id[8],
                    "update": busy_session_update,
                },
            }
        )
        cases.append(
            {
                "id": "cancel_active_terminal",
                "ok": (
                    seen_by_id[9].get("result", {}).get("status") == "cancelled"
                    and seen_by_id[7].get("result", {}).get("status") == "cancelled"
                ),
                "detail": {
                    "cancel": seen_by_id[9],
                    "prompt": seen_by_id[7],
                },
            }
        )

    cases.append(
        {
            "id": "stdout_protocol_only",
            "ok": all(
                line.strip().startswith("{") and line.strip().endswith("}")
                for line in harness.stdout_lines
            ),
            "detail": {"frame_count": len(harness.stdout_lines)},
        }
    )
    cases.append(
        {
            "id": "stderr_diagnostics_only",
            "ok": all('"jsonrpc"' not in line for line in harness.stderr_text.splitlines()),
            "detail": {"stderr": harness.stderr_text},
        }
    )

    return cases
