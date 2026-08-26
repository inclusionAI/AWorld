## ADDED Requirements

### Requirement: Canonical trajectory manifest
Every successful run SHALL expose exactly one executor-produced canonical trajectory file through the existing run-file API. Its manifest SHALL declare kind `trajectory`, format `atif`, an ATIF schema version, role `canonical`, size, checksum, and download identity.

#### Scenario: Query run completes successfully
- **WHEN** a query executor returns a final ATIF trajectory and successful result
- **THEN** the run response identifies its canonical trajectory file and the file endpoint returns the executor-produced bytes

#### Scenario: Benchmark run completes successfully
- **WHEN** a benchmark executor returns a final ATIF trajectory and successful result
- **THEN** the same run and file contracts expose the canonical trajectory without a benchmark-specific download API

### Requirement: Optional raw provider trajectories
An executor MAY retain provider-native trajectories as additional manifest entries with role `provider_raw`; these SHALL NOT replace the canonical ATIF entry.

#### Scenario: Provider returns native and ATIF traces
- **WHEN** an executor returns a provider-native trace and a normalized ATIF trace
- **THEN** both files are retrievable and only the ATIF file is identified as canonical

### Requirement: No control-plane trajectory fabrication
The control plane SHALL validate and persist trajectory manifests but SHALL NOT synthesize trajectory content for production providers.

#### Scenario: Provider omits ATIF
- **WHEN** a provider returns nominal success without a canonical ATIF file
- **THEN** the worker fails the run with `trajectory_missing` and does not invent a replacement file

### Requirement: Immutable per-run files
The system SHALL store stdout, stderr, normalized events, result data, trajectories, and discovered artifacts under an immutable run identity or equivalent object-storage prefix.

#### Scenario: Executor emits logs and artifacts
- **WHEN** an executor returns file metadata
- **THEN** the system preserves separate manifest entries with size and checksum

### Requirement: Sequenced structured events
The system SHALL assign each run event a monotonically increasing sequence and SHALL preserve unknown provider event payloads without failing the run.

#### Scenario: Unknown provider event type
- **WHEN** a provider emits an unrecognized valid event
- **THEN** a generic structured event is published at the next sequence and raw evidence remains available according to policy

### Requirement: Reconnectable event streaming
The system SHALL expose event pagination and Server-Sent Events and resume after the last acknowledged sequence.

#### Scenario: Reconnect to a live stream
- **WHEN** a client reconnects with `Last-Event-ID`
- **THEN** only later events are emitted until terminal state or disconnection

### Requirement: Secure file retrieval
The system SHALL list files independently and allow bounded or byte-range reads only for authorized, manifest-listed files.

#### Scenario: Read part of a large file
- **WHEN** an authorized client requests a valid byte range
- **THEN** the system returns only that range with correct range metadata

### Requirement: Durable terminal result
The system SHALL expose actual timing, exit status, terminal state, error, file summary, canonical trajectory identity, and benchmark reward/result when applicable from persisted state.

#### Scenario: Inspect a terminal run after restart
- **WHEN** a client retrieves a terminal run after process restart
- **THEN** its terminal and trajectory metadata match the durable repository

#### Scenario: Inspect a terminal benchmark after restart
- **WHEN** a benchmark completed with a provider- or adapter-produced outcome
- **THEN** its finite reward and JSON-compatible verifier result match the durable repository
