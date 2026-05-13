import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld_cli.console import AWorldCLI
from aworld_cli.core.plugin_manager import get_builtin_plugin_roots
from aworld_cli.executors.stats import format_context_bar
from aworld.plugins.discovery import discover_plugins
from aworld_cli.plugin_capabilities.hud import collect_hud_lines


def _get_builtin_aworld_hud_root() -> Path:
    for root in get_builtin_plugin_roots():
        if root.name == "aworld_hud":
            return root
    raise AssertionError("built-in aworld_hud plugin root not found")


def _get_builtin_ralph_plugin_root() -> Path:
    for root in get_builtin_plugin_roots():
        if root.name == "ralph_session_loop":
            return root
    raise AssertionError("built-in ralph_session_loop plugin root not found")


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


def test_builtin_aworld_hud_manifest_declares_hook_entrypoints():
    plugin_root = _get_builtin_aworld_hud_root()
    plugin = discover_plugins([plugin_root])[0]

    hook_entrypoints = plugin.manifest.entrypoints.get("hooks", ())

    assert [entry.entrypoint_id for entry in hook_entrypoints] == [
        "task-started",
        "task-progress",
        "task-completed",
        "task-error",
        "task-interrupted",
    ]


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

    assert text.startswith("Agent: Aworld")
    assert "Agent: Aworld / Chat" not in text
    assert "Workspace: aworld" in text


def test_status_bar_text_shows_unread_cron_count_from_plugin_hud():
    plugin_root = _get_builtin_aworld_hud_root()
    plugin = discover_plugins([plugin_root])[0]

    class FakeNotificationCenter:
        def get_unread_count(self):
            return 1

    class FakeRuntime:
        def __init__(self):
            self._notification_center = FakeNotificationCenter()

        def build_hud_context(self, agent_name, mode, workspace_name, git_branch):
            return {
                "workspace": {"name": workspace_name},
                "session": {"agent": agent_name, "mode": mode},
                "notifications": {"cron_unread": self._notification_center.get_unread_count()},
                "vcs": {"branch": git_branch},
            }

        def get_hud_lines(self, context):
            return collect_hud_lines([plugin], context)

    cli = AWorldCLI()
    text = cli._build_status_bar_text(FakeRuntime(), agent_name="Aworld", mode="Chat")

    assert "Cron: 1 unread" in text


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
    assert lines[0].segments[0] == "Agent: Aworld"
    assert "Task: task_001 (running)" in lines[1].segments
    assert "Elapsed: 12.5s" in lines[1].segments


def test_builtin_aworld_hud_prefers_plugin_state_for_dynamic_segments():
    plugin_root = _get_builtin_aworld_hud_root()
    plugin = discover_plugins([plugin_root])[0]

    lines = collect_hud_lines(
        plugins=[plugin],
        context={
            "workspace": {"name": "aworld"},
            "session": {"agent": "Aworld", "mode": "Chat", "model": "stale-model", "elapsed_seconds": 1.0},
            "task": {"current_task_id": "stale-task", "status": "idle"},
            "usage": {"input_tokens": 1, "output_tokens": 2, "context_percent": 1},
            "notifications": {"cron_unread": 0},
            "vcs": {"branch": "feat/hud"},
        },
        plugin_state_provider=lambda plugin_id, scope, context: {
            "session": {"model": "gpt-5", "elapsed_seconds": 12.5},
            "task": {"current_task_id": "task_001", "status": "running"},
            "usage": {"input_tokens": 1200, "output_tokens": 300, "context_percent": 34},
        },
    )

    assert lines[0].segments[1] == "Model: gpt-5"
    assert "Task: task_001 (running)" in lines[1].segments
    assert "Tokens: in 1.2k out 300" in lines[1].segments
    assert "Ctx: 34%" in lines[1].segments
    assert "Elapsed: 12.5s" in lines[1].segments


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
    assert lines[0].startswith("Agent: Aworld")
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


