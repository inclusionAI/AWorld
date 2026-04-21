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
