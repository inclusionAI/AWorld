import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld_cli.console import AWorldCLI
from aworld_cli.plugin_framework.discovery import discover_plugins
from aworld_cli.plugin_framework.hud import collect_hud_lines


def test_collect_hud_lines_orders_by_section_and_priority():
    plugin_root = Path("tests/fixtures/plugins/hud_like").resolve()
    plugin = discover_plugins([plugin_root])[0]

    lines = collect_hud_lines(
        plugins=[plugin],
        context={
            "workspace": {"name": "aworld"},
            "session": {"agent": "developer", "mode": "Chat"},
            "notifications": {"cron_unread": 0},
        },
    )

    assert [line.section for line in lines] == ["session", "custom"]
    assert lines[0].text.startswith("Agent:")


def test_status_bar_text_merges_plugin_hud_lines():
    plugin_root = Path("tests/fixtures/plugins/hud_like").resolve()
    plugin = discover_plugins([plugin_root])[0]

    class FakeNotificationCenter:
        def get_unread_count(self):
            return 0

    class FakeRuntime:
        def __init__(self):
            self._notification_center = FakeNotificationCenter()

        def get_hud_lines(self, context):
            return collect_hud_lines([plugin], context)

    cli = AWorldCLI()
    text = cli._build_status_bar_text(FakeRuntime(), agent_name="Aworld", mode="Chat")

    assert "Agent: Aworld" in text
    assert "Plugin: HUD ready" in text


def test_status_bar_text_truncates_to_max_width():
    class FakeRuntime:
        def build_hud_context(self, agent_name, mode, workspace_name, git_branch):
            return {
                "workspace": {"name": workspace_name},
                "session": {"agent": agent_name, "mode": mode},
                "notifications": {"cron_unread": 0},
                "vcs": {"branch": git_branch},
            }

        def get_hud_lines(self, context):
            return [SimpleNamespace(text="Plugin: " + ("x" * 120))]

    cli = AWorldCLI()
    text = cli._build_status_bar_text(FakeRuntime(), agent_name="Aworld", mode="Chat", max_width=90)

    assert len(text) <= 90
    assert text.endswith("...")
