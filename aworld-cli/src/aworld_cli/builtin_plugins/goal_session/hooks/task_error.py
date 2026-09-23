from aworld_cli.builtin_plugins.goal_session.hooks.task_completed import (
    _persistable_state,
    apply_turn_outcome,
    build_goal_context_prompt,
    is_goal_active,
    summarize_text,
)


def handle_event(event, state):
    if not is_goal_active(state):
        return {"action": "allow"}

    handle = state.get("__plugin_state__")
    if handle is None:
        return {"action": "allow"}

    latest = handle.read()
    if not is_goal_active(latest):
        return {"action": "allow"}
    state = latest

    error_text = event.get("error") or ""
    updated, should_continue = apply_turn_outcome(state, {
        **event,
        "semantic_status": "incomplete",
        "completion_reason": event.get("error_type") or "attempt_error",
    })
    updated.update(
        {
            "last_task_id": event.get("task_id") or state.get("last_task_id"),
            "last_task_status": event.get("task_status") or "error",
            "last_error": error_text,
            "last_error_excerpt": summarize_text(error_text),
            "last_final_answer": "",
            "last_final_answer_excerpt": None,
            "last_partial_answer": "",
            "last_partial_answer_excerpt": None,
        }
    )
    handle.write(_persistable_state(updated))
    if should_continue:
        return {"action": "block_and_continue", "follow_up_prompt": build_goal_context_prompt(updated)}
    return {"action": "allow"}
