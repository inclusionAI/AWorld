# Reminder-To-Cron Routing Design

## Goal

Make Aworld prefer `cron` for natural-language reminder and future-trigger requests such as "1 minute later remind me to drink water", while adding a narrow terminal guard that blocks shell-based fake reminders like `sleep 60 && echo ...`.

## Scope

This design covers:

- Aworld system prompt and tool-usage policy for reminder-style requests
- A narrow terminal-command guard that rejects shell-based delayed reminders
- Tests that lock in the desired prompt policy and guard behavior

This design does not cover:

- Input-side intent classification or hard routing before the model runs
- Scheduler core redesign
- Broad shell safety policy changes unrelated to reminder behavior

## Problem Statement

Aworld already has a usable `cron` capability:

- [aworld/tools/cron_tool.py](/Users/wuman/Documents/workspace/aworld-mas/aworld/aworld/tools/cron_tool.py)
- [aworld/core/scheduler/scheduler.py](/Users/wuman/Documents/workspace/aworld-mas/aworld/aworld/core/scheduler/scheduler.py)
- [aworld-cli/src/aworld_cli/runtime/base.py](/Users/wuman/Documents/workspace/aworld-mas/aworld/aworld-cli/src/aworld_cli/runtime/base.py)

But natural-language reminder requests can still be solved incorrectly with foreground shell commands such as `sleep 60 && echo "reminder"`. That behavior is wrong for three reasons:

1. It blocks the current execution chain instead of creating a scheduled task and returning immediately.
2. It is unreliable across process exit, session switching, or workspace changes.
3. It bypasses the productized scheduler and notification path that already exists.

The desired behavior is that reminder-like future actions are model-selected and executed through `cron`, not by shell delay simulation.

## Requirements

### Functional Requirements

1. Aworld should treat reminder-like future requests as a `cron` use case.
2. Aworld should explicitly avoid using terminal `sleep`, foreground waiting, or temp-file echo flows to implement reminders.
3. One-time reminder requests should still be model-driven, not pre-routed by a deterministic input parser.
4. Shell commands that clearly simulate delayed reminders should be rejected before execution and redirected toward `cron`.
5. Ordinary shell usage, including short `sleep` for development workflows, must continue to work.

### Non-Functional Requirements

1. The solution should follow the existing Aworld prompt/tool policy structure.
2. The guard must be narrow enough to avoid harming normal terminal workflows.
3. The implementation must be testable without requiring a live scheduler wait in end-to-end time.

## Design Overview

The solution has two layers:

### Layer 1: Prompt-Led Tool Strategy

Update Aworld's system guidance so the model learns a clear policy:

- Requests about reminders, delayed execution, future-time triggers, and recurring reminders should prefer `cron`.
- For one-time reminders, the model should create a scheduled task and acknowledge immediately.
- For recurring reminders, the model should use recurring `cron` scheduling.
- The model must not use terminal `sleep`, foreground wait loops, temp-file polling, or similar shell hacks to fake reminder behavior.

This keeps intent resolution inside the model, which matches the user requirement.

### Layer 2: Narrow Terminal Guard

Add a lightweight guard in the terminal execution path to catch the known failure mode:

- The command contains explicit delay behavior such as `sleep <seconds>`
- The same command chain also contains obvious reminder semantics such as `提醒`, `remind`, `reminder`, `喝水`, or similar reminder wording
- The command is using shell output/file-writing behavior to emit the delayed reminder rather than performing ordinary development work

When matched, the command should not execute. The tool should return a compact guidance message telling the model that delayed reminders must be implemented with `cron`.

This keeps the model in control while preventing the most common incorrect execution pattern.

## Components

### 1. Aworld Prompt Policy

Primary file:

- [aworld-cli/src/aworld_cli/inner_plugins/smllc/agents/aworld_agent.py](/Users/wuman/Documents/workspace/aworld-mas/aworld/aworld-cli/src/aworld_cli/inner_plugins/smllc/agents/aworld_agent.py)

Change:

- Extend the Aworld system instructions with a "scheduled/reminder requests" policy section.

Expected guidance language:

- Use `cron` for future reminders and recurring reminders.
- Acknowledge after creating the schedule; do not wait until trigger time.
- Do not use `sleep`, foreground bash waiting, or temp files to simulate a reminder.

### 2. Terminal Guard

Primary target:

- [examples/gaia/mcp_collections/tools/terminal.py](/Users/wuman/Documents/workspace/aworld-mas/aworld/examples/gaia/mcp_collections/tools/terminal.py)
- Specifically, the command validation/execution path used by `mcp_execute_command`

