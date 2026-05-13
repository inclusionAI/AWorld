from types import SimpleNamespace

import pytest

from aworld.core.context.amni import ApplicationContext
from aworld.core.context.amni.prompt.assembly import (
    CacheAwarePromptAssemblyProvider,
    DefaultPromptAssemblyProvider,
    PromptAssemblyPlan,
)
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


def test_application_context_resolves_default_and_agent_prompt_assembly_provider() -> None:
    context = ApplicationContext.create(
        session_id="session-1",
        task_id="task-1",
        task_content="hello",
    )

    default_provider = context.get_prompt_assembly_provider()
    assert isinstance(default_provider, DefaultPromptAssemblyProvider)
    assert context.get_prompt_assembly_provider() is default_provider

    custom_provider = object()
    agent = SimpleNamespace(prompt_assembly_provider=custom_provider)
    assert context.get_prompt_assembly_provider(agent=agent) is custom_provider

    cache_aware_provider = context.get_prompt_assembly_provider(
        agent=SimpleNamespace(prompt_assembly_provider=None, _is_context_cache_enabled=lambda _context: True)
    )
    assert isinstance(cache_aware_provider, CacheAwarePromptAssemblyProvider)


def test_application_context_deep_copy_preserves_prompt_assembly_provider_runtime_slots() -> None:
    context = ApplicationContext.create(
        session_id="session-1",
        task_id="task-1",
        task_content="hello",
    )
    copied = context.deep_copy()

    provider = copied.get_prompt_assembly_provider(
        agent=SimpleNamespace(prompt_assembly_provider=None, _is_context_cache_enabled=lambda _context: True)
    )

    assert isinstance(provider, CacheAwarePromptAssemblyProvider)
    assert copied.get_prompt_assembly_provider(
        agent=SimpleNamespace(prompt_assembly_provider=None, _is_context_cache_enabled=lambda _context: True)
    ) is provider


@pytest.mark.asyncio
async def test_system_prompt_augment_op_uses_injected_prompt_assembly_provider() -> None:
    captured = {}

    class CustomPromptAssemblyProvider:
        def build_plan(self, *, messages, tools=None, metadata=None):
            captured["messages"] = list(messages)
            captured["metadata"] = dict(metadata or {})
            return PromptAssemblyPlan(
                messages=[
                    {"role": "system", "content": "assembled rules"},
                    {"role": "system", "content": "assembled memory"},
                ],
                stable_hash="stable-hash-1",
                observability={"assembly_provider": "CustomPromptAssemblyProvider"},
                metadata=dict(metadata or {}),
            )

    class FakeContext:
        def get_prompt_assembly_provider(self, agent=None):
            return CustomPromptAssemblyProvider()

        def get_task(self):
            return SimpleNamespace(session_id="session-1", id="task-1", user_id="user-1")

    op = SystemPromptAugmentOp()
    command = await op.build_system_command(
        FakeContext(),
        SimpleNamespace(agent_id="agent-1", agent_name="Agent One", user_query="hello", system_prompt="base rules"),
        {"memory": "memory chunk"},
    )

    assert captured["messages"] == [
        {"role": "system", "content": "base rules"},
        {"role": "system", "content": "memory chunk"},
    ]
    assert command.item.content == "assembled rules\n\nassembled memory"
