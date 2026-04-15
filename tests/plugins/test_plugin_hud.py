import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld_cli.console import AWorldCLI
from aworld_cli.core.plugin_manager import get_builtin_plugin_roots
from aworld_cli.plugin_framework.discovery import discover_plugins
from aworld_cli.plugin_framework.hud import collect_hud_lines


def _get_builtin_aworld_hud_root() -> Path:
    for root in get_builtin_plugin_roots():
        if root.name == "aworld_hud":
            return root
    raise AssertionError("built-in aworld_hud plugin root not found")


def test_collect_hud_lines_orders_by_section_and_priority():
    plugin_root = _get_builtin_aworld_hud_root()
    plugin = discover_plugins([plugin_root])[0]

    lines = collect_hud_lines(
        plugins=[plugin],
        context={
            "workspace": {"name": "aworld"},
            "session": {"agent": "developer", "mode": "Chat"},
            "notifications": {"cron_unread": 0},
        },
    )

    assert [line.section for line in lines] == ["identity", "activity"]
    assert lines[0].segments[0].startswith("Agent:")


def test_status_bar_text_merges_plugin_hud_lines():
    plugin_root = _get_builtin_aworld_hud_root()
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

    assert text.startswith("Agent: Aworld / Chat")
    assert "Workspace: aworld" in text


def test_collect_hud_lines_preserves_grouped_segments():
    plugin_root = _get_builtin_aworld_hud_root()
    plugin = discover_plugins([plugin_root])[0]

    lines = collect_hud_lines(
        plugins=[plugin],
        context={
            "workspace": {"name": "aworld"},
            "session": {"agent": "Aworld", "mode": "Chat", "model": "gpt-5", "elapsed_seconds": 12.5},
            "task": {"current_task_id": "task_001", "status": "running"},
            "activity": {"current_tool": "bash", "recent_tools": ["bash"], "tool_calls_count": 2},
            "usage": {"input_tokens": 1200, "output_tokens": 300, "context_percent": 34},
            "notifications": {"cron_unread": 0},
            "vcs": {"branch": "feat/hud"},
            "plugins": {"active_count": 1},
        },
    )

    assert [line.section for line in lines] == ["identity", "activity"]
    assert lines[0].segments[0].startswith("Agent: Aworld / Chat")
    assert any(segment.startswith("Task: task_001") for segment in lines[1].segments)


def test_status_bar_text_renders_two_lines_from_grouped_hud_segments():
    plugin_root = _get_builtin_aworld_hud_root()
    plugin = discover_plugins([plugin_root])[0]

    class FakeRuntime:
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
            return collect_hud_lines([plugin], context)

    cli = AWorldCLI()
    text = cli._build_status_bar_text(FakeRuntime(), agent_name="Aworld", mode="Chat", max_width=120)

    lines = text.splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("Agent: Aworld / Chat")
    assert "Task: task_001" in lines[1]


def test_status_bar_text_falls_back_when_plugin_rendering_raises():
    class BrokenRuntime:
        def build_hud_context(self, agent_name, mode, workspace_name, git_branch):
            return {
                "workspace": {"name": workspace_name},
                "session": {"agent": agent_name, "mode": mode},
                "notifications": {"cron_unread": 0},
                "vcs": {"branch": git_branch},
            }

        def get_hud_lines(self, context):
            raise RuntimeError("boom")

    cli = AWorldCLI()
    text = cli._build_status_bar_text(BrokenRuntime(), agent_name="Aworld", mode="Chat", max_width=120)

    assert "Agent: Aworld" in text
    assert "Mode: Chat" in text
    assert "boom" not in text


def test_status_bar_text_falls_back_when_hud_entrypoint_raises(tmp_path):
    hud_module = tmp_path / "hud.py"
    hud_module.write_text(
        "def render_lines(context):\n"
        "    raise RuntimeError('boom')\n",
        encoding="utf-8",
    )
    entrypoint = SimpleNamespace(entrypoint_id="status", target="hud.py")
    manifest = SimpleNamespace(
        plugin_root=tmp_path,
        plugin_id="test-plugin",
        entrypoints={"hud": [entrypoint]},
    )
    plugin = SimpleNamespace(manifest=manifest)

    class BrokenRuntime:
        def build_hud_context(self, agent_name, mode, workspace_name, git_branch):
            return {
                "workspace": {"name": workspace_name},
                "session": {"agent": agent_name, "mode": mode},
                "notifications": {"cron_unread": 0},
                "vcs": {"branch": git_branch},
            }

        def get_hud_lines(self, context):
            return collect_hud_lines([plugin], context)

    cli = AWorldCLI()
    text = cli._build_status_bar_text(BrokenRuntime(), agent_name="Aworld", mode="Chat", max_width=120)

    assert "Agent: Aworld" in text
    assert "Mode: Chat" in text
    assert "boom" not in text