Implementation shape:

- Add a small helper responsible for detecting shell-based delayed reminder simulation.
- Run that helper before command execution.
- If matched, return a tool-level failure result with a message instructing the model to use `cron`.

Guard matching should require both:

1. Delay pattern
   - `sleep <N>`

2. Reminder simulation pattern
   - reminder-oriented text in the command body, or
   - obvious delayed notification output such as chained `echo`, `cat`, or file write used to surface the reminder

The intent is not to detect every future reminder phrasing in shell, only to stop the bad pattern we already observed.

### 3. Tests

Test areas:

- Prompt-policy coverage test
- Guard positive-match tests
- Guard negative-match tests

## Data Flow

### Successful Reminder Path

1. User asks in natural language for a future reminder.
2. Aworld reads system guidance that this is a `cron` use case.
3. Aworld calls `cron` to create a one-time or recurring task.
4. Aworld returns immediately with a confirmation message.
5. Scheduler later executes and notification flow surfaces the reminder.

### Guarded Failure Path

1. User asks for a future reminder.
2. Model incorrectly attempts terminal-based delay simulation.
3. Terminal guard detects `sleep` + reminder simulation pattern.
4. Command is rejected before execution.
5. Model receives feedback that reminder scheduling must use `cron`.
6. Model retries with `cron`.

## Guard Rules

The guard should match only when all of the following are true:

1. The command includes an explicit delay token matching `sleep <integer-or-float>`.
2. The command also includes evidence of reminder simulation:
   - reminder keywords such as `提醒`, `remind`, `reminder`
   - or common reminder content patterns such as `喝水`, `该.*了`
3. The command is chaining delayed output behavior, for example:
   - `&& echo ...`
   - `; echo ...`
   - `> /tmp/...`
   - `&& cat ...`

This deliberately does not block:

- `sleep 1`
- `sleep 1 && echo done`
- ordinary build/test scripts that happen to sleep but do not simulate reminders

## Error Handling

### Prompt Layer

No special runtime behavior is required beyond clear instruction text. If the model follows the prompt, it will choose `cron`.

### Guard Layer

When the guard matches:

- Do not execute the command.
- Return a deterministic, concise message.
- The message should explain that future reminders must be created with `cron`, not shell waiting.

Suggested response shape:

- execution blocked
- reason: delayed reminder simulation is not allowed
- next action: use `cron` to create a scheduled reminder

## Testing Strategy

### Prompt Policy Test

Verify that Aworld's prompt or configuration contains the scheduled-reminder policy text, including:

- reminder/future-time requests should use `cron`
- shell `sleep`/foreground waiting must not be used for reminders

This should be a stable unit test against the generated prompt content or a helper that builds it.

### Guard Positive Tests

Commands that must be blocked:

- `sleep 60 && echo "提醒我喝水"`
- `sleep 300; echo "reminder"`
- `sleep 60 && echo "⏰ 该喝水了" > /tmp/x`

Expected result:

- command does not run
- tool returns the cron guidance message

### Guard Negative Tests

Commands that must still run:

- `sleep 1`
- `sleep 1 && echo done`
- non-reminder shell workflows with delay

Expected result:

- guard does not trigger

## Risks And Mitigations

### Risk: Guard Too Broad

If the guard is too broad, it may block legitimate terminal workflows.

Mitigation:

- Require both delay and reminder simulation patterns.
- Keep keyword set narrow.
- Add negative tests for ordinary shell usage.

### Risk: Prompt Text Alone Is Ignored

Models can still try the wrong tool path.

Mitigation:

- Add the terminal guard so the most obvious incorrect path self-corrects.

### Risk: Guard Message Is Too Vague

If the rejection text is vague, the model may retry a different shell hack.

Mitigation:

- Return explicit guidance that the correct mechanism is `cron`.

## Implementation Notes

The implementation should prefer adding a small reusable helper for guard matching rather than embedding ad hoc string checks directly into a large executor method. That keeps the behavior focused and independently testable.

The solution should also preserve the current model-driven flow: the model chooses to use `cron`; the system does not rewrite user input into `/cron` commands before reasoning.

## Acceptance Criteria

1. Aworld system guidance explicitly says that reminder-like future requests should use `cron`.
2. Aworld guidance explicitly forbids shell-based delayed reminder simulation.
3. Shell commands like `sleep 60 && echo "提醒我喝水"` are blocked with a cron guidance message.
4. Ordinary shell commands with `sleep` but without reminder semantics still run.
5. Tests cover both prompt policy and guard behavior.
