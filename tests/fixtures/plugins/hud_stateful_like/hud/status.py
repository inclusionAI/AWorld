from aworld_cli.plugin_capabilities.hud_helpers import (
    format_hud_elapsed,
    format_hud_tokens,
)


def render_lines(context, plugin_state):
    usage = context.get("usage", {})
    session = context.get("session", {})

    segments = [f"PluginStatus: {plugin_state.get('status', 'idle')}"]
    if plugin_state.get("task_id"):
        segments.append(f"Observed Task: {plugin_state['task_id']}")
    if usage.get("input_tokens") is not None or usage.get("output_tokens") is not None:
        segments.append(
            f"Usage: in {format_hud_tokens(usage.get('input_tokens') or 0)} out {format_hud_tokens(usage.get('output_tokens') or 0)}"
        )
    if session.get("elapsed_seconds") is not None:
        segments.append(f"Elapsed: {format_hud_elapsed(session['elapsed_seconds'])}")

    return [
        {
            "section": "activity",
            "priority": 10,
            "segments": segments,
        }
    ]
