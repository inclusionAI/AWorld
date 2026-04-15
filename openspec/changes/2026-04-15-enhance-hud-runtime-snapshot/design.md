## Context

The current framework already supports manifest-driven HUD providers and a shared `HudContext`, but the data contract is still too thin to support a useful status bar. Today `build_hud_context()` mainly exposes:

- agent
- mode
- workspace name
- git branch
- cron unread count

At the same time, important runtime information already exists elsewhere in the CLI flow:

- local executor prints `Running task: <task_id>`
- streaming stats show token input and output
- streaming stats show tool call counts
- streaming stats can derive context usage bars

That split is the real gap. The problem is not that `aworld-hud` lacks string formatting logic. The problem is that the runtime does not yet own a live, structured telemetry snapshot that HUD plugins can consume safely.

This change keeps the framework-first direction established by the plugin-system work:

- HUD remains a plugin surface, not a hard-coded toolbar exception
- runtime owns structured state
- plugins render from that state
- CLI owns final presentation and width management

The `claude_hud` implementation remains a useful reference for line-oriented status rendering, but AWorld should not copy its subprocess-centric collection model. The better fit is executor-driven runtime snapshot updates plus plugin rendering.

## Goals / Non-Goals

**Goals**
- Make live runtime telemetry available through the framework HUD context instead of terminal-only output.
- Keep the runtime/HUD boundary explicit: executors report state, runtime stores state, plugins render state, CLI presents state.
- Define a two-line layered HUD that is informative without becoming visually noisy.
- Ensure HUD density stays bounded and width-aware.
- In interactive chat mode, make HUD the primary state surface instead of duplicating the same state in message-stream stats output.
- Keep `aworld-hud` as the first built-in plugin case, not the definition of the framework.

**Non-Goals**
- Introduce a general event bus or subscription framework for all plugins in this change.
- Let HUD plugins take over the full toolbar renderer.
- Force every runtime to expose every HUD field if the underlying data is unavailable.
- Change non-interactive task mode or batch-mode stats output behavior in this change.
- Remove all textual stats fallback behavior; interactive chat still needs a degraded path when HUD is unavailable.

## Decisions

### Decision: Runtime Owns A Live HUD Snapshot

The runtime should own one mutable HUD snapshot per active CLI session. Executors update that snapshot at defined lifecycle points instead of making HUD providers infer state from console output.

Why:

- runtime is the correct boundary for cross-plugin shared execution state
- HUD plugins need a stable read contract, not executor internals
- future plugins beyond HUD may also need the same runtime summary state

Alternative considered:
- have HUD providers parse log output or history records
  Rejected because it is brittle, delayed, and consumer-specific.

### Decision: Executors Push HUD Updates Into Runtime

Executors should push structured HUD updates into runtime at these points:

- task start
- stream or chunk progress
- task completion
- task failure or idle transition

This keeps the data flow direct:

`executor lifecycle -> runtime snapshot store -> build_hud_context() -> HUD plugins -> CLI toolbar`

The snapshot update API should support partial updates so executors can refresh only the state they own, such as activity or usage, without rebuilding the full HUD payload each time.

### Decision: HUD Context Uses Stable Semantic Buckets

The runtime HUD snapshot should extend the existing base context with stable semantic buckets rather than exposing arbitrary executor objects.

The buckets for this change are:

- `workspace`
  - `name`
  - `path`
- `session`
  - `agent`
  - `mode`
  - `session_id`
  - `model`
  - `elapsed_seconds`
- `task`
  - `current_task_id`
  - `status`
  - `started_at`
- `activity`
  - `current_tool`
  - `recent_tools`
  - `tool_calls_count`
  - `last_event`
- `usage`
  - `input_tokens`
  - `output_tokens`
  - `total_tokens`
  - `context_used`
  - `context_max`
  - `context_percent`
- `notifications`
  - `cron_unread`
- `vcs`
  - `branch`
  - `dirty`
  - `ahead`
  - `behind`
- `plugins`
  - `active_count`
  - `active_ids`

Unavailable data should remain omitted or marked unknown. Providers must not re-query external systems to fill missing data in a render path.

### Decision: `aworld-hud` Renders A Two-Line Layered Toolbar

The built-in `aworld-hud` plugin should render two layers:

- line 1: session identity and environment
- line 2: live activity and usage

Recommended composition:

- line 1
  - `Agent/Mode`
  - `Model`
  - `Workspace`
  - `Git`
  - `Cron/Elapsed`
- line 2
  - `Task`
  - `Tool Activity`
  - `Token Usage`
  - `Context`
  - `Plugins` when meaningful

This split matches user expectations:

- line 1 answers "where am I and what session is this?"
- line 2 answers "what is happening right now and how full is the session?"

For the current interactive chat experience, the line composition is:

- line 1 priority
  - `Agent/Mode`
  - `Workspace`
  - `Branch`
  - `Cron`
  - `Model`
- line 2 runtime summary
  - `Task: <real_task_id>`
  - `Tokens: in <input> out <output>`
  - `Ctx: <bar> <percent>`
  - `<elapsed>`

During active execution, line 2 can still include transient activity such as tool usage if width allows. Once the task transitions to idle, the HUD should retain only the last stable summary state and should not continue showing transient tool fields.

### Decision: Interactive Chat Uses HUD As The Primary State Surface

Interactive chat mode should not present the same runtime status in two places at once.

When a HUD capability is active:

- the bottom HUD is the only primary state surface
- interactive message flow should no longer emit the normal `Aworld stats ...` status line
- executor/runtime state still flows through the same telemetry path, but presentation is HUD-first

This keeps a single user-facing mental model:

- message area for answers and events
- HUD for live session/task/resource state

