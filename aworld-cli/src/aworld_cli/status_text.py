from __future__ import annotations

from typing import Any
from rich.style import Style
from rich.text import Text


_DEFAULT_SEGMENT_STYLE = ("#181b2d", "#d8def5")
_SEGMENT_STYLES = [
    ("#181b2d", "#84c7c6"),
    ("#181b2d", "#d8def5"),
    ("#181b2d", "#8ed081"),
    ("#181b2d", "#b8c0da"),
    ("#181b2d", "#a88bd8"),
    ("#181b2d", "#8ea0c4"),
]
_SEGMENT_STYLES_WITH_UNREAD = [
    ("#181b2d", "#84c7c6"),
    ("#181b2d", "#d8def5"),
    ("#181b2d", "#f2c14e"),
    ("#181b2d", "#b8c0da"),
    ("#181b2d", "#a88bd8"),
    ("#181b2d", "#8ea0c4"),
]
_DIVIDER_STYLE = ("#181b2d", "#4f5877")


def should_render_status_bar(runtime) -> bool:
    if runtime is None:
        return False

    if hasattr(runtime, "active_plugin_capabilities"):
        try:
            return "hud" in tuple(runtime.active_plugin_capabilities())
        except Exception:
            return True

    return True


def fallback_status_segments(
    hud_context: dict[str, Any],
    agent_name: str,
    mode: str,
    workspace_name: str,
    git_branch: str,
) -> list[str]:
    unread_count = hud_context.get("notifications", {}).get("cron_unread", -1)
    if unread_count < 0:
        cron_status = "Cron: offline"
    elif unread_count > 0:
        cron_status = f"Cron: {unread_count} unread"
    else:
        cron_status = "Cron: clear"

    return [
        f"Agent: {hud_context.get('session', {}).get('agent', agent_name)}",
        f"Mode: {hud_context.get('session', {}).get('mode', mode)}",
        cron_status,
        f"Workspace: {hud_context.get('workspace', {}).get('name', workspace_name)}",
        f"Branch: {hud_context.get('vcs', {}).get('branch', git_branch)}",
    ]


def reduce_segments(
    segments: list[str],
    max_width: int | None,
    priority_labels: set[str] | None = None,
) -> list[str]:
    if max_width is None:
        return list(segments)
    kept = list(segments)
    priority_labels = priority_labels or set()
    while len(kept) > 1 and len(" | ".join(kept)) > max_width:
        if len(kept) <= 2:
            kept.pop()
            continue
        removable_index = None
        for index in range(len(kept) - 1, -1, -1):
            segment = kept[index]
            label = segment.split(":", 1)[0].strip().lower()
            if label not in priority_labels:
                removable_index = index
                break
        if removable_index is None:
            removable_index = len(kept) - 1
        kept.pop(removable_index)
    return kept


def render_status_line(segments: list[str], max_width: int | None, section: str | None = None) -> str:
    priority_labels = None
    if section == "activity":
        priority_labels = {"task", "ctx"}
    reduced = reduce_segments(segments, max_width, priority_labels=priority_labels)
    text = " | ".join(reduced)
    if max_width is not None and max_width > 3 and len(text) > max_width:
        return text[: max_width - 3].rstrip() + "..."
    return text


def _build_hud_context(runtime, agent_name: str, mode: str, workspace_name: str, git_branch: str) -> dict[str, Any]:
    if runtime and hasattr(runtime, "build_hud_context"):
        return runtime.build_hud_context(
            agent_name=agent_name,
            mode=mode,
            workspace_name=workspace_name,
            git_branch=git_branch,
        )

    notification_center = getattr(runtime, "_notification_center", None)
    if not notification_center:
        unread_count = -1
    else:
        unread_count = notification_center.get_unread_count()
    return {
        "workspace": {"name": workspace_name},
        "session": {"agent": agent_name, "mode": mode},
        "notifications": {"cron_unread": unread_count},
        "vcs": {"branch": git_branch},
    }


def _render_status_bar_lines_from_context(
    runtime,
    hud_context: dict[str, Any],
    agent_name: str,
    mode: str,
    workspace_name: str,
    git_branch: str,
    max_width: int | None,
) -> list[str]:
    try:
        plugin_lines = runtime.get_hud_lines(hud_context) if runtime and hasattr(runtime, "get_hud_lines") else []
    except Exception:
        plugin_lines = []

    if not plugin_lines:
        fallback_segments = fallback_status_segments(
            hud_context,
            agent_name,
            mode,
            workspace_name,
            git_branch,
        )
        return [render_status_line(fallback_segments, max_width)]

    rendered_lines = []
    for line in plugin_lines[:2]:
        segments = list(getattr(line, "segments", ()) or ())
        if not segments:
            text = getattr(line, "text", "")
            if text:
                segments = [str(text)]
        if not segments:
            continue
        section = getattr(line, "section", "")
        rendered_lines.append(render_status_line(segments, max_width, section=section))

    if not rendered_lines:
        fallback_segments = fallback_status_segments(
            hud_context,
            agent_name,
            mode,
            workspace_name,
            git_branch,
        )
        return [render_status_line(fallback_segments, max_width)]

    return rendered_lines


