## 1. DingTalk Callback Reliability

- [x] 1.1 Return DingTalk stream callback acknowledgements without waiting for full agent execution.
- [x] 1.2 Add short-window duplicate suppression for repeated DingTalk callback deliveries.

## 2. DingTalk Cron Routing

- [x] 2.1 Capture cron job ids created during DingTalk sessions from gateway-observed tool outputs.
- [x] 2.2 Persist DingTalk session routing metadata for those cron jobs in a gateway-local store.
- [x] 2.3 Route cron completion notifications back through DingTalk using the existing scheduler notification sink extension point.
- [x] 2.4 Prepare the gateway-local cron scheduler runtime for DingTalk-originated cron execution and fanout.

## 3. Validation

- [x] 3.1 Add gateway tests for duplicate callback suppression.
- [x] 3.2 Add gateway tests for DingTalk cron binding capture and notification fanout.
- [x] 3.3 Run targeted DingTalk gateway and bridge tests.
- [x] 3.4 Add coverage for scheduler bootstrap on DingTalk startup and duplicate suppression without provider ids.
