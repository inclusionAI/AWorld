from aworld_cli.builtin_plugins.ralph_session_loop.common import summarize_text


def handle_event(event, state):
    handle = state.get("__plugin_state__")
    if handle is None or not state.get("active"):
        return {"action": "allow"}

    final_answer = event.get("final_answer") or ""
    handle.update(
        {
            "last_task_status": event.get("task_status") or "completed",
            "last_final_answer": final_answer,
            "last_final_answer_excerpt": summarize_text(final_answer),
        }
    )
    return {"action": "allow"}
