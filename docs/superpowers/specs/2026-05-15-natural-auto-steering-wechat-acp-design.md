# Natural Auto-Steering for WeChat and ACP

## Date
2026-05-15

## Goal

Make same-session follow-up input feel natural in chat-like surfaces by treating new single-session input as steering while a task is still running, without requiring explicit user commands or a separate manual steering control.

The first implementation phase applies this policy to:
- WeChat direct-message sessions
- ACP sessions

The longer-term target is a common inbound steering policy that other channels can adopt.

## Scope

Included:
- natural auto-steering semantics for WeChat DM
- natural auto-steering semantics for ACP session prompts
- silent capture behavior for running-task follow-up input
- reuse of the existing session steering queue, interrupt, checkpoint, and follow-up execution path
- configuration for WeChat auto-steer behavior
- tests covering running-turn, completed-turn, reset-command, and queued-follow-up behavior

Excluded:
- group-chat auto-steering
- new user-visible “steer now” buttons or commands
- frontend or Happy-side protocol changes
- a new separate steering RPC/input type for ACP
- WeCom and other channel implementations in this phase
- redesign of the steering coordinator or executor internals beyond small integration changes

## Problem

Current behavior is technically safe but product-suboptimal for chat-driven use.

For WeChat:
- messages from the same DM session are serialized at the connector layer
- follow-up messages sent while a task is still running do not reach the router/backend immediately
- they therefore cannot enter the steering queue
- users experience them as separate turns only after the previous task ends

For ACP:
- paused turns already support follow-up steering through the next `prompt`
- but a new prompt arriving while a turn is still actively running does not naturally behave like steering
- this makes ACP and chat-based usage feel inconsistent

The resulting UX does not match how users naturally interact in chat:
- they send one request
- they notice something to clarify
- they send one or more short follow-up messages
- they expect those follow-ups to modify the in-flight task, not wait to become unrelated later turns

## Product Decision

### 1. Natural follow-up input becomes steering while a turn is active

When a session already has an active running task:
- new same-session follow-up input is interpreted as steering by default
- the follow-up text is queued into the existing steering coordinator
- execution continues until the next steering checkpoint or terminal fallback follow-up path consumes the queued steering

This behavior should feel automatic and natural.

The user should not need to:
- type a special steering command
- explicitly request “interrupt”
- choose between “new turn” and “steer current turn”

### 2. Natural auto-steering is silent

When input is captured into the steering queue:
- the system must not emit a user-visible steering acknowledgment
- the system must not send extra “received your update” text
- the only visible effect is that subsequent task output reflects the follow-up input

This keeps the experience simple and chat-native.

### 3. Auto-steering only applies while the task is still running

Natural auto-steering only has meaning when the session still has an active running turn.

If the task has already ended:
- the next input must start a normal new turn
- it must not be interpreted as steering against a completed task

This preserves an intuitive boundary:
- before completion: follow-up input modifies the current task
- after completion: follow-up input becomes the next request

### 4. Single-chat direct messages are the default target

To reduce semantic ambiguity and avoid group-chat misfires:
- WeChat DM sessions enable natural auto-steering by default
- group chats do not auto-steer in this phase

This default is intentional:
- DM sessions have a clear single operator
- follow-up intent is much easier to infer
- group chat has much higher risk of accidental steering capture

### 5. Reset/new-session commands still take precedence

Explicit new-session intent must override auto-steering.

For example:
- `/new`
- `/summary`
- `新会话`
- `压缩上下文`

If such an input arrives:
- it must perform the existing new-session/reset behavior
- it must not be queued as steering

### 6. ACP and WeChat should share the same mental model

The user mental model should be:
- if the current task is still running, my next same-session input updates that task
- if it has stopped, my next same-session input starts a fresh turn

ACP and WeChat should therefore behave the same at this semantic level, even if their transport/protocol surfaces differ.

## Runtime Semantics

### Shared session policy

Each supported inbound surface should apply this policy:

#### Auto-steer eligible

