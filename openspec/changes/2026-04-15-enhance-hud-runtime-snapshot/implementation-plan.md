# Live HUD Runtime Snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a runtime-owned live HUD snapshot, wire executor telemetry into that snapshot, and render a two-line `aworld-hud` toolbar with deterministic width reduction.

**Architecture:** Introduce a small runtime HUD snapshot store that owns semantic state buckets for session, task, activity, and usage. Executors publish partial updates into that store, `build_hud_context()` merges the snapshot into the shared HUD context, and CLI renders grouped HUD segments from the built-in `aworld-hud` plugin using a two-line layout with fallback behavior.

**Tech Stack:** Python, `dataclasses`, `copy`, `typing`, `prompt_toolkit`, existing `aworld_cli` runtime/executor modules, `pytest`

---

## File Structure

- Create: `aworld-cli/src/aworld_cli/runtime/hud_snapshot.py`
  Responsibility: own the runtime-side HUD snapshot store and provide merge/settle helpers for semantic HUD buckets.
- Modify: `aworld-cli/src/aworld_cli/runtime/base.py`
  Responsibility: initialize the snapshot store, expose snapshot update helpers, and merge live HUD state into `build_hud_context()`.
- Modify: `aworld-cli/src/aworld_cli/executors/stats.py`
  Responsibility: expose stream stats as a structured HUD-friendly usage snapshot instead of only formatted terminal strings.
- Modify: `aworld-cli/src/aworld_cli/executors/local.py`
  Responsibility: publish task-start, stream-progress, and task-finish updates into the runtime HUD snapshot.
- Modify: `aworld-cli/src/aworld_cli/plugin_framework/hud.py`
  Responsibility: carry optional grouped HUD segments in `HudLine` so CLI can reduce visible detail by priority without asking plugins to write markup.
- Modify: `aworld-cli/src/aworld_cli/console.py`
  Responsibility: render a two-line bottom toolbar, reduce grouped segments by width, and fall back cleanly if HUD providers fail.
- Modify: `aworld-cli/src/aworld_cli/plugins/aworld_hud/hud/status.py`
  Responsibility: render two grouped HUD lines from structured context without duplicating base identity fields.
- Create: `tests/plugins/test_runtime_hud_snapshot.py`
  Responsibility: cover snapshot store behavior, runtime context merging, and executor-to-runtime HUD publishing helpers.
- Modify: `tests/plugins/test_plugin_hud.py`
  Responsibility: cover grouped HUD lines, two-line toolbar rendering, width reduction, and provider-failure fallback.
- Modify: `tests/plugins/test_plugin_end_to_end.py`
  Responsibility: cover runtime behavior when built-in `aworld-hud` is enabled or disabled through the plugin system.

### Task 1: Add Runtime HUD Snapshot Primitives

**Files:**
- Create: `aworld-cli/src/aworld_cli/runtime/hud_snapshot.py`
- Modify: `aworld-cli/src/aworld_cli/runtime/base.py`
- Test: `tests/plugins/test_runtime_hud_snapshot.py`

- [ ] **Step 1: Write the failing snapshot-store and runtime-merge tests**

```python
# tests/plugins/test_runtime_hud_snapshot.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld_cli.runtime.base import BaseCliRuntime


class DummyRuntime(BaseCliRuntime):
    def __init__(self):
        super().__init__(agent_name="Aworld")
        self.plugin_dirs = []

    async def _load_agents(self):
        return []

    async def _create_executor(self, agent):
        return None

    def _get_source_type(self):
        return "TEST"

    def _get_source_location(self):
        return "test://runtime"


def test_build_hud_context_merges_live_snapshot():
    runtime = DummyRuntime()

    runtime.update_hud_snapshot(
        session={"session_id": "session-1", "model": "gpt-5"},
        task={"current_task_id": "task_001", "status": "running"},
        activity={"recent_tools": ["bash"], "tool_calls_count": 1},
        usage={"input_tokens": 1200, "output_tokens": 80, "context_percent": 34},
    )

    context = runtime.build_hud_context(
        agent_name="Aworld",
        mode="Chat",
        workspace_name="aworld",
        git_branch="feat/hud",
    )

    assert context["session"]["agent"] == "Aworld"
    assert context["session"]["session_id"] == "session-1"
    assert context["session"]["model"] == "gpt-5"
    assert context["task"]["current_task_id"] == "task_001"
    assert context["activity"]["recent_tools"] == ["bash"]
    assert context["usage"]["context_percent"] == 34


def test_settle_hud_snapshot_keeps_last_useful_state():
    runtime = DummyRuntime()

    runtime.update_hud_snapshot(
        task={"current_task_id": "task_001", "status": "running"},
        activity={"current_tool": "bash", "recent_tools": ["bash"], "tool_calls_count": 1},
    )

    runtime.settle_hud_snapshot(task_status="idle")
    context = runtime.build_hud_context(
        agent_name="Aworld",
        mode="Chat",
        workspace_name="aworld",
        git_branch="main",
    )

    assert context["task"]["status"] == "idle"
    assert context["task"]["current_task_id"] == "task_001"
    assert context["activity"]["current_tool"] is None
    assert context["activity"]["recent_tools"] == ["bash"]
```

