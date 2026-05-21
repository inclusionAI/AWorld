# Hooks Minimal Fix Design

**Goal:** Repair the current Hooks V2 runtime integration so explicitly loaded config hooks and workspace-bound config hooks are consistently visible to the execution paths that consume them.

**Scope:**
- Fix hook discovery for explicitly loaded config files.
- Fix missing `workspace_path` propagation on runtime hook call sites.
- Preserve existing public hook protocol and existing hook point names.

**Non-Goals:**
- No new hook points.
- No renaming from `session_started` to `session_start` or similar terminology cleanup.
- No redesign of the hook cache model beyond what is required to restore current behavior.

## Root Cause

`HookFactory.hooks()` currently merges config hooks only when the requested workspace resolves to `<workspace>/.aworld/hooks.yaml`. That is correct for strict workspace isolation, but it drops explicitly loaded config files that live outside that convention. Several tests and runtime flows rely on explicit loading followed by `hooks()` or `run_hooks()` without an exact matching workspace path.

Separately, multiple runtime call sites invoke `run_hooks()` without forwarding `context.workspace_path`. Those flows fall back to `os.getcwd()`, so config hooks disappear whenever the executing process cwd differs from the logical workspace.

## Minimal Design

1. Keep strict workspace isolation for standard workspace configs under `.aworld/hooks.yaml`.
2. Add a narrow fallback in `HookFactory.hooks()` for explicitly loaded non-workspace config files:
   - If the requested workspace standard config is not found in cache, and there is exactly one cached config whose path is not a standard `.aworld/hooks.yaml`, merge that cached config.
   - Do not use this fallback when multiple nonstandard configs are loaded.
3. Normalize `run_hooks()` to prefer:
   - explicit `workspace_path` argument,
   - else `context.workspace_path`,
   - else `os.getcwd()`.
4. Update runtime call sites that currently omit `workspace_path` so they consistently pass the logical workspace.

## Files Expected To Change

- `aworld/runners/hook/hook_factory.py`
- `aworld/runners/hook/utils.py`
- `aworld/core/tool/base.py`
- `aworld/runners/event_runner.py`
- Possibly one or two tests if a missing assertion is needed for the new explicit-load fallback.

## Validation

Primary validation is the current failing hook suite:
- `tests/hooks/test_hook_factory.py`
- `tests/hooks/test_legacy_protocol_e2e.py`
- `tests/hooks/test_user_input_gate.py`
- `tests/hooks/test_user_input_gate_e2e.py`
- `tests/hooks/test_tool_gate_simple.py`

Secondary validation:
- targeted workspace isolation and auto-load tests still pass.
