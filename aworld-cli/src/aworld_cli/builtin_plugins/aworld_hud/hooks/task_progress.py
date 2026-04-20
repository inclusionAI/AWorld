def handle_event(event, state):
    handle = state.get("__plugin_state__")
    if handle is not None:
        usage_payload = event.get("usage") or {}

        task_state = dict(state.get("task") or {})
        task_state.update(
            {
                "current_task_id": event.get("task_id"),
                "status": "running",
            }
        )

        session_state = dict(state.get("session") or {})
        if usage_payload.get("model") is not None:
            session_state["model"] = usage_payload.get("model")
        if event.get("elapsed_seconds") is not None:
            session_state["elapsed_seconds"] = event.get("elapsed_seconds")

        usage_state = dict(state.get("usage") or {})
        usage_state.update(usage_payload)

        handle.update(
            {
                "task": task_state,
                "session": session_state,
                "usage": usage_state,
            }
        )
    return {"action": "allow"}
