from __future__ import annotations

import asyncio

from aworld.models.model_response import Function, ModelResponse, ToolCall
from aworld.output.base import ChunkOutput, MessageOutput, ToolResultOutput

from ..steering import STEERING_CAPTURED_ACK, SteeringCoordinator


SELF_TEST_TEXT_PROMPT = "__acp_self_test_text__"
SELF_TEST_TOOL_PROMPT = "__acp_self_test_tool__"
SELF_TEST_TURN_ERROR_PROMPT = "__acp_self_test_turn_error__"
SELF_TEST_FINAL_ONLY_PROMPT = "__acp_self_test_final_only__"
SELF_TEST_SLOW_PROMPT = "__acp_self_test_slow__"


class DeterministicSelfTestOutputBridge:
    def __init__(self) -> None:
        self._steering = SteeringCoordinator()

    def queue_steering(self, *, record, text: str) -> str:
        self._steering.begin_task(record.aworld_session_id, f"self-test-{record.aworld_session_id}")
        self._steering.enqueue_text(record.aworld_session_id, text)
        self._steering.request_interrupt(record.aworld_session_id)
        return STEERING_CAPTURED_ACK

    def prepare_paused_resume_prompt(self, *, record, text: str) -> tuple[str, list[object]]:
        self._steering.begin_task(record.aworld_session_id, f"self-test-{record.aworld_session_id}")
        self._steering.enqueue_text(record.aworld_session_id, text)
        follow_up_prompt, drained_items, _interrupt_requested = self._steering.consume_terminal_fallback(
            record.aworld_session_id
        )
        if not follow_up_prompt:
            raise ValueError("expected paused steering follow-up prompt")
        return follow_up_prompt, drained_items

    async def stream_outputs(self, *, record, prompt_text):
        current_prompt = prompt_text
        while True:
            if current_prompt == SELF_TEST_TEXT_PROMPT:
                yield ChunkOutput(
                    data=ModelResponse(id="resp-text", model="aworld-cli/self-test", content="self-test"),
                    metadata={},
                )
            elif current_prompt == SELF_TEST_TOOL_PROMPT:
                tool_call = ToolCall(
                    id="self-test-tool-1",
                    function=Function(name="shell", arguments='{"command":"pwd"}'),
                )
                yield MessageOutput(
                    source=ModelResponse(
                        id="resp-tool-start",
                        model="aworld-cli/self-test",
                        content="",
                        tool_calls=[tool_call],
                    )
                )
                yield ToolResultOutput(
                    tool_name="shell",
                    data={"cwd": record.cwd},
                    origin_tool_call=tool_call,
                )
            elif current_prompt == SELF_TEST_TURN_ERROR_PROMPT:
                tool_call = ToolCall(
                    id="self-test-tool-error-1",
                    function=Function(name="shell", arguments='{"command":"pwd"}'),
                )
                yield MessageOutput(
                    source=ModelResponse(
                        id="resp-tool-error-start",
                        model="aworld-cli/self-test",
                        content="",
                        tool_calls=[tool_call],
                    )
                )
                yield {
                    "event_type": "turn_error",
                    "seq": 2,
                    "code": "AWORLD_ACP_REQUIRES_HUMAN",
                    "message": "Human approval/input flow is not bridged in phase 1.",
                    "retryable": True,
                    "origin": "runtime",
                }
                yield MessageOutput(
                    source=ModelResponse(
                        id="resp-late-text",
                        model="aworld-cli/self-test",
                        content="late-text",
                    )
                )
                yield MessageOutput(
                    source=ModelResponse(
                        id="resp-late-tool",
                        model="aworld-cli/self-test",
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="self-test-tool-error-2",
                                function=Function(name="shell", arguments='{"command":"ls"}'),
                            )
                        ],
                    )
                )
            elif current_prompt == SELF_TEST_FINAL_ONLY_PROMPT:
                yield MessageOutput(
                    source=ModelResponse(
                        id="resp-final-only",
                        model="aworld-cli/self-test",
                        content="final-only",
                    )
                )
            elif current_prompt == SELF_TEST_SLOW_PROMPT:
                await asyncio.sleep(10)
                yield MessageOutput(
                    source=ModelResponse(
                        id="resp-slow",
                        model="aworld-cli/self-test",
                        content="slow-finished",
                    )
                )
            else:
                yield MessageOutput(
                    source=ModelResponse(
                        id=f"resp-fallback-{record.acp_session_id}",
                        model="aworld-cli/self-test",
                        content=current_prompt,
                    )
                )

            follow_up_prompt, _drained_items, _interrupt_requested = self._steering.consume_terminal_fallback(
                record.aworld_session_id
            )
            if not follow_up_prompt:
                self._steering.end_task(record.aworld_session_id, clear_pending=True)
                return
            current_prompt = follow_up_prompt
