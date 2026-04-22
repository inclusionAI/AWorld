## Why

The DingTalk gateway currently processes inbound callbacks synchronously inside the stream callback handler. That makes the platform acknowledgement depend on full agent execution, which increases the chance of provider retries and duplicate user-visible replies.

The same gateway path also does not preserve enough DingTalk session routing metadata to push later cron execution results back through the originating DingTalk conversation. Users can create reminder-style cron jobs from DingTalk, but completion notifications are not routed back to the same channel.

## What Changes

- Acknowledge DingTalk callbacks immediately and continue agent execution in the background.
- Suppress duplicate DingTalk callback processing within a short retry window.
- Capture cron job ids created during a DingTalk session and persist a local `job_id -> DingTalk session` binding in the gateway layer.
- Start and configure the gateway-local cron scheduler runtime that executes DingTalk-originated jobs, including agent swarm resolution and notification fanout wiring.
- Emit gateway-side operational logs for DingTalk inbound queries, runtime outputs, and final replies so `aworld-cli gateway server` can be inspected from the background process.
- Align DingTalk visible streaming behavior with the existing claw gateway by separating assistant-visible text streaming from full runtime output observation.
- Enrich DingTalk inbound messages with sender/conversation context and convert inbound attachments into gateway-ready multimodal input where possible.
- Throttle intermediate AI Card streaming updates so tiny chunk bursts do not spam DingTalk while preserving final card completion.
- Reuse the existing cron `notification_sink` extension point to fan out cron completion notifications back to DingTalk without changing core scheduler semantics.

## Capabilities

### Added Capabilities
- `gateway-channels`: DingTalk callbacks are retriable without duplicate user-visible replies, and DingTalk-originated cron notifications can route back to the source conversation.
- `gateway-channels`: DingTalk visible streaming is assistant-text-only while gateway runtime observation remains available for cron binding capture and logging.
- `gateway-channels`: DingTalk inbound attachments are downloaded, normalized, and forwarded as multimodal input when supported.

## Impact

- Affects `aworld_gateway/channels/dingding/*`.
- Preserves existing framework scheduler behavior by using the existing notification sink extension point instead of changing cron execution semantics.
- Preserves the existing DingTalk cron pushback chain by keeping runtime `on_output` observation intact even when visible text streaming is filtered.
- Does not introduce new framework-level hook points.