- [ ] **Step 2: Run the snapshot tests to verify they fail**

Run: `pytest tests/plugins/test_runtime_hud_snapshot.py -v`
Expected: FAIL with `AttributeError: 'DummyRuntime' object has no attribute 'update_hud_snapshot'`

- [ ] **Step 3: Implement the snapshot store and runtime merge helpers**

```python
# aworld-cli/src/aworld_cli/runtime/hud_snapshot.py
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any


DEFAULT_BUCKETS = (
    "workspace",
    "session",
    "task",
    "activity",
    "usage",
    "notifications",
    "vcs",
    "plugins",
)


@dataclass
class HudSnapshotStore:
    _snapshot: dict[str, dict[str, Any]] = field(default_factory=dict)

    def update(self, **sections: dict[str, Any]) -> dict[str, dict[str, Any]]:
        for name, payload in sections.items():
            if not payload:
                continue
            bucket = self._snapshot.setdefault(name, {})
            bucket.update(payload)
        return self.snapshot()

    def settle(self, task_status: str = "idle") -> dict[str, dict[str, Any]]:
        task_bucket = self._snapshot.setdefault("task", {})
        activity_bucket = self._snapshot.setdefault("activity", {})
        task_bucket["status"] = task_status
        activity_bucket["current_tool"] = None
        return self.snapshot()

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return deepcopy(self._snapshot)
```

```python
# aworld-cli/src/aworld_cli/runtime/base.py
from pathlib import Path
from typing import Any, Optional

from .hud_snapshot import HudSnapshotStore


# inside BaseCliRuntime.__init__
self._plugin_state_store = None
self._hud_snapshot_store = HudSnapshotStore()


def update_hud_snapshot(self, **sections: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return self._hud_snapshot_store.update(**sections)


def settle_hud_snapshot(self, task_status: str = "idle") -> dict[str, dict[str, Any]]:
    return self._hud_snapshot_store.settle(task_status=task_status)


def get_hud_snapshot(self) -> dict[str, dict[str, Any]]:
    return self._hud_snapshot_store.snapshot()


def build_hud_context(
    self,
    agent_name: str = "Aworld",
    mode: str = "Chat",
    workspace_name: str | None = None,
    git_branch: str | None = None,
) -> dict[str, Any]:
    unread_count = 0
    if self._notification_center and hasattr(self._notification_center, "get_unread_count"):
        try:
            unread_count = int(self._notification_center.get_unread_count())
        except Exception:
            unread_count = 0

    context = {
        "workspace": {"name": workspace_name or Path.cwd().name, "path": str(Path.cwd())},
        "session": {"agent": agent_name, "mode": mode},
        "notifications": {"cron_unread": unread_count},
        "vcs": {"branch": git_branch or "n/a"},
        "plugins": {
            "active_count": len(self._plugins),
            "active_ids": [plugin.manifest.plugin_id for plugin in self._plugins],
        },
    }

    for bucket, payload in self.get_hud_snapshot().items():
        context.setdefault(bucket, {})
        context[bucket].update(payload)

    return context
```

- [ ] **Step 4: Run the snapshot tests to verify they pass**

Run: `pytest tests/plugins/test_runtime_hud_snapshot.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aworld-cli/src/aworld_cli/runtime/hud_snapshot.py \
  aworld-cli/src/aworld_cli/runtime/base.py \
  tests/plugins/test_runtime_hud_snapshot.py
git commit -m "feat: add runtime HUD snapshot store"
```

