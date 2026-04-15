import sys
from pathlib import Path
from unittest.mock import MagicMock

from rich.text import Text

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld_cli.executors.local import LocalAgentExecutor
from aworld_cli.executors.stats import StreamTokenStats


class HudRuntime:
    def active_plugin_capabilities(self):
        return ("hud", "tools")


class NoHudRuntime:
    def active_plugin_capabilities(self):
        return ("tools",)


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

    assert executor.console.print.call_count == 2
    assert isinstance(executor.console.print.call_args_list[0].args[0], Text)
