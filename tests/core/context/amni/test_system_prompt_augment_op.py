from types import SimpleNamespace

import pytest

from aworld.core.context.amni.config import AgentContextConfig
from aworld.core.context.amni.processor.op.system_prompt_augment_op import SystemPromptAugmentOp


@pytest.mark.asyncio
async def test_system_prompt_augment_op_enables_relevant_memory_neuron_with_aworld_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aworld.core.context.amni.prompt.neurons.aworld_file_neuron import AWORLD_FILE_NEURON_NAME
    from aworld.core.context.amni.prompt.neurons.relevant_memory_neuron import (
        RELEVANT_MEMORY_NEURON_NAME,
    )

    captured = {}

    class FakeContext:
        session_id = "session-1"

        def get_agent_context_config(self, namespace):
            return AgentContextConfig(
                enable_aworld_file=True,
                enable_system_prompt_augment=True,
                neuron_names=[],
            )

    def fake_get_neurons_by_names(names):
        captured["names"] = list(names)
        return []

    monkeypatch.setattr(
        "aworld.core.context.amni.processor.op.system_prompt_augment_op.AgentFactory.agent_instance",
        lambda agent_id: SimpleNamespace(ptc_tools=None, skill_configs=None),
    )
    monkeypatch.setattr(
        "aworld.core.context.amni.processor.op.system_prompt_augment_op.neuron_factory.get_neurons_by_names",
        fake_get_neurons_by_names,
    )

    op = SystemPromptAugmentOp()
    result = await op._process_neurons(
        FakeContext(),
        SimpleNamespace(agent_id="agent-1", namespace=None),
    )

    assert result == {}
    assert AWORLD_FILE_NEURON_NAME in captured["names"]
    assert RELEVANT_MEMORY_NEURON_NAME in captured["names"]