### Task 2: Publish Executor Telemetry Into The Runtime HUD Snapshot

**Files:**
- Modify: `aworld-cli/src/aworld_cli/executors/stats.py`
- Modify: `aworld-cli/src/aworld_cli/executors/local.py`
- Modify: `tests/plugins/test_runtime_hud_snapshot.py`

- [ ] **Step 1: Write the failing executor-publish tests**

```python
# tests/plugins/test_runtime_hud_snapshot.py
from aworld_cli.executors.local import LocalAgentExecutor
from aworld_cli.executors.stats import StreamTokenStats


def test_stream_token_stats_exports_hud_usage_snapshot():
    stats = StreamTokenStats()
    stats.update(
        agent_id="agent-1",
        agent_name="Aworld",
        output_tokens=300,
        input_tokens=1200,
        tool_calls_count=2,
        model_name="gpt-4o",
    )

    usage = stats.to_hud_usage()

    assert usage["input_tokens"] == 1200
    assert usage["output_tokens"] == 300
    assert usage["total_tokens"] == 1500
    assert usage["context_used"] == 1500


def test_local_executor_publishes_stream_updates_to_runtime():
    runtime = DummyRuntime()
    executor = object.__new__(LocalAgentExecutor)
    executor._base_runtime = runtime
    executor.session_id = "session-1"

    stats = StreamTokenStats()
    stats.update(
        agent_id="agent-1",
        agent_name="Aworld",
        output_tokens=300,
        input_tokens=1200,
        tool_calls_count=2,
        model_name="gpt-4o",
    )

    executor._publish_hud_stream_update(
        task_id="task_001",
        stream_token_stats=stats,
        current_tool="bash",
        elapsed_seconds=12.5,
    )

    context = runtime.build_hud_context(
        agent_name="Aworld",
        mode="Chat",
        workspace_name="aworld",
        git_branch="main",
    )

    assert context["task"]["current_task_id"] == "task_001"
    assert context["task"]["status"] == "running"
    assert context["activity"]["current_tool"] == "bash"
    assert context["activity"]["tool_calls_count"] == 2
    assert context["usage"]["total_tokens"] == 1500
    assert context["session"]["elapsed_seconds"] == 12.5
```

- [ ] **Step 2: Run the executor HUD tests to verify they fail**

Run: `pytest tests/plugins/test_runtime_hud_snapshot.py -k "hud_usage or publishes_stream_updates" -v`
Expected: FAIL with `AttributeError: 'StreamTokenStats' object has no attribute 'to_hud_usage'`

- [ ] **Step 3: Implement usage export and local-executor HUD publishing helpers**

```python
# aworld-cli/src/aworld_cli/executors/stats.py
class StreamTokenStats:
    def to_hud_usage(self) -> Dict[str, Any]:
        stats = self.get_current_stats() or self._last_for_history
        if not stats:
            return {}

        input_tokens = stats.get("input_tokens") or 0
        output_tokens = stats.get("output_tokens") or 0
        total_tokens = self._compute_total_tokens(stats) or (input_tokens + output_tokens)
        model_name = stats.get("model_name")
        context_max = 0
        if model_name and ModelUtils:
            try:
                context_max = ModelUtils.get_context_window(model_name)
            except Exception:
                context_max = 0

        context_percent = int((total_tokens / context_max) * 100) if context_max else None
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "context_used": total_tokens,
            "context_max": context_max or None,
            "context_percent": context_percent,
            "model": model_name,
            "tool_calls_count": stats.get("tool_calls_count", 0),
        }
```

