from types import SimpleNamespace

import pytest

from aworld.core.context.amni.prompt.neurons.action_info_neuron import ActionInfoNeuron


class _FakeContext:
    def get_config(self):
        return SimpleNamespace(debug_mode=False)


@pytest.mark.asyncio
async def test_action_info_neuron_uses_supported_knowledge_tool_names():
    neuron = ActionInfoNeuron()

    result = await neuron.format(
        _FakeContext(),
        items=["  <knowledge id='artifact-1' summary='preview'></knowledge>\n"],
    )

    assert "artifact-1" in result
    assert "list_knowledge_info(limit, offset)" in result
    assert "get_knowledge_by_id(knowledge_id)" in result
    assert "grep_knowledge(knowledge_id, pattern)" in result
    assert "get_knowledge_by_lines(knowledge_id, start_line, end_line)" in result
    assert "get_knowledge(knowledge_id_xxx)" not in result
