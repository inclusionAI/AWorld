# Active Steering Terminal Redesign

**Status:** Proposed and validated with user in-session

**Scope:** `aworld-cli` local interactive sessions only, and only while a task is actively running inside the current steering loop.

**Out of Scope:**
- Ordinary idle chat prompt rendering
- ACP transport and aworld channel transport
- Full-screen TUI rewrite
- Changes to aworld framework context management

## Goal

Make active steering usable in practice by separating:
- committed output history
- a single lightweight runtime status line
- a stable bottom input prompt

The redesign should remove the current mixed rendering pattern where Rich status, Live streaming, hook logs, tool output, prompt echo, and patch stdout all contend for the same terminal surface.

## Problem Summary

The current active steering experience is unreliable even after the earlier stabilization work:
- users do not have a clear place to type while a task is running
- executor-side stream rendering still competes with prompt rendering
- transient logs such as `Running task`, `Thinking`, and hook debug output visually pollute the same area as user input
- ANSI control remnants and partial redraw artifacts still leak into the visible transcript

The usability issue is not primarily a style problem. It is an ownership problem: multiple layers believe they can write directly to the active terminal surface during a running task.

## Reference Interaction Model

The target interaction is intentionally close to the reference screenshot the user provided:
- a stable historical transcript area
- a single bottom input line that remains obviously interactive
- one compact runtime status line such as `Working (15s • type to steer • Esc to interrupt)`
- no token-level streaming into the shared terminal surface during active steering

This is not a request to clone the screenshot exactly. It is a request to adopt the same interaction contract.

## Chosen Direction

Use a dedicated active-steering interaction mode with a single prompt owner.

During active steering:
- `prompt_toolkit` remains the only input owner
- executor runtime no longer writes token-streaming output directly into the terminal transcript
- executor output is converted into committed display events
- the CLI appends only committed blocks into transcript history
- transient progress information is compressed into a single status line

This is effectively a bounded redesign of the active steering surface, not a global terminal architecture rewrite.

## Rejected Alternatives

### 1. Keep patching the current `Live + patch_stdout + prompt_async` stack

Rejected because it keeps the same core flaw: multiple render owners competing over one surface. It may reduce visible bugs temporarily, but it does not produce a trustworthy input affordance.

### 2. Full aworld-cli terminal renderer rewrite

Rejected for now because it would expand into ordinary chat mode, remote mode, ACP, plugin rendering, and HUD unification. That is a larger product effort and not necessary to make active steering usable.

## Interaction Contract

When a local task is running in active steering mode, the terminal surface has exactly three layers:

### 1. Transcript History

A stable append-only region containing committed blocks only.

Allowed block kinds:
- `assistant_message`
- `tool_calls`
- `tool_result`
- `system_notice`
- `error`

Examples:
- complete assistant reply paragraph
- a summarized tool call block
- a summarized or full tool result block
- `Steering queued for the next checkpoint.`
- `Queued steering applied at checkpoint.`
- interrupt accepted / task interrupted / task failed

### 2. Runtime Status Line

A single ephemeral line describing current runtime state.

Examples:
- `Working (15s • type to steer • Esc to interrupt)`
- `Calling bash`
- `Applying queued steering at checkpoint`

This line is allowed to update in place. It is not part of transcript history unless explicitly promoted into a committed block.

### 3. Bottom Prompt

A single stable input line, always visible, always clearly interactive.

The prompt remains available for:
- natural steering text
- `Esc` interrupt
- `/interrupt` fallback

## Visibility Rules

### Collapse Into Status Line

These should not be appended into transcript history during active steering:
- `Running task: ...`
- `Thinking...`
- file parse hook progress lines
- low-level executor phase transitions
- transient handoff/loading messages

They should instead become short status updates.

### Commit Into Transcript History

These should be appended as durable blocks:
- final assistant content chunks once they are complete enough to read as a block
- tool call summaries
- tool result summaries or full structured blocks
- steering acknowledgements
- steering checkpoint application notices
- interrupt accepted / interrupted / completed / failed

## Streaming Policy

For active steering mode only:
- disable token-by-token terminal streaming
- continue collecting stream events internally
- aggregate them into committed blocks
- append a block only when it reaches a natural boundary

Natural boundaries:
- a `MessageOutput`
- a tool-call emission that can be summarized coherently
- a `ToolResultOutput`
- a task completion or failure

