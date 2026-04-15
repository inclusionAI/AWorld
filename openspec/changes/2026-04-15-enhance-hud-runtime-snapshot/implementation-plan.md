# Interactive HUD Primary Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `aworld-hud` the only primary runtime-status surface in interactive chat mode, and fall back to the existing `Aworld stats ...` stream output when the HUD capability is unavailable or broken.

**Architecture:** Reuse the existing runtime HUD snapshot as the single source of truth for interactive chat status. `aworld-hud` renders the stable two-line view from that snapshot, `console.py` decides whether the bottom toolbar should exist, and `LocalAgentExecutor` decides whether textual `Aworld stats ...` should be suppressed or emitted as the degraded interactive path.

**Tech Stack:** Python, `prompt_toolkit`, `rich`, existing `aworld_cli` runtime/executor modules, `pytest`

---

## File Structure

- Modify: `aworld-cli/src/aworld_cli/plugins/aworld_hud/hud/status.py`
  Responsibility: refine the built-in HUD field set so line 2 shows stable summary fields and does not expose internal plugin-count noise.
- Modify: `aworld-cli/src/aworld_cli/console.py`
  Responsibility: treat `hud` capability as the gate for bottom-toolbar rendering, and keep fallback rendering available only when HUD is active but provider rendering fails.
- Modify: `aworld-cli/src/aworld_cli/executors/local.py`
  Responsibility: suppress interactive `Aworld stats ...` output while HUD is active, but preserve the existing stats output path when HUD is disabled or unavailable.
- Modify: `aworld-cli/src/aworld_cli/executors/stats.py`
  Responsibility: continue to provide the existing textual stats formatter for degraded-mode output and reuse shared formatting helpers needed by HUD rendering.
- Modify: `tests/plugins/test_plugin_hud.py`
  Responsibility: verify the refined HUD layout, context bar rendering, idle-summary retention, and plugin-count removal.
- Modify: `tests/plugins/test_runtime_hud_snapshot.py`
  Responsibility: verify idle summaries keep stable values that HUD needs after task completion.
- Modify: `tests/plugins/test_plugin_end_to_end.py`
  Responsibility: verify runtime capability presence when `aworld-hud` is enabled or disabled.
- Create or Modify: `tests/executors/test_interactive_stats_fallback.py`
  Responsibility: verify interactive stats output is suppressed when HUD is active and restored when HUD is unavailable.

## Current-State Notes

- The runtime snapshot store already exists in `aworld-cli/src/aworld_cli/runtime/hud_snapshot.py`.
- `BaseCliRuntime.build_hud_context()` already merges runtime snapshot buckets into HUD context.
- `aworld-hud` already renders two lines, but its second line still exposes `Plugins: <count>` and still formats context too weakly.
- `AWorldCLI` already gates bottom-toolbar existence on `hud` capability, so disabling `aworld-hud` removes the toolbar in new sessions.
- `LocalAgentExecutor` still prints `Aworld stats ...`-style lines from the message-stream path even when HUD is available. That is the remaining duplication to remove.

### Task 1: Refine `aworld-hud` To Match The Approved Interactive Layout

**Files:**
- Modify: `aworld-cli/src/aworld_cli/plugins/aworld_hud/hud/status.py`
- Modify: `aworld-cli/src/aworld_cli/executors/stats.py`
- Test: `tests/plugins/test_plugin_hud.py`
- Test: `tests/plugins/test_runtime_hud_snapshot.py`

- [ ] **Step 1: Write the failing HUD layout tests**

