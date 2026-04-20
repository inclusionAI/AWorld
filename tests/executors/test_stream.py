import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld_cli.executors.stats import StreamTokenStats
from aworld_cli.executors.stream import (
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

