# Review Notes

## Validation

- `openspec validate hud-plugin-boundary`
  - Result: valid

## Review Feedback Closure

The primary review concerns were:

1. plugin-scoped state was read-only and lacked a write-back path
2. live HUD-capable plugins could not observe task lifecycle events generically
3. HUD providers only accepted `render_lines(context)` and therefore could not consume plugin-owned state directly
4. built-in HUD formatting still depended on private host internals
5. the naming boundary between host-owned capability support and plugin-owned code remained unclear

## Resolution Status

### 1. Plugin state write-back

Addressed.

- Added explicit plugin state handles with `read`, `write`, `update`, and `clear`
- Runtime hook state now exposes `__plugin_state__` for scoped persistence
- Tests cover persistence and clear semantics

### 2. Task lifecycle hooks

Addressed.

- Added generic lifecycle hook support for:
  - `task_started`
  - `task_progress`
  - `task_completed`
  - `task_error`
- Local executor now delegates these events through runtime-owned hook execution paths

### 3. HUD provider contract

Addressed.

- HUD providers now support `render_lines(context, plugin_state)`
- Compatibility is preserved for older `render_lines(context)` providers
- End-to-end tests prove hook-driven plugin state feeds HUD rendering through the generic contract

### 4. Plugin-facing HUD helper boundary

Addressed.

- Introduced `aworld_cli.plugin_capabilities.hud_helpers`
- Built-in `aworld-hud` now consumes the explicit helper boundary instead of importing private executor formatting helpers directly

### 5. Host/plugin naming boundary

Addressed for the active implementation path.

- Real host-owned capability support now lives under `aworld_cli.plugin_capabilities.*`
- `plugin_runtime.*` and `plugin_framework.*` remain compatibility re-export layers only
- Internal callers and non-compat tests were moved to `plugin_capabilities.*`

## Residual Items

### Manual validation

Still pending.

- OpenSpec task `4.4` remains open because the accepted HUD baseline must still be confirmed manually in `aworld-cli`
- A manual validation attempt was run through the agent PTY environment with `aworld-cli --no-banner`
- That environment reported `WARNING: your terminal doesn't support cursor position requests (CPR)`
- Subsequent prompt/input rendering became unreliable, so the session is not a trustworthy substitute for a real interactive terminal baseline
- Accepted screenshot-based HUD validation should therefore still be performed in a real user terminal session before closing `4.4`

### Compatibility alias removal

Addressed.

- `aworld_cli.plugin_runtime.*` compatibility re-exports were removed
- `aworld_cli.plugin_framework.*` compatibility re-exports were removed
- in-repo imports and tests now target canonical plugin paths only

## Outcome

The review feedback is now reflected in both implementation and OpenSpec documentation.

What changed:

- the functional plugin contract gaps identified in review are closed
- the built-in HUD now exercises the same generic contract as a third-party HUD plugin
- the host-owned implementation path is named and documented as `plugin_capabilities`

What remains:

- manual CLI validation against the accepted HUD screenshots
