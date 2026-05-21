def render_lines(context, plugin_state):
    steering = context.get("steering", {})
    if not isinstance(steering, dict) or not steering:
        return []

    active = "active" if steering.get("active") else "idle"
    pending = int(steering.get("pending_count", 0) or 0)
    interrupted = "yes" if steering.get("interrupt_requested") else "no"
    return [
        {
            "section": "session",
            "priority": 25,
            "segments": (
                f"Steering: {active}",
                f"Pending: {pending}",
                f"Interrupt: {interrupted}",
            ),
        }
    ]
