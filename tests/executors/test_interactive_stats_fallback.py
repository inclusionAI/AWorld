import sys
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from rich.console import Console
from rich.text import Text

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld_cli.executors.local import LocalAgentExecutor
from aworld_cli.executors.stats import StreamTokenStats
from aworld_cli.executors.stream import (
    FixedBottomHudRenderer,
    StreamDisplayBuffer,
    StreamDisplayConfig,
    StreamDisplayController,
    build_loading_status_renderable,
    build_stream_renderable,
    print_buffer_to_console,
)
from aworld_cli.console import AWorldCLI
from aworld_cli.status_text import build_status_bar_rich_lines
from aworld.output.base import MessageOutput


class HudRuntime:
    def active_plugin_capabilities(self):
        return ("hud", "tools")


class NoHudRuntime:
    def active_plugin_capabilities(self):
        return ("tools",)


class BrokenCapabilityRuntime:
    def active_plugin_capabilities(self):
        raise RuntimeError("capability probe failed")


class HudRenderRuntime:
    def active_plugin_capabilities(self):
        return ("hud",)

    def build_hud_context(self, agent_name, mode, workspace_name, git_branch):
        return {
            "workspace": {"name": workspace_name},
            "session": {"agent": agent_name, "mode": mode, "model": "gpt-5", "elapsed_seconds": 12.5},
            "task": {"current_task_id": "task_001", "status": "running"},
            "activity": {"current_tool": "bash", "recent_tools": ["bash"], "tool_calls_count": 2},
            "usage": {"input_tokens": 1200, "output_tokens": 300, "context_percent": 34},
            "notifications": {"cron_unread": 0},
            "vcs": {"branch": git_branch},
            "plugins": {"active_count": 1},
        }

    def get_hud_lines(self, context):
        return [
            type(
                "HudLine",
                (),
                {
                    "section": "identity",
                    "segments": (
                        "Agent: Aworld / Chat",
                        "Model: gpt-5",
                        "Workspace: aworld",
                        "Branch: feat/hud",
                        "Cron: clear",
                    ),
                },
            )(),
            type(
                "HudLine",
                (),
                {
                    "section": "activity",
                    "segments": (
                        "Task: task_001 (running)",
                        "Tokens: in 1.2k out 300",
                        "Ctx: 34%",
                        "Elapsed: 12.5s",
                    ),
                },
            )(),
        ]


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


def test_stream_renderable_appends_hud_lines_when_hud_render_fn_is_available():
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
        hud_lines_fn=lambda: [
            "Agent: Aworld / Chat | Workspace: aworld | Branch: feat/hud | Cron: clear",
            "Task: task_001 (running) | Tokens: in 1.2k out 300 | Ctx: 34% | Elapsed: 12.5s",
        ],
    )

    console.print(renderable)
    output = console.export_text()

    assert "Aworld stats" not in output
    assert "Task: task_001 (running)" in output
    assert "Ctx: 34%" in output


def test_stream_renderable_keeps_activity_hud_line_when_width_is_constrained():
    buffer = StreamDisplayBuffer(accumulated_content="hello", displayed_content_chars=0)
    console = Console(record=True, width=80)

    renderable = build_stream_renderable(
        buffer=buffer,
        stream_token_stats=_build_stats(),
        status_start_time=None,
        format_tool_calls_fn=lambda calls: [],
        format_elapsed_fn=lambda elapsed: f"{elapsed:.1f}s",
        config=StreamDisplayConfig(),
        should_emit_interactive_stats_fn=lambda: False,
        hud_lines_fn=lambda: [
            "Agent: Aworld / Chat | Workspace: aworld | Branch: feat/hud | Cron: clear",
            "Task: task_001 (running) | Tokens: in 1.2k out 300 | Ctx: 34% | Elapsed: 12.5s",
        ],
    )

    console.print(renderable)
    output = console.export_text()

    assert "Task: task_001 (running)" in output
    assert "Ctx: 34%" in output


def test_cli_can_build_execution_hud_lines_with_activity_priority():
    cli = AWorldCLI()
    cli._toolbar_workspace_name = "aworld"
    cli._toolbar_git_branch = "feat/hud"

    lines = cli._build_status_bar_text(
        HudRenderRuntime(),
        agent_name="Aworld",
        mode="Chat",
        max_width=80,
    ).splitlines()

    assert len(lines) == 2
    assert "Task: task_001 (running)" in lines[1]


def test_loading_status_renderable_uses_hud_lines_before_streaming_starts():
    renderable = build_loading_status_renderable(
        base_message="💭 Thinking...",
        elapsed_str="0.0s",
        streaming_mode=False,
        should_emit_interactive_stats=False,
        stream_token_stats=_build_stats(),
        hud_lines_fn=lambda: [
            "Agent: Aworld / Chat | Workspace: aworld | Branch: feat/hud | Cron: clear",
            "Task: task_001 (running) | Tokens: in 1.2k out 300 | Ctx: 34% | Elapsed: 12.5s",
        ],
        use_fixed_hud_layout=True,
    )

    console = Console(record=True, width=120)
    console.print(renderable)
    output = console.export_text()

    assert "Task: task_001 (running)" in output
    assert "Thinking" not in output


