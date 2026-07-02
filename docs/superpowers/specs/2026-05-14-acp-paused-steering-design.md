# ACP Paused Steering Design

## Date
2026-05-14

## Goal

Make ACP treat human/approval boundaries as a resumable paused state instead of a terminal error, while keeping follow-up session input on the same `prompt` path and interpreting it as steering.

## Scope

This design applies only to ACP session execution in `aworld-cli`.

Included:
- ACP `prompt` requests while a turn is running
- ACP behavior when execution hits `AcpRequiresHumanError`
- ACP behavior when execution hits `AWORLD_ACP_APPROVAL_UNSUPPORTED`
- ACP turn pause, resume, steering, and cancel semantics
- ACP compatibility constraints for existing Happy ACP clients

Excluded:
- local terminal active steering UI
- gateway channel protocol changes
- a distinct ACP `human_reply` RPC or input type
- a true approval workflow with structured approve/reject semantics
- executor or handler redesign outside the ACP server orchestration layer

## Problem

ACP currently reports human/approval boundaries as retryable errors. That behavior is technically consistent with the current phase-one implementation, but it is product-incorrect for interactive operator steering.

The operator intent is not “the task failed.” The actual state is:
- the task reached a boundary where autonomous execution should stop
- the session should remain recoverable
- the operator should be able to send more steering and continue

Returning a terminal error forces clients to model a recoverable pause as failure, complicates UX, and breaks the mental model already used for gateway and local steering.

## Product Decision

### 1. Follow-up ACP input remains steering

ACP does not introduce a second input protocol for human replies.

Once a session has an active or paused turn:
- the next same-session `prompt` is interpreted as steering
- steering is queued against the existing session-scoped steering coordinator
- execution resumes from the existing task/session context rather than reinterpreting the operator input as a brand-new independent request

This keeps ACP aligned with the current gateway and local steering model.

### 2. Human/approval boundaries become resumable, not terminal

When ACP execution hits either:
- `AcpRequiresHumanError`
- `AWORLD_ACP_APPROVAL_UNSUPPORTED`

the default protocol behavior becomes:
- emit only ACP update types that the current Happy ACP client already understands
- surface an operator-facing pause message through ordinary agent text output
- avoid returning a terminal JSON-RPC error
- keep the session resumable

This is a pause-for-steering contract, not a hard failure contract.

### 3. Compatibility is preserved via a legacy error mode switch

Default behavior should move to resumable paused steering mode, but ACP should retain a compatibility switch for clients that still depend on the current error contract.

Recommended shape:
- default: paused steering mode enabled
- optional environment variable or ACP server setting enables legacy error behavior

The service should not emit both paused and error for the same boundary. Dual semantics would make client state ambiguous and substantially complicate tests and recovery logic.

### 4. Happy compatibility constrains the wire shape

This release must work with the existing Happy ACP client in `/Users/wuman/Documents/workspace/happy` without requiring Happy-side protocol changes.

That imposes two concrete constraints:
- do not require Happy to understand a new ACP `sessionUpdate` type such as `run_paused`
- do not rely on Happy consuming a `prompt` response status such as `paused`

Therefore, the paused behavior must be expressed using ACP surfaces Happy already handles:
- `agent_message_chunk`
- existing `tool_call` / `tool_call_update`
- the current idle/turn-end completion path

## Runtime Semantics

## Normal running turn

When a turn is actively executing:
- additional same-session `prompt` requests are treated as steering
- ACP queues the steering text
- ACP acknowledges with the existing steering acknowledgment text
- the running turn continues until the next safe checkpoint

This behavior already exists and remains unchanged.

## Paused turn

When a running turn hits a human/approval boundary in paused steering mode:
- the turn enters `paused`
- ACP emits an ordinary `agent_message_chunk` telling the operator that execution is paused and another prompt will continue steering
- ACP closes any open tool lifecycle using existing `tool_call_update` semantics when needed
- ACP does not return a terminal JSON-RPC error in default mode
- the turn is not considered completed or failed
- the session remains resumable

While paused:
- the next same-session `prompt` is treated as steering
- the steering text is queued using the same steering coordinator path used during running turns
- ACP resumes the paused turn rather than starting a new unrelated turn

Only explicit `cancel` ends the paused turn without resuming.

## Cancel semantics

ACP cancel semantics become:

- `running` turn:
  - cancel the active task
  - clear the turn state
  - return `{"status": "cancelled"}`

- `paused` turn:
  - clear paused turn state
  - clear pending steering for that session
  - return `{"status": "cancelled"}`

- no turn:
  - keep existing no-op behavior

## Protocol Design

## Session updates

In the default mode, ACP should not introduce a new update type for pause.

Instead, when a human/approval boundary is hit:
- emit an `agent_message_chunk` with an operator-facing message such as:
  - `Execution paused. Send another prompt to steer the task forward.`
- if a tool lifecycle is open, close it using existing `tool_call_update` statuses that Happy already recognizes
- allow the existing ACP client to reach its normal idle path after the pause message has been emitted

This keeps the wire contract compatible with the current Happy ACP implementation, which already consumes:
- `agent_message_chunk`
- `agent_thought_chunk`
- `tool_call`
- `tool_call_update`