This means the executor still consumes streamed events for logic, stats, and HUD updates, but the terminal transcript receives block-level output instead of raw stream rendering.

## Minimal Architecture Change

Keep the implementation bounded to `aworld-cli` local interactive steering.

### Console Responsibilities

`AWorldCLI` should own:
- active steering transcript buffer
- active steering status line text
- committed block append operations
- the prompt session loop

The console should stop trying to coexist with executor-owned live terminal streaming during active steering.

### Executor Responsibilities

`LocalAgentExecutor` should produce active-steering-safe display events instead of directly rendering streaming content when the active steering mode is enabled.

Expected event categories:
- `status_changed`
- `message_committed`
- `tool_calls_committed`
- `tool_result_committed`
- `system_notice`
- `error`

The executor should still:
- collect token stats
- update HUD/plugin hooks
- retain normal rendering behavior outside active steering mode

### Stream Controller Responsibilities

The existing `StreamDisplayController` remains useful for ordinary streaming mode, but active steering should stop depending on its `Live` terminal rendering path.

That means:
- no `Live` ownership in active steering mode
- no token-level console writes in active steering mode
- no reliance on `patch_stdout` as the primary correctness mechanism

## File-Level Impact

### `aworld-cli/src/aworld_cli/console.py`

Primary integration point.

Needed changes:
- introduce an active-steering transcript/session view model
- render only prompt + status line during active steering input
- append committed blocks into history in display order
- route user steering acknowledgements into transcript blocks

### `aworld-cli/src/aworld_cli/executors/local.py`

Needed changes:
- branch active steering mode away from direct stream rendering
- emit committed display events instead of printing/interleaving stream output
- compress internal progress messages into status updates

### `aworld-cli/src/aworld_cli/executors/stream.py`

Likely changes:
- preserve the current streaming helpers for non-active-steering mode
- optionally extract a smaller block-aggregation helper reusable by active steering mode

### `aworld-cli/src/aworld_cli/executors/file_parse_hook.py`

Needed change:
- during active steering mode, avoid direct console progress output
- expose brief status text instead

### Tests

Primary test targets:
- `tests/test_interactive_steering.py`
- `tests/executors/test_stream.py`
- possibly a new focused test file for active steering terminal presentation logic

## Plugin and ACP Compatibility

This redesign is intentionally local-first.

Short-term rule:
- only the local interactive CLI steering surface changes now

Compatibility constraint for later:
- the display/event model should not bake in local-only semantics so deeply that ACP or aworld channels cannot consume the same committed event categories later

This should be documented as a compatibility constraint, not implemented immediately.

## Success Criteria

The redesign is successful when all of the following are true in a real terminal run:
- the user can always tell where to type during an active task
- mid-task natural input can be entered without confusion
- the transcript contains readable committed blocks instead of redraw fragments
- `Esc` interrupt remains usable
- internal progress noise no longer floods transcript history
- ordinary non-active-steering chat behavior is unchanged

## Failure Modes To Avoid

- accidentally changing ordinary chat rendering
- breaking token statistics or HUD updates while muting terminal streaming
- dropping tool result visibility entirely
- buffering so aggressively that long-running work appears frozen
- introducing a second terminal rendering owner through another helper

## Delivery Strategy

Implement in two bounded phases inside the current branch:

### Phase 1

Introduce the active steering presentation model:
- single status line
- bottom prompt
- committed transcript blocks

Keep formatting simple; correctness and readability matter more than polish.

### Phase 2

Refine block formatting:
- clearer tool call blocks
- clearer tool result blocks
- better status wording
- suppression of noisy internal logs

Only do Phase 2 after Phase 1 is stable in a real terminal run.

## Validation Plan

### Automated

- add tests for active steering transcript commit behavior
- add tests ensuring active steering does not invoke live stream rendering
- add tests that status-only messages do not enter committed transcript history
- preserve current steering queue/application observability tests

### Manual

Run a real local interactive session and verify:
- prompt stays visually stable while task runs
- entering natural steering text is obvious
- history grows in readable blocks
- no repeated ANSI fragments appear
- `Esc` interrupt still works

## Open Questions Resolved In-Session

- natural input during task execution should default to queued steering
- only explicit interrupt should cut execution
- `Esc` is preferred over a typed interrupt command
- scope is limited to `aworld-cli` local interaction for now
- active steering should use block-level transcript commits instead of live token streaming
- low-level runtime logs should be folded into a compact status line rather than printed verbatim
