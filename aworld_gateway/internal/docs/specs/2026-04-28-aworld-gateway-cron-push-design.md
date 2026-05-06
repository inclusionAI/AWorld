# Aworld Gateway Cron Push Abstraction Design

**Date:** 2026-04-28

## Goal

Add a gateway-level reusable cron push abstraction so push-oriented channels can automatically receive follow-up cron notifications in the original conversation after a user creates a cron job.

The first concrete target is to make `wechat` support the same cron push behavior that `dingtalk` already has today, while also migrating `dingtalk` onto the shared abstraction so future channels such as `wecom` and `feishu` can reuse it.

## Scope

This design covers:

- a shared gateway-level cron push subsystem
- persistent binding of `cron` job ids to channel conversation targets
- shared scheduler notification fanout
- a shared notification text formatter
- `wechat` integration
- `dingtalk` migration onto the shared abstraction
- router / agent-backend changes required to observe runtime tool outputs in channels that currently only receive final text
- test coverage for the shared subsystem and channel integrations

This design does not cover:

- a general-purpose gateway event bus for all asynchronous channel events
- AI card, streaming card updates, or rich-interaction abstractions across channels
- attachment or media abstractions unrelated to cron notifications
- multi-process distributed storage or distributed notification fanout

## Problem Statement

The current branch already supports cron follow-up push for `dingtalk`, but the implementation is channel-local:

- `dingtalk` extracts `job_id` values from `cron` tool results
- it persists a job-to-session binding
- it installs a `scheduler.notification_sink`
- later cron notifications are pushed back through DingTalk `sessionWebhook`

`wechat` does not currently have that behavior. It can send normal outbound replies, but it lacks:

- job-id extraction from runtime tool outputs
- persisted bindings between a cron job and the original WeChat conversation
- registration into scheduler notification fanout
- a mechanism to send cron notification text back into the original chat

If `wechat` copies the current `dingtalk` implementation directly, the repository will accumulate channel-specific duplicates of the same cron push lifecycle. That duplication will grow again when `wecom` or `feishu` need the same ability.

## Design Principles

### 1. Abstract only the cron push lifecycle

The shared layer should abstract only the parts that are truly common:

- extract `job_id` values from `cron` tool outputs
- persist job bindings
- hook a shared scheduler notification sink
- format and fan out notifications
- clean up terminal bindings

It should not attempt to unify all channel inbound/outbound semantics.

### 2. Keep channel-specific transport at the edge

Each channel still owns:

- how it identifies the original reply target
- how it sends a text message back to that target

The shared subsystem should work with channel-agnostic bindings plus a channel-specific sender callback.

### 3. Preserve the main message path

Cron push is auxiliary behavior. Failures in binding creation, notification fanout, or channel-specific send must not break:

- the original user request
- the original agent reply
- the scheduler execution itself

### 4. Extend the generic router path instead of cloning DingTalk’s bridge

`wechat` already uses the generic `router -> agent_backend` path. The missing capability is runtime output observation, not a separate execution model. The design should add optional output observation to the generic gateway execution stack instead of introducing a second DingTalk-like custom bridge for WeChat.

## Current-State Summary

### DingTalk

`dingtalk` already contains:

- a local binding store
- a local notifier
- local `cron` output parsing
- local scheduler notification sink installation

This proves the product behavior is useful and already accepted, but the implementation is not reusable.

### WeChat

`wechat` currently contains:

- inbound polling and message translation
- reply sending through `send_text(chat_id=..., text=...)`
- attachment/media handling
- generic router-driven request/response handling

It does not currently observe intermediate runtime outputs, so it cannot detect that a `cron` job was created during the request.

### Gateway Router / Backend

The generic gateway execution path currently returns final text. It does not expose optional observation of intermediate outputs such as:

- `tool_call_result`
- `tool_execution`
- any future structured runtime events

That limitation is the main blocker for `wechat`.

## Proposed Architecture

Add a shared subsystem under a new package:

```text
aworld_gateway/cron_push/
├── __init__.py
├── types.py
├── store.py
├── formatter.py
└── bridge.py
```

### 1. `CronPushBindingStore`

Responsibilities:

- persist `job_id -> binding` records to disk
- provide `upsert`, `get`, and `remove`
- tolerate missing or malformed storage files

This replaces channel-local binding stores for cron push.

### 2. `CronNotificationFormatter`

Responsibilities:

