"""
Stream display helpers for CLI: buffer state, render logic, and display controller.

Extracted from local.py for clarity. Provides atomic functions and a controller
for streaming content, tool calls, and tool results with gradual typewriter display.
"""
import asyncio
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, List, Optional

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.status import Status
from rich.text import Text
from aworld.logs.util import logger

from .stats import StreamTokenStats, format_elapsed
from ..status_text import build_status_bar_ansi_lines, build_status_bar_rich_lines


def get_terminal_size() -> tuple[int, int]:
    """Get (cols, rows). Fallback to 80x24 on error."""
    try:
        return shutil.get_terminal_size()
    except OSError:
        return 80, 24


def truncate_lines_for_display(
    lines: List[str],
    max_lines: int,
    has_more: bool,
    no_truncate: bool = False,
) -> List[str]:
    """Truncate lines to max_lines, optionally prepending [dim]...[/dim] if has_more.
    When no_truncate is True (e.g. via NO_TRUNCATE=1), return all lines.
    """
    if no_truncate or len(lines) <= max_lines:
        return lines
    out = lines[-max_lines:]
    if has_more:
        out.insert(0, "[dim]...[/dim]")
    return out


from .base_executor import env_stream_no_truncate


@dataclass
class StreamDisplayConfig:
    """Config for stream display behavior.
    Output rate scales up when buffer backlog is large (chars_per_render and line steps increase).
    Set NO_TRUNCATE=1 to show full content/tool_calls/tool_results without folding.
    """
    render_interval: float = 0.02
    chars_per_render: int = 1
    chars_per_render_max: int = 20  # Max chars per render when backlog is large
    backlog_chars_threshold: int = 100  # Chars of backlog before scaling up
    tool_lines_step_max: int = 5  # Max tool/tool_result lines per render when backlog is large
    no_truncate: bool = field(default_factory=env_stream_no_truncate)


@dataclass
class StreamDisplayBuffer:
    """
    Holds accumulated stream content and display progress.
    Content, tool_calls, and tool_results advance gradually for typewriter effect.
    """
    accumulated_content: str = ""
    accumulated_tool_calls: List[Any] = field(default_factory=list)
    accumulated_tool_result_lines: List[str] = field(default_factory=list)
    displayed_content_chars: int = 0
    displayed_tool_lines: int = 0
    displayed_tool_result_lines: int = 0

    def advance_content(self, chars_per_render: int) -> None:
        """Advance displayed content by chars_per_render."""
        if self.accumulated_content:
            self.displayed_content_chars = min(
                self.displayed_content_chars + chars_per_render,
                len(self.accumulated_content),
            )

    def advance_tool_lines(self, tool_lines: List[str], step: int = 1) -> None:
        """Advance displayed tool lines by step."""
        if tool_lines:
            self.displayed_tool_lines = min(
                self.displayed_tool_lines + step,
                len(tool_lines),
            )

    def advance_tool_result_lines(self, step: int = 1) -> None:
        """Advance displayed tool result lines by step."""
        if self.accumulated_tool_result_lines:
            self.displayed_tool_result_lines = min(
                self.displayed_tool_result_lines + step,
                len(self.accumulated_tool_result_lines),
            )

    def clear(self) -> None:
        """Reset all accumulated and displayed state."""
        self.accumulated_content = ""
        self.accumulated_tool_calls = []
        self.accumulated_tool_result_lines = []
        self.displayed_content_chars = 0
        self.displayed_tool_lines = 0
        self.displayed_tool_result_lines = 0

    def has_content(self) -> bool:
        return bool(self.accumulated_content)

    def has_tool_calls(self) -> bool:
        return bool(self.accumulated_tool_calls)

    def has_tool_results(self) -> bool:
        return bool(self.accumulated_tool_result_lines)

    def content_caught_up(self) -> bool:
        return not self.accumulated_content or self.displayed_content_chars >= len(self.accumulated_content)

    def tool_caught_up(self, tool_lines: List[str]) -> bool:
        return not tool_lines or self.displayed_tool_lines >= len(tool_lines)

    def tool_result_caught_up(self) -> bool:
        return not self.accumulated_tool_result_lines or self.displayed_tool_result_lines >= len(self.accumulated_tool_result_lines)

    def has_content_pending(self) -> bool:
        return bool(self.accumulated_content and self.displayed_content_chars < len(self.accumulated_content))

    def has_tool_pending(self, tool_lines: List[str]) -> bool:
        return bool(tool_lines and self.displayed_tool_lines < len(tool_lines))

    def has_tool_result_pending(self) -> bool:
        return bool(self.accumulated_tool_result_lines and self.displayed_tool_result_lines < len(self.accumulated_tool_result_lines))


