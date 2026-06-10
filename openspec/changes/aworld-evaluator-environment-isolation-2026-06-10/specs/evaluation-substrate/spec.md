## MODIFIED Requirements

### Requirement: Environment lifecycle for runtime evaluation

Runtime-composed evaluation flows SHALL support an opt-in trusted environment lifecycle that resets environment state before a rollout and cleans it up afterward.

#### Scenario: Runtime harness uses environment fixture
- **WHEN** a runtime-composed suite wraps its harness with an environment fixture
- **THEN** the framework SHALL call the fixture reset hook before executing the base rollout
- **AND** the framework SHALL call the fixture cleanup hook after the base rollout finishes

#### Scenario: Environment metadata is serializable
- **WHEN** a fixture returns environment metadata
- **THEN** the framework SHALL preserve only serializable metadata in rollout state, evaluator state, and reports
- **AND** the framework SHALL exclude live handles such as clients, file handles, subprocesses, and credentials from serialized state

#### Scenario: Base harness needs environment context
- **WHEN** environment reset succeeds
- **THEN** the framework SHALL expose the environment snapshot to the base harness through case input, case metadata, and target metadata

### Requirement: Environment isolation across trials

Trial-based evaluation SHALL be able to reset environment state independently for each trial.

#### Scenario: Multi-trial evaluation uses environment isolation
- **WHEN** a suite declares multiple trials and wraps its runtime harness with environment isolation
- **THEN** each expanded trial SHALL receive a distinct reset lifecycle

#### Scenario: Retry runs inside one isolated trial
- **WHEN** a suite composes retry inside the environment-isolated harness
- **THEN** retry attempts SHALL share one environment reset for that trial
- **AND** retry attempts SHALL NOT increase environment reset count

### Requirement: Environment lifecycle failure handling

Environment lifecycle handling SHALL fail closed and preserve cleanup attempts.

#### Scenario: Reset fails
- **WHEN** an environment reset hook fails
- **THEN** the framework SHALL NOT execute the base rollout
- **AND** the evaluation SHALL surface the reset error through the normal runtime error path

#### Scenario: Rollout fails
- **WHEN** the base rollout raises after reset succeeds
- **THEN** the framework SHALL attempt cleanup
- **AND** the framework SHALL preserve the original rollout error if cleanup also fails

#### Scenario: Cleanup fails after rollout success
- **WHEN** cleanup fails after the base rollout returns a terminal state
- **THEN** the framework SHALL mark the rollout state failed and record cleanup error metadata unless the fixture explicitly declares cleanup failure non-fatal

### Requirement: Sandbox execution remains deferred

Environment lifecycle support SHALL define trusted reset/cleanup boundaries without introducing untrusted sandbox command execution.

#### Scenario: Suite requests command-backed sandbox reset
- **WHEN** a suite requires shell commands, container lifecycle, workflow engines, database snapshotting, or filesystem reset
- **THEN** this change SHALL treat that as adapter-specific future work rather than executing arbitrary commands in the evaluator substrate