```python
# tests/plugins/test_plugin_hud.py
def test_status_bar_text_keeps_idle_summary_and_hides_tool_details():
    plugin_root = _get_builtin_aworld_hud_root()
    plugin = discover_plugins([plugin_root])[0]

    class FakeRuntime:
        def build_hud_context(self, agent_name, mode, workspace_name, git_branch):
            return {
                "workspace": {"name": workspace_name},
                "session": {"agent": agent_name, "mode": mode, "model": "claude-sonnet-4-5", "elapsed_seconds": 16.8},
                "task": {"current_task_id": "task_20260415210612", "status": "idle"},
                "activity": {"current_tool": None, "recent_tools": ["bash"], "tool_calls_count": 4},
                "usage": {"input_tokens": 6500, "output_tokens": 122, "context_used": 60000, "context_max": 200000, "context_percent": 30},
                "notifications": {"cron_unread": 0},
                "vcs": {"branch": git_branch},
                "plugins": {"active_count": 2},
            }

        def get_hud_lines(self, context):
            return collect_hud_lines([plugin], context)

    cli = AWorldCLI()
    text = cli._build_status_bar_text(FakeRuntime(), agent_name="Aworld", mode="Chat", max_width=160)

    assert "Task: task_20260415210612" in text
    assert "Tokens: in 6.5k out 122" in text
    assert "Ctx: ███" in text
    assert "16.8s" in text
    assert "Tool:" not in text
    assert "Plugins:" not in text


def test_context_bar_uses_visual_progress_format():
    assert "Ctx ███" in format_context_bar(60000, 200000, bar_width=10)
```

- [ ] **Step 2: Run the HUD tests to verify they fail**

Run: `pytest tests/plugins/test_plugin_hud.py::test_status_bar_text_keeps_idle_summary_and_hides_tool_details -v`
Expected: FAIL because the current HUD still shows `Plugins:` and plain `Ctx: 30%` instead of the approved bar format.

- [ ] **Step 3: Implement the minimal HUD rendering changes**

```python
# aworld-cli/src/aworld_cli/plugins/aworld_hud/hud/status.py
from aworld_cli.executors.stats import format_context_bar, format_elapsed, format_tokens


def _identity_segments(context):
    session = context.get("session", {})
    workspace = context.get("workspace", {})
    vcs = context.get("vcs", {})
    notifications = context.get("notifications", {})

    segments = [f"Agent: {session.get('agent', 'Aworld')} / {session.get('mode', 'Chat')}"]
    segments.append(f"Workspace: {workspace.get('name', 'workspace')}")
    segments.append(f"Branch: {vcs.get('branch', 'n/a')}")

    cron = notifications.get("cron_unread", 0)
    cron_segment = "Cron: clear" if cron == 0 else (f"Cron: {cron} unread" if cron > 0 else "Cron: offline")
    segments.append(cron_segment)

    model = session.get("model")
    if model:
        segments.append(f"Model: {model}")
    return segments


def _activity_segments(context):
    session = context.get("session", {})
    task = context.get("task", {})
    usage = context.get("usage", {})

    segments = []
    current_task_id = task.get("current_task_id")
    if current_task_id:
        segments.append(f"Task: {current_task_id}")

    if usage.get("input_tokens") is not None or usage.get("output_tokens") is not None:
        segments.append(
            f"Tokens: in {format_tokens(usage.get('input_tokens') or 0)} out {format_tokens(usage.get('output_tokens') or 0)}"
        )

    if usage.get("context_used") is not None and usage.get("context_max"):
        ctx_text = format_context_bar(usage["context_used"], usage["context_max"], bar_width=10)
        segments.append(ctx_text.replace("[green]", "").replace("[/green]", "").replace("[yellow]", "").replace("[/yellow]", "").replace("[red]", "").replace("[/red]", ""))
    elif usage.get("context_percent") is not None:
        segments.append(f"Ctx: {usage['context_percent']}%")

    elapsed = session.get("elapsed_seconds")
    if elapsed is not None:
        segments.append(format_elapsed(elapsed))

    return segments
```

- [ ] **Step 4: Run the HUD-focused test set**

Run: `pytest tests/plugins/test_plugin_hud.py tests/plugins/test_runtime_hud_snapshot.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aworld-cli/src/aworld_cli/plugins/aworld_hud/hud/status.py \
  aworld-cli/src/aworld_cli/executors/stats.py \
  tests/plugins/test_plugin_hud.py \
  tests/plugins/test_runtime_hud_snapshot.py
git commit -m "feat: refine interactive aworld hud summary"
```

### Task 2: Suppress Interactive `Aworld stats ...` While HUD Is Active

**Files:**
- Modify: `aworld-cli/src/aworld_cli/executors/local.py`
- Modify: `aworld-cli/src/aworld_cli/console.py`
- Test: `tests/executors/test_interactive_stats_fallback.py`