def test_loading_status_renderable_keeps_hud_on_last_lines_in_fixed_layout():
    renderable = build_loading_status_renderable(
        base_message="💭 Thinking...",
        elapsed_str="0.0s",
        streaming_mode=False,
        should_emit_interactive_stats=False,
        stream_token_stats=_build_stats(),
        hud_lines_fn=lambda: [
            "Agent: Aworld / Chat | Workspace: aworld | Branch: feat/hud | Cron: clear",
            "Task: task_001 (running) | Tokens: in 1.2k out 300 | Ctx: 34% | Elapsed: 12.5s",
        ],
        use_fixed_hud_layout=True,
    )

    console = Console(record=True, width=120, height=8)
    console.print(renderable)
    lines = [line.rstrip() for line in console.export_text().splitlines() if line.strip()]

    assert "Layout(name='body')" not in console.export_text()
    assert "Agent: Aworld / Chat" in lines[-2]
    assert "Task: task_001 (running)" in lines[-1]


def test_execution_hud_lines_are_rendered_with_segment_styles():
    renderables = build_status_bar_rich_lines(
        [
            "Agent: Aworld / Chat | Workspace: aworld | Branch: feat/hud | Cron: clear",
            "Task: task_001 (running) | Tokens: in 1.2k out 300 | Ctx: 34% | Elapsed: 12.5s",
        ]
    )

    assert len(renderables) == 2
    assert all(renderable.spans for renderable in renderables)


class _TtyBuffer(StringIO):
    def isatty(self):
        return True


def test_fixed_bottom_hud_renderer_uses_segment_styles():
    output = _TtyBuffer()
    console = Console(file=output, force_terminal=True, width=120, color_system="truecolor")
    renderer = FixedBottomHudRenderer(
        console=console,
        hud_lines_fn=lambda: [
            "Agent: Aworld / Chat | Workspace: aworld | Branch: feat/hud | Cron: clear",
            "Task: task_001 (running) | Tokens: in 1.2k out 300 | Ctx: 34% | Elapsed: 12.5s",
        ],
    )

    renderer.start()

    rendered = output.getvalue()

    assert "Agent: Aworld / Chat" in rendered
    assert "Task: task_001 (running)" in rendered
    assert "\x1b[38;2;" in rendered
    assert ";48;2;" in rendered
    assert "\x1b[1;" in rendered
    assert "r" in rendered


def test_render_simple_message_output_skips_response_when_content_already_streamed():
    executor = object.__new__(LocalAgentExecutor)
    executor.console = Console(record=True, width=120)

    answer, rendered = executor._render_simple_message_output(
        MessageOutput(response="hello from stream"),
        answer="",
        agent_name="Aworld",
        content_already_streamed=True,
    )

    output = executor.console.export_text()

    assert answer == "hello from stream"
    assert rendered == "hello from stream"
    assert "hello from stream" not in output


def test_final_task_answer_should_render_when_last_message_had_no_visible_response():
    executor = object.__new__(LocalAgentExecutor)
    executor.console = MagicMock()

    should_render = executor._should_render_final_task_answer(
        final_answer="一分钟后提醒你站立",
        last_message_output=MessageOutput(response=""),
        response_rendered_to_console=False,
    )

    assert should_render is True


def test_final_task_answer_should_not_render_when_response_already_rendered():
    executor = object.__new__(LocalAgentExecutor)
    executor.console = MagicMock()

    should_render = executor._should_render_final_task_answer(
        final_answer="一分钟后提醒你站立",
        last_message_output=MessageOutput(response=""),
        response_rendered_to_console=True,
    )

    assert should_render is False


def test_final_task_answer_should_not_render_when_message_already_had_response():
    executor = object.__new__(LocalAgentExecutor)
    executor.console = MagicMock()

    should_render = executor._should_render_final_task_answer(
        final_answer="一分钟后提醒你站立",
        last_message_output=MessageOutput(response="我会在一分钟后提醒你站立。"),
        response_rendered_to_console=False,
    )

    assert should_render is False


def test_visible_response_content_helper_ignores_empty_and_whitespace_only_chunks():
    executor = object.__new__(LocalAgentExecutor)

    assert executor._has_visible_response_content("") is False
    assert executor._has_visible_response_content("   \n\t") is False
    assert executor._has_visible_response_content(None) is False


def test_visible_response_content_helper_detects_actual_text():
    executor = object.__new__(LocalAgentExecutor)

    assert executor._has_visible_response_content("已为你创建提醒。") is True
    assert executor._has_visible_response_content("\n已为你创建提醒。") is True


