import pytest

from aworld.core.common import ActionResult
from aworld.core.context.amni.utils.memoryutils import MemoryItemConvertor


class _FakeContext:
    session_id = "session-1"
    user_id = "user-1"
    task_id = "task-1"


@pytest.mark.asyncio
async def test_memory_item_convertor_compacts_large_tool_result_for_amni_history():
    raw_output = "HEADER\n" + ("A" * 9000) + "\nFOOTER"

    messages = await MemoryItemConvertor.convert_tool_result_to_memory(
        "Aworld",
        "call-1",
        ActionResult(
            content=raw_output,
            tool_call_id="call-1",
            tool_name="terminal",
            action_name="exec",
            success=True,
            metadata={},
        ),
        _FakeContext(),
    )

    stored = messages[0]

    assert stored.content != raw_output
    assert "Tool output compacted for context reuse." in stored.content
    assert "HEADER" in stored.content
    assert "FOOTER" in stored.content
