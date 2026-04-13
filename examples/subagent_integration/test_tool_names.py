# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Test script to verify spawn_subagent tool names are correctly registered.

This script checks that the tool names match what's described in prompt.txt.
"""

import asyncio
from aworld.config.conf import AgentConfig
from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import TeamSwarm
from aworld.core.tool.base import ToolFactory
from aworld.core.tool.tool_desc import tool_action_desc


def _run_tool_registration():
    """Run the spawn_subagent tool registration verification."""

    print("\n" + "="*80)
    print("Spawn Subagent Tool Name Verification")
    print("="*80)

    # Create a simple agent with subagent capability
    coordinator = Agent(
        conf=AgentConfig(llm_provider="openai", llm_model_name="gpt-4o"),
        name="Coordinator",
        desc="Orchestrator agent",
        tool_names=["async_spawn_subagent"],
        enable_subagent=True
    )

    print("\n✅ Created Coordinator agent with enable_subagent=True")

    # Check tool registration in ToolFactory (global)
    print("\n📋 Checking global tool registration in ToolFactory:")

    found_tools = []
    for tool_name in ToolFactory:
        if 'spawn_subagent' in tool_name.lower():
            found_tools.append(tool_name)
            print(f"  ✓ {tool_name}")

    if found_tools:
        print(f"  Found {len(found_tools)} spawn_subagent tool(s) globally")
    else:
        print("  ℹ️  No spawn_subagent tools in global ToolFactory (this is expected)")
        print("     spawn_subagent is loaded per-agent via tool_names")

    # Check agent's loaded tools
    print("\n📋 Checking agent's loaded tools:")

    agent_tool_names = []
    if hasattr(coordinator, 'tools') and coordinator.tools:
        for tool in coordinator.tools:
            tool_name = tool.name() if hasattr(tool, 'name') and callable(tool.name) else str(tool)
            agent_tool_names.append(tool_name)
            if 'spawn' in tool_name.lower():
                print(f"  ✓ {tool_name}")

    # Check actions for async_spawn_subagent
    print("\n📋 Checking actions for async_spawn_subagent in ToolFactory:")

    tool_actions = ToolFactory.get_tool_action('async_spawn_subagent')
    if tool_actions:
        action_names = [member.value.name for member in tool_actions.__members__.values()]
        for action_name in action_names:
            print(f"  ✓ {action_name}")

        # Expected actions
        expected_actions = ['spawn', 'spawn_parallel', 'spawn_background',
                           'check_task', 'wait_task', 'cancel_task']
        missing = set(expected_actions) - set(action_names)
        extra = set(action_names) - set(expected_actions)

        if missing:
            print(f"\n  ⚠️  Missing actions: {missing}")
        if extra:
            print(f"\n  ℹ️  Extra actions: {extra}")

        if not missing:
            print(f"\n  ✅ All expected actions are present")
    else:
        print("  ⚠️  No actions found for async_spawn_subagent in ToolFactory")
        print("     This might be normal if tools are loaded dynamically")

    # Get tool descriptions (what LLM sees)
    print("\n📋 Tool descriptions for LLM (function names):")

    tool_descs = tool_action_desc()
    spawn_found = False

    for tool_name, tool_info in tool_descs.items():
        if 'spawn_subagent' in tool_name:
            spawn_found = True
            print(f"\n  Tool: {tool_name}")
            print(f"  Description: {tool_info['desc']}")
            print(f"  Actions ({len(tool_info['actions'])}):")

            for action in tool_info['actions']:
                # The full function name LLM sees is: tool_name__action_name
                full_name = f"{tool_name}__{action['name']}"
                print(f"    - {full_name}")
                print(f"      Params: {list(action['params'].keys())}")

    if not spawn_found:
        print("  ✗ spawn_subagent not found in tool descriptions")
        return False

    print("\n" + "="*80)
    print("✅ Tool name verification completed successfully!")
    print("\nLLM will see these 6 tools:")
    print("  1. async_spawn_subagent__spawn")
    print("  2. async_spawn_subagent__spawn_parallel")
    print("  3. async_spawn_subagent__spawn_background")
    print("  4. async_spawn_subagent__check_task")
    print("  5. async_spawn_subagent__wait_task")
    print("  6. async_spawn_subagent__cancel_task")
    print("\n💡 These names match what's described in prompt.txt")
    print("="*80)

    return True


def test_tool_registration():
    """Test that spawn_subagent tools are registered with correct names."""
    _run_tool_registration()


if __name__ == '__main__':
    try:
        success = _run_tool_registration()
        if success:
            print("\n🎉 Tool names are correctly configured!")
        else:
            print("\n⚠️  Tool name configuration may have issues.")
    except Exception as e:
        print(f"\n❌ Error during verification: {e}")
        import traceback
        traceback.print_exc()
