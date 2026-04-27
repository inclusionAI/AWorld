# macOS UI Automation MCP Server

Host-local macOS UI automation capability for AWorld. Phase 1 uses a thin
Peekaboo CLI backend and exposes a stable AWorld-owned action surface over MCP.

This capability is host-local only. It is intended to let an AWorld-based agent
operate macOS applications on the same machine where the agent process is
running. It does not include remote-Mac control, companion apps, or
PeekabooBridge host ownership.

## Phase 1 Actions

- `permissions`
- `list_apps`
- `launch_app`
- `list_windows`
- `focus_window`
- `see`
- `click`
- `type`
- `press`
- `scroll`

## Opt-In

Enable the capability explicitly:

```bash
export AWORLD_ENABLE_MAC_UI_AUTOMATION=1
export AWORLD_MAC_UI_AUTOMATION_BACKEND=peekaboo_cli
```

The built-in `aworld-cli` `aworld` agent stays unchanged unless the gate above
is set. When enabled, it opts into the shared `mac_ui_automation` server rather
than embedding any macOS-specific behavior directly in the agent.

## Runtime Requirements

- macOS host
- Peekaboo CLI installed and available on `PATH`
- Accessibility and Screen Recording permission granted to the host process
- `AWORLD_ENABLE_MAC_UI_AUTOMATION=1`
- optional backend selector `AWORLD_MAC_UI_AUTOMATION_BACKEND=peekaboo_cli`

## Standard AWorld Reuse

Any AWorld-based agent can reuse the capability through standard MCP
configuration:

- builtin tool path: add `mac_ui_automation` to `builtin_tools`
- agent enablement path: include `mac_ui_automation` in the agent's
  `mcp_servers`
- explicit MCP config path: point a stdio server entry at
  `aworld/sandbox/tool_servers/platforms/mac/ui_automation/src/main.py --stdio`

The built-in `aworld` agent is only the first consumer of this shared server.

## Local Startup Flow

1. Install `peekaboo` and confirm it is on `PATH`.
2. Grant Accessibility and Screen Recording to the host process that will run
   AWorld.
3. Export:

```bash
export AWORLD_ENABLE_MAC_UI_AUTOMATION=1
export AWORLD_MAC_UI_AUTOMATION_BACKEND=peekaboo_cli
```

4. Start an AWorld-based agent that includes `mac_ui_automation` in
   `mcp_servers`, or use the built-in `aworld-cli` `aworld` agent with the gate
   enabled.
5. Use `permissions` first, then `list_apps` / `launch_app` / `see` before
   interaction actions.

## Validation Anchor

Phase 1 is anchored on a Xiaoyuzhou validation flow:

- open the host-local Xiaoyuzhou app
- inspect same-day subscribed podcast updates
- collect visible summary information
- return a structured summary to the user

Phase 2 extends that baseline with memory-informed playback selection. See the
OpenSpec validation runbook for the manual verification flow.