def test_status_bar_text_reduces_grouped_segments_to_fit_width():
    class FakeRuntime:
        def build_hud_context(self, agent_name, mode, workspace_name, git_branch):
            return {
                "workspace": {"name": workspace_name},
                "session": {"agent": agent_name, "mode": mode},
                "notifications": {"cron_unread": 0},
                "vcs": {"branch": git_branch},
            }

        def get_hud_lines(self, context):
            return [
                SimpleNamespace(
                    segments=(
                        "Agent: Aworld / Chat",
                        "Workspace: aworld",
                        "Branch: main",
                    )
                )
            ]

    cli = AWorldCLI()
    text = cli._build_status_bar_text(FakeRuntime(), agent_name="Aworld", mode="Chat", max_width=30)

    assert text == "Agent: Aworld / Chat"


def test_status_bar_text_truncates_long_segment_after_reduction():
    class FakeRuntime:
        def build_hud_context(self, agent_name, mode, workspace_name, git_branch):
            return {
                "workspace": {"name": workspace_name},
                "session": {"agent": agent_name, "mode": mode},
                "notifications": {"cron_unread": 0},
                "vcs": {"branch": git_branch},
            }

        def get_hud_lines(self, context):
            return [
                SimpleNamespace(
                    segments=("Agent: " + ("x" * 120),)
                )
            ]

    cli = AWorldCLI()
    text = cli._build_status_bar_text(FakeRuntime(), agent_name="Aworld", mode="Chat", max_width=40)

    assert len(text) <= 40
    assert text.endswith("...")


def test_status_bar_text_prefers_task_and_context_over_tools_and_tokens():
    plugin_root = _get_builtin_aworld_hud_root()
    plugin = discover_plugins([plugin_root])[0]

    class FakeRuntime:
        def build_hud_context(self, agent_name, mode, workspace_name, git_branch):
            return {
                "workspace": {"name": workspace_name},
                "session": {"agent": agent_name, "mode": mode},
                "task": {"current_task_id": "task_001", "status": "running"},
                "activity": {"current_tool": "bash", "tool_calls_count": 2},
                "usage": {"input_tokens": 1200, "output_tokens": 300, "context_percent": 34},
                "notifications": {"cron_unread": 0},
                "vcs": {"branch": git_branch},
                "plugins": {"active_count": 1},
            }

        def get_hud_lines(self, context):
            return collect_hud_lines([plugin], context)

    cli = AWorldCLI()
    text = cli._build_status_bar_text(FakeRuntime(), agent_name="Aworld", mode="Chat", max_width=36)

    lines = text.splitlines()
    assert len(lines) == 2
    assert "Task: task_001" in lines[1]
    assert "Ctx: 34%" in lines[1]
    assert "Tool:" not in lines[1]
    assert "Tokens:" not in lines[1]


def test_collect_hud_lines_treats_string_segments_as_single_segment(tmp_path):
    hud_module = tmp_path / "hud.py"
    hud_module.write_text(
        "def render_lines(context):\n"
        "    return [{\"section\": \"custom\", \"segments\": \"Only segment\"}]\n",
        encoding="utf-8",
    )
    entrypoint = SimpleNamespace(entrypoint_id="status", target="hud.py")
    manifest = SimpleNamespace(
        plugin_root=tmp_path,
        plugin_id="test-plugin",
        entrypoints={"hud": [entrypoint]},
    )
    plugin = SimpleNamespace(manifest=manifest)

    lines = collect_hud_lines([plugin], context={})

    assert lines[0].segments == ("Only segment",)


def test_status_bar_renders_multiline_html(monkeypatch):
    plugin_root = _get_builtin_aworld_hud_root()
    plugin = discover_plugins([plugin_root])[0]

    class FakeRuntime:
        def build_hud_context(self, agent_name, mode, workspace_name, git_branch):
            return {
                "workspace": {"name": workspace_name},
                "session": {"agent": agent_name, "mode": mode},
                "task": {"current_task_id": "task_001", "status": "running"},
                "activity": {"current_tool": "bash", "tool_calls_count": 2},
                "usage": {"input_tokens": 1200, "output_tokens": 300, "context_percent": 34},
                "notifications": {"cron_unread": 0},
                "vcs": {"branch": git_branch},
                "plugins": {"active_count": 1},
            }

        def get_hud_lines(self, context):
            return collect_hud_lines([plugin], context)

    monkeypatch.setattr(
        "aworld_cli.console.shutil.get_terminal_size",
        lambda fallback=(160, 24): os.terminal_size((120, 24)),
    )

    cli = AWorldCLI()
    html = cli._build_status_bar(FakeRuntime(), agent_name="Aworld", mode="Chat")
    rendered = getattr(html, "value", str(html))

    assert "\n" in rendered
    assert "Task: task_001" in rendered
