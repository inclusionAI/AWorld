## ADDED Requirements

### Requirement: DingTalk callback acknowledgement is decoupled from full execution
The gateway SHALL acknowledge DingTalk stream callbacks without waiting for complete downstream agent execution.

#### Scenario: DingTalk user sends a message that takes time to process
- **WHEN** the DingTalk stream callback is received
- **THEN** the gateway returns the provider acknowledgement immediately
- **AND** agent execution continues asynchronously in the gateway

### Requirement: DingTalk duplicate callback deliveries do not produce duplicate replies
The gateway SHALL suppress duplicate DingTalk callback deliveries within the provider retry window so repeated delivery does not produce repeated user-visible responses.

#### Scenario: DingTalk retries the same inbound callback
- **WHEN** the gateway receives the same DingTalk callback payload again within the retry window
- **THEN** it does not execute the same message round twice
- **AND** it does not send a duplicate reply to the user

### Requirement: DingTalk-originated cron jobs can route notifications back to DingTalk
The gateway SHALL preserve DingTalk session routing metadata for cron jobs created from DingTalk conversations and use that metadata when cron notifications are published.

#### Scenario: DingTalk user creates a reminder through cron
- **WHEN** the DingTalk conversation creates one or more cron jobs
- **THEN** the gateway records a binding between each created cron job id and the DingTalk session routing metadata
- **AND** later cron completion notifications can be pushed back to the originating DingTalk conversation through the existing scheduler notification sink extension point
