## ADDED Requirements

### Requirement: Durable tenant-owned workspace creation
The system SHALL create a tenant-owned durable workspace record before provisioning provider-compatible execution resources and SHALL return a stable workspace identifier.

#### Scenario: Create a workspace from an allowed profile
- **WHEN** a client submits a unique idempotency key and an administrator-defined workspace profile
- **THEN** the system persists one workspace in the caller's tenant, provisions its current backing resources, and transitions it to `READY`

#### Scenario: Repeat an idempotent create request
- **WHEN** the same client repeats workspace creation with the same idempotency key and equivalent payload
- **THEN** the system returns the original workspace without provisioning a duplicate

### Requirement: Workspace inspection and listing
The system SHALL expose authorized workspace lifecycle state, provider-neutral runtime policy summary, timestamps, active run identity, and credential-configuration presence without exposing secrets or provider-internal scheduling details.

#### Scenario: Inspect a ready workspace
- **WHEN** a client requests an existing ready workspace
- **THEN** the response identifies it as `READY` and describes allowed execution targets without returning secret contents

### Requirement: Serialized workspace execution
The system SHALL allow no more than one active run to mutate a workspace at a time, regardless of run mode or Executor Provider.

#### Scenario: Submit while workspace is busy
- **WHEN** a workspace owns a `STARTING`, `RUNNING`, or `CANCELLING` run and another run is submitted
- **THEN** the system rejects the request with `workspace_busy`

### Requirement: Safe workspace release
The system SHALL make workspace release idempotent and SHALL refuse ordinary release while a run is active.

#### Scenario: Release an idle workspace
- **WHEN** a client releases a `READY` workspace
- **THEN** the system removes disposable provider resources, retains auditable metadata according to retention policy, and transitions it to `RELEASED`

#### Scenario: Release a busy workspace
- **WHEN** a client releases a workspace that owns an active run
- **THEN** the system returns `workspace_busy` and does not remove workspace data

### Requirement: Configuration preservation
The system SHALL initialize workspace configuration only when absent, preserve it across provider resource replacement, and retain only secret references at the control-plane boundary.

#### Scenario: Recreate provider resources
- **WHEN** a provider recreates resources for a configured workspace
- **THEN** existing configuration retains its prior checksum and secret values are not returned through the workspace API
