from aworld_cli.builtin_plugins.goal_session.hooks.task_completed import goal_status


def render_lines(context, plugin_state):
    status = goal_status(plugin_state)
    if status == "none":
        return []

    turn_count = plugin_state.get("turn_count") or 1
    max_turns = plugin_state.get("max_turns")
    verification_commands = plugin_state.get("verification_commands") or []

    segments = [f"Goal: {status}"]
    segments.append(f"Turns: {turn_count}/{max_turns if max_turns is not None else 'unbounded'}")
    segments.append(f"Verify: {len(verification_commands)}")

    return [
        {
            "section": "session",
            "priority": 25,
            "segments": segments,
        }
    ]
