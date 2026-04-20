## Why

The recent HUD work improved the user experience, but it also exposed an architectural problem: meaningful HUD changes still tend to spill into `aworld-cli` host code instead of remaining inside the HUD plugin contract. That makes `aworld-hud` feel less like a real plugin and more like a built-in feature split across host and plugin directories.

This change formalizes a stricter boundary before more HUD work lands. The CLI should provide a generic HUD surface and renderer, while `aworld-hud` remains a built-in plugin that derives its content through plugin capabilities, hooks, and plugin state rather than through plugin-specific host branches.

## What Changes

- Define the bottom HUD as a generic CLI host surface rather than an `aworld-hud`-specific behavior path.
- Clarify that HUD content, grouping, and session-specific business logic belong in plugins, including built-in plugins.
- Require built-in HUD plugins to follow the same capability contract as third-party HUD plugins, except for shipping location and default enablement.
- Add the missing framework contracts required for third-party HUD plugins: plugin state write-back, task lifecycle hook points, and plugin-state-aware HUD provider rendering.
- Define an explicit plugin SDK boundary for reusable HUD helpers so built-in plugins do not rely on private host internals.
- Shift HUD state collection toward hook-driven plugin state and shared runtime context instead of host-side `aworld-hud` business branching.
- Keep namespace cleanup in scope, but make it non-blocking relative to the functional plugin-contract gaps above.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `agent-runtime`: clarify that runtime exposes generic HUD context and plugin-scoped state bridges without embedding `aworld-hud` business rules.
- `cli-experience`: clarify that CLI owns a generic HUD surface and renderer, while plugin content remains data-driven and plugin-owned.
- `plugin-system`: clarify that built-in HUD plugins use the same capability contract as external plugins and should not require per-plugin host code paths for content changes.
- `workflow-hooks`: extend hook expectations so plugins can drive HUD-oriented state through hook outputs and plugin-scoped state rather than direct host customization.

## Impact

- Affects HUD-related host code in `aworld-cli`, especially generic rendering, capability loading, and naming.
- Affects the built-in `aworld-hud` plugin contract and future HUD plugin authoring expectations.
- Affects hook, runtime-state, and plugin-state boundaries used to assemble HUD context.
- Affects the hook contract by requiring task lifecycle hook points suitable for live HUD updates.
- Affects the HUD provider contract by requiring plugin-state-aware rendering inputs.
- Creates a reviewable path for future HUD refactors without reopening the current rendering regressions.
- Establishes a review standard for future HUD changes: host code may improve extensibility, but HUD-specific behavior must land in plugin-facing contracts and plugin-owned code.
- Establishes a delivery constraint for follow-up work: architecture must converge toward the plugin-boundary design without regressing the currently accepted HUD behavior already demonstrated in manual validation.
