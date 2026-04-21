## ADDED Requirements

### Requirement: DingTalk complex requests send an immediate visible processing acknowledgement
The gateway SHALL send a user-visible acknowledgement before downstream execution for DingTalk requests that are likely to take noticeable time.

#### Scenario: DingTalk user submits a complex research-style request
- **WHEN** the inbound DingTalk request includes signals such as multi-step analysis, file reasoning, report generation, or artifact creation
- **THEN** the gateway sends an immediate “processing” acknowledgement to the DingTalk conversation
- **AND** downstream agent execution continues asynchronously after that acknowledgement

### Requirement: DingTalk downstream execution preserves user-declared delivery constraints
The gateway SHALL carry explicit guardrails for user-declared constraints when forwarding DingTalk text to downstream agents.

#### Scenario: DingTalk user asks for file analysis and HTML delivery
- **WHEN** the user request explicitly mentions source files or logs, time scope, output format, or delivery expectations
- **THEN** the gateway forwards the original request together with an execution guardrail that preserves those constraints
- **AND** downstream task decomposition is instructed not to drop those constraints

### Requirement: DingTalk AI Card streaming failures remain user-visible
The gateway SHALL avoid silent user experience regressions when DingTalk AI Card streaming is unavailable.

#### Scenario: AI Card cannot be created for a complex DingTalk request
- **WHEN** the DingTalk channel cannot create or deliver an AI Card for a request that includes strong complexity signals such as attachments, report generation, reminder scheduling, or multi-step analysis
- **THEN** the gateway sends a lightweight visible acknowledgement to the DingTalk conversation before downstream execution completes
- **AND** the gateway still sends the final text reply when downstream execution finishes
- **AND** cron job binding and notification fanout continue to use raw runtime outputs unchanged

#### Scenario: AI Card cannot be created for a short fast DingTalk request
- **WHEN** the DingTalk channel cannot create or deliver an AI Card for a short request without strong complexity signals
- **AND** downstream execution finishes within the fallback acknowledgement delay window
- **THEN** the gateway sends only the final text reply
- **AND** the user does not receive an extra immediate “processing” acknowledgement

#### Scenario: AI Card cannot be created for a short slow DingTalk request
- **WHEN** the DingTalk channel cannot create or deliver an AI Card for a short request without strong complexity signals
- **AND** downstream execution exceeds the fallback acknowledgement delay window
- **THEN** the gateway sends a delayed lightweight visible acknowledgement while execution continues
- **AND** the gateway still sends the final text reply when downstream execution finishes

### Requirement: DingTalk AI Card lifecycle is diagnosable
The gateway SHALL emit diagnostic logs for the AI Card lifecycle so operators can explain whether user-visible streaming was attempted, skipped, degraded, or finalized.

#### Scenario: Operator inspects a DingTalk request with missing visible streaming
- **WHEN** a DingTalk request enters execution
- **THEN** the gateway logs whether AI Card creation was disabled, misconfigured, skipped for missing target data, or failed during create/deliver calls
- **AND** the gateway logs whether streaming updates and finalization succeeded or fell back to plain text
- **AND** the logs do not change the raw runtime output stream used for cron job binding