- [ ] **Step 1: Write the failing fallback-policy tests**

```python
# tests/executors/test_interactive_stats_fallback.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld_cli.executors.local import LocalAgentExecutor
from aworld_cli.executors.stats import StreamTokenStats


class FakeRuntime:
    def __init__(self, capabilities):
        self._capabilities = capabilities

    def active_plugin_capabilities(self):
        return self._capabilities


def test_interactive_stats_are_suppressed_when_hud_capability_is_active():
    executor = object.__new__(LocalAgentExecutor)
    executor._base_runtime = FakeRuntime(("hud",))

    assert executor._should_emit_interactive_stats() is False


def test_interactive_stats_return_when_hud_capability_is_missing():
    executor = object.__new__(LocalAgentExecutor)
    executor._base_runtime = FakeRuntime(("agents",))

    assert executor._should_emit_interactive_stats() is True
```

- [ ] **Step 2: Run the new fallback-policy tests to verify they fail**

Run: `pytest tests/executors/test_interactive_stats_fallback.py -v`
Expected: FAIL with `AttributeError: 'LocalAgentExecutor' object has no attribute '_should_emit_interactive_stats'`

- [ ] **Step 3: Implement the minimal suppression/fallback gate**

```python
# aworld-cli/src/aworld_cli/executors/local.py
def _should_emit_interactive_stats(self) -> bool:
    runtime = getattr(self, "_base_runtime", None)
    if runtime is None or not hasattr(runtime, "active_plugin_capabilities"):
        return True
    try:
        return "hud" not in tuple(runtime.active_plugin_capabilities())
    except Exception:
        return True
```

```python
# aworld-cli/src/aworld_cli/executors/local.py
# inside the STREAM=0 message-output path
if stream_token_stats and stream_token_stats.get_current_stats() and self._should_emit_interactive_stats():
    elapsed_sec = (datetime.now() - ctrl.status_start_time).total_seconds() if ctrl.status_start_time else None
    if elapsed_sec is not None:
        elapsed_str = format_elapsed(elapsed_sec)
        msg = stream_token_stats.format_streaming_line(elapsed_str)
        if msg and self.console:
            from rich.text import Text
            self.console.print(Text.from_markup(msg))
            self.console.print()
```

Add the same `_should_emit_interactive_stats()` guard anywhere else the interactive message path prints `Aworld stats ...`.

- [ ] **Step 4: Run the focused fallback-policy tests**

Run: `pytest tests/executors/test_interactive_stats_fallback.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aworld-cli/src/aworld_cli/executors/local.py \
  tests/executors/test_interactive_stats_fallback.py
git commit -m "fix: suppress interactive stats when hud is active"
```

### Task 3: Preserve The Existing Interactive Degraded Path

**Files:**
- Modify: `tests/executors/test_interactive_stats_fallback.py`
- Modify: `tests/plugins/test_plugin_end_to_end.py`
- Modify: `aworld-cli/src/aworld_cli/console.py`

- [ ] **Step 1: Extend the failing tests for degraded-mode behavior**

```python
# tests/plugins/test_plugin_end_to_end.py
def test_cli_runtime_excludes_disabled_builtin_hud_plugin(monkeypatch, tmp_path):
    plugin_dir = _set_isolated_plugin_dir(monkeypatch, tmp_path)
    manager = PluginManager(plugin_dir=plugin_dir)
    manager.disable("aworld-hud")

    runtime = CliRuntime(local_dirs=[], remote_backends=[])

    assert "hud" not in runtime.active_plugin_capabilities()
    assert not any(path.name == "aworld_hud" for path in runtime.plugin_dirs)
```

```python
# tests/executors/test_interactive_stats_fallback.py
def test_interactive_stats_gate_is_conservative_when_runtime_query_fails():
    class BrokenRuntime:
        def active_plugin_capabilities(self):
            raise RuntimeError("boom")

    executor = object.__new__(LocalAgentExecutor)
    executor._base_runtime = BrokenRuntime()

    assert executor._should_emit_interactive_stats() is True
```

- [ ] **Step 2: Run the degraded-path tests**

