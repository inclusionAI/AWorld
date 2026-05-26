# Goal Session

This document explains the built-in goal-session plugin that owns persisted goal state in AWorld CLI.

## Commands

The plugin exposes a single session-scoped command surface:

```text
/goal "Build a REST API" --verify "pytest tests/api -q" --completion-promise "COMPLETE" --max-turns 5
/goal status
/goal pause
/goal clear
```

Behavior:

- `/goal "..."` starts a new active goal contract and hands it to the current agent session
- `status` prints the current goal contract
- `pause` marks an active goal as paused so the session can exit cleanly
- `clear` removes the stored goal state

## Status Model

The visible states are:

- `active`
- `paused`
- `budget_limited`
- `complete`

Only `active` goals are allowed to continue automatically or block exit.

## Stored Shape

Goal-session tracks a shared contract with fields such as:

- `objective`
- `turn_count`
- `max_turns`
- `verification_commands`
- `completion_promise`
- `source`
- last answer, error, or partial-answer excerpts

The `source` field records where the goal was created. Native `/goal` sessions use `goal`.

## Hook Responsibilities

The plugin hooks are intentionally split:

- `task_completed` updates the turn result and decides whether to continue
- `task_error` records the latest error excerpt
- `task_interrupted` records the latest partial answer excerpt
- `stop` denies exit only when an active goal still exists

This keeps continuation logic out of the stop hook and keeps exit behavior predictable.
