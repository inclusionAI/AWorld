from aworld_cli.builtin_plugins.ralph_session_loop.common import summarize_text


def handle_event(event, state):
    handle = state.get("__plugin_state__")
    if handle is None or not state.get("active"):
        return {"action": "allow"}

    error_text = event.get("error") or ""
    handle.update(
        {
            "last_task_status": event.get("task_status") or "error",
            "last_final_answer": "",
            "last_final_answer_excerpt": summarize_text(error_text),
        }
    )
    return {"action": "allow"}
