## ADDED Requirements

### Requirement: Tenant-scoped authorization
Every workspace, run, event, file, idempotency key, and provider operation SHALL be scoped to an authenticated tenant and authorized principal before shared private-cloud deployment.

#### Scenario: Cross-tenant resource request
- **WHEN** a principal requests a resource owned by another tenant
- **THEN** access is denied without disclosing the resource's existence or metadata

### Requirement: Server-owned execution policy
The system SHALL derive provider, mounts, images, resources, and network policy from administrator configuration and SHALL reject client overrides outside that policy.

#### Scenario: Client attempts infrastructure override
- **WHEN** a request supplies an arbitrary host path, privileged mode, provider credential, or unrestricted network option
- **THEN** the system rejects the request without forwarding that value to an executor

### Requirement: Runtime isolation defaults
Providers SHALL use a non-root identity where supported, SHALL NOT expose a host container socket, SHALL NOT use privileged mode, and SHALL apply administrator-defined resource and network limits.

#### Scenario: Provider request is constructed
- **WHEN** the worker starts a run
- **THEN** only allow-listed workspace data, secret references, resource limits, and network policy are included

### Requirement: Independent execution identity
Each workspace SHALL use isolated configuration and credentials and SHALL NOT mount or expose the control-plane service account's home or credentials.

#### Scenario: Workspace is reused
- **WHEN** an executor recreates a workspace runtime
- **THEN** workspace-owned configuration persists without being replaced by host credentials

### Requirement: Secret confidentiality
Secrets SHALL be referenced through an administrator-controlled secret boundary and SHALL never be returned through APIs, events, ordinary diagnostics, or file manifests.

#### Scenario: Provider output contains a configured secret
- **WHEN** secret material appears in output selected for ordinary publication
- **THEN** the published value is redacted and an auditable security event is recorded according to policy

### Requirement: Private network policy
Ingress and egress SHALL be determined by tenant-aware administrator profiles. Provider callbacks and artifact access SHALL be authenticated, scoped, auditable, and time-limited where applicable.

#### Scenario: Run requests broader egress
- **WHEN** a caller requests network access beyond its assigned profile
- **THEN** the request is rejected or executed with the narrower server-owned policy, according to the published API contract

### Requirement: Read-only reference enforcement
Optional reference repositories SHALL be selected by administrators and mounted read-only; Cloud core SHALL NOT require unrelated source trees or benchmark suites.

#### Scenario: Runtime attempts to modify a reference
- **WHEN** a process writes beneath a configured reference mount
- **THEN** the provider denies the write while permitted workspace writes continue
