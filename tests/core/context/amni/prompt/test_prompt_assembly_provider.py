from aworld.core.context.amni.prompt.assembly import (
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
