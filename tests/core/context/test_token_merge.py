"""
Token Merge Fix Verification Tests

Tests to verify the token merge bug fix in ApplicationContext.merge_sub_context().
The bug was: parent class Context.merge_context() already adds net increment,
but child class ApplicationContext.merge_sub_context() was adding total usage again,
causing double counting.

Fix: Removed duplicate token addition in ApplicationContext.merge_sub_context().
"""

import pytest
from aworld.core.context.amni import ApplicationContext
from aworld.core.context.amni.state import ApplicationTaskContextState, TaskWorkingState, TaskInput, TaskOutput
from aworld.core.context.base import Context
from aworld.config.conf import AgentConfig


class TestTokenMergeFix:
    """Test suite for token merge bug fix"""

    @pytest.fixture
    def parent_context(self):
        """Create a parent context with initial token usage"""
        task_input = TaskInput(session_id="test_session", task_id="parent_task", content="Parent task")
        working_state = TaskWorkingState(messages=[], user_profiles=[], kv_store={})
        task_state = ApplicationTaskContextState(
            task_input=task_input,
            working_state=working_state,
            task_output=TaskOutput()
        )
        context = ApplicationContext(task_state=task_state)

        # Simulate parent has consumed some tokens
        context.add_token({
            'input_tokens': 100,
            'output_tokens': 50
        })

        return context

    @pytest.fixture
    def child_context(self):
        """Create a child context (simulating subtask execution)"""
        task_input = TaskInput(session_id="test_session", task_id="child_task", content="Child task")
        working_state = TaskWorkingState(messages=[], user_profiles=[], kv_store={})
        task_state = ApplicationTaskContextState(
            task_input=task_input,
            working_state=working_state,
            task_output=TaskOutput()
        )
        context = ApplicationContext(task_state=task_state)

        # Child starts with zero tokens (will accumulate during execution)
        return context

    @pytest.mark.asyncio
    async def test_single_merge_no_double_counting(self, parent_context, child_context):
        """
        Test Case 1: Single merge doesn't double count tokens

        Scenario:
        1. Parent has 100 input + 50 output tokens
        2. Child executes and adds 30 input + 20 output tokens
        3. After merge, parent should have 130 input + 70 output tokens (not 160 + 90)
        """
        # Setup: parent has consumed tokens
        parent_initial_input = parent_context.token_usage.get('input_tokens', 0)
        parent_initial_output = parent_context.token_usage.get('output_tokens', 0)

        assert parent_initial_input == 100
        assert parent_initial_output == 50

        # Child executes and consumes tokens
        child_context.add_token({
            'input_tokens': 30,
            'output_tokens': 20
        })

        # Merge child into parent
        parent_context.merge_sub_context(child_context)

        # Verify: parent should have sum of both (net increment added)
        # Expected: 100 + 30 = 130 input, 50 + 20 = 70 output
        parent_final_input = parent_context.token_usage.get('input_tokens', 0)
        parent_final_output = parent_context.token_usage.get('output_tokens', 0)

        assert parent_final_input == 130, \
            f"Expected 130 input tokens (100 + 30), but got {parent_final_input}"
        assert parent_final_output == 70, \
            f"Expected 70 output tokens (50 + 20), but got {parent_final_output}"

        print("✅ Test passed: Single merge correctly adds net increment only")

    @pytest.mark.asyncio
    async def test_nested_merge_no_double_counting(self, parent_context):
        """
        Test Case 2: Nested merges (subagent of subagent) don't double count

        Scenario:
        1. Parent (100, 50) creates Child1
        2. Child1 adds (30, 20), creates GrandChild
        3. GrandChild adds (10, 5)
        4. Merge GrandChild -> Child1 -> Parent
        5. Final parent should be (140, 75) not higher
        """
        parent_initial = parent_context.token_usage.copy()

        # Create child1 context
        task_input1 = TaskInput(session_id="test_session", task_id="child1_task", content="Child1 task")
        working_state1 = TaskWorkingState(messages=[], user_profiles=[], kv_store={})
        task_state1 = ApplicationTaskContextState(
            task_input=task_input1,
            working_state=working_state1,
            task_output=TaskOutput()
        )
        child1 = ApplicationContext(task_state=task_state1)

        # Child1 executes
        child1.add_token({'input_tokens': 30, 'output_tokens': 20})

        # Create grandchild context
        task_input2 = TaskInput(session_id="test_session", task_id="grandchild_task", content="Grandchild task")
        working_state2 = TaskWorkingState(messages=[], user_profiles=[], kv_store={})
        task_state2 = ApplicationTaskContextState(
            task_input=task_input2,
            working_state=working_state2,
            task_output=TaskOutput()
        )
        grandchild = ApplicationContext(task_state=task_state2)

        # Grandchild executes
        grandchild.add_token({'input_tokens': 10, 'output_tokens': 5})

        # Merge grandchild into child1
        child1.merge_sub_context(grandchild)

        # Verify child1 tokens
        assert child1.token_usage.get('input_tokens', 0) == 40, \
            "Child1 should have 30 + 10 = 40 input tokens"
        assert child1.token_usage.get('output_tokens', 0) == 25, \
            "Child1 should have 20 + 5 = 25 output tokens"

        # Merge child1 into parent
        parent_context.merge_sub_context(child1)

        # Verify final parent tokens
        parent_final_input = parent_context.token_usage.get('input_tokens', 0)
        parent_final_output = parent_context.token_usage.get('output_tokens', 0)

        assert parent_final_input == 140, \
            f"Expected 140 input tokens (100 + 30 + 10), but got {parent_final_input}"
        assert parent_final_output == 75, \
            f"Expected 75 output tokens (50 + 20 + 5), but got {parent_final_output}"

        print("✅ Test passed: Nested merges correctly add net increments only")

    @pytest.mark.asyncio
    async def test_multiple_parallel_merges_no_double_counting(self, parent_context):
        """
        Test Case 3: Multiple parallel subtasks merge correctly

        Scenario:
        1. Parent (100, 50) spawns 3 parallel subtasks
        2. Subtask1 adds (20, 10)
        3. Subtask2 adds (30, 15)
        4. Subtask3 adds (25, 12)
        5. After all merges, parent should be (175, 87)
        """
        # Create 3 parallel subtask contexts
        subtasks = []
        token_increments = [
            {'input_tokens': 20, 'output_tokens': 10},
            {'input_tokens': 30, 'output_tokens': 15},
            {'input_tokens': 25, 'output_tokens': 12}
        ]

        for i, tokens in enumerate(token_increments):
            task_input = TaskInput(session_id="test_session", task_id=f"subtask_{i}", content=f"Subtask {i}")
            working_state = TaskWorkingState(messages=[], user_profiles=[], kv_store={})
            task_state = ApplicationTaskContextState(
                task_input=task_input,
                working_state=working_state,
                task_output=TaskOutput()
            )
            subtask = ApplicationContext(task_state=task_state)
            subtask.add_token(tokens)
            subtasks.append(subtask)

        # Merge all subtasks into parent
        for subtask in subtasks:
            parent_context.merge_sub_context(subtask)

        # Verify final parent tokens
        parent_final_input = parent_context.token_usage.get('input_tokens', 0)
        parent_final_output = parent_context.token_usage.get('output_tokens', 0)

        expected_input = 100 + 20 + 30 + 25  # 175
        expected_output = 50 + 10 + 15 + 12  # 87

        assert parent_final_input == expected_input, \
            f"Expected {expected_input} input tokens, but got {parent_final_input}"
        assert parent_final_output == expected_output, \
            f"Expected {expected_output} output tokens, but got {parent_final_output}"

        print("✅ Test passed: Multiple parallel merges correctly add net increments only")

    @pytest.mark.asyncio
    async def test_zero_token_subtask_merge(self, parent_context):
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
        task_input = TaskInput(session_id="test_session", task_id="zero_token_child", content="Zero token child")
        working_state = TaskWorkingState(messages=[], user_profiles=[], kv_store={})
        task_state = ApplicationTaskContextState(
            task_input=task_input,
            working_state=working_state,
            task_output=TaskOutput()
        )
        child_context = ApplicationContext(task_state=task_state)
        # Don't add any tokens

        # Merge child into parent
        parent_context.merge_sub_context(child_context)

        # Verify parent tokens unchanged
        parent_final_input = parent_context.token_usage.get('input_tokens', 0)
        parent_final_output = parent_context.token_usage.get('output_tokens', 0)

        assert parent_final_input == parent_initial_input, \
            f"Parent input tokens should remain {parent_initial_input}"
        assert parent_final_output == parent_initial_output, \
            f"Parent output tokens should remain {parent_initial_output}"

        print("✅ Test passed: Zero-token subtask merge doesn't change parent tokens")

    @pytest.mark.asyncio
    async def test_backward_compatibility_with_existing_subtask_scenarios(self):
        """
        Test Case 5: Verify existing subtask scenarios still work correctly

        This test simulates the typical subtask pattern used in aworld:
        1. Create parent context
        2. Build sub_context via build_sub_context()
        3. Execute subtask (adds tokens)
        4. Merge back via merge_sub_context()
        """
        # Create parent context
        task_input = TaskInput(session_id="test_session", task_id="parent_task", content="Parent task")
        working_state = TaskWorkingState(messages=[], user_profiles=[], kv_store={})
        task_state = ApplicationTaskContextState(
            task_input=task_input,
            working_state=working_state,
            task_output=TaskOutput()
        )
        parent = ApplicationContext(task_state=task_state)
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
        parent.merge_sub_context(sub_context)

        # Parent should have (130, 70) - the net increment (30, 20) was added
        # NOT (230, 120) which would be double counting
        parent_final_input = parent.token_usage.get('input_tokens', 0)
        parent_final_output = parent.token_usage.get('output_tokens', 0)

        assert parent_final_input == 130, \
            f"Expected 130 input tokens (net increment 30 added), but got {parent_final_input}"
        assert parent_final_output == 70, \
            f"Expected 70 output tokens (net increment 20 added), but got {parent_final_output}"

        print("✅ Test passed: Backward compatibility with build_sub_context pattern maintained")


if __name__ == '__main__':
    """Run tests directly with pytest"""
    pytest.main([__file__, '-v', '-s'])
