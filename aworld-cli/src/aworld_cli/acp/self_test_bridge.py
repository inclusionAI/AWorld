from __future__ import annotations

import asyncio

from aworld.models.model_response import Function, ModelResponse, ToolCall
from aworld.output.base import ChunkOutput, MessageOutput, ToolResultOutput


SELF_TEST_TEXT_PROMPT = "__acp_self_test_text__"
SELF_TEST_TOOL_PROMPT = "__acp_self_test_tool__"
SELF_TEST_TURN_ERROR_PROMPT = "__acp_self_test_turn_error__"
SELF_TEST_FINAL_ONLY_PROMPT = "__acp_self_test_final_only__"
SELF_TEST_SLOW_PROMPT = "__acp_self_test_slow__"


class DeterministicSelfTestOutputBridge:
    async def stream_outputs(self, *, record, prompt_text):
        if prompt_text == SELF_TEST_TEXT_PROMPT:
            yield ChunkOutput(
                data=ModelResponse(id="resp-text", model="aworld-cli/self-test", content="self-test"),
                metadata={},
            )
            return

        if prompt_text == SELF_TEST_TOOL_PROMPT:
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
            return

        if prompt_text == SELF_TEST_TURN_ERROR_PROMPT:
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
            return

        if prompt_text == SELF_TEST_FINAL_ONLY_PROMPT:
            yield MessageOutput(
                source=ModelResponse(
                    id="resp-final-only",
                    model="aworld-cli/self-test",
                    content="final-only",
                )
            )
            return

        if prompt_text == SELF_TEST_SLOW_PROMPT:
            await asyncio.sleep(10)
            yield MessageOutput(
                source=ModelResponse(
                    id="resp-slow",
                    model="aworld-cli/self-test",
                    content="slow-finished",
                )
            )
            return

        yield MessageOutput(
            source=ModelResponse(
                id=f"resp-fallback-{record.acp_session_id}",
                model="aworld-cli/self-test",
                content=prompt_text,
            )
        )
