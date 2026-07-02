def handle_event(event, state):
    state["__plugin_state__"].update(
        {
            "status": "started",
            "task_id": event.get("task_id"),
            "session_id": event.get("session_id"),
        }
    )
    return {"action": "allow"}
