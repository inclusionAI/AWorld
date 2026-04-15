## Why

The plugin framework now allows HUD providers, but the current `aworld-hud` remains mostly a placeholder. Most of the useful runtime signals that users care about, such as the active task, current tool activity, token usage, and context usage, still live in executor log output instead of in the shared HUD context contract.

That leaves the framework in an awkward state:

- HUD is technically pluggable, but not yet informative.
- Runtime state is still scattered across executor-specific output paths.
- HUD plugins cannot build a useful status bar without scraping console text or reaching into executor internals.

This change strengthens the framework-level HUD model by making runtime own a live HUD snapshot and by making `aworld-hud` the first built-in consumer of that shared state.

## What Changes

- Add a runtime-owned live HUD snapshot that executors update during task start, streaming progress, and task completion.
- Define stable semantic HUD buckets for session, task, activity, usage, workspace, notifications, VCS, and plugins.
- Make `build_hud_context()` merge base toolbar context with the live runtime snapshot rather than only returning static session metadata.
- Evolve the CLI bottom toolbar into a two-line layered HUD:
  - line 1 for session identity and environment
  - line 2 for live activity and resource usage
- Define bounded HUD density and width-aware reduction so the toolbar stays readable and never expands into an unbounded column wall.
- Keep `aworld-hud` as a built-in plugin case, not a privileged special-case renderer outside the framework.

## Capabilities

### Modified Capabilities
- `agent-runtime`: Add executor-driven live HUD snapshot updates and stable semantic HUD state buckets.
- `cli-experience`: Add layered two-line HUD presentation and priority-based width reduction for runtime HUD state.

## Impact

- Affects runtime/executor coordination for task lifecycle telemetry.
- Affects bottom-toolbar composition and rendering behavior in `aworld-cli`.
- Preserves the framework-first plugin direction: HUD remains one consumer of shared plugin/runtime state.
- Creates a better foundation for future plugins that need runtime state, such as code review, workflow steering, or richer task/session status plugins.
