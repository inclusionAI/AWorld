from aworld_cli.executors.stats import format_context_bar_hud, format_elapsed, format_tokens

def _identity_segments(context):
    session = context.get("session", {})
    workspace = context.get("workspace", {})
    vcs = context.get("vcs", {})
    notifications = context.get("notifications", {})
    agent = session.get("agent", "Aworld")
    mode = session.get("mode", "Chat")
    model = session.get("model")
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

    segments.append(cron_segment)
    return segments


def _activity_segments(context):
    task = context.get("task", {})
    session = context.get("session", {})
    usage = context.get("usage", {})

    segments = []
    current_task_id = task.get("current_task_id")
    if current_task_id:
        segments.append(f"Task: {current_task_id}")

    if usage.get("input_tokens") is not None or usage.get("output_tokens") is not None:
        input_tokens = usage.get("input_tokens") or 0
        output_tokens = usage.get("output_tokens") or 0
        segments.append(f"Tokens: in {format_tokens(input_tokens)} out {format_tokens(output_tokens)}")

    context_used = usage.get("context_used")
    context_max = usage.get("context_max")
    if context_used is not None and context_max:
        segments.append(format_context_bar_hud(context_used, context_max, bar_width=10))
    elif usage.get("context_percent") is not None:
        segments.append(f"Ctx: {usage['context_percent']}%")

    elapsed = session.get("elapsed_seconds")
    if elapsed is not None:
        segments.append(format_elapsed(elapsed))

    return segments


def render_lines(context):
    return [
        {"section": "identity", "priority": 10, "segments": _identity_segments(context)},
        {"section": "activity", "priority": 20, "segments": _activity_segments(context)},
    ]
