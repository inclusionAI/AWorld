## ADDED Requirements

### Requirement: Gateway MUST expose `wecom` as a distinct enterprise-WeChat channel

The gateway MUST register `wecom` as a separate channel from `wechat`.

#### Scenario: Channel list distinguishes personal and enterprise WeChat

- **WHEN** an operator lists built-in channels
- **THEN** `wechat` and `wecom` MUST appear as distinct entries
- **AND** `wecom` MUST NOT depend on the `wechat` connector implementation

### Requirement: `wecom` MUST use a channel-local WebSocket connector

The `wecom` channel MUST encapsulate its WebSocket session, pending request futures, and reply caches inside `aworld_gateway/channels/wecom/`.

#### Scenario: Enterprise WeChat runtime state is needed

- **WHEN** `wecom` starts and handles messages
- **THEN** the WebSocket transport and `req_id` correlation state MUST stay inside the channel-local subsystem
- **AND** gateway-wide routing code MUST NOT become responsible for WeCom protocol state

#### Scenario: The live WebSocket session is interrupted

- **WHEN** the active `wecom` WebSocket transport closes or raises a read error after startup
- **THEN** the connector MUST fail outstanding pending request futures for the interrupted session
- **AND** the connector MUST retry establishing a new subscribed WebSocket session with connector-local backoff
- **AND** after reconnection, new inbound callbacks and outbound sends MUST use the replacement transport without requiring gateway-wide router changes

#### Scenario: The session stays idle

- **WHEN** the `wecom` connector remains connected but otherwise idle
- **THEN** the connector MUST periodically send application-level `ping` frames to keep the session alive

### Requirement: `wecom` phase-1 delivery MUST preserve the current final-text router contract

Phase 1 of the `wecom` channel MUST integrate with the existing `InboundEnvelope -> GatewayRouter -> OutboundEnvelope` final-text contract.

#### Scenario: A text callback is handled in phase 1

- **WHEN** a text callback arrives through the `wecom` connector
- **THEN** the channel MUST translate it into an `InboundEnvelope`
- **AND** the gateway router MUST produce a final `OutboundEnvelope`
- **AND** the channel MUST send the final text reply back through the WeCom gateway

### Requirement: `wecom` MUST support reply-bound text sends via cached `req_id`

The channel MUST remember callback `req_id` values so replies can be correlated to prior inbound callbacks.

#### Scenario: Reply uses cached callback `req_id`

- **WHEN** an inbound `wecom` callback is accepted for routing
- **THEN** the channel MUST cache the callback `req_id` for the message and chat
- **AND** a later outbound reply SHOULD use the cached `req_id` when available

### Requirement: `wecom` phase-1 MUST support basic DM and group policy filtering

Phase 1 of `wecom` MUST support DM and group enablement rules before routing inbound callbacks.

#### Scenario: Group traffic is disabled

- **WHEN** an inbound callback comes from a group chat and the configured group policy is `disabled`
- **THEN** the connector MUST drop the callback before invoking the router

### Requirement: `wecom` inbound media MUST preserve the current envelope contract

When enterprise-WeChat callbacks contain image or file content, the connector MUST keep using the current text-plus-metadata envelope contract.

#### Scenario: Inbound image or file callback is received

- **WHEN** a `wecom` callback carries image or file payloads
- **THEN** the connector MUST cache supported media into a channel-local attachments directory under `.aworld/gateway/wecom/attachments/<bot_id>/`
- **AND** the resulting `InboundEnvelope` MUST keep using the existing text-plus-metadata contract
- **AND** the connector MUST expose compatibility attachment metadata, structured `wecom_media` metadata, and image-oriented `multimodal_parts` when applicable
- **AND** if no user text exists, the connector MUST still be able to route the message using an attachment prompt derived from the cached files

### Requirement: `wecom` outbound media MUST support channel-local attachment intent without changing the router contract

Outbound enterprise-WeChat media sending MUST stay behind the channel-local adapter and connector boundary.

#### Scenario: Final reply includes outbound attachment intent

- **WHEN** a final outbound `wecom` reply carries channel-local attachment intent through `OutboundEnvelope.events` or `metadata.outbound_attachments`
- **THEN** the `wecom` adapter MUST translate events into connector-local outbound attachment requests
- **AND** the connector MUST load the referenced local file, upload it through `aibot_upload_media_init/chunk/finish`, and send the resulting native media message
- **AND** if reply-correlated `req_id` state is available, the media send SHOULD use reply-bound delivery
- **AND** any remaining text MUST continue to be deliverable as a follow-up markdown message without changing the gateway-wide router contract

#### Scenario: Outbound media exceeds native type limits but can still be sent as a file

- **WHEN** an outbound `wecom` attachment is classified as `image`, `video`, or `voice`
- **AND** the file violates the native media constraints but still fits within the general file limit
- **THEN** the connector MUST downgrade the send to native `file` delivery instead of failing immediately
- **AND** the connector MUST send a follow-up markdown note that explains the downgrade reason
- **AND** voice content that is not AMR MUST be treated as unsupported native voice and downgraded to `file`

#### Scenario: Outbound media exceeds the absolute upload limit

- **WHEN** an outbound `wecom` attachment is larger than the maximum file upload size supported by the channel
- **THEN** the connector MUST reject the media before starting the upload handshake
- **AND** the connector MUST return an error result for the send attempt
- **AND** the connector MUST send a follow-up markdown note that explains the size limit failure
