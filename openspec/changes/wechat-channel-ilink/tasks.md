## 1. Gateway Control Plane

- [x] 1.1 Add `WechatChannelConfig` to `aworld_gateway/config/models.py` and wire it into `ChannelConfigMap`.
- [x] 1.2 Export the new config model from `aworld_gateway/config/__init__.py`.
- [x] 1.3 Update `aworld_gateway/config/loader.py` to normalize any legacy or placeholder-shaped `wechat` payloads safely.
- [x] 1.4 Register `wechat` in `aworld_gateway/registry.py` with the correct implementation flag, label, adapter class, and configuration checks.
- [x] 1.5 Extend `aworld_gateway/runtime.py` status behavior so `wechat` participates in enabled/configured/running/degraded state derivation.

## 2. WeChat Channel Subsystem

- [x] 2.1 Create `aworld_gateway/channels/wechat/` and add the adapter/connector/store module skeleton.
- [x] 2.2 Implement account credential restore for `account_id / token / base_url` without putting runtime state into gateway config.
- [x] 2.3 Implement `context_token` storage keyed by account + peer.
- [x] 2.4 Implement `getupdates` long-poll intake with dedup, retry, and message-to-envelope translation.
- [x] 2.5 Implement final-text outbound send that reuses the latest `context_token` when available.
- [x] 2.6 Implement Phase 1 policy filtering for DM/group traffic and text chunk splitting behavior.

## 3. Validation

- [x] 3.1 Add `tests/gateway/test_wechat_config.py` for defaults and config-loader behavior.
- [x] 3.2 Add `tests/gateway/test_wechat_adapter.py` for lifecycle and send-path behavior.
- [x] 3.3 Add `tests/gateway/test_wechat_connector.py` for long-poll intake, dedup, token caching, and final send behavior.
- [x] 3.4 Extend `tests/gateway/test_registry.py` and `tests/gateway/test_runtime.py` to cover `wechat`.
- [x] 3.5 Validate the OpenSpec change with `openspec validate wechat-channel-ilink`.

## 4. Phase-2 Media Support

- [x] 4.1 Add `aworld_gateway/channels/wechat/media.py` for CDN URL validation, AES helpers, and explicit local-media reference parsing.
- [x] 4.2 Extend `aworld_gateway/channels/wechat/connector.py` to download inbound image/file/video/voice payloads into local attachment cache paths and surface them through attachment prompts plus metadata.
- [x] 4.3 Extend outbound `wechat` send flow to detect explicit local media references, upload ciphertext through iLink CDN, and send media items without redesigning the gateway-wide router contract.
- [x] 4.4 Extend `tests/gateway/test_wechat_connector.py` to cover inbound media download, CDN allowlist rejection, and outbound local image upload behavior.
- [x] 4.5 Extend `aworld_gateway/channels/wechat/adapter.py` and send-path tests so `OutboundEnvelope.events` can express outbound attachment intent, including forced file-attachment delivery for image paths and explicit `video/voice` type overrides.
- [x] 4.6 Extend inbound `wechat` media translation so metadata includes both compatibility attachments and a richer `wechat_media` / `multimodal_parts` structure for later multimodal consumers.
