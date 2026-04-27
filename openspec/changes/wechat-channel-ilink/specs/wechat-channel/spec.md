## ADDED Requirements

### Requirement: Gateway MUST expose `wechat` as a distinct personal-WeChat channel

The gateway MUST register a `wechat` channel for personal WeChat access and MUST keep it distinct from the existing `wecom` enterprise channel.

#### Scenario: Channel list shows separate personal and enterprise WeChat entries

- **WHEN** an operator lists built-in gateway channels
- **THEN** `wechat` MUST appear as a distinct channel entry
- **AND** `wecom` MUST remain a separate channel entry
- **AND** the design MUST NOT require a merged `wx` or shared primary connector abstraction

### Requirement: `wechat` coexistence with Happy ACP MUST use separate host processes

When users need both Happy ACP access and `wechat` channel access to the same AWorld deployment, the system MUST support that coexistence through separate host processes rather than a single mixed ACP+channel process.

#### Scenario: Happy ACP and WeChat access are enabled for the same worker host

- **WHEN** an operator wants the same AWorld deployment to be reachable from Happy ACP and from the `wechat` channel
- **THEN** the supported host shape MUST be separate processes such as `aworld-cli acp` and `aworld-cli gateway server`
- **AND** the design MUST NOT require embedding the `wechat` channel runtime directly into the ACP stdio host process

### Requirement: `wechat` MUST use a channel-local stateful connector model

The `wechat` channel MUST encapsulate its long-polling, token, and account state within a channel-local subsystem rather than spreading that state into gateway-wide routing components.

#### Scenario: WeChat runtime state is required for message delivery

- **WHEN** the `wechat` channel receives and replies to messages
- **THEN** long-poll state, account credentials, and peer-scoped `context_token` state MUST be managed within `aworld_gateway/channels/wechat/`
- **AND** the gateway-wide router contract MUST NOT become responsible for storing WeChat protocol state

### Requirement: Phase-1 `wechat` delivery MUST preserve the current final-text router contract

Phase 1 of the `wechat` channel MUST integrate with the existing `InboundEnvelope -> GatewayRouter -> OutboundEnvelope` final-text contract and MUST NOT require a gateway-wide streaming router redesign.

#### Scenario: A WeChat text message is handled in phase 1

- **WHEN** a text message arrives through the `wechat` connector
- **THEN** the channel MUST translate it into an `InboundEnvelope`
- **AND** the gateway router MUST produce a final `OutboundEnvelope`
- **AND** the `wechat` channel MUST send the final text reply back to the platform
- **AND** phase 1 MUST NOT depend on message-edit based streaming delivery

### Requirement: `wechat` MUST retain the latest peer-scoped `context_token`

The `wechat` channel MUST cache the latest `context_token` observed for a peer and MUST reuse that token on later outbound replies when the platform contract requires it.

#### Scenario: Reply reuses the latest context token

- **WHEN** an inbound `wechat` message from peer `P` contains a `context_token`
- **THEN** the channel MUST update the cached token for that peer
- **AND** a later outbound reply to `P` MUST include the latest cached token when available

### Requirement: `wechat` MUST support phase-1 long-poll text intake without media parity

Phase 1 of the `wechat` channel MUST support long-poll based text intake and final-text reply even if full media handling remains deferred.

#### Scenario: Text-only phase-1 deployment

- **WHEN** an operator enables and configures `wechat` with the required credentials
- **THEN** the channel MUST be able to start its long-poll intake loop
- **AND** text messages MUST be processed end-to-end
- **AND** the absence of full media parity MUST NOT block phase-1 startup or text delivery

### Requirement: `wechat` media intake MUST preserve the current envelope contract

When inbound personal-WeChat messages contain media items, the channel MUST keep the gateway-wide `InboundEnvelope` schema unchanged while still surfacing the downloaded artifacts to downstream agents.

#### Scenario: Inbound image or file message is received

- **WHEN** a `wechat` inbound message contains image, file, video, or voice items
- **THEN** the connector MUST download supported media into channel-local cache paths
- **AND** the resulting `InboundEnvelope` MUST keep using the existing text-plus-metadata contract
- **AND** the connector MUST surface the cached paths through attachment metadata and attachment prompt text rather than introducing a new gateway-wide media envelope type
- **AND** the connector MUST expose a structured channel-local media metadata list for programmatic consumers
- **AND** image attachments SHOULD additionally expose multimodal-friendly image parts in metadata so later multimodal routing work has a stable handoff shape

### Requirement: `wechat` media download MUST restrict fetches to trusted WeChat CDN hosts

Inbound media download helpers MUST reject untrusted remote hosts to avoid turning the connector into a general-purpose fetch primitive.

#### Scenario: Media payload points at an untrusted host

- **WHEN** a `wechat` media item resolves to a `full_url` outside the trusted WeChat CDN host allowlist
- **THEN** the connector MUST refuse the download
- **AND** the refusal MUST happen before any outbound network fetch is attempted for that URL

### Requirement: `wechat` outbound media MUST support explicit local file references

The final-text router contract remains unchanged, but outbound `wechat` replies MUST be able to turn explicit local media references into native WeChat media messages.

#### Scenario: Final reply includes a local image or file reference

- **WHEN** the final outbound `wechat` text contains an explicit local media reference such as a markdown image, markdown link, or `file://` style marker
- **THEN** the connector MUST resolve the local file path
- **AND** the connector MUST upload the encrypted payload through the iLink media upload flow
- **AND** the connector MUST send a native media item message to the peer
- **AND** any remaining plain text MUST continue to be delivered through the existing final-text send path

#### Scenario: Final reply carries media send intent through envelope events

- **WHEN** the final outbound `wechat` reply includes media-oriented `OutboundEnvelope.events`
- **THEN** the `wechat` adapter MUST translate those events into connector-local outbound attachment requests
- **AND** event types such as `file` MUST be able to force file-attachment delivery even when the referenced path is an image
- **AND** explicit event types such as `video` or `voice` MUST be able to override file-extension based media inference when the sender already knows the intended delivery type
- **AND** this translation MUST remain channel-local rather than changing the gateway-wide router contract