Run: `pytest tests/plugins/test_plugin_end_to_end.py::test_cli_runtime_excludes_disabled_builtin_hud_plugin tests/executors/test_interactive_stats_fallback.py -v`
Expected: PASS for runtime capability absence, and PASS for the conservative degraded-path gate.

- [ ] **Step 3: Tighten the console helper so fallback toolbar rendering only happens when HUD is active but a provider fails**

```python
# aworld-cli/src/aworld_cli/console.py
def _should_render_status_bar(self, runtime) -> bool:
    if runtime is None:
        return False

    if hasattr(runtime, "active_plugin_capabilities"):
        try:
            return "hud" in tuple(runtime.active_plugin_capabilities())
        except Exception:
            return True

    return True
```

Keep `_build_status_bar_text()` returning `""` when `_should_render_status_bar(runtime)` is false. This preserves the intended behavior:

- HUD enabled: toolbar exists
- HUD provider crashes: toolbar falls back to text
- HUD capability disabled: no toolbar, degraded textual `Aworld stats ...` path remains the only status surface

- [ ] **Step 4: Run the full regression slice for this change**

Run: `pytest tests/plugins/test_plugin_hud.py tests/plugins/test_runtime_hud_snapshot.py tests/plugins/test_plugin_end_to_end.py tests/executors/test_interactive_stats_fallback.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aworld-cli/src/aworld_cli/console.py \
  tests/plugins/test_plugin_end_to_end.py \
  tests/executors/test_interactive_stats_fallback.py
git commit -m "test: cover interactive hud fallback behavior"
```

### Task 4: Final Verification And OpenSpec Convergence

**Files:**
- Modify: `openspec/changes/2026-04-15-enhance-hud-runtime-snapshot/tasks.md`
- Modify: `openspec/changes/2026-04-15-enhance-hud-runtime-snapshot/implementation-plan.md`

- [ ] **Step 1: Run the complete relevant verification suite**

Run:

```bash
pytest tests/test_slash_commands.py \
  tests/plugins/test_plugin_hud.py \
  tests/plugins/test_runtime_hud_snapshot.py \
  tests/plugins/test_plugin_end_to_end.py \
  tests/plugins/test_plugin_cli_lifecycle.py \
  tests/plugins/test_plugin_commands.py \
  tests/executors/test_interactive_stats_fallback.py -q
```

Expected: PASS with no failures.

- [ ] **Step 2: Perform manual interactive acceptance**

Run:

```bash
aworld-cli plugins enable aworld-hud
aworld-cli
```

In the new interactive session, verify:

- HUD appears while `aworld-hud` is enabled
- no `Aworld stats ...` line is emitted during normal chat turns
- line 2 shows `Task / Tokens / Ctx / elapsed`
- `Plugins:` is absent

Then verify degraded mode:

```bash
aworld-cli plugins disable aworld-hud
aworld-cli
```

In the new interactive session, verify:

- no bottom HUD is shown
- `Aworld stats ...` lines appear as before in the message stream

- [ ] **Step 3: Update OpenSpec task tracking**

```markdown
# openspec/changes/2026-04-15-enhance-hud-runtime-snapshot/tasks.md
## 3. CLI And HUD Rendering

- [x] 3.1 Update HUD context assembly to merge the runtime snapshot with base toolbar context.
- [x] 3.2 Update `aworld-hud` to render a two-line layered toolbar using grouped segments.
- [x] 3.3 Add width-aware grouped-segment reduction so narrow terminals preserve core identity, task, and context information first.

## 4. Validation

- [x] 4.1 Add tests for runtime snapshot updates and merge behavior.
- [x] 4.2 Add tests for two-line HUD rendering, duplicate suppression, and width reduction.
- [x] 4.3 Add CLI integration tests for default enablement, lifecycle toggles, provider-failure fallback, and interactive stats fallback.
```

- [ ] **Step 4: Commit**

```bash
git add openspec/changes/2026-04-15-enhance-hud-runtime-snapshot/tasks.md \
  openspec/changes/2026-04-15-enhance-hud-runtime-snapshot/implementation-plan.md
git commit -m "docs: finalize interactive hud execution plan"
```