def build_status_bar_lines(
    runtime,
    agent_name: str = "Aworld",
    mode: str = "Chat",
    workspace_name: str = "workspace",
    git_branch: str = "n/a",
    max_width: int | None = 160,
) -> list[str]:
    if not should_render_status_bar(runtime):
        return []

    hud_context = _build_hud_context(runtime, agent_name, mode, workspace_name, git_branch)
    return _render_status_bar_lines_from_context(
        runtime,
        hud_context,
        agent_name,
        mode,
        workspace_name,
        git_branch,
        max_width,
    )


def build_status_bar_lines_from_context(
    runtime,
    hud_context: dict[str, Any],
    agent_name: str = "Aworld",
    mode: str = "Chat",
    workspace_name: str = "workspace",
    git_branch: str = "n/a",
    max_width: int | None = 160,
) -> list[str]:
    if not should_render_status_bar(runtime):
        return []
    return _render_status_bar_lines_from_context(
        runtime,
        hud_context,
        agent_name,
        mode,
        workspace_name,
        git_branch,
        max_width,
    )


def build_execution_hud_lines(
    runtime,
    agent_name: str = "Aworld",
    mode: str = "Chat",
    workspace_name: str = "workspace",
    git_branch: str = "n/a",
    max_width: int | None = 160,
) -> list[str]:
    return build_status_bar_lines(
        runtime,
        agent_name=agent_name,
        mode=mode,
        workspace_name=workspace_name,
        git_branch=git_branch,
        max_width=max_width,
    )


def build_status_bar_text(
    runtime,
    agent_name: str = "Aworld",
    mode: str = "Chat",
    workspace_name: str = "workspace",
    git_branch: str = "n/a",
    max_width: int | None = 160,
) -> str:
    return "\n".join(
        build_status_bar_lines(
            runtime,
            agent_name=agent_name,
            mode=mode,
            workspace_name=workspace_name,
            git_branch=git_branch,
            max_width=max_width,
        )
    )


def build_status_bar_rich_lines(lines: list[str]) -> list[Text]:
    renderables: list[Text] = []
    for line_text in lines:
        text = build_status_bar_rich_line(line_text)
        if text is not None:
            renderables.append(text)
    return renderables


def iter_status_bar_segments(line_text: str) -> list[tuple[str, tuple[str, str]]]:
    segments = [segment.strip() for segment in line_text.split("|") if segment.strip()]
    if not segments:
        return []
    has_unread = any("unread" in segment.lower() for segment in segments)
    palette = _SEGMENT_STYLES_WITH_UNREAD if has_unread else _SEGMENT_STYLES
    styled_segments: list[tuple[str, tuple[str, str]]] = []
    for index, segment in enumerate(segments):
        style = palette[index] if index < len(palette) else _DEFAULT_SEGMENT_STYLE
        styled_segments.append((segment, style))
    return styled_segments


def build_status_bar_rich_line(line_text: str) -> Text | None:
    styled_segments = iter_status_bar_segments(line_text)
    if not styled_segments:
        return None
    text = Text()
    for index, (segment, (bg, fg)) in enumerate(styled_segments):
        text.append(f" {segment} ", style=Style(color=fg, bgcolor=bg))
        if index < len(styled_segments) - 1:
            div_bg, div_fg = _DIVIDER_STYLE
            text.append(" | ", style=Style(color=div_fg, bgcolor=div_bg))
    return text


def build_status_bar_ansi_lines(lines: list[str], color_system: str | None = "truecolor") -> list[str]:
    ansi_lines: list[str] = []
    resolved_color_system = color_system or "truecolor"
    for line_text in lines:
        rich_line = build_status_bar_rich_line(line_text)
        if rich_line is None:
            continue
        fragments: list[str] = []
        plain = rich_line.plain
        spans = list(rich_line.spans)
        cursor = 0
        for span in spans:
            if span.start > cursor:
                fragments.append(plain[cursor:span.start])
            style = span.style if isinstance(span.style, Style) else Style.parse(str(span.style))
            fragments.append(style.render(plain[span.start:span.end], color_system=resolved_color_system))
            cursor = span.end
        if cursor < len(plain):
            fragments.append(plain[cursor:])
        ansi_lines.append("".join(fragments))
    return ansi_lines
