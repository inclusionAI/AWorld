# Aworld Gateway / Channel Design

## Goal

Add a gateway-oriented channel access layer for Aworld that:

- runs as an `aworld-cli` subcommand
- keeps gateway code outside the Aworld SDK core
- supports phased channel rollout
- makes all enabled channels able to reach the current default Aworld Agent
- preserves a clean path to future multi-Agent routing

## Scope

This design covers:

- repository placement for gateway code
- CLI command surface for gateway runtime
- runtime config and state layout
- channel registration and lifecycle model
- unified inbound/outbound message model
- session binding and default Agent routing
- first-phase channel delivery scope

This design does not cover:

- full DingTalk, Feishu, or WeCom implementations
- attachment, card, or rich-interaction protocol support
- a standalone daemon deployment model
- complete multi-Agent rule execution in phase one

## Product Positioning

`aworld` remains the Agent SDK and local runtime foundation.

`aworld-cli` remains the primary local entry point.

`aworld_gateway` is a separate access-layer package for external channels. It is not part of the Aworld SDK core and should not live under `aworld/core/`.

The gateway should start from `aworld-cli` as a dedicated subcommand instead of as a default background service.

## Current-State Summary

The current repository already contains:

- local/SDK runtime and agent execution through Aworld
- an existing `web` command path under [aworld/cmd/web/web_server.py](/Users/wuman/Documents/workspace/aworld-gateway/aworld/aworld/cmd/web/web_server.py)
- an `env_channel` topic/message transport under [env/env_channel/env_channel/server/channel_server.py](/Users/wuman/Documents/workspace/aworld-gateway/aworld/env/env_channel/env_channel/server/channel_server.py)

The repository does not yet contain an OpenClaw-style unified gateway/channel runtime with:

- channel registry
- shared session binding
- shared routing
- shared lifecycle and health states

## Phase Strategy

### Phase One

Phase one should build the gateway backbone and deliver:

- `aworld gateway` CLI entry
- `aworld_gateway` package
- unified channel registry and routing
- full `telegram` implementation
- `web`, `dingding`, `feishu`, and `wecom` registration skeletons
- default single-Agent routing to the current Aworld default Agent

### Deferred Work

Later phases can add:

- real `dingding`, `feishu`, and `wecom` inbound/outbound implementations
- richer routing rules
- standalone deployment mode
- richer message types

## Repository Layout

Gateway code should live in a dedicated top-level Python package:

```text
aworld_gateway/
├── __init__.py
├── runtime.py
├── registry.py
├── router.py
├── session_binding.py
├── agent_resolver.py
├── types.py
├── config/
├── channels/
│   ├── base.py
│   ├── web/
│   ├── telegram/
│   ├── dingding/
│   ├── feishu/
│   └── wecom/
└── http/
    ├── server.py
    └── routers/
```

This placement keeps gateway code outside `aworld/` and avoids incorrectly presenting it as SDK-core functionality.

Gateway runtime state and config should live under:

```text
.aworld/gateway/
├── config.yaml
├── logs/
├── sessions/
└── runtime/
```

This follows the repository's existing `.aworld/` runtime-state pattern.

## CLI Design

Gateway should be launched through `aworld-cli` as a dedicated command group.

### Phase-One Commands

- `aworld gateway serve`
- `aworld gateway status`
- `aworld gateway channels list`

### Deferred Commands

- `aworld gateway channels enable <channel>`
- `aworld gateway channels disable <channel>`
- `aworld gateway config init`

The CLI layer should be thin. It should delegate runtime behavior to `aworld_gateway` instead of embedding gateway logic inside `aworld-cli`.

## Configuration Design

Primary config file:

- `.aworld/gateway/config.yaml`

Suggested shape:

```yaml
default_agent_id: aworld

gateway:
  host: 127.0.0.1
  port: 18888

channels:
  web:
    enabled: false

  telegram:
    enabled: false
    default_agent_id: aworld
    bot_token_env: AWORLD_TELEGRAM_BOT_TOKEN
    webhook_path: /webhooks/telegram

  dingding:
    enabled: false
    default_agent_id: aworld

  feishu:
    enabled: false
    default_agent_id: aworld

  wecom:
    enabled: false
    default_agent_id: aworld

routes: []
```

### Config Rules

1. First run may generate a minimal default config if none exists.
2. No channel is enabled by default in phase one.
3. Sensitive credentials should prefer environment-variable references, not plaintext config storage.
4. `status` output must distinguish:
   - registered
   - enabled
   - configured
   - implemented
   - running

## Channel Model

Each channel is represented by a `ChannelAdapter` implementation plus a registry entry.

### Registry Responsibilities

- define built-in channel ids
- expose labels and implementation status
- validate whether a channel is configured enough to start
- construct enabled adapters

### Phase-One Built-In Channels

