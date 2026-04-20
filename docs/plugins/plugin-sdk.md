# AWorld Plugin SDK

This document describes the current host-supported contract for framework plugins.

## Manifest Contract

- Canonical manifest path: `plugin_root/.aworld-plugin/plugin.json`
- JSON Schema: [aworld/plugins/plugin.schema.json](/Users/wuman/Documents/workspace/aworld/aworld/plugins/plugin.schema.json)
- Required fields:
  - `id`
  - `version`
- Common optional fields:
  - `name`
  - `activation_scope`
  - `entrypoints`
  - `dependencies`
  - `conflicts`

## Validation

Validate an installed plugin by name:

```bash
aworld-cli plugins validate aworld-hud
```

Validate an arbitrary plugin root on disk:

```bash
aworld-cli plugins validate --path /path/to/plugin
```

Validate an installed plugin from inside the interactive CLI:

```text
/plugins validate aworld-hud
```

Validation currently checks:

- manifest shape can be loaded
- entrypoint targets resolve inside the plugin root
- legacy plugin roots still expose `agents/` or `skills/`

## Activation Resolution

At runtime, manifest-declared activation metadata is now consumed:

- `dependencies`: a plugin is skipped unless all declared plugin IDs are already active
- `conflicts`: a plugin is skipped if it conflicts with any already active plugin ID

Resolution is deterministic and follows plugin discovery order.

## Public Capability Surface

Host-owned plugin capability helpers live under `aworld_cli.plugin_capabilities`.

Current plugin-safe HUD helper API:

- `aworld_cli.plugin_capabilities.hud_helpers.format_hud_tokens`
- `aworld_cli.plugin_capabilities.hud_helpers.format_hud_elapsed`
- `aworld_cli.plugin_capabilities.hud_helpers.format_hud_context_bar`

Current plugin-safe hook typing API:

- `aworld_cli.plugin_capabilities.hooks.StopHookEvent`
- `aworld_cli.plugin_capabilities.hooks.TaskStartedHookEvent`
- `aworld_cli.plugin_capabilities.hooks.TaskProgressHookEvent`
- `aworld_cli.plugin_capabilities.hooks.TaskCompletedHookEvent`
- `aworld_cli.plugin_capabilities.hooks.TaskErrorHookEvent`
- `aworld_cli.plugin_capabilities.hooks.TaskInterruptedHookEvent`
- `aworld_cli.plugin_capabilities.hooks.HookEventPayload`

## Hook State Contract

Hook handlers receive:

- `event`: host-owned event payload for the current hook point
- `state`: plugin-scoped persisted state plus host-owned identifiers like `session_id`, `workspace_path`, `task_id`

Writable plugin state is exposed through:

- `state["__plugin_state__"].read()`
- `state["__plugin_state__"].write(payload)`
- `state["__plugin_state__"].update(payload)`
- `state["__plugin_state__"].clear()`

Current task lifecycle hook points available to plugins:

- `task_started`
- `task_progress`
- `task_completed`
- `task_error`
- `task_interrupted`
- `stop`

Event payload fields currently emitted by the host:

- `stop`
  - `transcript_path`
  - `workspace_path`
  - `session_id`
  - `task_id`
- `task_started`
  - `task_id`
  - `session_id`
  - `workspace_path`
  - `message`
- `task_progress`
  - `task_id`
  - `session_id`
  - `workspace_path`
  - `current_tool`
  - `elapsed_seconds`
  - `usage`
- `task_completed`
  - `task_id`
  - `session_id`
  - `workspace_path`
  - `task_status`
  - `final_answer`
- `task_error`
  - `task_id`
  - `session_id`
  - `workspace_path`
  - `task_status`
  - `error`
  - `error_type`
- `task_interrupted`
  - `task_id`
  - `session_id`
  - `workspace_path`
  - `task_status`
  - `partial_answer`

## HUD Contract

HUD providers are plugin-owned. The host only collects and renders lines.

Supported provider signatures:

- `render_lines(context)`
- `render_lines(context, plugin_state)`

`context` is host-owned runtime data. `plugin_state` is plugin-owned state read from the plugin state store.
