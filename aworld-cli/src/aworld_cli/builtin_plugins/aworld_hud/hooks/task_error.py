def handle_event(event, state):
    handle = state.get("__plugin_state__")
    if handle is not None:
        task_state = dict(state.get("task") or {})
        task_state.update(
            {
                "current_task_id": event.get("task_id"),
                "status": event.get("task_status") or "error",
            }
        )
        handle.update({"task": task_state})
    return {"action": "allow"}