```python
# aworld-cli/src/aworld_cli/executors/local.py
def _publish_hud_task_started(self, task) -> None:
    runtime = getattr(self, "_base_runtime", None)
    if runtime is None:
        return
    runtime.update_hud_snapshot(
        session={"session_id": self.session_id},
        task={
            "current_task_id": task.id,
            "status": "running",
            "started_at": datetime.now().isoformat(),
        },
        activity={"current_tool": None, "recent_tools": [], "tool_calls_count": 0},
    )


def _publish_hud_stream_update(
    self,
    task_id: str,
    stream_token_stats: StreamTokenStats,
    current_tool: str | None,
    elapsed_seconds: float | None,
) -> None:
    runtime = getattr(self, "_base_runtime", None)
    if runtime is None:
        return

    usage = stream_token_stats.to_hud_usage()
    recent_tools = [current_tool] if current_tool else []
    runtime.update_hud_snapshot(
        session={
            "session_id": self.session_id,
            "model": usage.get("model"),
            "elapsed_seconds": elapsed_seconds,
        },
        task={"current_task_id": task_id, "status": "running"},
        activity={
            "current_tool": current_tool,
            "recent_tools": recent_tools,
            "tool_calls_count": usage.get("tool_calls_count", 0),
        },
        usage=usage,
    )


def _publish_hud_task_finished(self, task_id: str, task_status: str = "idle") -> None:
    runtime = getattr(self, "_base_runtime", None)
    if runtime is None:
        return
    runtime.update_hud_snapshot(task={"current_task_id": task_id})
    runtime.settle_hud_snapshot(task_status=task_status)
```

- [ ] **Step 4: Wire the helper calls into the existing local executor flow and run the focused tests**

```python
# aworld-cli/src/aworld_cli/executors/local.py
task = await self._build_task(task_content, session_id=self.session_id, image_urls=image_urls)
self._publish_hud_task_started(task)
elapsed_sec = (datetime.now() - ctrl.status_start_time).total_seconds() if ctrl.status_start_time else None
current_tool_name = None
tool_calls = output.tool_calls if hasattr(output, "tool_calls") and output.tool_calls else []
if tool_calls:
    first_tool = tool_calls[0]
    tool_data = getattr(first_tool, "data", first_tool)
    function = getattr(tool_data, "function", None)
    current_tool_name = getattr(function, "name", None)
self._publish_hud_stream_update(
    task_id=task.id,
    stream_token_stats=stream_token_stats,
    current_tool=current_tool_name,
    elapsed_seconds=elapsed_sec,
)
self._publish_hud_task_finished(task.id, task_status="idle")
```

Run: `pytest tests/plugins/test_runtime_hud_snapshot.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aworld-cli/src/aworld_cli/executors/stats.py \
  aworld-cli/src/aworld_cli/executors/local.py \
  tests/plugins/test_runtime_hud_snapshot.py
git commit -m "feat: publish executor HUD telemetry"
```

### Task 3: Render Two-Line Grouped HUD Output

**Files:**
- Modify: `aworld-cli/src/aworld_cli/plugin_framework/hud.py`
- Modify: `aworld-cli/src/aworld_cli/console.py`
- Modify: `aworld-cli/src/aworld_cli/plugins/aworld_hud/hud/status.py`
- Modify: `tests/plugins/test_plugin_hud.py`

- [ ] **Step 1: Write the failing grouped-HUD and multiline-toolbar tests**

```python
# tests/plugins/test_plugin_hud.py
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
```

- [ ] **Step 2: Run the HUD rendering tests to verify they fail**

Run: `pytest tests/plugins/test_plugin_hud.py -v`
Expected: FAIL with `AttributeError: 'HudLine' object has no attribute 'segments'`

- [ ] **Step 3: Extend the HUD line contract and implement two-line `aworld-hud` segments**

```python
# aworld-cli/src/aworld_cli/plugin_framework/hud.py
@dataclass(frozen=True)
class HudLine:
    section: str
    priority: int
    text: str
    provider_id: str
    segments: tuple = ()


def collect_hud_lines(plugins: Iterable[Any], context: dict[str, Any]) -> list[HudLine]:
    lines: list[HudLine] = []
    for plugin in plugins:
        resolver = PluginResourceResolver(Path(plugin.manifest.plugin_root), plugin.manifest.plugin_id)
        for entrypoint in plugin.manifest.entrypoints.get("hud", ()):
            module_path = resolver.resolve_asset(entrypoint.target)
            spec = spec_from_file_location(
                f"hud_{plugin.manifest.plugin_id}_{entrypoint.entrypoint_id}",
                module_path,
            )
            module = module_from_spec(spec)
            spec.loader.exec_module(module)
            payloads = module.render_lines(dict(context))
            for payload in payloads:
                section = str(payload["section"]).strip().lower()
                segments = tuple(str(item) for item in payload.get("segments", ()) if str(item).strip())
                text = str(payload.get("text") or " | ".join(segments))
                lines.append(
                    HudLine(
                        section=section,
                        priority=int(payload.get("priority", 100)),
                        text=text,
                        provider_id=entrypoint.entrypoint_id,
                        segments=segments,
                    )
                )
    return sorted(lines, key=lambda item: (SECTION_ORDER.get(item.section, len(SECTION_ORDER)), item.priority, item.provider_id, item.text))
```

