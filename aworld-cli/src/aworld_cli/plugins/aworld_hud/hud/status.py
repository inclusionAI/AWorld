from aworld_cli.executors.stats import format_elapsed, format_tokens

def _identity_segments(context):
    session = context.get("session", {})
    workspace = context.get("workspace", {})
    vcs = context.get("vcs", {})
    notifications = context.get("notifications", {})
    agent = session.get("agent", "Aworld")
    mode = session.get("mode", "Chat")
    model = session.get("model")
    elapsed = session.get("elapsed_seconds")
    cron = notifications.get("cron_unread", 0)

    segments = [f"Agent: {agent} / {mode}"]
    if model:
        segments.append(f"Model: {model}")
    segments.append(f"Workspace: {workspace.get('name', 'workspace')}")
    segments.append(f"Branch: {vcs.get('branch', 'n/a')}")

    if cron < 0:
        cron_segment = "Cron: offline"
    elif cron > 0:
        cron_segment = f"Cron: {cron} unread"
    else:
        cron_segment = "Cron: clear"

    if elapsed:
        cron_segment = f"{cron_segment} ({format_elapsed(elapsed)})"
    segments.append(cron_segment)
    return segments


def _activity_segments(context):
    task = context.get("task", {})
    activity = context.get("activity", {})
    usage = context.get("usage", {})
    plugins = context.get("plugins", {})

    segments = []
    current_task_id = task.get("current_task_id")
    if current_task_id:
        segments.append(f"Task: {current_task_id} ({task.get('status', 'idle')})")

    if usage.get("input_tokens") is not None or usage.get("output_tokens") is not None:
        input_tokens = usage.get("input_tokens") or 0
        output_tokens = usage.get("output_tokens") or 0
        segments.append(f"Tokens: in {format_tokens(input_tokens)} out {format_tokens(output_tokens)}")

    if usage.get("context_percent") is not None:
        segments.append(f"Ctx: {usage['context_percent']}%")

    current_tool = activity.get("current_tool")
    tool_calls_count = activity.get("tool_calls_count", 0)
    if current_tool:
        if tool_calls_count:
            segments.append(f"Tool: {current_tool} x{tool_calls_count}")
        else:
            segments.append(f"Tool: {current_tool}")
    elif tool_calls_count:
        segments.append(f"Tools: {tool_calls_count}")

    if plugins.get("active_count", 0) > 1:
        segments.append(f"Plugins: {plugins['active_count']}")

    return segments


def render_lines(context):
    return [
        {"section": "identity", "priority": 10, "segments": _identity_segments(context)},
        {"section": "activity", "priority": 20, "segments": _activity_segments(context)},
    ]
