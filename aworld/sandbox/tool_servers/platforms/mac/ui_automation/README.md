# macOS UI Automation MCP Server

Host-local macOS UI automation capability for AWorld. Phase 1 uses a thin
Peekaboo CLI backend and exposes a stable AWorld-owned action surface over MCP.

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

## Runtime Requirements

- macOS host
- Peekaboo CLI installed and available on `PATH`
- Accessibility and Screen Recording permission granted to the host process

## Status

This package currently defines the shared phase-1 contract. Backend execution and
agent integration are implemented in follow-up tasks within the same change.
