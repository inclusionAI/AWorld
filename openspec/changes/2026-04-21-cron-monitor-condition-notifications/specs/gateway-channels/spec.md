## ADDED Requirements

### Requirement: DingTalk cron fanout supports silent terminal cleanup
The gateway SHALL support cron notifications that are not user-visible while still using them to clean up DingTalk cron routing bindings.

#### Scenario: Cron job ends with a silent final success
- **WHEN** a DingTalk-originated cron job reaches its final scheduled run with a non-user-visible terminal notification
- **THEN** the gateway does not send a new DingTalk message to the user
- **AND** it still clears the stored `job_id -> DingTalk session` binding for that finished job

### Requirement: DingTalk cron fanout preserves immediate user-visible event notifications
The gateway SHALL continue to push user-visible cron notifications back to the originating DingTalk conversation as soon as they are published.

#### Scenario: User-visible recurring event occurs
- **WHEN** the scheduler publishes a user-visible notification for a DingTalk-originated recurring cron execution
- **THEN** the gateway fans that notification back to the bound DingTalk conversation immediately
- **AND** the recurring job remains bound for later notifications until the job finally ends