```python
# aworld-cli/src/aworld_cli/plugins/aworld_hud/hud/status.py
from aworld_cli.executors.stats import format_elapsed, format_tokens


def _identity_segments(context):
    session = context.get("session", {})
    workspace = context.get("workspace", {})
    vcs = context.get("vcs", {})
    notifications = context.get("notifications", {})
    agent = session.get("agent", "Aworld")
    mode = session.get("mode", "Chat")
    model = session.get("model")
    elapsed = session.get("elapsed_seconds")
    cron = notifications.get("cron_unread", 0)

    segments = [f"Agent: {agent} / {mode}"]
    if model:
        segments.append(f"Model: {model}")
    segments.append(f"Workspace: {workspace.get('name', 'workspace')}")
    segments.append(f"Branch: {vcs.get('branch', 'n/a')}")
    segments.append(
        f"Cron: {cron} unread" if cron > 0 else f"Cron: clear | {format_elapsed(elapsed)}" if elapsed else "Cron: clear"
    )
    return segments


def _activity_segments(context):
    task = context.get("task", {})
    activity = context.get("activity", {})
    usage = context.get("usage", {})
    plugins = context.get("plugins", {})

    segments = []
    if task.get("current_task_id"):
        segments.append(f"Task: {task['current_task_id']} ({task.get('status', 'idle')})")
    if activity.get("current_tool"):
        segments.append(f"Tool: {activity['current_tool']} ×{activity.get('tool_calls_count', 0)}")
    elif activity.get("tool_calls_count"):
        segments.append(f"Tools: {activity['tool_calls_count']}")
    if usage.get("input_tokens") is not None or usage.get("output_tokens") is not None:
        segments.append(
            f"Tokens: ↑{format_tokens(usage.get('input_tokens', 0))} ↓{format_tokens(usage.get('output_tokens', 0))}"
        )
    if usage.get("context_percent") is not None:
        segments.append(f"Ctx: {usage['context_percent']}%")
    if plugins.get("active_count", 0) > 1:
        segments.append(f"Plugins: {plugins['active_count']}")
    return segments


def render_lines(context):
    return [
        {"section": "identity", "priority": 10, "segments": _identity_segments(context)},
        {"section": "activity", "priority": 20, "segments": _activity_segments(context)},
    ]
```

- [ ] **Step 4: Refactor the CLI toolbar to render grouped lines, reduce width, and rerun the tests**

```python
# aworld-cli/src/aworld_cli/console.py
def _fallback_status_segments(self, hud_context, agent_name: str, mode: str) -> list[str]:
    unread_count = hud_context.get("notifications", {}).get("cron_unread", -1)
    cron_status = "Cron: offline" if unread_count < 0 else (
        f"Cron: {unread_count} unread" if unread_count > 0 else "Cron: clear"
    )
    return [
        f"Agent: {hud_context.get('session', {}).get('agent', agent_name)}",
        f"Mode: {hud_context.get('session', {}).get('mode', mode)}",
        cron_status,
        f"Workspace: {hud_context.get('workspace', {}).get('name', self._toolbar_workspace_name)}",
        f"Branch: {hud_context.get('vcs', {}).get('branch', self._toolbar_git_branch)}",
    ]


def _reduce_segments(self, segments: list[str], max_width: int | None) -> list[str]:
    if max_width is None:
        return segments
    kept = list(segments)
    while kept and len(" | ".join(kept)) > max_width:
        kept.pop()
    return kept or segments[:1]


def _build_status_bar_text(self, runtime, agent_name: str = "Aworld", mode: str = "Chat", max_width: int | None = 160) -> str:
    if runtime and hasattr(runtime, "build_hud_context"):
        hud_context = runtime.build_hud_context(
            agent_name=agent_name,
            mode=mode,
            workspace_name=self._toolbar_workspace_name,
            git_branch=self._toolbar_git_branch,
        )
    else:
        hud_context = {
            "workspace": {"name": self._toolbar_workspace_name},
            "session": {"agent": agent_name, "mode": mode},
            "notifications": {"cron_unread": -1},
            "vcs": {"branch": self._toolbar_git_branch},
        }

    try:
        plugin_lines = runtime.get_hud_lines(hud_context) if runtime and hasattr(runtime, "get_hud_lines") else []
    except Exception:
        plugin_lines = []

    if not plugin_lines:
        return " | ".join(self._fallback_status_segments(hud_context, agent_name, mode))

    rendered_lines = []
    for line in plugin_lines[:2]:
        segments = list(getattr(line, "segments", ()) or [line.text])
        rendered_lines.append(" | ".join(self._reduce_segments(segments, max_width)))
    return "\n".join(rendered_lines)
```