- convert scheduler notification payloads into user-visible text
- preserve current text semantics already used by `dingtalk`
- support silent terminal cleanup through `user_visible=False`

Expected formatting behavior:

- include `summary` if present
- include `detail` if present and different from `summary`
- include `下次执行：{next_run_at}` when `next_run_at` is present

### 3. `CronPushRegistry`

Responsibilities:

- register sender functions per channel name
- resolve the sender for a binding’s `channel`

This is a lightweight registry, not a new channel registry replacement.

### 4. `CronPushBridge`

Responsibilities:

- install a shared scheduler `notification_sink` fanout once
- extract `job_id` values from runtime outputs for successful `cron` calls
- create and persist bindings from channel-specific context
- receive scheduler notifications and dispatch them to the correct sender
- clean up terminal bindings

The bridge is the coordination layer. Channels do not manipulate `scheduler.notification_sink` directly anymore.

## Data Model

Use a shared binding structure with common routing metadata plus a channel-specific `target`.

Suggested persisted shape:

```json
{
  "job_id": "job-main",
  "channel": "wechat",
  "account_id": "wx-account",
  "conversation_id": "chat-123",
  "sender_id": "user-1",
  "target": {
    "chat_id": "chat-123"
  },
  "meta": {
    "created_from": "cron_tool"
  }
}
```

For `dingtalk`, the shape becomes:

```json
{
  "job_id": "job-main",
  "channel": "dingtalk",
  "account_id": "",
  "conversation_id": "conv-1",
  "sender_id": "user-1",
  "target": {
    "session_webhook": "https://callback"
  },
  "meta": {
    "created_from": "cron_tool"
  }
}
```

### Field Rules

- `job_id`: required, normalized string key
- `channel`: required channel id used to resolve the sender
- `account_id`: channel account context when applicable
- `conversation_id`: original conversation identifier for diagnostics and future reuse
- `sender_id`: optional but useful for logs and future policy work
- `target`: channel-specific minimum send target
- `meta`: reserved extensibility bag

The persistent record must remain pure data. It must not contain connector objects, session objects, or runtime-only callbacks.

## Runtime Output Observation

### Requirement

The shared bridge can only bind cron jobs if channels can observe runtime outputs.

### Design

Extend the gateway execution stack with an optional `on_output` callback:

- `agent_backend.run(..., on_output=None)`
- `router.handle_inbound(..., on_output=None)`

Behavior:

- when `on_output` is absent, existing behavior remains unchanged
- when `on_output` is provided, the backend forwards each runtime output event to the callback
- the main return value remains the final text response

This is intentionally a minimal API extension. It preserves the current router contract while allowing `wechat` and future channels to react to intermediate tool outputs.

## Channel Integration

### 1. WeChat

`wechat` should remain on the generic router path.

Flow:

1. `WechatConnector` receives an inbound message
2. it calls `router.handle_inbound(..., on_output=...)`
3. the `on_output` callback forwards outputs plus a WeChat binding context to `CronPushBridge.bind_outputs(...)`
4. when a successful `cron` tool result appears, the bridge stores bindings
5. later, scheduler notifications are dispatched back through the WeChat sender

WeChat sender behavior:

- use the persisted `target.chat_id`
- use the persisted `account_id` context when needed to restore token lookup scope
- call the existing `WechatConnector.send_text(chat_id=..., text=...)`

Recommended WeChat binding target:

```json
{
  "chat_id": "<conversation_id>"
}
```

### 2. DingTalk

`dingtalk` should migrate from channel-local cron push logic to the shared bridge.

Flow:

- keep the existing DingTalk-specific execution bridge for streaming and AI card behavior
- replace local cron binding persistence and sink installation with calls into `CronPushBridge`
- register a DingTalk sender that uses `target.session_webhook`

Recommended DingTalk binding target:

```json
{
  "session_webhook": "<sessionWebhook>"
}
```

This preserves DingTalk’s richer execution path while removing duplicate cron push logic.

## Message Flow

### Binding Creation Flow

1. user sends a message in `wechat` or `dingtalk`
2. channel executes the agent request
3. runtime emits `tool_call_result` for `cron`
4. channel-provided `on_output` hands the output plus current binding context to `CronPushBridge`
5. bridge extracts one or more `job_id` values
6. bridge persists one binding per `job_id`
7. bridge ensures scheduler notification fanout is installed
8. normal agent reply continues unchanged

### Notification Delivery Flow

