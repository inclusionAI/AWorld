## 1. DingTalk Callback Reliability

- [x] 1.1 Return DingTalk stream callback acknowledgements without waiting for full agent execution.
- [x] 1.2 Add short-window duplicate suppression for repeated DingTalk callback deliveries.

## 2. DingTalk Cron Routing

- [x] 2.1 Capture cron job ids created during DingTalk sessions from gateway-observed tool outputs.
- [x] 2.2 Persist DingTalk session routing metadata for those cron jobs in a gateway-local store.
- [x] 2.3 Route cron completion notifications back through DingTalk using the existing scheduler notification sink extension point.
- [x] 2.4 Prepare the gateway-local cron scheduler runtime for DingTalk-originated cron execution and fanout.
- [x] 2.5 Emit backend-observable DingTalk runtime logs for inbound queries, observed outputs, and final replies.

## 3. DingTalk Streaming Alignment

- [x] 3.1 Filter DingTalk visible streaming down to assistant-facing text while preserving raw runtime observation callbacks for cron routing and logging.
- [x] 3.2 Enrich DingTalk inbound text with sender/conversation context and transform downloadable attachments into multimodal bridge input.
- [x] 3.3 Throttle intermediate AI Card streaming updates while preserving final response delivery semantics.

## 4. Validation

- [x] 4.1 Add gateway tests for duplicate callback suppression.
- [x] 4.2 Add gateway tests for DingTalk cron binding capture and notification fanout.
- [x] 4.3 Run targeted DingTalk gateway and bridge tests.
- [x] 4.4 Add coverage for scheduler bootstrap on DingTalk startup and duplicate suppression without provider ids.
- [x] 4.5 Add coverage for DingTalk runtime logging in the gateway process.
- [x] 4.6 Add coverage for assistant-only visible streaming, inbound attachment input assembly, and AI Card throttling.
