# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Quick verification script for spawn_subagent prompt optimization.

This script checks that the prompt.txt has been correctly updated with
the new spawn_subagent tool description.
"""

def verify_prompt_update():
    """Verify that prompt.txt contains the optimized spawn_subagent description."""

    prompt_path = "/Users/wuman/Documents/workspace/aworld-mas/aworld/aworld-cli/src/aworld_cli/inner_plugins/smllc/agents/prompt.txt"

    print("\n" + "="*80)
    print("Prompt Update Verification")
    print("="*80)

    try:
        with open(prompt_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Check for key phrases from the optimized description
        checks = [
            ("Mode 1: spawn (Default - Blocking)", "Mode 1 description"),
            ("Mode 2: spawn_parallel (Batch - Multiple Tasks in One Call)", "Mode 2 description"),
            ("Mode 3: spawn_background (Non-blocking - Fire and Continue)", "Mode 3 description"),
            ("Decision Tree:", "Decision tree section"),
            ("each mode counts as ONE tool call per step", "One tool per step compatibility"),
        ]

        print("\n✅ Checking for required content...")
        all_passed = True

        for phrase, description in checks:
            if phrase in content:
                print(f"  ✓ Found: {description}")
            else:
                print(f"  ✗ Missing: {description}")
                all_passed = False

        if all_passed:
            print("\n✅ All checks passed! Prompt has been successfully updated.")
            print("\n📋 Summary of changes:")
            print("  - Added 3 execution modes (spawn, spawn_parallel, spawn_background)")
            print("  - Included decision tree for mode selection")
            print("  - Clarified compatibility with 'one tool per step' rule")
            print("  - Provided examples and workflow for each mode")

            # Count lines
            lines = content.split('\n')
            spawn_section_start = None
            spawn_section_end = None

            for i, line in enumerate(lines):
                if '`spawn_subagent`:' in line:
                    spawn_section_start = i
                if spawn_section_start and i > spawn_section_start and line.strip().startswith('*   `'):
                    spawn_section_end = i
                    break

            if spawn_section_start and spawn_section_end:
                section_length = spawn_section_end - spawn_section_start
                print(f"\n📏 spawn_subagent section: {section_length} lines (was ~11 lines before)")
        else:
            print("\n❌ Some checks failed. The prompt may not have been fully updated.")
            print("   Please review the prompt.txt file manually.")

        print("\n" + "="*80)
        return all_passed

    except Exception as e:
        print(f"\n❌ Error reading prompt file: {e}")
        return False


if __name__ == '__main__':
    success = verify_prompt_update()

    if success:
        print("\n🎉 Verification completed successfully!")
        print("\nNext steps:")
        print("  1. Test with actual LLM: Create tasks that require parallel/background execution")
        print("  2. Monitor LLM's mode selection: Check if it correctly uses spawn_parallel/spawn_background")
        print("  3. Collect feedback: Record success/failure cases for further optimization")
    else:
        print("\n⚠️  Verification failed. Please check the prompt.txt file.")
