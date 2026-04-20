## Why

The DingTalk gateway currently processes inbound callbacks synchronously inside the stream callback handler. That makes the platform acknowledgement depend on full agent execution, which increases the chance of provider retries and duplicate user-visible replies.

The same gateway path also does not preserve enough DingTalk session routing metadata to push later cron execution results back through the originating DingTalk conversation. Users can create reminder-style cron jobs from DingTalk, but completion notifications are not routed back to the same channel.

## What Changes

- Acknowledge DingTalk callbacks immediately and continue agent execution in the background.
- Suppress duplicate DingTalk callback processing within a short retry window.
- Capture cron job ids created during a DingTalk session and persist a local `job_id -> DingTalk session` binding in the gateway layer.
- Reuse the existing cron `notification_sink` extension point to fan out cron completion notifications back to DingTalk without changing core scheduler semantics.

## Capabilities

### Added Capabilities
- `gateway-channels`: DingTalk callbacks are retriable without duplicate user-visible replies, and DingTalk-originated cron notifications can route back to the source conversation.

## Impact

- Affects `aworld_gateway/channels/dingding/*`.
- Preserves existing framework scheduler behavior by using the existing notification sink extension point instead of changing cron execution semantics.
- Does not introduce new framework-level hook points.
