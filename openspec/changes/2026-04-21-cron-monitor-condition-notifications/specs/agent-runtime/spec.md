## ADDED Requirements

### Requirement: Cron executions can emit user-visible success events
The runtime SHALL allow a successful cron execution to explicitly mark itself as user-visible so channels can surface that execution as an immediate event notification.

#### Scenario: Recurring task produces a user-visible hit event
- **WHEN** a recurring cron execution returns success with a user-visible result
- **THEN** the runtime publishes that execution as a notification event immediately
- **AND** recurring scheduling continues according to the job configuration unless the run budget is exhausted

### Requirement: Cron executions can stay silent on success
The runtime SHALL allow a successful cron execution to complete without producing a user-visible notification.

#### Scenario: Recurring task completes successfully without a visible event
- **WHEN** a recurring cron execution returns success with `user_visible = false`
- **THEN** the runtime does not publish a user-visible notification for that execution
- **AND** the job remains eligible for later scheduled runs while it still has a next run

### Requirement: Final silent completion still supports channel cleanup
The runtime SHALL allow a final successful cron execution to remain silent to the user while still producing a non-user-visible terminal notification for channel cleanup.

#### Scenario: Final scheduled run ends silently
- **WHEN** a cron job reaches its last successful run and that run is marked `user_visible = false`
- **THEN** the runtime does not surface a user-visible message
- **AND** it still publishes a terminal non-user-visible notification so channel integrations can clean up routing state