Run: `pytest tests/plugins/test_plugin_hud.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aworld-cli/src/aworld_cli/plugin_framework/hud.py \
  aworld-cli/src/aworld_cli/console.py \
  aworld-cli/src/aworld_cli/plugins/aworld_hud/hud/status.py \
  tests/plugins/test_plugin_hud.py
git commit -m "feat: render two-line aworld hud"
```

### Task 4: Validate Built-In HUD Lifecycle And Fallback Integration

**Files:**
- Modify: `tests/plugins/test_plugin_end_to_end.py`
- Modify: `tests/plugins/test_plugin_hud.py`

- [ ] **Step 1: Write the failing end-to-end lifecycle test for built-in HUD enablement**

```python
# tests/plugins/test_plugin_end_to_end.py
def test_cli_runtime_skips_disabled_builtin_aworld_hud(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugins"
    manager = PluginManager(plugin_dir=plugin_home)

    assert manager.disable("aworld-hud")

    runtime = CliRuntime(local_dirs=[], remote_backends=[])

    assert all(path.name != "aworld_hud" for path in runtime.plugin_dirs)


def test_cli_runtime_includes_builtin_aworld_hud_when_enabled(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugins"
    manager = PluginManager(plugin_dir=plugin_home)

    assert manager.enable("aworld-hud")

    runtime = CliRuntime(local_dirs=[], remote_backends=[])

    assert any(path.name == "aworld_hud" for path in runtime.plugin_dirs)
```

- [ ] **Step 2: Run the built-in HUD integration tests to verify they fail**

Run: `pytest tests/plugins/test_plugin_end_to_end.py -k "builtin_aworld_hud" -v`
Expected: FAIL if runtime still includes disabled built-in HUD unconditionally or if HOME/plugin state is not isolated in the test setup

- [ ] **Step 3: Isolate plugin state in the tests and add the provider-fallback assertion to the focused suite**

```python
# tests/plugins/test_plugin_end_to_end.py
def test_cli_runtime_skips_disabled_builtin_aworld_hud(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    manager = PluginManager(plugin_dir=tmp_path / ".aworld" / "plugins")

    assert manager.disable("aworld-hud")

    runtime = CliRuntime(local_dirs=[], remote_backends=[])

    assert all(path.name != "aworld_hud" for path in runtime.plugin_dirs)


def test_cli_runtime_includes_builtin_aworld_hud_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    manager = PluginManager(plugin_dir=tmp_path / ".aworld" / "plugins")

    assert manager.enable("aworld-hud")

    runtime = CliRuntime(local_dirs=[], remote_backends=[])

    assert any(path.name == "aworld_hud" for path in runtime.plugin_dirs)
```

```python
# tests/plugins/test_plugin_hud.py
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
    assert "\n" not in text
```

- [ ] **Step 4: Run the focused HUD and plugin integration suite**

Run: `pytest tests/plugins/test_runtime_hud_snapshot.py tests/plugins/test_plugin_hud.py tests/plugins/test_plugin_end_to_end.py -v`
Expected: PASS

Run: `pytest tests/test_plugin_cli_entrypoint.py tests/test_slash_commands.py tests/plugins -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/plugins/test_plugin_end_to_end.py \
  tests/plugins/test_plugin_hud.py \
  tests/plugins/test_runtime_hud_snapshot.py
git commit -m "test: cover live hud runtime snapshot"
```
