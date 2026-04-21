## ADDED Requirements

### Requirement: Gateway server startup suppresses low-value boot noise
The gateway CLI SHALL reduce low-value startup noise while preserving operationally important gateway logs.

#### Scenario: Operator starts `aworld-cli gateway server`
- **WHEN** the gateway server boots in its dedicated server mode
- **THEN** agent loader and plugin manager file-by-file startup details are emitted below the default console verbosity
- **AND** gateway, DingTalk, cron scheduler, and runtime business logs remain visible at normal console verbosity

### Requirement: Quiet boot remains scoped to gateway server startup
The gateway CLI SHALL scope quiet boot behavior to the gateway server path rather than changing general CLI logging defaults.

#### Scenario: Other CLI entrypoints load agents
- **WHEN** agent loading happens outside gateway server mode
- **THEN** existing non-gateway logging behavior remains unchanged
