## ADDED Requirements

### Requirement: Runtime maintains a live HUD snapshot during execution
The AWorld runtime SHALL maintain a live HUD snapshot for the active CLI session so HUD providers can consume current execution state without reconstructing it from console output, history files, or executor internals.

#### Scenario: Executor updates HUD state during a task lifecycle
- **WHEN** an executor starts a task, reports streaming progress, or finishes execution
- **THEN** it updates the shared runtime HUD snapshot through the runtime HUD contract
- **AND** subsequent toolbar refreshes observe the updated snapshot state

#### Scenario: Multiple HUD providers consume the same live state
- **WHEN** more than one HUD provider renders during the same refresh cycle
- **THEN** each provider receives the same live runtime HUD snapshot for that cycle
- **AND** no provider needs to recollect executor activity independently

### Requirement: Runtime preserves a usable HUD snapshot across transient gaps
The AWorld runtime SHALL preserve a usable HUD snapshot across short task transitions and partial telemetry gaps so the toolbar remains stable while execution state changes.

#### Scenario: A task finishes between toolbar refreshes
- **WHEN** a task completes or transitions to idle before the next toolbar refresh
- **THEN** the runtime retains the last useful HUD snapshot long enough for the toolbar to remain informative
- **AND** active-running markers are cleared or downgraded appropriately

#### Scenario: Some live HUD fields are temporarily unavailable
- **WHEN** the runtime cannot populate one or more live HUD fields such as model, VCS detail, or context capacity
- **THEN** the runtime still returns a valid HUD snapshot
- **AND** unavailable fields are omitted or marked unknown rather than breaking snapshot assembly