and does not require Happy to learn a new paused event family.

## Prompt response

In the default Happy-compatible mode, the server should avoid terminal JSON-RPC errors for paused boundaries, but the wire contract must not depend on clients understanding a new result status.

Recommended rule:
- the default path is driven by emitted updates plus resumable server-side state
- the implementation may return a non-error result, but client compatibility must not depend on a new `status=paused` value being interpreted
- legacy mode continues returning the current structured errors

## Legacy compatibility mode

When legacy compatibility mode is enabled:
- `AcpRequiresHumanError` continues to map to `AWORLD_ACP_REQUIRES_HUMAN`
- `AWORLD_ACP_APPROVAL_UNSUPPORTED` continues to map to the current structured error response
- no pause message is emitted as a resumable default-path substitute

Paused mode and legacy error mode are mutually exclusive.

## State Machine

ACP turn control should expand from a binary `idle/running` model to a three-state model:

- `idle`
- `running`
- `paused`

### Allowed transitions

- `idle -> running`
  - first prompt starts a new turn

- `running -> paused`
  - execution hits human/approval boundary in paused mode

- `running -> idle`
  - execution completes, fails in non-paused terminal ways, or is cancelled

- `paused -> running`
  - same-session steering prompt arrives and resume begins

- `paused -> idle`
  - explicit cancel

### Rejected transitions

- `paused -> paused` via unrelated new turn creation
  - a new same-session prompt must be interpreted as steering/resume, not as a parallel turn

- `running -> running` via a second independent turn
  - still rejected; same-session prompt remains queued steering

## Implementation Boundaries

This should be implemented primarily in ACP orchestration code:

- `aworld-cli/src/aworld_cli/acp/server.py`
- `aworld-cli/src/aworld_cli/acp/turn_controller.py`

Minimal supporting changes may be needed in:
- `aworld-cli/src/aworld_cli/acp/errors.py`
- ACP validation/self-test fixtures and tests

### Keep unchanged where possible

- `AcpHumanInterceptHandler` should continue raising `AcpRequiresHumanError`
- executor internals should not be redesigned just for this feature
- gateway protocols should not change
- local terminal steering behavior should not change

The ACP server layer should own the translation from runtime boundary to paused/resume protocol behavior.

## Suggested Internal Structure

`TurnController` should explicitly track per-session turn status rather than only task presence.

Recommended responsibilities:
- track whether a session turn is `running`, `paused`, or `idle`
- hold the active task when running
- retain a resumable paused marker when paused
- support “resume existing paused turn” instead of forcing a new turn allocation path

`server.py` responsibilities:
- translate human/approval boundaries into Happy-compatible pause behavior when paused mode is enabled
- keep steering queue behavior consistent across running and paused states
- resume paused execution on the next same-session prompt
- preserve legacy error behavior when compatibility mode is enabled

`event_mapper.py` responsibilities:
- remain unchanged unless a small compatibility mapping is strictly necessary
- not become the owner of paused-turn state logic

## Error Handling Rules

Paused mode only changes the following boundaries:
- `AcpRequiresHumanError`
- `AWORLD_ACP_APPROVAL_UNSUPPORTED`

Other errors keep current behavior:
- invalid session
- busy session in unsupported code paths
- unsupported prompt content
- invalid cwd
- unsupported MCP server requests
- ordinary execution failures and terminal runtime errors

This prevents paused mode from turning all failures into resumable states.

## Testing Requirements

Add or update ACP tests to verify:

1. `AcpRequiresHumanError` emits a normal `agent_message_chunk` pause notice instead of a terminal error in default mode
2. `AWORLD_ACP_APPROVAL_UNSUPPORTED` follows the same Happy-compatible pause path in default mode
3. after paused state, the next same-session `prompt` is queued as steering and resumes execution
4. paused session `cancel` clears the paused turn and returns `cancelled`
5. legacy compatibility mode preserves current structured error behavior
6. default paused mode does not emit both resumable pause output and legacy error for the same boundary
7. tool lifecycle updates remain well-formed when a turn pauses mid-tool sequence
8. paused/resume behavior works in stdio integration tests, not only unit tests
9. the emitted default-mode updates use only ACP event types already consumed by Happy

## Rollout

### Phase 1

- add paused mode implementation
- default paused mode on
- keep legacy error mode behind a switch
- update ACP validation/self-test fixtures to reflect Happy-compatible paused mode by default

### Phase 2

- migrate clients off legacy error mode
- remove the compatibility switch after ACP clients no longer depend on the old contract

## Non-Goals

This design does not implement:
- structured approve/reject RPCs
- resumable human conversation transcripts as a first-class ACP input type
- approval object persistence
- gateway or terminal protocol unification
- generalized workflow pausing for arbitrary runtime conditions

## Success Criteria

The design is successful when:
- ACP clients can model human/approval boundaries as pause, not failure
- the next same-session `prompt` continues to mean steering
- the paused session resumes without inventing a new input protocol
- the default wire behavior works with the current Happy ACP client without Happy-side code changes
- legacy ACP clients can temporarily retain old behavior behind a switch
