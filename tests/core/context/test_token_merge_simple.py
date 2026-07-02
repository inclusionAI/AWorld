"""
Token Merge Fix Verification Tests (Simplified)

Simplified tests that directly test the core token merge logic without
requiring complex ApplicationContext setup.

The bug was: parent class Context.merge_context() already adds net increment,
but child class ApplicationContext.merge_sub_context() was adding total usage again,
causing double counting.

Fix: Removed duplicate token addition in ApplicationContext.merge_sub_context().
"""

import pytest
from aworld.core.context.base import Context


class TestTokenMergeFixSimple:
    """Simplified test suite for token merge bug fix"""

    @pytest.fixture
    def parent_context(self):
        """Create a parent context with initial token usage"""
        context = Context()
        # Simulate parent has consumed some tokens
        context.add_token({
            'input_tokens': 100,
            'output_tokens': 50
        })

        return context

    @pytest.fixture
    def child_context(self):
        """Create a child context (simulating subtask execution)"""
        context = Context()
        # Child starts with zero tokens (will accumulate during execution)
        return context

    def test_single_merge_no_double_counting(self, parent_context):
        """
        Test Case 1: Single merge doesn't double count tokens

        Scenario:
        1. Parent has 100 input + 50 output tokens
        2. Child (deep_copy of parent) executes and adds 30 input + 20 output tokens
        3. After merge, parent should have 130 input + 70 output tokens (not 160 + 90)
        """
        # Setup: parent has consumed tokens
        parent_initial_input = parent_context.token_usage.get('input_tokens', 0)
        parent_initial_output = parent_context.token_usage.get('output_tokens', 0)

        assert parent_initial_input == 100
        assert parent_initial_output == 50

        # Create child through deep_copy (simulating build_sub_context behavior)
        child_context = parent_context.deep_copy()

        # Child executes and consumes additional tokens
        child_context.add_token({
            'input_tokens': 30,
            'output_tokens': 20
        })

        # Now child has 130 input + 70 output total
        assert child_context.token_usage.get('input_tokens', 0) == 130
        assert child_context.token_usage.get('output_tokens', 0) == 70

        # Merge child into parent
        parent_context.merge_context(child_context)

        # Verify: parent should have sum of both (net increment added)
        # Expected: 100 + 30 = 130 input, 50 + 20 = 70 output
        parent_final_input = parent_context.token_usage.get('input_tokens', 0)
        parent_final_output = parent_context.token_usage.get('output_tokens', 0)

        assert parent_final_input == 130, \
            f"Expected 130 input tokens (100 + 30), but got {parent_final_input}"
        assert parent_final_output == 70, \
            f"Expected 70 output tokens (50 + 20), but got {parent_final_output}"

        print("✅ Test passed: Single merge correctly adds net increment only")

    def test_nested_merge_no_double_counting(self, parent_context):
        """
        Test Case 2: Nested merges (subagent of subagent) don't double count

        Scenario:
        1. Parent (100, 50) creates Child1 via deep_copy
        2. Child1 adds (30, 20), creates GrandChild via deep_copy
        3. GrandChild adds (10, 5)
        4. Merge GrandChild -> Child1 -> Parent
        5. Final parent should be (140, 75) not higher
        """
        parent_initial = parent_context.token_usage.copy()

        # Create child1 via deep_copy (inherits parent's 100, 50)
        child1 = parent_context.deep_copy()

        # Child1 executes and adds tokens
        child1.add_token({'input_tokens': 30, 'output_tokens': 20})
        # Now child1 has (130, 70)

        # Create grandchild via deep_copy (inherits child1's 130, 70)
        grandchild = child1.deep_copy()

        # Grandchild executes and adds tokens
        grandchild.add_token({'input_tokens': 10, 'output_tokens': 5})
        # Now grandchild has (140, 75)

        # Merge grandchild into child1
        child1.merge_context(grandchild)

        # Verify child1 tokens: should have net increment from grandchild (10, 5)
        assert child1.token_usage.get('input_tokens', 0) == 140, \
            "Child1 should have 130 + 10 = 140 input tokens"
        assert child1.token_usage.get('output_tokens', 0) == 75, \
            "Child1 should have 70 + 5 = 75 output tokens"

        # Merge child1 into parent
        parent_context.merge_context(child1)

        # Verify final parent tokens
        parent_final_input = parent_context.token_usage.get('input_tokens', 0)
        parent_final_output = parent_context.token_usage.get('output_tokens', 0)

        assert parent_final_input == 140, \
            f"Expected 140 input tokens (100 + 30 + 10), but got {parent_final_input}"
        assert parent_final_output == 75, \
            f"Expected 75 output tokens (50 + 20 + 5), but got {parent_final_output}"

        print("✅ Test passed: Nested merges correctly add net increments only")

    def test_nested_token_usage_details_merge_uses_recursive_net_increment(self):
        """Nested token usage dicts should merge by net increment, not full child totals."""
        parent = Context()
        parent.add_token(
            {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "prompt_tokens_details": {
                    "cached_tokens": 40,
                    "cache_creation_input_tokens": 10,
                    "cache_read_input_tokens": 30,
                },
            }
        )

        child = parent.deep_copy()
        child.add_token(
            {
                "prompt_tokens": 30,
                "completion_tokens": 5,
                "prompt_tokens_details": {
                    "cached_tokens": 12,
                    "cache_creation_input_tokens": 5,
                    "cache_read_input_tokens": 7,
                },
            }
        )

        parent.merge_context(child)

        assert parent.token_usage["prompt_tokens"] == 130
        assert parent.token_usage["completion_tokens"] == 25
        assert parent.token_usage["prompt_tokens_details"] == {
            "cached_tokens": 52,
            "cache_creation_input_tokens": 15,
            "cache_read_input_tokens": 37,
        }

    def test_multiple_sequential_merges_no_double_counting(self, parent_context):
        """
        Test Case 3: Multiple sequential subtasks merge correctly

        Scenario:
        1. Parent (100, 50) spawns subtask1 via deep_copy
        2. Subtask1 adds (20, 10), merges back → parent becomes (120, 60)
        3. Parent spawns subtask2 via deep_copy
        4. Subtask2 adds (30, 15), merges back → parent becomes (150, 75)
        5. Parent spawns subtask3 via deep_copy
        6. Subtask3 adds (25, 12), merges back → parent becomes (175, 87)
        """
        # Initial parent state
        assert parent_context.token_usage.get('input_tokens', 0) == 100
        assert parent_context.token_usage.get('output_tokens', 0) == 50

        # Subtask 1
        subtask1 = parent_context.deep_copy()
        subtask1.add_token({'input_tokens': 20, 'output_tokens': 10})
        parent_context.merge_context(subtask1)
        assert parent_context.token_usage.get('input_tokens', 0) == 120
        assert parent_context.token_usage.get('output_tokens', 0) == 60

        # Subtask 2
        subtask2 = parent_context.deep_copy()
        subtask2.add_token({'input_tokens': 30, 'output_tokens': 15})
        parent_context.merge_context(subtask2)
        assert parent_context.token_usage.get('input_tokens', 0) == 150
        assert parent_context.token_usage.get('output_tokens', 0) == 75

        # Subtask 3
        subtask3 = parent_context.deep_copy()
        subtask3.add_token({'input_tokens': 25, 'output_tokens': 12})
        parent_context.merge_context(subtask3)
        assert parent_context.token_usage.get('input_tokens', 0) == 175
        assert parent_context.token_usage.get('output_tokens', 0) == 87

        print("✅ Test passed: Multiple sequential merges correctly add net increments only")

    def test_preserved_merge_baseline_survives_transport_copy(self, parent_context):
        """
        Transport copies of an already-executed child context must preserve the
        original merge baseline so the parent can still recover the pending net increment.
        """
        child_context = parent_context.deep_copy()
        child_context.add_token(
            {
                "input_tokens": 30,
                "output_tokens": 20,
            }
        )

        transported_context = child_context.deep_copy(preserve_merge_baseline=True)
        parent_context.merge_context(transported_context)

        assert parent_context.token_usage.get("input_tokens", 0) == 130
        assert parent_context.token_usage.get("output_tokens", 0) == 70

    def test_zero_token_subtask_merge(self, parent_context):
        """
        Test Case 4: Merging a subtask that consumed no tokens

        Scenario:
        1. Parent has (100, 50)
        2. Child executes but uses no tokens (0, 0)
        3. After merge, parent should still be (100, 50)
        """
        parent_initial_input = parent_context.token_usage.get('input_tokens', 0)
        parent_initial_output = parent_context.token_usage.get('output_tokens', 0)

        # Create child that consumes no tokens
        child_context = Context()
        # Don't add any tokens

        # Merge child into parent
        parent_context.merge_context(child_context)

        # Verify parent tokens unchanged
        parent_final_input = parent_context.token_usage.get('input_tokens', 0)
        parent_final_output = parent_context.token_usage.get('output_tokens', 0)

        assert parent_final_input == parent_initial_input, \
            f"Parent input tokens should remain {parent_initial_input}"
        assert parent_final_output == parent_initial_output, \
            f"Parent output tokens should remain {parent_initial_output}"

        print("✅ Test passed: Zero-token subtask merge doesn't change parent tokens")

    def test_backward_compatibility_with_deep_copy_pattern(self):
        """
        Test Case 5: Verify the typical build_sub_context pattern works correctly

        This simulates: parent.deep_copy() → modify → merge back
        which is the pattern used by build_sub_context()
        """
        # Create parent context
        parent = Context()
        parent.add_token({'input_tokens': 100, 'output_tokens': 50})

        # Simulate build_sub_context (creates a deep copy)
        sub_context = parent.deep_copy()

        # Sub_context inherits parent's tokens (100, 50) due to deep_copy
        assert sub_context.token_usage.get('input_tokens', 0) == 100
        assert sub_context.token_usage.get('output_tokens', 0) == 50

        # Subtask executes and adds more tokens
        sub_context.add_token({'input_tokens': 30, 'output_tokens': 20})

        # Now sub_context has (130, 70) total
        assert sub_context.token_usage.get('input_tokens', 0) == 130
        assert sub_context.token_usage.get('output_tokens', 0) == 70

        # Merge sub_context back to parent
        parent.merge_context(sub_context)

        # Parent should have (130, 70) - the net increment (30, 20) was added
        # NOT (230, 120) which would be double counting
        parent_final_input = parent.token_usage.get('input_tokens', 0)
        parent_final_output = parent.token_usage.get('output_tokens', 0)

        assert parent_final_input == 130, \
            f"Expected 130 input tokens (net increment 30 added), but got {parent_final_input}"
        assert parent_final_output == 70, \
            f"Expected 70 output tokens (net increment 20 added), but got {parent_final_output}"

        print("✅ Test passed: Backward compatibility with deep_copy pattern maintained")


if __name__ == '__main__':
    """Run tests directly with pytest"""
    pytest.main([__file__, '-v', '-s'])