def test_collect_hud_lines_skips_broken_provider_and_keeps_healthy_provider(tmp_path):
    good_root = tmp_path / "good"
    bad_root = tmp_path / "bad"

    for root, plugin_id, source in (
        (
            good_root,
            "good-hud",
            "def render_lines(context):\n    return [{'section': 'identity', 'segments': ['Agent: Healthy'], 'priority': 10}]\n",
        ),
        (
            bad_root,
            "bad-hud",
            "def render_lines(context):\n    raise RuntimeError('boom')\n",
        ),
    ):
        (root / ".aworld-plugin").mkdir(parents=True)
        (root / "hud").mkdir()
        (root / ".aworld-plugin" / "plugin.json").write_text(
            (
                "{"
                f"\"id\": \"{plugin_id}\", "
                f"\"name\": \"{plugin_id}\", "
                "\"version\": \"1.0.0\", "
                "\"entrypoints\": {"
                "\"hud\": ["
                "{"
                "\"id\": \"status\", "
                "\"target\": \"hud/status.py\""
                "}"
                "]"
                "}"
                "}"
            ),
            encoding="utf-8",
        )
        (root / "hud" / "status.py").write_text(source, encoding="utf-8")

    plugins = discover_plugins([bad_root, good_root])

    lines = collect_hud_lines(
        plugins=plugins,
        context={
            "workspace": {"name": "aworld"},
            "session": {"agent": "Aworld", "mode": "Chat"},
        },
    )

    assert [line.text for line in lines] == ["Agent: Healthy"]


def test_status_bar_is_not_rendered_without_hud_capability():
    class FakeRuntime:
        def active_plugin_capabilities(self):
            return ()

    cli = AWorldCLI()

    assert cli._should_render_status_bar(FakeRuntime()) is False
    assert cli._build_status_bar_text(FakeRuntime(), agent_name="Aworld", mode="Chat") == ""


def test_prompt_kwargs_clear_bottom_toolbar_when_hud_is_disabled():
    class FakeRuntime:
        def active_plugin_capabilities(self):
            return ()

    cli = AWorldCLI()
    prompt_kwargs = cli._build_prompt_kwargs(FakeRuntime(), agent_name="Aworld", mode="Chat")

    assert "bottom_toolbar" in prompt_kwargs
    assert prompt_kwargs["bottom_toolbar"] is None
    assert "refresh_interval" not in prompt_kwargs


def test_prompt_kwargs_avoid_toolbar_background_when_hud_is_enabled():
    class FakeRuntime:
        def active_plugin_capabilities(self):
            return ("hud",)

    cli = AWorldCLI()
    prompt_kwargs = cli._build_prompt_kwargs(FakeRuntime(), agent_name="Aworld", mode="Chat")

    assert "style" in prompt_kwargs
    style_rules = getattr(prompt_kwargs["style"], "style_rules", [])
    assert any(
        name == "bottom-toolbar"
        and "fg:#d8def5" in rule
        and "bg:default" in rule
        and "noreverse" in rule
        for name, rule in style_rules
    )


def test_status_bar_html_uses_text_color_without_background_markup(monkeypatch):
    class FakeRuntime:
        def active_plugin_capabilities(self):
            return ("hud",)

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
                    section="identity",
                    segments=("Agent: Aworld",),
                )
            ]

    monkeypatch.setattr(
        "aworld_cli.console.shutil.get_terminal_size",
        lambda fallback=(160, 24): os.terminal_size((60, 24)),
    )

    cli = AWorldCLI()
    html = cli._build_status_bar(FakeRuntime(), agent_name="Aworld", mode="Chat")
    rendered = getattr(html, "value", str(html))

    assert "bg='" not in rendered
    assert "fg='#84c7c6'" in rendered


