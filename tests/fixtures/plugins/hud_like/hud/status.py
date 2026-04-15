def render_lines(context):
    session = context.get("session", {})
    return [
        {
            "section": "session",
            "priority": 10,
            "text": f"Agent: {session.get('agent', 'unknown')}",
        },
        {
            "section": "custom",
            "priority": 50,
            "text": "Plugin: HUD ready",
        },
    ]