1. scheduler emits a cron notification
2. bridge receives the notification through installed `notification_sink`
3. bridge loads the binding by `job_id`
4. bridge formats user-visible text
5. bridge resolves the channel sender from `channel`
6. bridge invokes the sender with the binding and formatted text
7. if `next_run_at` is empty, bridge removes the binding
8. otherwise the binding remains for future recurring notifications

## Job-ID Extraction Rules

The shared bridge should preserve current DingTalk extraction semantics:

- only accept outputs whose `output_type()` is `tool_call_result`
- only accept outputs whose `tool_name` resolves to `cron`
- ignore outputs with `success is False`
- extract:
  - top-level `job_id`
  - `advance_reminder.job_id`
- deduplicate extracted job ids

This keeps behavior stable across migration.

## Scheduler Fanout Design

The shared bridge owns installation of the scheduler fanout hook.

Rules:

- installation is idempotent
- only one bridge instance should install the hook in a process
- any pre-existing `notification_sink` must remain chained
- the bridge should await previous async sinks when present

Fanout order:

1. call previous sink if one exists
2. process shared cron push dispatch

This preserves existing scheduler consumers such as CLI notification centers or ACP bridging.

## Error Handling

### 1. Binding creation failure

If binding persistence fails:

- log a warning
- do not fail the current request
- do not alter the agent’s immediate response

### 2. Notification dispatch failure

If sender resolution or channel send fails:

- log a warning with `job_id` and `channel`
- do not fail scheduler execution

### 3. Silent terminal notifications

If `user_visible` is `False`:

- do not send a user-visible message
- still perform terminal cleanup for one-shot jobs

### 4. Missing sender registration

If the binding’s `channel` has no registered sender:

- log a warning
- keep the binding for recurring jobs so a later registration can still recover delivery
- remove the binding only when the current notification is terminal and there is no future run

### 5. Restart behavior

Bindings are file-backed and survive process restart. On restart:

- the bridge re-installs `scheduler.notification_sink`
- later notifications continue to resolve against persisted bindings

This design assumes the scheduler’s own job persistence already exists independently.

## Storage Design

Use a single gateway-level file rather than channel-local files.

Suggested default path:

```text
.aworld/gateway/cron-push-bindings.json
```

Advantages:

- one source of truth across channels
- easier operator inspection
- simpler future cleanup tooling

## Testing Strategy

### 1. Shared subsystem unit tests

Add tests for:

- binding store read/write/remove behavior
- malformed storage-file tolerance
- formatter output combinations
- `job_id` extraction including `advance_reminder`
- recurring vs terminal cleanup behavior
- chaining to a previous notification sink

### 2. Router / backend integration tests

Add tests for:

- `router.handle_inbound(..., on_output=...)` forwarding runtime outputs
- backward compatibility when `on_output` is omitted

### 3. DingTalk migration tests

Adapt current DingTalk cron push tests so they verify:

- bindings are persisted through the shared bridge
- notifications still fan out to DingTalk text sending
- current behavior is preserved after migration

### 4. WeChat integration tests

Add tests for:

- successful `cron` tool output during a WeChat request creates bindings
- scheduler notifications call WeChat `send_text(chat_id=..., text=...)`
- terminal notifications clear one-shot bindings
- recurring notifications preserve bindings

## Migration Plan

### Phase 1

Introduce the shared subsystem and unit tests without removing the existing DingTalk behavior until the shared bridge is validated.

### Phase 2

Wire `dingtalk` onto the shared bridge and update existing DingTalk tests.

### Phase 3

Extend router / backend output observation and connect `wechat`.

### Phase 4

Delete the old DingTalk-local cron push code after shared-path coverage passes.

This ordering minimizes regression risk because the existing working DingTalk behavior is used as the baseline throughout the migration.

## Acceptance Criteria

The design is complete when the implementation can satisfy all of the following:

1. A user creates a cron reminder from a WeChat conversation and later receives the cron notification back in the same WeChat conversation.
2. Existing DingTalk cron push behavior still works after migration to the shared bridge.
3. The generic gateway router path can optionally expose runtime outputs without breaking existing callers.
4. Shared scheduler notification fanout coexists with any previously installed sink.
5. Failures in cron push binding or dispatch do not break the immediate request or the scheduler run.
6. The shared bridge is channel-reusable and does not depend on DingTalk-specific or WeChat-specific connector internals beyond registered sender callbacks.
