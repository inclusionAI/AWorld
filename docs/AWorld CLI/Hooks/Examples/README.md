# CLI Hook Examples

This directory contains shell hook examples for AWorld CLI. Use them as starting points for auditing, guardrails, output filtering, and session notifications.

## Files

```text
Examples/
├── README.md
├── audit_logger.sh
├── dangerous_command_blocker.sh
├── hooks.yaml.example
├── output_filter.sh
├── path_sanitizer.sh
├── rate_limiter.sh
└── session_notification.sh
```

## Configure Hooks

Create `.aworld/hooks.yaml` in your workspace:

```yaml
version: "v2"
hooks:
  - name: "audit-logger"
    hook_point: "after_tool_call"
    enabled: true
    command: "./docs/AWorld CLI/Hooks/Examples/audit_logger.sh"
    timeout: 5000

  - name: "path-sanitizer"
    hook_point: "before_tool_call"
    enabled: true
    command: "./docs/AWorld CLI/Hooks/Examples/path_sanitizer.sh"
```

For a fuller example, see `hooks.yaml.example` in this directory.

## Hook Environment

Hook scripts can read:

- `AWORLD_SESSION_ID`
- `AWORLD_TASK_ID`
- `AWORLD_CWD`
- `AWORLD_HOOK_POINT`
- `AWORLD_MESSAGE_JSON`
- `AWORLD_CONTEXT_JSON`

## Output Contract

Hook scripts must print JSON to `stdout`. Minimal valid output:

```json
{"continue": true}
```

Common patterns:

```json
{
  "continue": false,
  "stop_reason": "Reason for stopping",
  "permission_decision": "deny"
}
```

```json
{
  "continue": true,
  "updated_input": [{"tool_name": "terminal", "args": {"command": "safe command"}}]
}
```

```json
{
  "continue": true,
  "updated_output": {
    "observation": {"content": "filtered content"}
  }
}
```

## Included Examples

- `audit_logger.sh`: records tool-call activity for later inspection.
- `path_sanitizer.sh`: rewrites suspicious path arguments before execution.
- `output_filter.sh`: redacts sensitive output after execution.
- `dangerous_command_blocker.sh`: blocks obviously unsafe shell commands.
- `rate_limiter.sh`: limits excessive tool-call frequency.
- `session_notification.sh`: sends notifications for session lifecycle events.

## Testing

```bash
export AWORLD_SESSION_ID="test-session-123"
export AWORLD_TASK_ID="test-task-456"
export AWORLD_HOOK_POINT="before_tool_call"
export AWORLD_MESSAGE_JSON='{"category":"tool_call","payload":[{"tool_name":"terminal","args":{"command":"ls"}}]}'

./docs/AWorld\ CLI/Hooks/Examples/audit_logger.sh | jq .
```

## Notes

- Keep hook scripts simple and fast.
- Write diagnostics to `stderr`; reserve `stdout` for JSON only.
- Hook failures should be observable in logs, but they should not silently weaken workspace trust assumptions.
