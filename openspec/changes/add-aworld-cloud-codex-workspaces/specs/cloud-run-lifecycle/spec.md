## ADDED Requirements

### Requirement: Versioned unified run submission
The system SHALL accept `query` and `benchmark` modes through one versioned run resource and lifecycle. An omitted mode or request schema version SHALL default to `query` and the current v1 request schema.

#### Scenario: Existing client submits without a mode
- **WHEN** a client submits task text and omits mode and request schema version
- **THEN** the system persists a `query` run using the current v1 request schema and returns a stable run ID in `QUEUED` state

#### Scenario: Client submits a benchmark run
- **WHEN** a client submits mode `benchmark` with dataset and task identifiers and optional harness and verifier identifiers
- **THEN** the system persists that structured metadata on the same run resource used by query mode

#### Scenario: Request mode and metadata conflict
- **WHEN** a query request includes benchmark metadata or a benchmark request omits required benchmark metadata
- **THEN** the system rejects the request as invalid without creating a run

#### Scenario: Client submits benchmark outcome
- **WHEN** a client supplies reward or verifier result in a run request
- **THEN** the system rejects it because benchmark outcome is terminal provider or adapter output

#### Scenario: Benchmark produces a verifier outcome
- **WHEN** a benchmark executor or configured adapter produces a finite reward and JSON-compatible result
- **THEN** the terminal run persists and returns that trusted outcome while retry omits the prior outcome

#### Scenario: Query produces benchmark-only output
- **WHEN** a query executor returns benchmark reward or verifier output
- **THEN** the run fails rather than exposing benchmark-only fields on a query result

#### Scenario: Unsupported request schema
- **WHEN** a client submits an unknown request schema version
- **THEN** the system rejects the request rather than guessing its semantics

### Requirement: Durable run submission
The system SHALL persist each accepted run in `QUEUED` state before acknowledging submission, including request version, mode, and optional benchmark metadata.

#### Scenario: Submit to a ready workspace
- **WHEN** a valid query or benchmark request targets a `READY` workspace
- **THEN** the returned run can be loaded after restart with identical request semantics

### Requirement: Transactional run claiming
The worker SHALL claim queued runs with an atomic expected-state transition so a run is owned by at most one worker, independent of mode or executor provider.

#### Scenario: Multiple workers contend for one run
- **WHEN** two workers attempt to claim the same `QUEUED` run
- **THEN** exactly one transition to `STARTING` succeeds and only that worker invokes an executor

### Requirement: Shared explicit lifecycle
Both modes SHALL use the same run state machine and SHALL record each accepted transition as a sequenced event.

#### Scenario: Successful execution with canonical trajectory
- **WHEN** an executor exits successfully and returns exactly one canonical ATIF trajectory manifest entry
- **THEN** the run transitions through `STARTING` and `RUNNING` to `SUCCEEDED` with actual timestamps

#### Scenario: Executor omits canonical trajectory
- **WHEN** an executor otherwise reports success without exactly one canonical ATIF trajectory file
- **THEN** the run becomes `FAILED` with error code `trajectory_missing`

#### Scenario: Executor failure
- **WHEN** the executor fails to start or exits unsuccessfully
- **THEN** the run becomes `FAILED` with a stable error code and redacted diagnostic message

### Requirement: Provider-neutral execution
The worker SHALL depend on a provider-neutral executor protocol for start, wait,
inspect, and cancel operations. The MVP SHALL configure Local Docker directly;
OpenSandbox MAY replace it for production and `aworld-env` MAY provide
compatibility/reuse. AWorld Cloud SHALL NOT expose a Kubernetes layer or API
contract.

#### Scenario: Provider is replaced
- **WHEN** an administrator selects another conforming executor provider
- **THEN** Server API resources, lifecycle states, events, and file contracts remain unchanged

### Requirement: Optional benchmark adaptation
Benchmark preparation and verification SHALL remain at the provider/adapter
boundary, and query mode SHALL NOT require an adapter. The MVP MAY invoke Harbor
through its CLI but SHALL NOT import Harbor Python modules into Cloud core.

#### Scenario: Query runs without benchmark packages
- **WHEN** no benchmark adapter is installed and a query run is submitted
- **THEN** the query remains executable through the configured provider

### Requirement: Benchmark terminal outcome
The system SHALL persist an optional finite reward and JSON-compatible verifier result only on a terminal benchmark run. Query runs SHALL NOT carry benchmark outcome.

#### Scenario: Benchmark provider returns an outcome
- **WHEN** a benchmark run reaches a terminal state with provider- or adapter-produced reward and result
- **THEN** subsequent reads return the same immutable outcome after process restart

#### Scenario: Query provider returns benchmark-only output
- **WHEN** a query provider result contains benchmark outcome
- **THEN** the worker rejects that provider result and does not attach the outcome to the query run

### Requirement: Restart recovery
The system SHALL use persisted leases and executor identities to reconcile non-terminal work after an API or worker restart.

#### Scenario: Recover queued work
- **WHEN** the worker starts and durable `QUEUED` runs exist
- **THEN** those runs remain eligible for claiming without client resubmission

#### Scenario: Expired run cannot be reattached
- **WHEN** a non-terminal run has an expired lease and its executor cannot be positively reattached
- **THEN** the system marks it `FAILED` with `worker_lease_expired` rather than blindly replaying it

### Requirement: Idempotent cancellation and retry lineage
Cancellation SHALL be safe to repeat. Retry SHALL create a new run, retain the original attempt, and copy request version, mode, and benchmark metadata.

#### Scenario: Retry a failed benchmark run
- **WHEN** a client retries a failed benchmark run
- **THEN** the new `QUEUED` run increments attempt, references the source, and preserves its benchmark request semantics

### Requirement: Configurable concurrency
The worker SHALL limit active runs globally while preserving one active run per workspace.

#### Scenario: Capacity is exhausted
- **WHEN** active runs equal configured capacity
- **THEN** additional durable runs remain `QUEUED` until a slot is available
