# AWorld Plugin SDK

This document describes the current host-supported contract for framework plugins.

## Manifest Contract

- Canonical manifest path: `plugin_root/.aworld-plugin/plugin.json`
- JSON schema file: `aworld/plugins/plugin.schema.json`
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

If you only need to manage installed plugins from the interactive session, see [Plugins](../Commands/Plugins.md).

## Activation Resolution

At runtime, manifest-declared activation metadata is now consumed:

- `dependencies`: a plugin is skipped unless all declared plugin IDs are already active
- `conflicts`: a plugin is skipped if it conflicts with any already active plugin ID

Resolution is deterministic and follows plugin discovery order.

## Public Capability Surface

Host-owned plugin capability helpers live under `aworld_cli.plugin_capabilities`.
Legacy alias namespaces `aworld_cli.plugin_runtime.*` and `aworld_cli.plugin_framework.*` have been removed.

## Interactive Slash Command Contract

Framework plugins may contribute interactive slash commands through manifest `entrypoints.commands`.

Minimal markdown-backed manifest example:

```json
{
  "id": "example-command-plugin",
  "version": "1.0.0",
  "entrypoints": {
    "commands": [
      {
        "id": "review-loop",
        "name": "review-loop",
        "target": "commands/review-loop.md",
        "scope": "workspace"
      }
    ]
  }
}
```

Markdown-backed command behavior:

- the target file is loaded as prompt text
- the CLI appends `User args: ...`
- the generated prompt is executed through the active agent session

Framework plugins may also contribute Python-backed interactive commands.

Minimal Python-backed manifest example:

```json
{
  "id": "example-command-plugin",
  "version": "1.0.0",
  "entrypoints": {
    "commands": [
      {
        "id": "ralph-loop",
        "name": "ralph-loop",
        "target": "commands/ralph_loop.py",
        "scope": "session",
        "metadata": {
          "factory": "build_command"
        }
      }
    ]
  }
}
```

Python target module contract:

- default factory name is `build_command`
- the factory is called as `build_command(plugin, entrypoint)`
- the factory must return an `aworld_cli.core.command_system.Command` instance

Useful host integration points for Python-backed commands:

- `CommandContext.cwd`
- `CommandContext.user_args`
- `CommandContext.runtime`
- `CommandContext.session_id`

If a command needs access to plugin-scoped persisted state, it should resolve state through the runtime-owned plugin state store rather than creating ad hoc dotfiles.

## CLI Command Contract

Framework plugins may contribute top-level CLI commands through manifest `entrypoints.cli_commands`.

Minimal manifest example:

```json
{
  "id": "example-cli-plugin",
  "version": "1.0.0",
  "entrypoints": {
    "cli_commands": [
      {
        "id": "example",
        "name": "example",
        "target": "cli_commands/example.py",
        "metadata": {
          "factory": "build_command",
          "aliases": ["example-short"]
        }
      }
    ]
  }
}
```

Target module contract:

- default factory name is `build_command`
- the factory must return a `TopLevelCommand`-compatible object
- alternatively, the module may expose a `COMMAND` object directly

Command object requirements:

- `name`
- `description`
- `register_parser(subparsers)`
- `run(args, context)`

Current bootstrap behavior:

- only enabled framework plugins contribute top-level CLI commands
- `metadata.aliases` registers additional top-level command aliases
- aliases are normalized to the canonical command name before `argparse` dispatch
- `visibility: "hidden"` suppresses registration
- reserved kernel command names cannot be overridden by plugins

Reserved top-level kernel commands:

- `interactive`
- `list`
- `serve`
- `batch`
- `batch-job`
- `plugins`
- `gateway`

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