def test_stream_renderable_keeps_hud_fixed_to_bottom_lines_when_content_is_long():
    buffer = StreamDisplayBuffer(
        accumulated_content="\n".join(f"line{i}" for i in range(12)),
        displayed_content_chars=0,
    )
    console = Console(record=True, width=120, height=8)

    renderable = build_stream_renderable(
        buffer=buffer,
        stream_token_stats=_build_stats(),
        status_start_time=None,
        format_tool_calls_fn=lambda calls: [],
        format_elapsed_fn=lambda elapsed: f"{elapsed:.1f}s",
        config=StreamDisplayConfig(chars_per_render=200),
        should_emit_interactive_stats_fn=lambda: False,
        hud_lines_fn=lambda: [
            "Agent: Aworld / Chat | Workspace: aworld | Branch: feat/hud | Cron: clear",
            "Task: task_001 (running) | Tokens: in 1.2k out 300 | Ctx: 34% | Elapsed: 12.5s",
        ],
        use_fixed_hud_layout=True,
    )

    console.print(renderable)
    lines = [line.rstrip() for line in console.export_text().splitlines() if line.strip()]

    assert "Layout(name='body')" not in console.export_text()
    assert "Agent: Aworld / Chat" in lines[-2]
    assert "Task: task_001 (running)" in lines[-1]


def test_print_buffer_to_console_omits_hud_lines_in_fixed_hud_mode():
    buffer = StreamDisplayBuffer(accumulated_content="hello world", displayed_content_chars=0)
    console = Console(record=True, width=120)

    print_buffer_to_console(
        console=console,
        buffer=buffer,
        stream_token_stats=_build_stats(),
        format_tool_calls_fn=lambda calls: [],
        should_emit_interactive_stats_fn=lambda: False,
        hud_lines_fn=lambda: [
            "Agent: Aworld / Chat | Workspace: aworld | Branch: feat/hud | Cron: clear",
            "Task: task_001 (running) | Tokens: in 1.2k out 300 | Ctx: 34% | Elapsed: 12.5s",
        ],
        use_fixed_hud_layout=True,
    )

    output = console.export_text()

    assert "🤖 Aworld" in output
    assert "hello world" in output
    assert "Task: task_001 (running)" not in output
    assert "Tokens: in 1.2k out 300" not in output


@pytest.mark.asyncio
async def test_fixed_hud_mode_avoids_live_stream_rendering():
    controller = StreamDisplayController(
        console=Console(record=True, width=120),
        stream_token_stats=_build_stats(),
        format_tool_calls_fn=lambda calls: [],
        config=StreamDisplayConfig(),
        should_emit_interactive_stats_fn=lambda: False,
        hud_lines_fn=lambda: [
            "Agent: Aworld / Chat | Workspace: aworld | Branch: feat/hud | Cron: clear",
            "Task: task_001 (running) | Tokens: in 1.2k out 300 | Ctx: 34% | Elapsed: 12.5s",
        ],
        use_fixed_hud_layout=True,
    )

    controller.start_loading("💭 Thinking...")

    assert controller.stream_live is None
    assert controller.loading_status is None
    assert controller.status_start_time is not None

    controller.stop_loading()
    assert controller.status_start_time is not None

    controller.close()


def test_fixed_hud_mode_streams_chunk_content_without_repeating_header():
    controller = StreamDisplayController(
        console=Console(record=True, width=120),
        stream_token_stats=_build_stats(),
        format_tool_calls_fn=lambda calls: [],
        config=StreamDisplayConfig(),
        should_emit_interactive_stats_fn=lambda: False,
        use_fixed_hud_layout=True,
    )

    controller.stream_fixed_chunk_content("Aworld", "hello")
    controller.stream_fixed_chunk_content("Aworld", " world\nnext")
    controller.finish_fixed_stream_content()

    output = controller.console.export_text()

    assert output.count("🤖 Aworld") == 1
    assert "hello world" in output
    assert "next" in output


def test_fixed_hud_mode_does_not_suspend_hud_for_each_stream_chunk():
    controller = StreamDisplayController(
        console=Console(record=True, width=120),
        stream_token_stats=_build_stats(),
        format_tool_calls_fn=lambda calls: [],
        config=StreamDisplayConfig(),
        should_emit_interactive_stats_fn=lambda: False,
        use_fixed_hud_layout=True,
    )

    class DummyHud:
        def __init__(self):
            self.suspend_calls = 0
            self.resume_calls = 0

        def is_active(self):
            return True

        def suspend(self):
            self.suspend_calls += 1

        def resume(self):
            self.resume_calls += 1

    dummy_hud = DummyHud()
    controller.fixed_hud_renderer = dummy_hud

    controller.stream_fixed_chunk_content("Aworld", "hello")
    controller.stream_fixed_chunk_content("Aworld", " world")

    assert dummy_hud.suspend_calls == 0
    assert dummy_hud.resume_calls == 0