def test_status_bar_html_renders_top_separator_line(monkeypatch):
    class FakeRuntime:
        def active_plugin_capabilities(self):
            return ("hud",)

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
                    section="identity",
                    segments=("Agent: Aworld",),
                )
            ]

    monkeypatch.setattr(
        "aworld_cli.console.shutil.get_terminal_size",
        lambda fallback=(160, 24): os.terminal_size((20, 24)),
    )

    cli = AWorldCLI()
    html = cli._build_status_bar(FakeRuntime(), agent_name="Aworld", mode="Chat")
    rendered = getattr(html, "value", str(html))

    assert rendered.startswith("<style fg='#4f5877'>────────────────────</style>\n")


def test_runtime_plugin_refresh_clears_active_prompt_session_when_hud_toggles():
    class FakeRuntime:
        def active_plugin_capabilities(self):
            return ()

    cli = AWorldCLI()
    active_session = object()
    cli._active_prompt_session = active_session

    cli._handle_runtime_plugin_capability_refresh(("hud",), FakeRuntime())

    assert cli._active_prompt_session is None


def test_ensure_prompt_session_recreates_when_active_prompt_session_was_cleared(monkeypatch):
    created = []

    class FakePromptSession:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            created.append(self)

    monkeypatch.setattr("aworld_cli.console.PromptSession", FakePromptSession)

    cli = AWorldCLI()
    stale_session = object()
    cli._active_prompt_session = None

    session = cli._ensure_prompt_session(
        stale_session,
        completer=object(),
    )

    assert session is created[0]
    assert cli._active_prompt_session is created[0]


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
                        "Agent: Aworld",
                        "Workspace: aworld",
                        "Branch: main",
                    )
                )
            ]

    cli = AWorldCLI()
    text = cli._build_status_bar_text(FakeRuntime(), agent_name="Aworld", mode="Chat", max_width=30)

    assert text == "Agent: Aworld"


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


def test_status_bar_text_keeps_idle_hud_stable_without_execution_stats():
    plugin_root = _get_builtin_aworld_hud_root()
    plugin = discover_plugins([plugin_root])[0]

    class FakeRuntime:
        def build_hud_context(self, agent_name, mode, workspace_name, git_branch):
            return {
                "workspace": {"name": workspace_name},
                "session": {
                    "agent": agent_name,
                    "mode": mode,
                    "model": "claude-sonnet-4-5",
                    "elapsed_seconds": 16.8,
                },
                "task": {"current_task_id": "task_20260415210612", "status": "idle"},
                "activity": {"current_tool": None, "recent_tools": ["bash"], "tool_calls_count": 4},
                "usage": {
                    "input_tokens": 6500,
                    "output_tokens": 122,
                    "context_used": 60000,
                    "context_max": 200000,
                    "context_percent": 30,
                },
                "notifications": {"cron_unread": 0},
                "vcs": {"branch": git_branch},
                "plugins": {"active_count": 2},
            }

        def get_hud_lines(self, context):
            return collect_hud_lines([plugin], context)

    cli = AWorldCLI()
    text = cli._build_status_bar_text(FakeRuntime(), agent_name="Aworld", mode="Chat", max_width=160)

    assert "Agent: Aworld" in text
    assert "Agent: Aworld / Chat" not in text
    assert "Model: claude-sonnet-4-5" in text
    assert "Workspace: aworld" in text
    assert "Branch:" in text
    assert "Cron: clear" in text
    assert "Status: idle" in text
    assert "Task: task_20260415210612 (idle)" not in text
    assert "Tokens: in 6.5k out 122" in text
    assert "Ctx:" in text
    assert "Elapsed: 16.8s" in text
    assert "Tool:" not in text
    assert "Plugins:" not in text


def test_context_bar_uses_visual_progress_format():
    assert "Ctx ███" in format_context_bar(60000, 200000, bar_width=10)


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


