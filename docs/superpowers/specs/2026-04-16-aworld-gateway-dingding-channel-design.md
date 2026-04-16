# Aworld Gateway DingTalk Channel Migration Design

**Date:** 2026-04-16

## Goal

Migrate the existing DingTalk channel implementation from `aworldclaw` into the current `aworld_gateway` branch so that `aworld-cli gateway serve` can start a fully functional DingTalk channel with Stream-mode message intake, `sessionWebhook` reply, attachment handling, and AI card updates, all routed into the current Aworld Agent runtime.

## Scope

This design covers the Phase-2 upgrade of the `dingding` channel only.

Included:
- DingTalk Stream-mode connector integration under `aworld_gateway/channels/dingding/`
- Full message receive/reply flow using DingTalk Stream + `sessionWebhook`
- Session reset semantics such as `/new` and `新会话`
- Attachment download/upload bridging
- AI card creation, streaming update, and finalization
- Gateway config/runtime/CLI exposure for DingTalk
- Test coverage for config, runtime state, connector behavior, and CLI smoke

Excluded:
- Web, Feishu, WeCom feature parity
- Global refactor of all gateway channels into a unified streaming event bus
- Interactive AI card action callback workflows beyond message display/update
- Automatic end-to-end online DingTalk tenant validation in this phase

## Architecture

### 1. Preserve the current gateway control plane

The existing gateway control plane remains intact:
- `aworld-cli gateway` stays the operator entrypoint
- `GatewayRuntime` continues to own channel lifecycle and runtime status
- `ChannelRegistry` continues to register channels and build adapters
- Non-DingTalk channels continue using the current lightweight adapter pattern

This keeps Phase-1 Telegram and placeholder channels stable while containing DingTalk complexity within the DingTalk channel package.

### 2. Promote DingTalk from placeholder to a full channel subsystem

`aworld_gateway/channels/dingding/` becomes a complete subsystem rather than a thin adapter.

The new `DingdingChannelAdapter` will:
- start and stop the DingTalk Stream client
- receive Stream callbacks
- parse text and attachment payloads
- maintain conversation-to-session mapping for DingTalk-specific session continuity
- call a DingTalk-specific Aworld execution bridge
- send text, file, and AI card responses back to DingTalk

This means DingTalk is intentionally more stateful and more feature-rich than Telegram, but the complexity remains localized.

### 3. Keep DingTalk on a dedicated streaming execution path

The current `GatewayRouter` contract is single-request / single-final-text and should stay that way for Phase-1 channels.

DingTalk will not be forced into that interface.

Instead, DingTalk gets a dedicated execution bridge that can:
- route to the current Aworld Agent
- consume streaming outputs/chunks/events
- progressively update DingTalk AI cards
- fall back to plain text when cards are unavailable
- preserve attachment behavior from `aworldclaw`

This avoids destabilizing the generic router while still allowing DingTalk to deliver near-complete parity with the existing `aworldclaw` user experience.

## Configuration Design

### 1. Replace DingTalk placeholder config with a dedicated model

`channels.dingding` should no longer use `PlaceholderChannelConfig`.

Introduce a dedicated `DingdingChannelConfig` with these fields:
- `enabled: bool = False`
- `default_agent_id: str | None = None`
- `client_id_env: str | None = "AWORLD_DINGTALK_CLIENT_ID"`
- `client_secret_env: str | None = "AWORLD_DINGTALK_CLIENT_SECRET"`
- `card_template_id_env: str | None = "AWORLD_DINGTALK_CARD_TEMPLATE_ID"`
- `enable_ai_card: bool = True`
- `enable_attachments: bool = True`
- `workspace_dir: str | None = None`

`workspace_dir` defaults effectively resolve under `.aworld/gateway/dingding/` when unset.

### 2. Runtime configuration rules

DingTalk is considered “configured enough to start” only when:
- `client_id_env` is present and resolves to a non-empty environment variable
- `client_secret_env` is present and resolves to a non-empty environment variable

AI card support is optional at runtime:
- if `enable_ai_card` is `False`, the channel still starts and replies with text/file messages only
- if AI card creation fails, the channel degrades per-message, not at whole-channel startup level

Attachment support is optional at runtime:
- if `enable_attachments` is `False`, attachment parsing/upload is skipped
- if upload/download fails, text reply flow still proceeds

### 3. Backward compatibility

`GatewayConfigLoader` should preserve compatibility with existing generated configs that still define `channels.dingding` as a placeholder-shaped object.

Loader behavior should tolerate missing new DingTalk fields and fill defaults.

## Dependency Strategy

The current repository already carries `httpx`, `FastAPI`, and `uvicorn`, but not `dingtalk_stream`.

The migration should add `dingtalk_stream` as a gateway-usable dependency.

Startup behavior must remain resilient:
- if `dingtalk_stream` is unavailable, the DingTalk channel reports `degraded` with a clear error
- the overall gateway process must remain startable so other channels are unaffected

The `aworldclaw`-specific agent-server configuration (`LLM_BASE_URL`, `LLM_MODEL`, etc.) should not be migrated.

DingTalk in this branch must route directly to the current Aworld Agent execution stack, not maintain a parallel model-serving configuration path.

## Message Flow Design

### 1. Inbound lifecycle

`DingdingChannelAdapter.start()` starts a Stream client and registers a message callback handler.

For each incoming callback:
1. parse callback payload
2. extract `sessionWebhook`, `conversationId`, `senderId`, `robotCode`, text, and attachment metadata
3. reject malformed payloads early with logging
4. detect session reset commands such as `/new`, `新会话`, `压缩上下文`, `/summary`
5. for reset commands, rotate the active session id for the conversation and send a confirmation text reply
6. otherwise, assemble Aworld user input and invoke the DingTalk execution bridge

