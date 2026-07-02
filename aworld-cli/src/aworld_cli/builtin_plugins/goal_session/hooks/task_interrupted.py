from aworld_cli.builtin_plugins.goal_session.hooks.task_completed import (
    _persistable_state,
    is_goal_active,
    summarize_text,
)


def handle_event(event, state):
    if not is_goal_active(state):
        return {"action": "allow"}

    handle = state.get("__plugin_state__")
    if handle is None:
        return {"action": "allow"}

    partial_answer = event.get("partial_answer") or ""
    updated = dict(state)
    updated.update(
        {
            "last_task_status": event.get("task_status") or "interrupted",
            "last_error": "",
            "last_error_excerpt": None,
            "last_partial_answer": partial_answer,
            "last_partial_answer_excerpt": summarize_text(partial_answer),
            "last_final_answer": "",
            "last_final_answer_excerpt": None,
        }
    )
    handle.write(_persistable_state(updated))
    return {"action": "allow"}
