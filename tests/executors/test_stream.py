import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld_cli.executors.stats import StreamTokenStats
from aworld_cli.executors.stream import (
    StreamDisplayController,
    StreamDisplayBuffer,
    StreamDisplayConfig,
    build_stream_renderable,
)


def _collect_text(renderable) -> str:
    parts = []
    for item in getattr(renderable, "renderables", ()):
        plain = getattr(item, "plain", None)
        if plain:
            parts.append(plain)
        else:
            parts.append(str(item))
    return "\n".join(parts)


def test_build_stream_renderable_hides_stats_line_when_requested():
    stats = StreamTokenStats()
    stats.update(
        agent_id="agent-1",
        agent_name="Aworld",
        input_tokens=7300,
        output_tokens=33,
        tool_calls_count=0,
        model_name="claude-sonnet-4-5-20250929",
    )

    buffer = StreamDisplayBuffer(accumulated_content="已成功设置提醒。")
    renderable = build_stream_renderable(
        buffer=buffer,
        stream_token_stats=stats,
        status_start_time=datetime.now(),
        format_tool_calls_fn=lambda _tool_calls: [],
        format_elapsed_fn=lambda _elapsed: "7.5s",
        config=StreamDisplayConfig(chars_per_render=100),
        show_stats_line=False,
    )

    text = _collect_text(renderable)
    assert "Aworld stats" not in text
    assert "🤖 Aworld" in text
    assert "已成功设置提醒。" in text


def test_stream_display_controller_can_disable_loading_status():
    controller = StreamDisplayController(
        console=Console(),
        stream_token_stats=StreamTokenStats(),
        format_tool_calls_fn=lambda _tool_calls: [],
        loading_enabled=False,
    )

    controller.start_loading("💭 Thinking...")

    assert controller.loading_status is None
    assert controller.status_start_time is not None
