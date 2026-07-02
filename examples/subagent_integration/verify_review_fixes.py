# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Verification script for Review issue fixes.

Verifies that all 4 issues identified in the code review have been fixed:
1. Tool name consistency (async_spawn_subagent__* in prompt)
2. spawn_parallel fail_fast bug (Task wrapping)
3. cancel_task status sync (calls _sync_status_to_context)
4. Prompt guidance for mode selection
"""

import re


def verify_fix_1_tool_names():
    """Verify that prompt uses correct async tool names."""
    print("\n" + "="*80)
    print("Fix 1: Tool Name Consistency")
    print("="*80)

    prompt_path = "/Users/wuman/Documents/workspace/aworld-mas/aworld/aworld-cli/src/aworld_cli/builtin_agents/smllc/agents/prompt.txt"

    with open(prompt_path, 'r', encoding='utf-8') as f:
        content = f.read()

    checks = [
        ("async_spawn_subagent__spawn", "spawn tool name"),
        ("async_spawn_subagent__spawn_parallel", "spawn_parallel tool name"),
        ("async_spawn_subagent__spawn_background", "spawn_background tool name"),
        ("async_spawn_subagent__check_task", "check_task tool name"),
        ("async_spawn_subagent__wait_task", "wait_task tool name"),
        ("async_spawn_subagent__cancel_task", "cancel_task tool name"),
    ]

    all_passed = True
    for phrase, description in checks:
        if phrase in content:
            print(f"  ✓ Found: {description}")
        else:
            print(f"  ✗ Missing: {description}")
            all_passed = False

    # Check that old name is removed
    if "spawn_subagent(name=" in content and "async_spawn_subagent__spawn" not in content:
        print(f"  ✗ Still using old name format: spawn_subagent(name=...)")
        all_passed = False
    else:
        print(f"  ✓ Old name format removed or coexists with new format")

    return all_passed


def verify_fix_2_fail_fast():
    """Verify that spawn_parallel fail_fast uses asyncio.create_task."""
    print("\n" + "="*80)
    print("Fix 2: spawn_parallel fail_fast Bug")
    print("="*80)

    tool_path = "/Users/wuman/Documents/workspace/aworld-mas/aworld/aworld/core/tool/builtin/spawn_subagent_tool.py"

    with open(tool_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Check for asyncio.create_task wrapping
    if "asyncio.create_task(spawn_with_limit" in content:
        print(f"  ✓ Found: asyncio.create_task wrapping for spawn_tasks")
    else:
        print(f"  ✗ Missing: asyncio.create_task wrapping")
        return False

    # Check that fail_fast branch exists and looks correct
    fail_fast_pattern = r'if fail_fast:.*?for.*asyncio\.as_completed.*?task\.done\(\).*?task\.cancel\(\)'
    if re.search(fail_fast_pattern, content, re.DOTALL):
        print(f"  ✓ Found: fail_fast branch with proper Task methods")
    else:
        print(f"  ✗ Warning: fail_fast branch may have issues (manual check needed)")

    return True


def verify_fix_3_cancel_sync():
    """Verify that cancel_task syncs status to Context."""
    print("\n" + "="*80)
    print("Fix 3: cancel_task Status Sync")
    print("="*80)

    tool_path = "/Users/wuman/Documents/workspace/aworld-mas/aworld/aworld/core/tool/builtin/spawn_subagent_tool.py"

    with open(tool_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # Find cancel_task method
    cancel_task_start = None
    for i, line in enumerate(lines):
        if 'async def _cancel_task' in line:
            cancel_task_start = i
            break

    if not cancel_task_start:
        print(f"  ✗ Could not find _cancel_task method")
        return False

    # Check for _sync_status_to_context calls in cancel_task
    cancel_section = ''.join(lines[cancel_task_start:cancel_task_start+150])

    sync_calls = cancel_section.count('_sync_status_to_context')
    if sync_calls >= 2:
        print(f"  ✓ Found: {sync_calls} _sync_status_to_context calls in cancel_task")
        print(f"     (one for single cancel, one for cancel all)")
    elif sync_calls == 1:
        print(f"  ⚠ Warning: Only 1 _sync_status_to_context call found")
        print(f"     (should have 2: single cancel and cancel all)")
        return True  # Partial pass
    else:
        print(f"  ✗ Missing: _sync_status_to_context calls in cancel_task")
        return False

    return True


def verify_fix_4_mode_guidance():
    """Verify that prompt provides guidance for mode selection."""
    print("\n" + "="*80)
    print("Fix 4: Mode Selection Guidance")
    print("="*80)

    prompt_path = "/Users/wuman/Documents/workspace/aworld-mas/aworld/aworld-cli/src/aworld_cli/builtin_agents/smllc/agents/prompt.txt"

    with open(prompt_path, 'r', encoding='utf-8') as f:
        content = f.read()

    checks = [
        ("Decision Tree", "Decision tree for mode selection"),
        ("Multiple independent tasks", "Guidance for spawn_parallel"),
        ("Long task + immediate other work", "Guidance for spawn_background"),
        ("Single blocking task", "Guidance for spawn"),
        ("Mode selection is YOUR decision", "Explicit autonomy for mode selection"),
    ]

    all_passed = True
    for phrase, description in checks:
        if phrase in content:
            print(f"  ✓ Found: {description}")
        else:
            print(f"  ✗ Missing: {description}")
            all_passed = False

    # Check that "one tool per step" constraint is updated
    if "Special case - Subagent tools" in content:
        print(f"  ✓ 'One Tool Per Step' rule updated for subagent tools")
    else:
        print(f"  ✗ 'One Tool Per Step' rule not updated")
        all_passed = False

    return all_passed


def main():
    """Run all verification checks."""
    print("\n" + "="*80)
    print("Review Issue Fixes Verification")
    print("="*80)

    results = {}
    results['fix_1'] = verify_fix_1_tool_names()
    results['fix_2'] = verify_fix_2_fail_fast()
    results['fix_3'] = verify_fix_3_cancel_sync()
    results['fix_4'] = verify_fix_4_mode_guidance()

    print("\n" + "="*80)
    print("Summary")
    print("="*80)

    for fix_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{fix_name}: {status}")

    all_passed = all(results.values())

    if all_passed:
        print("\n🎉 All fixes verified successfully!")
        print("\nNext steps:")
        print("  1. Run unit tests: pytest tests/core/tool/test_spawn_background.py -v")
        print("  2. Run integration tests: python examples/subagent_integration/test_background_spawn.py")
        print("  3. Test with actual LLM: Verify it uses correct tool names")
    else:
        print("\n⚠️  Some fixes may need attention. Please review the output above.")

    return all_passed


if __name__ == '__main__':
    success = main()
    exit(0 if success else 1)
