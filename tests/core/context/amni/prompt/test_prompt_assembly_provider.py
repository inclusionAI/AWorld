from types import SimpleNamespace

from aworld.core.context.amni import ApplicationContext
from aworld.core.context.amni.prompt.assembly import (
    CacheAwarePromptAssemblyProvider,
    DefaultPromptAssemblyProvider,
    PromptAssemblyPlan,
)


def test_default_prompt_assembly_provider_preserves_existing_message_order():
    provider = DefaultPromptAssemblyProvider()
    messages = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "tool", "content": "tool-output", "tool_call_id": "call-1"},
    ]
    tools = [{"function": {"name": "search"}}]

    plan = provider.build_plan(messages=messages, tools=tools)

    assert isinstance(plan, PromptAssemblyPlan)
    assert plan.messages == messages
    assert plan.to_model_messages() == messages
    assert plan.observability["assembly_provider"] == "DefaultPromptAssemblyProvider"
    assert plan.observability["stable_prefix_hash"]


def test_default_prompt_assembly_provider_stable_hash_changes_with_system_or_tools():
    provider = DefaultPromptAssemblyProvider()

    plan_a = provider.build_plan(
        messages=[
            {"role": "system", "content": "rules-a"},
            {"role": "user", "content": "hello"},
        ],
        tools=[{"function": {"name": "search"}}],
    )
    plan_b = provider.build_plan(
        messages=[
            {"role": "system", "content": "rules-b"},
            {"role": "user", "content": "hello"},
        ],
        tools=[{"function": {"name": "search"}}],
    )
    plan_c = provider.build_plan(
        messages=[
            {"role": "system", "content": "rules-a"},
            {"role": "user", "content": "hello"},
        ],
        tools=[{"function": {"name": "browse"}}],
    )

    assert plan_a.observability["stable_prefix_hash"] != plan_b.observability["stable_prefix_hash"]
    assert plan_a.observability["stable_prefix_hash"] != plan_c.observability["stable_prefix_hash"]


def test_cache_aware_prompt_assembly_provider_classifies_stable_and_dynamic_sections():
    provider = CacheAwarePromptAssemblyProvider()

    plan = provider.build_plan(
        messages=[
            {"role": "system", "content": "base rules"},
            {"role": "system", "content": "relevant memory"},
            {"role": "user", "content": "hello"},
        ],
        tools=[{"function": {"name": "search"}}],
        metadata={
            "system_section_hints": [
                {"name": "base_rules", "stability": "stable"},
                {"name": "relevant_memory", "stability": "dynamic"},
            ]
        },
    )

    assert [section.content for section in plan.stable_system_sections] == [
        {"role": "system", "content": "base rules"},
    ]
    assert [section.content for section in plan.dynamic_system_sections] == [
        {"role": "system", "content": "relevant memory"},
    ]
    assert plan.conversation_messages == [{"role": "user", "content": "hello"}]
    assert plan.observability["cache_aware_assembly"] is True


def test_cache_aware_prompt_assembly_provider_marks_runtime_stable_prefix_reuse():
    provider = CacheAwarePromptAssemblyProvider()
    messages = [
        {"role": "system", "content": "base rules"},
        {"role": "user", "content": "hello"},
    ]

    first_plan = provider.build_plan(messages=messages, tools=[{"function": {"name": "search"}}])
    second_plan = provider.build_plan(messages=messages, tools=[{"function": {"name": "search"}}])

    assert first_plan.observability["stable_prefix_reused"] is False
    assert second_plan.observability["stable_prefix_reused"] is True
    assert second_plan.metadata["stable_prefix_reused"] is True


def test_application_context_uses_cache_aware_provider_when_context_cache_enabled():
    context = ApplicationContext.create(
        session_id="session-1",
        task_id="task-1",
        task_content="hello",
    )
    agent = SimpleNamespace(
        prompt_assembly_provider=None,
        _is_context_cache_enabled=lambda _context: True,
    )

    provider = context.get_prompt_assembly_provider(agent=agent)

    assert isinstance(provider, CacheAwarePromptAssemblyProvider)


def test_application_context_falls_back_to_default_provider_when_context_cache_disabled():
    context = ApplicationContext.create(
        session_id="session-1",
        task_id="task-1",
        task_content="hello",
    )
    agent = SimpleNamespace(
        prompt_assembly_provider=None,
        _is_context_cache_enabled=lambda _context: False,
    )

    provider = context.get_prompt_assembly_provider(agent=agent)

    assert isinstance(provider, DefaultPromptAssemblyProvider)