def _compute_chars_per_render(buffer: StreamDisplayBuffer, config: StreamDisplayConfig) -> int:
    """Dynamically increase chars per render when content backlog is large."""
    if not buffer.accumulated_content:
        return config.chars_per_render
    pending = len(buffer.accumulated_content) - buffer.displayed_content_chars
    if pending <= config.backlog_chars_threshold:
        return config.chars_per_render
    extra = min(pending // config.backlog_chars_threshold, config.chars_per_render_max - config.chars_per_render)
    return min(config.chars_per_render + extra, config.chars_per_render_max)


def _compute_lines_step(displayed: int, total: int, config: StreamDisplayConfig) -> int:
    """Dynamically increase lines per render when tool/tool_result backlog is large."""
    pending = total - displayed
    if pending <= 3:
        return 1
    return min(1 + pending // 5, config.tool_lines_step_max)


def _normalize_fixed_layout_body(body_renderable: Any) -> Any:
    if isinstance(body_renderable, Text) and not body_renderable.plain:
        return Text(" ")
    if isinstance(body_renderable, Group) and not body_renderable.renderables:
        return Text(" ")
    return body_renderable


def _build_fixed_hud_layout(body_renderable: Any, hud_lines: List[str]) -> Layout:
    hud_renderables = build_status_bar_rich_lines(hud_lines)
    body_renderable = _normalize_fixed_layout_body(body_renderable)
    layout = Layout()
    layout.split_column(
        Layout(body_renderable, name="body", ratio=1),
        Layout(Group(*hud_renderables) if hud_renderables else Text(""), name="hud", size=max(1, len(hud_renderables))),
    )
    return layout


def _live_update(live: Optional[Live], renderable: Any) -> None:
    if live is None:
        return
    refresh = not getattr(live, "auto_refresh", True)
    live.update(renderable, refresh=refresh)


class FixedBottomHudRenderer:
    """Render HUD lines directly on the terminal bottom without reflowing body output."""

    def __init__(
        self,
        console: Console,
        hud_lines_fn: Optional[Callable[[], List[str]]] = None,
        reserved_lines: int = 2,
    ) -> None:
        self.console = console
        self.hud_lines_fn = hud_lines_fn
        self.reserved_lines = max(1, reserved_lines)
        self._active = False

    def _output_file(self):
        return getattr(self.console, "file", None) or sys.stdout

    def _supports_terminal_positioning(self) -> bool:
        file = self._output_file()
        try:
            return bool(hasattr(file, "isatty") and file.isatty())
        except Exception:
            return False

    def is_active(self) -> bool:
        return self._active and self._supports_terminal_positioning()

    def start(self) -> None:
        if not self._supports_terminal_positioning():
            return
        self._active = True
        self.render()

    def stop(self) -> None:
        if not self._active:
            return
        self.clear()
        self._active = False

    def suspend(self) -> None:
        if not self.is_active():
            return
        self.clear()

    def resume(self) -> None:
        if not self.is_active():
            return
        self.render()

    def _write(self, payload: str) -> None:
        file = self._output_file()
        file.write(payload)
        file.flush()

    def _build_hud_lines(self) -> List[str]:
        if self.hud_lines_fn is None:
            return []
        try:
            lines = [line for line in self.hud_lines_fn() if line]
        except Exception:
            lines = []
        return lines[-self.reserved_lines :]

    def _build_ansi_hud_lines(self, cols: int) -> List[str]:
        plain_lines = self._build_hud_lines()
        if not plain_lines:
            return []
        color_system = getattr(self.console, "color_system", None) or "truecolor"
        return build_status_bar_ansi_lines(plain_lines, color_system=str(color_system))

    def clear(self) -> None:
        if not self._supports_terminal_positioning():
            return
        _, rows = get_terminal_size()
        start_row = max(1, rows - self.reserved_lines + 1)
        ops = ["\x1b[s"]
        for offset in range(self.reserved_lines):
            ops.append(f"\x1b[{start_row + offset};1H")
            ops.append("\x1b[2K")
        ops.append("\x1b[u")
        self._write("".join(ops))

    def render(self) -> None:
        if not self.is_active():
            return
        cols, rows = get_terminal_size()
        lines = self._build_ansi_hud_lines(cols)
        start_row = max(1, rows - self.reserved_lines + 1)
        display_start = max(start_row, rows - len(lines) + 1) if lines else rows
        ops = ["\x1b[s"]
        for offset in range(self.reserved_lines):
            ops.append(f"\x1b[{start_row + offset};1H")
            ops.append("\x1b[2K")
        for index, line in enumerate(lines):
            ops.append(f"\x1b[{display_start + index};1H")
            ops.append(line)
        ops.append("\x1b[u")
        self._write("".join(ops))


def build_loading_status_renderable(
    *,
    base_message: str,
    elapsed_str: str,
    streaming_mode: bool,
    should_emit_interactive_stats: bool,
    stream_token_stats: StreamTokenStats,
    hud_lines_fn: Optional[Callable[[], List[str]]] = None,
    use_fixed_hud_layout: bool = False,
):
    if not should_emit_interactive_stats and hud_lines_fn is not None:
        try:
            hud_lines = [line for line in hud_lines_fn() if line]
        except Exception:
            hud_lines = []
        if hud_lines:
            if use_fixed_hud_layout:
                return _build_fixed_hud_layout(Text(""), hud_lines)
            return Group(*build_status_bar_rich_lines(hud_lines))

    if should_emit_interactive_stats and streaming_mode:
        msg = stream_token_stats.format_streaming_line(elapsed_str)
        if msg:
            return Text.from_markup(msg)

    return Text.from_markup(f"[dim]   {base_message} [{elapsed_str}][/dim]")


def build_stream_renderable(
    buffer: StreamDisplayBuffer,
    stream_token_stats: StreamTokenStats,
    status_start_time: Optional[datetime],
    format_tool_calls_fn: Callable[[List[Any]], List[str]],
    format_elapsed_fn: Callable[[float], str],
    config: StreamDisplayConfig,
    should_emit_interactive_stats_fn: Optional[Callable[[], bool]] = None,
    hud_lines_fn: Optional[Callable[[], List[str]]] = None,
    use_fixed_hud_layout: bool = False,
) -> Any:
    """
    Build the combined Rich renderable for Live display.
    Advances buffer display counters and optionally clears when caught up.
    """
    parts: List[Any] = []
    elapsed_str = format_elapsed_fn(
        (datetime.now() - status_start_time).total_seconds()
    ) if status_start_time else "0.0s"
    should_emit_interactive_stats = True
    if should_emit_interactive_stats_fn is not None:
        try:
            should_emit_interactive_stats = should_emit_interactive_stats_fn()
        except Exception:
            should_emit_interactive_stats = True

    msg = (
        stream_token_stats.format_streaming_line(elapsed_str)
        if should_emit_interactive_stats
        else None
    )
    if msg:
        parts.append(Text.from_markup(msg))
    stats = stream_token_stats.get_current_stats()
    aname = (stats or {}).get("agent_name") or "Assistant"

    # Show agent name header when displaying content or tool_calls; only skip when we have ONLY tool results (no content, no tool_calls)
    if msg or buffer.has_content() or buffer.has_tool_calls():
        parts.append(Text.from_markup(f"🤖 [bold cyan]{aname}[/bold cyan]"))

    tool_lines = format_tool_calls_fn(buffer.accumulated_tool_calls) if buffer.accumulated_tool_calls else []
    tool_result_lines = buffer.accumulated_tool_result_lines

    content_caught_up = buffer.content_caught_up()
    tool_caught_up = buffer.tool_caught_up(tool_lines)
    tool_result_caught_up = buffer.tool_result_caught_up()

    # Content: advance gradually, faster when backlog is large
    if buffer.accumulated_content:
        chars_this_frame = _compute_chars_per_render(buffer, config)
        buffer.advance_content(chars_this_frame)
        content_caught_up = buffer.content_caught_up()
        content = buffer.accumulated_content[:buffer.displayed_content_chars].strip("\n")
        if content:
            content = re.sub(r"\n{2,}", "\n", content)
            content_lines = content.split("\n")
            cols, rows = get_terminal_size()
            reserved = 5
            max_content_lines = max(3, rows - reserved - (4 if buffer.has_tool_calls() else 0))
            if cols < 60:
                max_content_lines = max(2, max_content_lines // 2)
            content_lines = truncate_lines_for_display(
                content_lines, max_content_lines,
                len(content_lines) > max_content_lines,
                no_truncate=config.no_truncate,
            )
            indented = "\n".join("   " + line for line in content_lines)
            parts.append(Text.from_markup(indented) if "[dim]" in indented else Text(indented))

    # Tool calls: only show after content has finished streaming, faster when backlog is large
    if buffer.accumulated_tool_calls and tool_lines and content_caught_up:
        tool_step = _compute_lines_step(buffer.displayed_tool_lines, len(tool_lines), config)
        buffer.advance_tool_lines(tool_lines, tool_step)
        tool_caught_up = buffer.tool_caught_up(tool_lines)
        displayed_tool = tool_lines[:buffer.displayed_tool_lines]
        _, rows = get_terminal_size()
        max_tool_lines = max(2, rows - 8)
        displayed_tool = truncate_lines_for_display(
            displayed_tool, max_tool_lines,
            buffer.displayed_tool_lines < len(tool_lines),
            no_truncate=config.no_truncate,
        )
        parts.append(Text.from_markup("🔧 [bold]Tool calls[/bold]"))
        tool_str = "\n".join(f"   {line}" for line in displayed_tool if line).rstrip("\n")
        if tool_str:
            parts.append(Text.from_markup(tool_str))

    # Tool results: only show after content and tool_calls have finished streaming, faster when backlog is large
    if tool_result_lines and content_caught_up and tool_caught_up:
        tr_step = _compute_lines_step(buffer.displayed_tool_result_lines, len(tool_result_lines), config)
        buffer.advance_tool_result_lines(tr_step)
        displayed_tr = tool_result_lines[:buffer.displayed_tool_result_lines]
        _, rows = get_terminal_size()
        max_tr_lines = max(50, rows + 30)
        displayed_tr = truncate_lines_for_display(
            displayed_tr, max_tr_lines,
            buffer.displayed_tool_result_lines < len(tool_result_lines),
            no_truncate=config.no_truncate,
        )
        tr_str = "\n".join(line for line in displayed_tr if line).rstrip("\n")
        if tr_str:
            parts.append(Text.from_markup(tr_str))

    if not should_emit_interactive_stats and hud_lines_fn is not None:
        try:
            hud_lines = [line for line in hud_lines_fn() if line]
        except Exception:
            hud_lines = []
        if hud_lines:
            if use_fixed_hud_layout:
                return _build_fixed_hud_layout(Group(*parts) if parts else Text(""), hud_lines)
            parts.extend(build_status_bar_rich_lines(hud_lines))

    return Group(*parts) if parts else Text("")


def _split_to_fit_width(text: str, indent: str, width: int) -> List[str]:
    """Compute max content per line from screen width, then split text into multiple lines for render."""
    if not text:
        return []
    indent_len = len(indent)
    max_content = max(20, width - indent_len)
    if len(text) <= max_content:
        return [indent + text]
    result = []
    remaining = text
    while remaining:
        if len(remaining) <= max_content:
            result.append(indent + remaining)
            break
        chunk = remaining[:max_content]
        last_space = chunk.rfind(" ")
        break_at = last_space + 1 if last_space > max_content // 2 else max_content
        result.append(indent + remaining[:break_at].rstrip())
        remaining = remaining[break_at:].lstrip()
    return result


def _print_tool_result_lines(console: Console, lines: List[str], indent: str = "   ") -> None:
    """Print tool result lines. Content is split by screen width so each line fits (no terminal wrap)."""
    cols, _ = get_terminal_size()
    width = cols - 1
    for line in lines:
        if line:
            if "⚡" in line:
                console.print(Text.from_markup(line))
            elif "\n" in line:
                for sub in line.split("\n"):
                    if sub.strip():
                        for out in _split_to_fit_width(sub.strip(), indent, width):
                            console.print(Text.from_markup(out))
                    else:
                        console.print(indent)
            else:
                stripped = line.strip()
                if stripped:
                    for out in _split_to_fit_width(stripped, indent, width):
                        console.print(Text.from_markup(out))


def print_buffer_to_console(
    console: Console,
    buffer: StreamDisplayBuffer,
    stream_token_stats: StreamTokenStats,
    format_tool_calls_fn: Callable[[List[Any]], List[str]],
    status_start_time: Optional[datetime] = None,
    format_elapsed_fn: Optional[Callable[[float], str]] = None,
    should_emit_interactive_stats_fn: Optional[Callable[[], bool]] = None,
    hud_lines_fn: Optional[Callable[[], List[str]]] = None,
    use_fixed_hud_layout: bool = False,
) -> None:
    """Print buffer content to console so it persists after Live stops."""
    if not (buffer.has_content() or buffer.has_tool_calls() or buffer.has_tool_results()):
        return
    stats = stream_token_stats.get_current_stats()
    aname = (stats or {}).get("agent_name") or "Assistant"
    # Print stats line first (before clear) so it persists in re-output
    # Only show stats when we have content or tool_calls; skip when buffer has ONLY tool results
    should_emit_interactive_stats = True
    if should_emit_interactive_stats_fn is not None:
        try:
            should_emit_interactive_stats = should_emit_interactive_stats_fn()
        except Exception:
            should_emit_interactive_stats = True

    if (
        should_emit_interactive_stats
        and status_start_time
        and format_elapsed_fn
        and stats
        and (buffer.has_content() or buffer.has_tool_calls())
    ):
        elapsed_str = format_elapsed_fn(
            (datetime.now() - status_start_time).total_seconds()
        )
        msg = stream_token_stats.format_streaming_line(elapsed_str)
        if msg:
            console.print(Text.from_markup(msg))
    # Only show agent name when we have content or tool_calls; skip when buffer has ONLY tool results
    if buffer.has_content() or buffer.has_tool_calls():
        console.print(Text.from_markup(f"🤖 [bold cyan]{aname}[/bold cyan]"))
    if buffer.accumulated_content:
        content = buffer.accumulated_content.strip("\n")
        content = re.sub(r"\n{2,}", "\n", content)
        for line in content.split("\n"):
            console.print("   " + line)
    if buffer.accumulated_tool_calls:
        tool_lines = format_tool_calls_fn(buffer.accumulated_tool_calls)
        if tool_lines:
            console.print(Text.from_markup("🔧 [bold]Tool calls[/bold]"))
            for line in tool_lines:
                if line:
                    console.print("   " + line)
    if buffer.accumulated_tool_result_lines:
        _print_tool_result_lines(console, buffer.accumulated_tool_result_lines)
    if use_fixed_hud_layout:
        return

    if not should_emit_interactive_stats and hud_lines_fn is not None:
        try:
            hud_lines = [line for line in hud_lines_fn() if line]
        except Exception:
            hud_lines = []
        for hud_line in build_status_bar_rich_lines(hud_lines):
            console.print(hud_line)


class StreamDisplayController:
    """
    Manages loading status, Live display, and the elapsed-time update loop.
    Used by LocalAgentExecutor for streaming output.
    """

    def __init__(
        self,
        console: Console,
        stream_token_stats: StreamTokenStats,
        format_tool_calls_fn: Callable[[List[Any]], List[str]],
        format_elapsed_fn: Callable[[float], str] = format_elapsed,
        config: Optional[StreamDisplayConfig] = None,
        should_emit_interactive_stats_fn: Optional[Callable[[], bool]] = None,
        hud_lines_fn: Optional[Callable[[], List[str]]] = None,
        use_fixed_hud_layout: bool = False,
    ):
        self.console = console
        self.stream_token_stats = stream_token_stats
        self.format_tool_calls_fn = format_tool_calls_fn
        self.format_elapsed_fn = format_elapsed_fn
        self.config = config or StreamDisplayConfig()
        self.should_emit_interactive_stats_fn = should_emit_interactive_stats_fn
        self.hud_lines_fn = hud_lines_fn
        self.use_fixed_hud_layout = use_fixed_hud_layout

        self.buffer = StreamDisplayBuffer()
        self.fixed_hud_renderer = FixedBottomHudRenderer(console, hud_lines_fn) if use_fixed_hud_layout else None
        self.loading_status: Optional[Status] = None
        self.status_start_time: Optional[datetime] = None
        self.status_update_task: Optional[asyncio.Task] = None
        self.base_message = ""
        self.stream_live: Optional[Live] = None
        self.streaming_mode = False

        self._pending_clear_after_display = False
        self._deferred_start_thinking = False
        self._deferred_thinking_message = ""
        self._deferred_stop_and_start_thinking = False

    def _fixed_hud_is_active(self) -> bool:
        return bool(self.fixed_hud_renderer and self.fixed_hud_renderer.is_active())

    def print_with_hud_suspended(self, callback: Callable[[], Any]) -> Any:
        if not self._fixed_hud_is_active():
            return callback()
        self.fixed_hud_renderer.suspend()
        try:
            return callback()
        finally:
            self.fixed_hud_renderer.resume()

    def start_loading(self, message: str) -> None:
        """Start or update loading status."""
        if not self.console:
            return
        self.base_message = message
        if self.use_fixed_hud_layout:
            self.status_start_time = self.status_start_time or datetime.now()
        else:
            self.status_start_time = datetime.now()
        should_emit_interactive_stats = True
        if self.should_emit_interactive_stats_fn is not None:
            try:
                should_emit_interactive_stats = self.should_emit_interactive_stats_fn()
            except Exception:
                should_emit_interactive_stats = True
        if self.use_fixed_hud_layout and not should_emit_interactive_stats:
            if self.fixed_hud_renderer is not None:
                self.fixed_hud_renderer.start()
            if ("Thinking" in message or "Calling tool" in message) and self.status_update_task is None:
                self.status_update_task = asyncio.create_task(self._update_elapsed_loop())
            return
        renderable = build_loading_status_renderable(
            base_message=message,
            elapsed_str="0.0s",
            streaming_mode=self.streaming_mode,
            should_emit_interactive_stats=should_emit_interactive_stats,
            stream_token_stats=self.stream_token_stats,
            hud_lines_fn=self.hud_lines_fn,
            use_fixed_hud_layout=self.use_fixed_hud_layout,
        )
        if self.loading_status:
            self.loading_status.update(renderable)
        else:
            if self.stream_live:
                self.stream_live.stop()
                self.stream_live = None
            self.loading_status = Status(renderable, console=self.console)
            self.loading_status.start()
        if ("Thinking" in message or "Calling tool" in message) and self.status_update_task is None:
            self.status_update_task = asyncio.create_task(self._update_elapsed_loop())

    def stop_loading(self) -> None:
        """Stop loading status and Live display. Print buffer to console before stopping Live."""
        if self.use_fixed_hud_layout:
            if self.fixed_hud_renderer is not None:
                self.fixed_hud_renderer.render()
            return
        if self.status_update_task:
            self.status_update_task.cancel()
            self.status_update_task = None
        if self.stream_live:
            tool_lines = self.format_tool_calls_fn(self.buffer.accumulated_tool_calls) if self.buffer.accumulated_tool_calls else []
            # Only print to console when display hasn't caught up; otherwise Live's last frame already has full content
            caught_up = (
                self.buffer.content_caught_up()
                and self.buffer.tool_caught_up(tool_lines)
                and self.buffer.tool_result_caught_up()
            )
            if (
                (self.buffer.has_content() or self.buffer.has_tool_calls() or self.buffer.has_tool_results())
                and not caught_up
            ):
                logger.info(f"stop_loading: {self.buffer.has_content()} {self.buffer.has_tool_calls()} {self.buffer.has_tool_results()}")
                if self.use_fixed_hud_layout:
                    self.stream_live.stop()
                    self.stream_live = None
                print_buffer_to_console(
                    self.console,
                    self.buffer,
                    self.stream_token_stats,
                    self.format_tool_calls_fn,
                    status_start_time=self.status_start_time,
                    format_elapsed_fn=self.format_elapsed_fn,
                    should_emit_interactive_stats_fn=self.should_emit_interactive_stats_fn,
                    hud_lines_fn=self.hud_lines_fn,
                    use_fixed_hud_layout=self.use_fixed_hud_layout,
                )
                self.buffer.clear()
            if self.stream_live is not None:
                self.stream_live.stop()
                self.stream_live = None
        if self.loading_status:
            self.loading_status.stop()
            self.loading_status = None
        self.status_start_time = None

    def ensure_live_running(self) -> None:
        """Create and start Live display if not already running."""
        if self.use_fixed_hud_layout:
            return
        if self.stream_live is not None:
            return
        if self.loading_status:
            self.loading_status.stop()
            self.loading_status = None
        cols, rows = get_terminal_size()
        live_console = Console(width=cols, height=rows)
        # In non-TTY (piped/CI), Live prints each frame instead of updating in place; use lower refresh to reduce duplicate output
        try:
            is_tty = sys.stdout.isatty() if hasattr(sys.stdout, "isatty") else False
        except Exception:
            is_tty = False
        refresh_rate = 1 if not is_tty else 10
        self.stream_live = Live(
            console=live_console,
            refresh_per_second=refresh_rate,
            auto_refresh=not self.use_fixed_hud_layout,
            transient=self.use_fixed_hud_layout,
            vertical_overflow="crop",
        )
        self.stream_live.start()
        self.status_start_time = self.status_start_time or datetime.now()
        if self.status_update_task is None or self.status_update_task.done():
            self.status_update_task = asyncio.create_task(self._update_elapsed_loop())

    def _render_display(self) -> Group:
        """Build renderable for current buffer state. Clears buffer when display caught up and pending."""
        result = build_stream_renderable(
            self.buffer,
            self.stream_token_stats,
            self.status_start_time,
            self.format_tool_calls_fn,
            self.format_elapsed_fn,
            self.config,
            should_emit_interactive_stats_fn=self.should_emit_interactive_stats_fn,
            hud_lines_fn=self.hud_lines_fn,
            use_fixed_hud_layout=self.use_fixed_hud_layout,
        )
        tool_lines = self.format_tool_calls_fn(self.buffer.accumulated_tool_calls) if self.buffer.accumulated_tool_calls else []
        if (
            self._pending_clear_after_display
            and self.buffer.content_caught_up()
            and self.buffer.tool_caught_up(tool_lines)
            and self.buffer.tool_result_caught_up()
        ):
            if self.stream_live and (
                self.buffer.has_content()
                or self.buffer.has_tool_calls()
                or self.buffer.has_tool_results()
            ):
                self.stream_live.stop()
                self.stream_live = None
            if self.console and (
                self.buffer.has_content()
                or self.buffer.has_tool_calls()
                or self.buffer.has_tool_results()
            ):
                logger.info(f"_render_display: {self.buffer.has_content()} {self.buffer.has_tool_calls()} {self.buffer.has_tool_results()}")
                print_buffer_to_console(
                    self.console,
                    self.buffer,
                    self.stream_token_stats,
                    self.format_tool_calls_fn,
                    status_start_time=self.status_start_time,
                    format_elapsed_fn=self.format_elapsed_fn,
                    should_emit_interactive_stats_fn=self.should_emit_interactive_stats_fn,
                    hud_lines_fn=self.hud_lines_fn,
                    use_fixed_hud_layout=self.use_fixed_hud_layout,
                )
            self.stream_token_stats.clear()
            self.buffer.clear()
            self._pending_clear_after_display = False
            self._deferred_stop_and_start_thinking = True
        return result

    def set_pending_clear(self) -> None:
        """Mark that we should wait for display to finish before clearing."""
        self._pending_clear_after_display = True

    def set_deferred_thinking(self, message: str = "💭 Thinking...") -> None:
        """Defer start of Thinking status until display is done."""
        self._deferred_start_thinking = True
        self._deferred_thinking_message = message

    def has_pending_display(
        self,
        stream_on: bool,
        received_chunk_output: bool,
        tool_result_pending: bool,
    ) -> bool:
        """Check if we have content/tool/tool_result still displaying."""
        tool_lines = self.format_tool_calls_fn(self.buffer.accumulated_tool_calls) if self.buffer.accumulated_tool_calls else []
        content_pending = self.buffer.has_content_pending()
        tool_pending = self.buffer.has_tool_pending(tool_lines)
        return stream_on and (
            (received_chunk_output and (content_pending or tool_pending)) or tool_result_pending
        )

    def has_any_pending(self, stream_on: bool) -> bool:
        """Check if we have any pending display (content, tool, or tool_result)."""
        tool_lines = self.format_tool_calls_fn(self.buffer.accumulated_tool_calls) if self.buffer.accumulated_tool_calls else []
        return stream_on and (
            self.buffer.has_content_pending()
            or self.buffer.has_tool_pending(tool_lines)
            or self.buffer.has_tool_result_pending()
        )

    async def _update_elapsed_loop(self) -> None:
        """Background task: update elapsed time and render stream buffer at fixed interval."""
        while (self.loading_status or self.stream_live or self._fixed_hud_is_active()) and self.status_start_time:
            elapsed = (datetime.now() - self.status_start_time).total_seconds()
            elapsed_str = self.format_elapsed_fn(elapsed)
            if self._fixed_hud_is_active() and not self.stream_live and not self.loading_status:
                self.fixed_hud_renderer.render()
                await asyncio.sleep(0.15)
                continue
            if self.stream_live:
                result = self._render_display()
                if self.stream_live is not None:
                    _live_update(self.stream_live, result)
                if self._deferred_stop_and_start_thinking:
                    self._deferred_stop_and_start_thinking = False
                    self.stop_loading()
                    if self._deferred_start_thinking:
                        self._deferred_start_thinking = False
                        self.start_loading(self._deferred_thinking_message)
                        self._deferred_thinking_message = ""
                await asyncio.sleep(self.config.render_interval)
                continue
            if self.loading_status:
                if self.streaming_mode:
                    should_emit_interactive_stats = True
                    if self.should_emit_interactive_stats_fn is not None:
                        try:
                            should_emit_interactive_stats = self.should_emit_interactive_stats_fn()
                        except Exception:
                            should_emit_interactive_stats = True
                    status_msg = build_loading_status_renderable(
                        base_message=self.base_message,
                        elapsed_str=elapsed_str,
                        streaming_mode=True,
                        should_emit_interactive_stats=should_emit_interactive_stats,
                        stream_token_stats=self.stream_token_stats,
                        hud_lines_fn=self.hud_lines_fn,
                        use_fixed_hud_layout=self.use_fixed_hud_layout,
                    )
                else:
                    should_emit_interactive_stats = True
                    if self.should_emit_interactive_stats_fn is not None:
                        try:
                            should_emit_interactive_stats = self.should_emit_interactive_stats_fn()
                        except Exception:
                            should_emit_interactive_stats = True
                    status_msg = build_loading_status_renderable(
                        base_message=self.base_message,
                        elapsed_str=elapsed_str,
                        streaming_mode=False,
                        should_emit_interactive_stats=should_emit_interactive_stats,
                        stream_token_stats=self.stream_token_stats,
                        hud_lines_fn=self.hud_lines_fn,
                        use_fixed_hud_layout=self.use_fixed_hud_layout,
                    )
                self.loading_status.update(status_msg)
            await asyncio.sleep(0.15)

    async def wait_for_display_done(self) -> None:
        """Wait until buffer has finished displaying (when pending clear)."""
        while self._pending_clear_after_display and self.stream_live:
            tool_lines = self.format_tool_calls_fn(self.buffer.accumulated_tool_calls) if self.buffer.accumulated_tool_calls else []
            content_done = self.buffer.content_caught_up()
            tool_done = self.buffer.tool_caught_up(tool_lines)
            tool_result_done = self.buffer.tool_result_caught_up()
            if content_done and tool_done and tool_result_done:
                break
            await asyncio.sleep(self.config.render_interval)

    def close(self) -> None:
        if self.status_update_task:
            self.status_update_task.cancel()
            self.status_update_task = None
        if self.stream_live:
            self.stream_live.stop()
            self.stream_live = None
        if self.loading_status:
            self.loading_status.stop()
            self.loading_status = None
        if self.fixed_hud_renderer is not None:
            self.fixed_hud_renderer.stop()
        self.status_start_time = None
