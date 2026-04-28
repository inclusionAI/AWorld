def render_lines(context, plugin_state):
    active = bool(plugin_state.get("active"))
    iteration = plugin_state.get("iteration")
    max_iterations = plugin_state.get("max_iterations")
    promise = plugin_state.get("completion_promise") or "none"

    if active:
        limit = max_iterations if max_iterations is not None else "unbounded"
        segments = [
            "Ralph: active",
            f"Iter: {iteration}/{limit}",
            f"Promise: {promise}",
        ]
    else:
        segments = ["Ralph: inactive"]

    return [
        {
            "section": "session",
            "priority": 30,
            "segments": segments,
        }
    ]
