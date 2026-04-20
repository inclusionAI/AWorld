## ADDED Requirements

### Requirement: AgentTrainer is the unified training entry point
The repository SHALL provide `AgentTrainer` as the unified entry point for agent training workflows.

#### Scenario: Starting a training run
- **WHEN** a contributor configures training through the supported training surface
- **THEN** the training flow can be initiated through `AgentTrainer`
- **AND** training components are coordinated from that entry point

### Requirement: Training integrations validate core training modules
Training integrations SHALL validate agent, dataset, reward, and configuration inputs before training begins.

#### Scenario: Initializing a training processor
- **WHEN** a training run is prepared
- **THEN** the training integration validates the configured agent, dataset, reward logic, and training configuration
- **AND** initialization fails clearly when required inputs are invalid

### Requirement: Training integrations support pluggable backends
The repository SHALL allow backend-specific training processors to integrate behind the shared training entry point.

#### Scenario: Registering a training backend
- **WHEN** a contributor adds or selects a supported training backend
- **THEN** that backend integrates through the repository's shared training abstraction
- **AND** the training entry point remains stable for callers
