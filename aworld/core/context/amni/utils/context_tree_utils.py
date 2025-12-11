# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Context Tree Utilities - Generate tree representation of context hierarchy.

This module provides utilities for visualizing the context hierarchy structure,
showing the relationship between parent and child contexts including subtasks.
"""


def build_context_tree(context: "ApplicationContext") -> str:
    """
    Generate a tree representation showing the current context's position in the context hierarchy.
    
    Traverses up the parent chain to build a visual tree structure that shows
    the current context's location relative to its parent contexts, including subtasks.
    
    Args:
        context: ApplicationContext instance to generate tree for
        
    Returns:
        str: A formatted tree string showing the context hierarchy with subtasks
    """
    # 1. Collect entire context hierarchy
    context_path = []
    current = context
    while current is not None:
        context_path.append(current)
        current = getattr(current, '_parent', None)

    # Reverse list so root context is first
    context_path.reverse()

    # 2. Get current task ID
    current_task_id = getattr(context, 'task_id', None)

    # 3. Create a set to track processed task IDs
    processed_task_ids = set()

    # 4. Add global flag to ensure current task is only displayed once
    current_task_marked = False

    # 5. Create result list
    tree_lines = []

    # 6. Recursively build tree
    def build_tree(context_node, level, prefix):
        nonlocal current_task_marked

        # Get context identifier
        context_id = getattr(context_node, 'task_id', None) or getattr(context_node, 'session_id', 'unknown')
        task_content = getattr(context_node, 'task_input', '')
        if isinstance(task_content, list):
            task_content = task_content[0]['text']

        # Build description
        swarm_desc = ':'.join([agent.name() for agent in context_node.swarm.topology])
        context_desc = f"[T]{context_id}: [R]{task_content} : [O]{context_node.task_input_object.origin_user_input}" if task_content else str(context_id)

        # Check if current context and not yet marked
        is_current = context_node is context and not current_task_marked

        # Add current context line, only if context ID hasn't been processed
        if context_id not in processed_task_ids:
            if is_current:
                tree_lines.append(f"{prefix}📍 {context_desc} (current)")
                current_task_marked = True
            else:
                tree_lines.append(f"{prefix}├─ {context_desc}")

            # Mark as processed
            processed_task_ids.add(context_id)

        # Get sub-task list
        sub_tasks = []
        if hasattr(context_node, 'task_state') and context_node.task_state:
            if hasattr(context_node.task_state.working_state, 'sub_task_list') and context_node.task_state.working_state.sub_task_list:
                sub_tasks = context_node.task_state.working_state.sub_task_list

        # Check if there is a next level context
        next_context_index = level + 1
        next_context = context_path[next_context_index] if next_context_index < len(context_path) else None
        next_context_id = getattr(next_context, 'task_id', None) if next_context else None

        # Calculate sub-task indentation
        child_prefix = prefix + "│   "

        # Process sub-tasks in original order
        valid_sub_tasks = []
        next_context_sub_task_index = -1

        # Collect valid sub-tasks (not yet processed)
        for i, sub_task in enumerate(sub_tasks):
            sub_task_id = getattr(sub_task, 'task_id', None)
            if sub_task_id and sub_task_id not in processed_task_ids:
                valid_sub_tasks.append((i, sub_task))
                # Check if it's the next level context
                if sub_task_id == next_context_id:
                    next_context_sub_task_index = len(valid_sub_tasks) - 1

        # Process valid sub-tasks
        for i, (original_index, sub_task) in enumerate(valid_sub_tasks):
            sub_task_id = getattr(sub_task, 'task_id', None)

            # Get sub-task content
            subtask_content = ""
            if hasattr(sub_task, 'input') and sub_task.input:
                subtask_content = getattr(sub_task.input, 'task_content', str(sub_task.input))
                if isinstance(subtask_content, list):
                    subtask_content = subtask_content[0]['text']
            else:
                subtask_content = str(sub_task)

            # Determine if it's the last sub-task
            is_last = i == len(valid_sub_tasks) - 1

            # Choose appropriate connector
            connector = "└─" if is_last else "├─"

            # Check if it contains the next level context
            is_next_context_task = i == next_context_sub_task_index and next_context

            # Add sub-task line
            if sub_task_id == current_task_id and not current_task_marked:
                tree_lines.append(f"{child_prefix}{connector} 📍{swarm_desc} {sub_task_id}: {subtask_content} (current)")
                current_task_marked = True
            else:
                tree_lines.append(f"{child_prefix}{connector} {swarm_desc} {sub_task_id}: {subtask_content}")

            # Mark as processed
            processed_task_ids.add(sub_task_id)

            # If it's a sub-task containing the next level context, recursively process the next level
            if is_next_context_task:
                next_child_prefix = child_prefix + ("    " if is_last else "│   ")
                build_tree(next_context, level + 1, next_child_prefix)

    # Start building tree from root context
    if context_path:
        build_tree(context_path[0], 0, "")

    # Add tree header
    tree_header = "Context Tree (from root to current):\n"

    return tree_header + "\n".join(tree_lines)