This decision only applies to interactive chat mode. Direct-run tasks, batch execution, and non-interactive flows keep their current textual stats output unless changed by a future spec.

### Decision: `Aworld stats ...` Remains The Interactive Fallback Path

HUD cannot be the only status path if users can disable the HUD plugin or if a provider fails.

In interactive chat mode:

- if `aworld-hud` is enabled and HUD rendering succeeds, only HUD is shown
- if the `hud` capability is unavailable because the plugin is disabled, missing, or not loaded, the CLI should fall back to the current `Aworld stats ...` output behavior
- if HUD rendering fails at runtime, the CLI should degrade to `Aworld stats ...` rather than leaving the user with no state feedback

This preserves robustness while still making HUD the normal path.

### Decision: HUD Density Must Stay Bounded

The HUD must remain readable. It should not grow into an unbounded wall of columns.

For this change the design constraints are:

- at most 5 visible grouped segments on one line
- at most 8 visible grouped segments across the normal two-line layout
- narrow layouts should reduce toward 6 visible grouped segments or fewer

The key rule is grouping, not field sprawl. Many fields may exist in the snapshot, but the HUD should present them as a small number of stable grouped segments.

### Decision: CLI Owns Width Reduction And Segment Priority

The CLI should continue to own final rendering and width reduction. Plugins contribute plain text lines, but CLI decides which grouped segments stay visible as width shrinks.

The reduction priority for this change is:

- line 1 keeps session identity first
- line 2 keeps task and context first
- plugins segment drops before core activity and usage segments
- detailed tool summaries compress before task and context disappear

Recommended shrink policy:

- wide: full two-line HUD
- medium: keep two lines, hide plugins, compress agent/mode and tool activity
- narrow: keep line 1 core identity, keep only `Task + Context` on line 2

### Decision: HUD Never Blocks The Main Execution Flow

HUD updates and rendering are best-effort. Failures must not interrupt task execution, message rendering, or plugin execution.

Rules:

- snapshot update failures are logged and ignored
- render failures from one HUD provider do not remove the whole toolbar
- missing values degrade to omission or `n/a`
- the last good snapshot may be retained briefly to avoid flicker during task transitions

This preserves CLI stability while still allowing richer status behavior.

### Decision: Plugin-Count Is Internal, Not A Primary HUD Field

The current `Plugins: <count>` field is not meaningful enough for end users and can be actively confusing because it exposes implementation-level plugin counts rather than user-understandable capabilities.

For this change:

- plugin count should not be a normal HUD segment
- the HUD should favor stable execution summary fields over internal framework counts
- plugin metadata can remain visible in `/plugins` and CLI plugin-management surfaces where it is operationally useful

## Data Flow

The executor-driven runtime snapshot updates should look like this:

### Task Start

Update:

- `task.current_task_id`
- `task.status=running`
- `task.started_at`
- `session.session_id`
- reset current activity fields

### Streaming Update

Update:

- `session.model`
- `session.elapsed_seconds`
- `activity.current_tool`
- `activity.recent_tools`
- `activity.tool_calls_count`
- `usage.input_tokens`
- `usage.output_tokens`
- `usage.total_tokens`
- `usage.context_used`
- `usage.context_max`
- `usage.context_percent`

### Task Finish

Update:

- `task.status=completed` or `idle` or `error`
- clear `activity.current_tool`
- keep a short `recent_tools` tail
- preserve latest cumulative usage summary
- preserve latest elapsed seconds for idle summary rendering

### Idle / Transition

The runtime should retain the last good stable snapshot rather than blanking the HUD immediately. In interactive chat mode, idle state should continue to show the last useful summary:

- `Task`
- `Tokens`
- `Ctx`
- `Elapsed`

Idle state should drop transient activity fields such as `Tool`.

## Testing Strategy

The implementation should validate behavior at four levels.

### Snapshot Store Tests

Verify:

- partial updates merge predictably
- task-start and task-finish transitions update the correct buckets
- recent-tool retention is bounded
- last-good snapshot fallback works

### Runtime Context Tests

Verify:

- `build_hud_context()` merges static toolbar data with live runtime snapshot data
- multiple HUD providers in one refresh observe the same assembled snapshot
- missing data does not break snapshot assembly

### HUD Plugin Rendering Tests

Verify:

- `aworld-hud` renders two lines
- duplicate identity fields are not repeated
- width reduction hides lower-priority grouped segments first
- line 2 prefers task and context over plugin-detail segments
- line 2 idle state keeps stable summary fields and drops transient tool fields

### Interactive Chat Presentation Tests

Verify:

- interactive chat suppresses `Aworld stats ...` when a HUD capability is active
- disabling `aworld-hud` removes the bottom toolbar from interactive chat
- disabling or breaking HUD restores `Aworld stats ...` output in interactive chat
- non-interactive flows keep their existing textual stats behavior

### CLI Integration Tests

Verify:

- built-in `aworld-hud` remains enabled by default
- `plugins enable/disable/reload aworld-hud` affects toolbar behavior predictably
- provider failures degrade to the baseline toolbar rather than breaking prompt rendering

## Risks And Mitigations

- Risk: snapshot writes become scattered and inconsistent
  Mitigation: centralize runtime snapshot update helpers and limit write ownership by bucket.

- Risk: HUD becomes too dense and noisy
  Mitigation: keep grouped-segment limits and CLI-owned width reduction rules.

- Risk: providers begin to depend on unstable internal fields
  Mitigation: keep the snapshot schema narrow and semantic, not raw-object oriented.

- Risk: implementation drifts toward a HUD-specific framework
  Mitigation: keep snapshot ownership in runtime and keep `aworld-hud` as only one consumer.