Follow-up input is auto-steer eligible only when all of the following are true:
- the session has an active running turn
- the session type is allowed by policy
- the input is ordinary text input
- the input is not an explicit reset/new-session command

#### Auto-steer ineligible

Input must not auto-steer when any of the following are true:
- there is no active turn
- the active turn has already finished
- the session type is disallowed by policy
- the input is a reset/new-session command

### WeChat semantics

#### WeChat DM

For WeChat direct messages:
- while the session has an active running turn, the next input should reach the existing router/backend immediately
- if the backend sees the session is already active, it should queue the input as steering through the existing session steering path
- the connector should swallow the steering acknowledgment instead of replying to the user

Once the running task ends:
- the next DM message becomes a normal new turn again

#### WeChat group

For WeChat group chats in this phase:
- keep current behavior
- do not auto-steer
- maintain serialized/non-auto-steer semantics

### ACP semantics

#### Running turn

For ACP while a turn is actively running:
- the next same-session `prompt` should be treated as steering instead of “busy”
- the input should be enqueued into the existing steering coordinator
- the server should complete the prompt call successfully without emitting an extra user-visible steering acknowledgment
- the running turn should continue until the next checkpoint or follow-up consumption path applies the steering

Silent here means:
- WeChat does not send a chat reply such as `STEERING_CAPTURED_ACK`
- ACP does not introduce a separate steering-status payload that the caller must render to end users

#### Paused turn

Paused-turn behavior remains unchanged:
- the next same-session `prompt` resumes through the existing paused follow-up steering path
- paused state handling must continue to work exactly as it does today

#### Completed turn

If the ACP turn has already finished:
- the next prompt starts a normal new turn
- it must not be attached to old steering state

## User Experience

The desired user experience is:

1. User sends a request.
2. The task starts running.
3. Before the task ends, the user sends one or more short follow-up messages.
4. Those messages are silently captured as steering.
5. The task reaches a checkpoint or follow-up continuation point.
6. The queued follow-up messages are applied as additional operator steering.
7. The user sees later output reflect the updated direction.

The user should not need to reason about:
- “interrupt” versus “new turn”
- steering queue commands
- protocol-specific steering controls

## Configuration

### WeChat

Add a WeChat channel config flag:
- `auto_steer_while_running: bool = True`

Semantics:
- `True`: WeChat DM follow-up input may auto-steer while a turn is active
- `False`: preserve current serialized-next-turn behavior even in DM

Important constraint:
- group-chat auto-steer remains off even if this flag is `True`

### ACP

ACP should enable the same behavior by default in this phase.

An explicit ACP config/env toggle is optional, but not required for phase one.
The implementation may hard-default to enabled if that keeps the ACP surface simpler.

## Implementation Strategy

### 1. Reuse existing steering machinery

Do not build a second steering queue implementation at the channel or ACP entry layer.

Reuse the existing:
- `SteeringCoordinator`
- session interrupt request path
- queued steering checkpoint pause path
- terminal fallback follow-up path
- steering observability logging

This keeps semantics unified and avoids duplicate logic.

### 2. Add a lightweight inbound auto-steer policy layer

Introduce a small integration-layer policy that decides whether new inbound input should:
- start a fresh turn
- enter the active session’s steering queue

This policy layer should not own steering state.
It should only decide whether to route the inbound text toward existing steering handling.

### 3. WeChat integration point

Primary implementation area:
- `aworld_gateway/channels/wechat/connector.py`

Current blocker:
- connector-level per-conversation serialization prevents running-turn follow-up messages from reaching the router in time

Required change:
- allow WeChat DM follow-up input to pass through to the router/backend while a same-session task is still running
- preserve existing serialized behavior for group chats and for DM when auto-steer is disabled

The router/backend already knows how to queue same-session concurrent input as steering.

The connector must additionally:
- recognize the returned steering-ack result
- suppress it so the user sees no extra message

### 4. Router/backend integration point

Primary implementation area:
- `aworld_gateway/router.py`

Expected behavior:
- keep existing same-session active-run detection
- keep existing `_queue_session_steering()` logic
- keep existing checkpoint and follow-up steering application logic