def test_collect_hud_lines_passes_plugin_state_to_provider(tmp_path):
    hud_module = tmp_path / "hud.py"
    hud_module.write_text(
        "def render_lines(context, plugin_state):\n"
        "    return [{\"section\": \"custom\", \"segments\": [plugin_state.get(\"status\", \"missing\")]}]\n",
        encoding="utf-8",
    )
    entrypoint = SimpleNamespace(entrypoint_id="status", target="hud.py", scope="workspace")
    manifest = SimpleNamespace(
        plugin_root=tmp_path,
        plugin_id="test-plugin",
        entrypoints={"hud": [entrypoint]},
    )
    plugin = SimpleNamespace(manifest=manifest)

    lines = collect_hud_lines(
        [plugin],
        context={"workspace": {"path": str(tmp_path)}},
        plugin_state_provider=lambda plugin_id, scope, context: {"status": f"{plugin_id}:{scope}"},
    )

    assert lines[0].segments == ("test-plugin:workspace",)


def test_plugin_facing_hud_helpers_match_host_conventions():
    from aworld_cli.plugin_capabilities.hud_helpers import (
        format_hud_context_bar,
        format_hud_elapsed,
        format_hud_tokens,
    )

    assert format_hud_tokens(6500) == "6.5k"
    assert format_hud_elapsed(12.5) == "12.5s"
    assert "Ctx" in format_hud_context_bar(60000, 200000, bar_width=10)


def test_builtin_aworld_hud_plugin_uses_explicit_helper_boundary():
    plugin_root = _get_builtin_aworld_hud_root()
    status_module = plugin_root / "hud" / "status.py"

    source = status_module.read_text(encoding="utf-8")

    assert "from aworld_cli.plugin_capabilities.hud_helpers import" in source
    assert "aworld_cli.executors.stats" not in source


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


def test_ralph_hud_renders_active_loop_state():
    plugin_root = _get_builtin_ralph_plugin_root()
    plugin = discover_plugins([plugin_root])[0]

    lines = collect_hud_lines(
        plugins=[plugin],
        context={
            "workspace": {"name": "aworld"},
            "session": {"agent": "Aworld", "mode": "Chat", "session_id": "session-1"},
        },
        plugin_state_provider=lambda plugin_id, scope, context: {
            "active": True,
            "iteration": 2,
            "max_iterations": 5,
            "completion_promise": "COMPLETE",
        },
    )

    assert [line.section for line in lines] == ["session"]
    assert lines[0].segments == (
        "Ralph: active",
        "Iter: 2/5",
        "Promise: COMPLETE",
    )


def test_status_bar_text_prioritizes_activity_line_over_secondary_session_plugin():
    aworld_plugin = discover_plugins([_get_builtin_aworld_hud_root()])[0]
    ralph_plugin = discover_plugins([_get_builtin_ralph_plugin_root()])[0]

    class FakeRuntime:
        def build_hud_context(self, agent_name, mode, workspace_name, git_branch):
            return {
                "workspace": {"name": workspace_name},
                "session": {"agent": agent_name, "mode": mode, "model": "claude-sonnet-4-5"},
                "task": {"current_task_id": "task_20260512112856", "status": "idle"},
                "activity": {"current_tool": None, "recent_tools": ["bash"], "tool_calls_count": 1},
                "usage": {
                    "input_tokens": 6500,
                    "output_tokens": 122,
                    "context_used": 60000,
                    "context_max": 200000,
                    "context_percent": 30,
                },
                "notifications": {"cron_unread": 0},
                "vcs": {"branch": git_branch},
            }

        def get_hud_lines(self, context):
            return collect_hud_lines(
                [aworld_plugin, ralph_plugin],
                context,
                plugin_state_provider=lambda plugin_id, scope, context: {"active": False}
                if plugin_id == "ralph_session_loop"
                else {},
            )

    cli = AWorldCLI()
    text = cli._build_status_bar_text(FakeRuntime(), agent_name="Aworld", mode="Chat", max_width=160)

    lines = text.splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("Agent: Aworld")
    assert "Tokens: in 6.5k out 122" in lines[1]
    assert "Ctx:" in lines[1]
    assert "Ralph: inactive" not in lines[1]
