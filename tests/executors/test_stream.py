import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld_cli.executors.stats import StreamTokenStats
from aworld_cli.executors.stream import (
    ActiveSteeringCommitBuffer,
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


def test_commit_buffer_keeps_short_tool_results_full():
    buffer = ActiveSteeringCommitBuffer(max_full_result_lines=8, max_summary_lines=4)

    committed = buffer.commit_tool_result(
        ["first line", "second line", "third line"],
        exit_code=0,
    )

    assert committed == {
        "kind": "tool_result_committed",
        "text": "first line\nsecond line\nthird line",
    }


def test_commit_buffer_summarizes_long_tool_results():
    buffer = ActiveSteeringCommitBuffer(max_full_result_lines=4, max_summary_lines=3)

    committed = buffer.commit_tool_result(
        [
            "line 1",
            "line 2",
            "line 3",
            "line 4",
            "line 5",
            "line 6",
        ],
        exit_code=7,
    )

    assert committed == {
        "kind": "tool_result_committed",
        "text": "Exit code: 7\nline 1\nline 2\nline 3\n... (3 more lines)",
    }


def test_commit_buffer_sanitizes_message_text():
    buffer = ActiveSteeringCommitBuffer()

    buffer.append_message_delta("\x1b[?1;36m\talpha")
    committed = buffer.commit_message("\n?[1;36m\x1b[0m\tbeta\x07\n", agent_name="Aworld")

    assert committed == {
        "kind": "message_committed",
        "agent_name": "Aworld",
        "text": "    alpha\n    beta",
    }


def test_commit_buffer_preserves_ordinary_bracketed_content():
    buffer = ActiveSteeringCommitBuffer()

    committed = buffer.commit_message(
        "array[0] [1] text [A] tail\n",
        agent_name="Aworld",
    )

    assert committed == {
        "kind": "message_committed",
        "agent_name": "Aworld",
        "text": "array[0] [1] text [A] tail",
    }


def test_commit_buffer_reset_clears_pending_message_chunks():
    buffer = ActiveSteeringCommitBuffer()

    buffer.append_message_delta("stale ")

    assert buffer.has_pending_message() is True

    buffer.reset()

    assert buffer.has_pending_message() is False
    assert buffer.commit_message(agent_name="Aworld") is None
