# Proposal

## Why

The DingTalk gateway connector currently falls back to a hard-coded `aworld` agent id when the channel-level `default_agent_id` is unset. This bypasses the gateway-level default agent configuration and can select an agent id that does not exist in the active runtime.

## What Changes

- Make the DingTalk runtime path inherit `GatewayConfig.default_agent_id` when `channels.dingding.default_agent_id` is unset.
- Remove the hard-coded `aworld` fallback from the DingTalk connector.
- Add regression coverage for the inherited default-agent behavior.
