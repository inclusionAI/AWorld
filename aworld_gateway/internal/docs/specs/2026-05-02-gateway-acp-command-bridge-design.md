# Gateway / ACP Command Bridge Design

## Goal

Add a shared command bridge so `aworld-cli` commands can be triggered from:

- gateway channels such as `wechat` and `dingtalk`
- ACP prompt requests

The bridge must support the existing `aworld-cli` command ecosystem instead of hardcoding `/memory` or `/cron`.

## Scope

Included:

- shared bootstrap for built-in slash commands and plugin-backed commands
- shared slash-command parsing and execution for `tool` commands
- gateway integration for channel text inputs that begin with `/`
- ACP integration for prompt text that begins with `/`
- tests covering bootstrap, dispatch, gateway routing bypass, and ACP execution

Excluded in phase one:

- `prompt`-type slash commands such as `/review`, `/commit`, and `/diff`
- non-slash alternate trigger syntaxes
- rich interactive CLI-only behaviors that require a terminal UI

## Problem

The current repository has two different sources of commands:

- built-in commands registered by importing `aworld_cli.commands`
- plugin-backed commands registered through `sync_plugin_commands(...)`

The interactive CLI wires both of these together during runtime startup, but gateway channels and ACP do not. As a result:

- `/cron` is not available as a shared reusable channel capability
- `/memory` is not available because plugin command bootstrap never runs
- each new channel would otherwise need to reimplement command parsing and execution

## Architecture

Add a shared module under `aworld-cli/src/aworld_cli/core/command_bridge.py`.

### 1. Registry Bootstrap

The bridge owns a reusable bootstrap step that:

- imports `aworld_cli.commands` to register built-in commands
- discovers runtime plugin roots through `PluginManager`
- resolves active plugins
- synchronizes plugin-backed commands into `CommandRegistry`

The bootstrap result should be reusable by gateway and ACP without requiring a full interactive CLI runtime.

### 2. Dispatch Surface

The bridge exposes a small async API:

- detect whether an input is a slash command
- resolve the command from `CommandRegistry`
- reject unsupported command types in phase one
- create `CommandContext`
- execute the command and return a structured result

Suggested result model:

- `handled: bool`
- `command_name: str | None`
- `status: "completed" | "unsupported" | "unknown"`
- `text: str`

### 3. Runtime Adapter

Some commands expect runtime-like attributes on `CommandContext.runtime`, especially for workspace/plugin state.

The bridge should provide a lightweight runtime adapter that supplies:

- `session_id`
- workspace-scoped plugin state store under `<cwd>/.aworld/plugin_state`
- `_resolve_plugin_state_path(...)`

This keeps plugin-backed workspace commands working without booting the full chat runtime.

## Gateway Integration

Gateway connectors should intercept slash input before calling `router.handle_inbound(...)`.

Flow:

1. inbound channel text is normalized
2. if text begins with `/`, call the shared command bridge
3. if the bridge reports `handled=True`, send the returned text back to the channel and skip agent routing
4. otherwise preserve the existing agent routing path

Phase one should wire this behavior into:

- `wechat`
- `dingtalk`

The shared bridge keeps future channels such as `wecom` and `feishu` straightforward.

## ACP Integration

ACP should intercept normalized prompt text before creating the normal streamed runner turn.

Flow:

1. `_handle_prompt(...)` normalizes prompt text
2. if the prompt is a slash command, execute it through the shared bridge
3. emit a normal ACP `agent_message_chunk` session update containing the command result text
4. return `{"status": "completed"}`
5. skip agent execution for that turn

This keeps ACP behavior deterministic and aligned with CLI command semantics.

## Error Handling

- unknown slash command: preserve current behavior as a user-visible error string from the bridge
- unsupported phase-one command type (`prompt`): return a clear message that the command is not yet supported in gateway/ACP bridge mode
- bootstrap failures: degrade safely with a visible error result instead of crashing channel workers
- command exceptions: catch and return a plain-text failure message

## Testing

Add coverage for:

- bootstrap loading built-in and plugin-backed commands
- execution of a built-in tool command
- execution of a plugin-backed tool command
- rejection of a prompt command in bridge mode
- `wechat` slash input bypassing router and returning bridge output
- `dingtalk` slash input bypassing bridge/agent path and returning bridge output
- ACP slash prompt returning a session update without invoking the output bridge

## Success Criteria

- `/cron ...` works from gateway channels through the shared bridge
- `/memory ...` works from gateway channels through the shared bridge
- ACP prompt requests can execute the same shared command path
- gateway and ACP share one bootstrap/dispatch implementation instead of channel-local special cases
