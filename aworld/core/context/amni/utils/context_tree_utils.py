# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Context Tree Utilities - Generate tree representation of context hierarchy.

This module provides utilities for visualizing the context hierarchy structure,
showing the relationship between parent and child contexts including subtasks.
"""


def format_task_content(task_content) -> str:
    """
    Format task content, handling both string and list formats (with images).
    
    When task_content is a list containing images, formats as "text [📷 x image(s)]"
    to avoid printing long base64 strings.
    
    Args:
        task_content: Task content, can be str or list of dicts
        
    Returns:
        Formatted string representation
        
    Example:
        >>> format_task_content("simple text")
        "simple text"
        >>> format_task_content([{'text': 'hello', 'type': 'text'}, {'image_url': {'url': 'data:image/png;base64,...'}, 'type': 'image_url'}])
        "hello [📷 1 image(s)]"
    """
    if not task_content:
        return ""
    
    if isinstance(task_content, str):
        return task_content
    
    if isinstance(task_content, list):
        text_parts = []
        image_count = 0
        
        for item in task_content:
            if not isinstance(item, dict):
                continue
            
            item_type = item.get('type')
            if item_type == 'text':
                text = item.get('text', '')
                if text:
                    text_parts.append(text)
            elif item_type == 'image_url':
                image_count += 1
        
        # Combine text parts
        text_content = ' '.join(text_parts).strip()
        
        # Format: "text [📷 x image(s)]" or just "[📷 x image(s)]" if no text
        if text_content and image_count > 0:
            return f"{text_content} [📷 {image_count} image(s)]"
        elif image_count > 0:
            return f"[📷 {image_count} image(s)]"
        elif text_content:
            return text_content
        else:
            return ""
    
    return str(task_content)


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
        # Format task content (handles images)
        formatted_task_content = format_task_content(task_content)

        # Build description
        swarm_desc = ':'.join([agent.name() for agent in context_node.swarm.ordered_agents])
        origin_input = getattr(context_node, 'task_input_object', None)
        origin_user_input = format_task_content(origin_input.origin_user_input) if origin_input and hasattr(origin_input, 'origin_user_input') else ""
        context_desc = f"[T]{context_id}: [R]{formatted_task_content} : [O]{origin_user_input}" if formatted_task_content else str(context_id)

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
                raw_content = getattr(sub_task.input, 'task_content', str(sub_task.input))
                # Format task content (handles images)
                subtask_content = format_task_content(raw_content)
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