- `telegram`
- `web`
- `dingding`
- `feishu`
- `wecom`

### Phase-One Implementation Status

- `telegram`: fully implemented
- `web`: registered skeleton only
- `dingding`: registered skeleton only
- `feishu`: registered skeleton only
- `wecom`: registered skeleton only

This intentionally avoids auto-enabling `web`. Local users can continue using `aworld-cli` directly without the gateway.

## Message Model

All channel traffic should pass through a unified internal message contract.

### InboundEnvelope

Required fields:

- `channel`
- `account_id`
- `conversation_id`
- `conversation_type`
- `sender_id`
- `sender_name`
- `message_id`
- `text`
- `raw_payload`
- `timestamp`
- `metadata`

### OutboundEnvelope

Required fields:

- `channel`
- `account_id`
- `conversation_id`
- `reply_to_message_id`
- `text`
- `events`
- `metadata`

### Message-Model Constraints

1. Channel adapters translate raw platform payloads into `InboundEnvelope`.
2. The router and Agent layer only consume unified envelopes.
3. Phase one supports text-first flows only.
4. Non-text or richer channel payloads should remain preserved in `raw_payload` or `metadata` for future work.

## Session Binding

Gateway sessions should not reuse ad hoc session naming from existing entry points. They need a stable channel-aware binding key.

Suggested format:

```text
gw:{agent_id}:{channel}:{account_id}:{conversation_type}:{conversation_id}
```

Examples:

```text
gw:aworld:telegram:bot_main:dm:12345678
gw:aworld:web:web_default:web:session_abc123
gw:ops_agent:feishu:corp_bot:group:oc_001
```

### Session-Binding Goals

1. Messages from the same external conversation bind to the same Aworld session.
2. Different channels never collide.
3. Different bot accounts never collide.
4. Future multi-Agent routing can remain stable once a session is created.

## Agent Routing

Phase one should support direct access to the current default Aworld Agent from any enabled channel.

Routing must still be designed as a dedicated layer so future multi-Agent support does not require redesigning channel adapters.

### AgentResolver Priority

1. explicit `agent_id` from the request or channel command surface
2. existing session-bound `agent_id`
3. channel/account-level default Agent
4. route rule match
5. global `default_agent_id`

### Phase-One Delivery

Phase one only needs these active behaviors:

- global `default_agent_id`
- optional per-channel default Agent

The route-rule layer should be structurally reserved but may remain empty by default.

### Future Route-Match Dimensions

- `channel`
- `account_id`
- `conversation_type`
- `conversation_id`
- `sender_id`
- command prefix
- mention rule

This keeps the design ready for multi-Agent routing without overloading phase one.

## Runtime Flow

### Inbound Flow

1. Channel adapter receives a platform event.
2. Adapter converts it to `InboundEnvelope`.
3. `SessionBinding` resolves a stable Aworld `session_id`.
4. `AgentResolver` determines the target Agent.
5. Router builds `ChatCompletionRequest`.
6. Router calls Aworld execution through the existing server/executor path.
7. Router emits unified outbound events.
8. Channel adapter sends the final response back to the channel.

### Outbound Flow

1. Router receives streamed or final Agent output.
2. Output is converted to `OutboundEnvelope`.
3. Adapter maps the unified response back to the platform-specific transport.

## Lifecycle and Failure Model

Each channel should share the same coarse runtime states:

- `registered`
- `configured`
- `running`
- `degraded`

### Runtime Rules

1. `GatewayRuntime` starts enabled channels independently.
2. A single channel failure must not stop the entire gateway.
3. A misconfigured enabled channel should surface as not configured rather than silently failing.
4. An enabled but unimplemented channel should fail fast with a clear status message.

### Error Classes

Phase one should explicitly handle:

- missing config
- startup failure
- inbound adapter failure
- Agent invocation failure
- unsupported or unimplemented channel usage

## Testing Strategy

### Unit Tests

- config loading
- channel registry
- session binding
- Agent resolver
- unified router behavior

### Integration Tests

- `aworld gateway serve` bootstraps from config
- `telegram` adapter can deliver text into the default Aworld Agent
- response flow returns through the adapter
- disabled channels do not start

### Explicit Non-Goals for Phase-One Tests

- real DingTalk/Feishu/WeCom live integration
- attachment and card rendering
- full route-rule engine coverage

## Design Constraints

1. Gateway must not be positioned as part of `aworld/core`.
2. Gateway must not enable `web` by default.
3. `aworld-cli` remains the local-first user entry.
4. All enabled channels must be able to reach the current default Aworld Agent.
5. The design must preserve a clean future path to multi-Agent routing.

## Open Decisions Intentionally Deferred

The following should stay out of phase one:

- whether `web` later becomes a full implemented gateway channel
- how route rules are authored from CLI/UI
- whether gateway later gains a standalone daemon mode
- how non-text message types are normalized