### 2. Session semantics

DingTalk needs session continuity that matches the existing `aworldclaw` behavior.

The channel will maintain an internal conversation-to-session map, keyed by a stable DingTalk conversation key:
- prefer `conversationId`
- fall back to `senderId` when necessary

This session map is intentionally channel-local because reset semantics are interaction-level behaviors, not just deterministic identity binding.

The existing gateway-level `default_agent_id` still applies, and later multi-agent routing can be layered on top.

### 3. Aworld execution bridge

A DingTalk-specific execution bridge should be introduced instead of reusing `GatewayRouter.handle_inbound(...)->OutboundEnvelope`.

The bridge must:
- resolve the target agent from DingTalk config/default routing
- run against the current Aworld Agent stack
- consume streaming outputs from Aworld
- progressively produce text content for AI card updates
- collect final text
- detect media/file references in output
- return a final result object usable by the DingTalk connector

This bridge can wrap existing Aworld streaming primitives already available in the repository instead of depending on the `aworldclaw` external agent-server path.

## Attachments Design

The attachment path should be migrated largely as-is from `aworldclaw`, but localized under `aworld_gateway/channels/dingding/`.

Included behaviors:
- parse incoming DingTalk attachment metadata/download codes
- download supported incoming files to the DingTalk workspace directory
- enrich user input with downloaded local file references where appropriate
- detect local file/image references in final agent output
- upload local images/files to DingTalk using the correct media endpoints
- inline image references where possible
- send file messages separately for regular files

Failure policy:
- attachment failures must not fail the entire message round
- text response remains primary
- attachment failures are logged and skipped

## AI Card Design

AI card behavior should preserve the current `aworldclaw` design as much as possible.

Per message round:
1. attempt to build card delivery target from callback payload
2. attempt to create a card instance
3. if successful, mark card as inputting
4. stream intermediate content updates during Aworld output generation
5. finalize the card with the full final content
6. if any card operation fails, fall back to plain text reply

Important boundary:
- AI card logic remains DingTalk-specific and does not move to gateway common code in this phase
- card callback interaction workflows are deferred

## Error Handling

Three-level degradation policy:

### 1. AI card failure
- fall back to plain text reply
- do not fail the overall DingTalk message handling

### 2. Attachment failure
- continue with text response
- skip broken file/image payloads
- log the error with enough context for debugging

### 3. Agent execution failure
- send an error text via `sessionWebhook`
- avoid leaving the callback path silent
- avoid crashing the Stream client loop

Startup-level errors such as missing dependency or missing required credentials should surface in runtime status as channel degradation rather than crashing the entire gateway process.

## Files and Module Boundaries

Likely file changes:

Create or expand under `aworld_gateway/channels/dingding/`:
- `adapter.py`
- `connector.py`
- `config.py` or localized helpers if needed
- `bridge.py`
- `types.py` if DingTalk-specific payload/result structs improve clarity

Modify:
- `aworld_gateway/config/models.py`
- `aworld_gateway/config/__init__.py`
- `aworld_gateway/config/loader.py`
- `aworld_gateway/registry.py`
- `aworld_gateway/runtime.py` only if startup/status semantics need extension
- packaging/dependency files to include `dingtalk_stream`
- `aworld-cli/src/aworld_cli/gateway_cli.py` only where status/serve integration requires it

Test files to add:
- `tests/gateway/test_dingding_config.py`
- `tests/gateway/test_dingding_adapter.py`
- `tests/gateway/test_dingding_connector.py`
- `tests/gateway/test_dingding_bridge.py`
- updates to runtime/registry/CLI tests

## Verification Plan

Completion is defined by four layers of validation.

### 1. Config validation
Tests must cover:
- default config generation
- DingTalk env-backed config validation
- backward compatibility with older configs
- missing env behavior and runtime degradation semantics

### 2. Runtime/registry validation
Tests must cover:
- DingTalk listed as `implemented=True`
- correct `configured/running/degraded` state transitions
- missing dependency behavior
- missing credentials behavior
- startup failure surfacing via `gateway status`

### 3. Connector behavior validation
Tests must cover:
- payload parsing
- session reset commands
- `sessionWebhook` text reply
- AI card create/update/finalize flows
- attachment upload/download flows
- fallback when AI card or attachment behavior fails

These tests should prefer fakes/mocks for `httpx` and the Stream SDK.

### 4. CLI smoke validation
Local verification should include:
- `aworld-cli gateway status`
- `aworld-cli gateway channels list`
- `aworld-cli gateway serve`
- mocked DingTalk callback / fake Stream client flow sufficient to verify startup and main round-trip behavior without requiring a live DingTalk tenant

## Delivery Boundary

This migration will deliver:
- a real DingTalk channel under `aworld_gateway`
- near-complete parity with the current `aworldclaw` DingTalk message experience
- gateway-visible configuration and runtime state
- local automated verification coverage

This migration will not deliver:
- Feishu/WeCom/Web channel upgrades
- whole-gateway streaming abstraction refactor
- advanced interactive AI card callback actions
- guaranteed live tenant E2E validation in the same phase

## Recommendation

Proceed by migrating DingTalk as a channel-local subsystem inside `aworld_gateway/channels/dingding/`, reusing `aworldclaw` logic selectively but replacing its external agent-server dependency with the current Aworld Agent execution stack already available in this repository.

This produces the highest feature completeness with the lowest cross-channel architectural risk.
