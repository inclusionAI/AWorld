import sys
from pathlib import Path
from unittest.mock import MagicMock

from rich.console import Console
from rich.text import Text

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld_cli.executors.local import LocalAgentExecutor
from aworld_cli.executors.stats import StreamTokenStats
from aworld_cli.executors.stream import StreamDisplayBuffer, StreamDisplayConfig, build_stream_renderable


class HudRuntime:
    def active_plugin_capabilities(self):
        return ("hud", "tools")


class NoHudRuntime:
    def active_plugin_capabilities(self):
        return ("tools",)


class BrokenCapabilityRuntime:
    def active_plugin_capabilities(self):
        raise RuntimeError("capability probe failed")


def _build_stats() -> StreamTokenStats:
    stats = StreamTokenStats()
    stats.update(
        agent_id="agent-1",
        agent_name="Aworld",
        output_tokens=10,
        input_tokens=20,
        tool_calls_count=0,
        model_name="gpt-4o",
    )
    return stats


def test_interactive_stats_are_suppressed_when_hud_capability_is_active():
    executor = object.__new__(LocalAgentExecutor)
    executor._base_runtime = HudRuntime()
    executor.console = MagicMock()

    executor._print_interactive_stats_fallback(_build_stats(), elapsed_seconds=1.2)

    executor.console.print.assert_not_called()


def test_interactive_stats_are_printed_when_hud_capability_is_missing():
    executor = object.__new__(LocalAgentExecutor)
    executor._base_runtime = NoHudRuntime()
    executor.console = MagicMock()

    executor._print_interactive_stats_fallback(_build_stats(), elapsed_seconds=1.2)

    text_args = [
        call.args[0]
        for call in executor.console.print.call_args_list
        if call.args and isinstance(call.args[0], Text)
    ]
    assert text_args
    assert any("stats" in text_arg.plain.lower() for text_arg in text_args)


def test_interactive_stats_gate_is_conservative_when_capability_probe_raises():
    executor = object.__new__(LocalAgentExecutor)
    executor._base_runtime = BrokenCapabilityRuntime()
    executor.console = MagicMock()

    executor._print_interactive_stats_fallback(_build_stats(), elapsed_seconds=1.2)

    text_args = [
        call.args[0]
        for call in executor.console.print.call_args_list
        if call.args and isinstance(call.args[0], Text)
    ]
    assert text_args
    assert any("stats" in text_arg.plain.lower() for text_arg in text_args)


def test_stream_renderable_hides_stats_when_hud_capability_is_active():
    buffer = StreamDisplayBuffer(accumulated_content="hello", displayed_content_chars=0)
    console = Console(record=True, width=120)

    renderable = build_stream_renderable(
        buffer=buffer,
        stream_token_stats=_build_stats(),
        status_start_time=None,
        format_tool_calls_fn=lambda calls: [],
        format_elapsed_fn=lambda elapsed: f"{elapsed:.1f}s",
        config=StreamDisplayConfig(),
        should_emit_interactive_stats_fn=lambda: False,
    )

    console.print(renderable)
    output = console.export_text()

    assert "Aworld stats" not in output
    assert "🤖 Aworld" in output
    assert "h" in output


def test_stream_renderable_keeps_stats_when_hud_capability_is_missing():
    buffer = StreamDisplayBuffer(accumulated_content="hello", displayed_content_chars=0)
    console = Console(record=True, width=120)

    renderable = build_stream_renderable(
        buffer=buffer,
        stream_token_stats=_build_stats(),
        status_start_time=None,
        format_tool_calls_fn=lambda calls: [],
        format_elapsed_fn=lambda elapsed: f"{elapsed:.1f}s",
        config=StreamDisplayConfig(),
        should_emit_interactive_stats_fn=lambda: True,
    )

    console.print(renderable)
    output = console.export_text()

    assert "Aworld stats" in output
