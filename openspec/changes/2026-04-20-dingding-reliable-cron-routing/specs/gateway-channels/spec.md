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

### Requirement: DingTalk gateway startup prepares the cron runtime used by routed notifications
The gateway SHALL ensure the local cron scheduler runtime used for DingTalk-originated jobs is configured and running before those jobs are expected to execute.

#### Scenario: DingTalk channel starts with cron-capable reminders enabled
- **WHEN** the DingTalk connector starts
- **THEN** it configures the scheduler executor so cron jobs can resolve the configured agent swarm
- **AND** it binds the configured default agent for later cron execution
- **AND** it registers the DingTalk cron notification fanout sink before any new notifications are published
- **AND** it starts the local scheduler if it is not already running

### Requirement: DingTalk gateway runtime emits operational logs for inbound and execution flow
The gateway SHALL emit backend-observable logs for DingTalk message intake and downstream execution progress.

#### Scenario: DingTalk user sends a message through the gateway
- **WHEN** the gateway accepts a DingTalk callback and starts a message round
- **THEN** the backend logs the inbound user query with conversation/session routing context
- **AND** runtime-observed outputs emitted through the bridge are logged in compact form
- **AND** the final reply sent back to DingTalk is logged

### Requirement: DingTalk visible streaming only exposes assistant-facing text
The gateway SHALL separate assistant-visible DingTalk text streaming from full runtime output observation so user-visible updates do not include tool or orchestration payloads.

#### Scenario: DingTalk bridge observes mixed runtime outputs during a message round
- **WHEN** the bridge receives runtime outputs that include assistant text plus tool results or other non-user-facing events
- **THEN** the bridge forwards all raw outputs to the runtime observation callback
- **AND** the visible text callback receives only assistant-facing text chunks
- **AND** the final reply rendered back to DingTalk is composed only from the assistant-facing text stream

### Requirement: DingTalk inbound messages preserve sender context and attachment content
The gateway SHALL enrich DingTalk-originated input with session metadata and attachment content before invoking the downstream agent.

#### Scenario: DingTalk user sends text with attachments
- **WHEN** the gateway receives a DingTalk message containing text and one or more downloadable attachments
- **THEN** it appends sender and conversation context to the textual prompt
- **AND** it downloads each attachment into the configured workspace-scoped DingTalk session area
- **AND** image attachments are forwarded as multimodal image input when available
- **AND** non-image attachments are represented in the textual prompt so the agent can reason over the downloaded files

### Requirement: DingTalk AI Card streaming is throttled without losing the final answer
The gateway SHALL throttle intermediate AI Card content updates while still finalizing the card with the full assistant reply.

#### Scenario: DingTalk assistant emits many tiny chunks quickly
- **WHEN** the gateway is streaming a DingTalk AI Card for an active message round
- **THEN** it coalesces rapid assistant text chunks into periodic intermediate updates instead of pushing every chunk immediately
- **AND** it still sends the final full assistant reply when the run finishes
- **AND** this throttling does not change the runtime output observation path used for cron job binding capture
