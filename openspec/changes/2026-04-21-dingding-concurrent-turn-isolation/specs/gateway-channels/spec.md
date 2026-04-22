## ADDED Requirements

### Requirement: DingTalk overlapping turns use isolated runtime sessions
The gateway SHALL avoid reusing the same AWorld runtime session for overlapping DingTalk turns from the same conversation.

#### Scenario: Sequential turns in one DingTalk conversation
- **WHEN** a DingTalk message arrives after the previous turn in the same conversation has already completed
- **THEN** the gateway reuses the existing conversation session for normal multi-turn continuity

#### Scenario: Overlapping turns in one DingTalk conversation
- **WHEN** a new DingTalk message arrives while another turn from the same conversation is still executing
- **THEN** the gateway assigns a new isolated `session_id` for the new turn
- **AND** the new turn does not share session-scoped runtime state with the in-flight turn
- **AND** the latest started turn becomes the conversation's current session for future sequential turns

#### Scenario: Cron notification routing remains unchanged during overlapping turns
- **WHEN** DingTalk overlapping turns are isolated with different runtime sessions
- **THEN** cron `job_id` binding and notification fanout continue to use raw runtime outputs from each turn independently
- **AND** the gateway does not require changes to the existing cron pushback chain