Only minimal changes should be needed, if any.
The main requirement is that upstream callers can use the existing result to implement silent capture behavior.

### 5. ACP integration point

Primary implementation areas:
- `aworld-cli/src/aworld_cli/acp/server.py`
- `aworld-cli/src/aworld_cli/acp/turn_controller.py`

Current blocker:
- a running session does not naturally treat the next prompt as steering

Required behavior:
- paused sessions keep their current paused-resume path
- actively running sessions should no longer reject the next prompt as merely “busy”
- instead, the new prompt text should be queued into the active session steering state
- the ACP server should return success without surfacing a visible steering acknowledgment

This should align ACP with WeChat DM semantics without changing the paused-turn contract.

### 6. Session reset remains explicit

WeChat new-session/reset commands must continue to:
- rotate the effective session binding
- clear stored context tokens as needed
- bypass auto-steering

ACP should likewise treat an explicit fresh-turn mechanism, if introduced later, as stronger than auto-steer.

## Observability

Natural auto-steering must remain debuggable.

The implementation should preserve or emit enough signal to answer:
- was this follow-up input auto-steered or treated as a new turn?
- when was the steering queued?
- when was it applied?
- at which checkpoint was it consumed?

The user-facing path is silent, but the operator/developer path must not be.

Expected observable signals:
- queued steering event
- applied steering event
- active session/task identifiers
- distinction between fresh-turn and steering-follow-up processing

## Risks

### 1. Silent capture can hide system state

Because the product decision is silent mode:
- users do not receive confirmation that follow-up input was captured
- if steering is never applied due to upstream failure, the experience may feel like the follow-up was ignored

This is an accepted trade-off for simplicity, but logs must make diagnosis easy.

### 2. Over-eager auto-steering can misclassify true new turns

The strongest protection in this phase is:
- only enable by default in single-user DM-like contexts
- only while a turn is genuinely still active
- keep explicit reset commands higher priority

### 3. WeChat connector behavior changes are sensitive

Removing or weakening connector serialization for DM must not reintroduce the earlier regression where ordinary consecutive messages were unintentionally reclassified in cases that should remain separate turns.

The new behavior is intentional only while the prior turn is still active.
Once the turn ends, later messages must behave as normal fresh turns.

## Test Matrix

### WeChat

- DM + running turn + second ordinary text:
  - no visible acknowledgment is sent
  - input is queued as steering
  - steering is later applied at checkpoint/follow-up

- DM + running turn + multiple follow-up texts:
  - all follow-ups enter the steering queue in order
  - later applied steering preserves that order

- DM + completed turn + next text:
  - new normal turn starts
  - no steering capture occurs

- DM + running turn + `/new`:
  - reset behavior takes precedence
  - no steering capture occurs

- group + running turn + next text:
  - no auto-steering
  - current group semantics remain intact

### ACP

- running turn + second prompt:
  - no busy rejection
  - input is queued as steering
  - no visible steering acknowledgment is emitted

- paused turn + next prompt:
  - existing paused resume behavior remains unchanged

- completed turn + next prompt:
  - starts a fresh turn

- running turn + multiple follow-up prompts:
  - queue order is preserved
  - later checkpoint/follow-up applies them in order

### Regression coverage

- same-session steering queue path in gateway router still functions
- steering checkpoint pause behavior still functions
- interrupt-only steering behavior remains intact
- existing WeChat `/new` behavior remains correct
- existing ACP paused steering behavior remains correct

## Acceptance Criteria

This design is successful when all of the following are true:

- In WeChat DM, while a task is still running, follow-up messages naturally modify the current task instead of waiting as unrelated later turns.
- In ACP, while a task is still running, the next same-session prompt naturally acts as steering instead of a separate or rejected prompt.
- Once the active task ends, the next input starts a fresh turn in both surfaces.
- Group-chat behavior is unchanged in this phase.
- No user-visible “steering captured” acknowledgment is emitted.
- The implementation reuses the existing steering queue/checkpoint machinery rather than introducing a duplicate steering stack.
