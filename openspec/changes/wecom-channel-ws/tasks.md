## 1. Gateway Control Plane

- [x] 1.1 Add `WecomChannelConfig` and wire it into `ChannelConfigMap`.
- [x] 1.2 Export the config model and normalize legacy `wecom` payloads in the loader.
- [x] 1.3 Register `wecom` as implemented and validate env-backed credentials in the registry.
- [x] 1.4 Extend runtime default-agent inheritance and status coverage for `wecom`.

## 2. WeCom Channel Subsystem

- [x] 2.1 Replace the placeholder adapter with an implemented adapter backed by a connector.
- [x] 2.2 Add a minimal WebSocket connector with subscribe handshake, callback intake, and correlated responses.
- [x] 2.3 Implement phase-1 text send with reply-bound and proactive fallback paths.
- [x] 2.4 Implement DM/group policy filtering and dedup for inbound callbacks.
- [x] 2.5 Implement phase-2 inbound image/file caching into `.aworld/gateway/wecom/attachments/<bot_id>/` and surface attachment / structured media / multimodal metadata without changing `InboundEnvelope`.
- [x] 2.6 Implement phase-2 outbound local media upload/send using channel-local attachment intent, upload-media websocket commands, and follow-up markdown delivery for remaining text.
- [x] 2.7 Enforce outbound media size/type limits, including downgrade-to-file behavior and pre-upload rejection for oversize payloads.
- [x] 2.8 Add application heartbeat and connector-local reconnect handling for interrupted WebSocket sessions.

## 3. Validation

- [x] 3.1 Add `tests/gateway/test_wecom_config.py`.
- [x] 3.2 Add `tests/gateway/test_wecom_adapter.py`.
- [x] 3.3 Add `tests/gateway/test_wecom_connector.py`.
- [x] 3.4 Update registry/runtime/config-loader tests for `wecom`.
- [x] 3.5 Validate with `openspec validate wecom-channel-ws`.
