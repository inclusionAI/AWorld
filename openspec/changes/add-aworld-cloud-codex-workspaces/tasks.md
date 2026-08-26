## 1. Domain Foundation (complete)

- [x] 1.1 Add `aworld.cloud` package structure, module documentation, typed IDs, UTC timestamp helpers, workspace and run enums, immutable records, and stable cloud error codes.
- [x] 1.2 Implement and unit-test allowed workspace and run state transitions, terminal-state immutability, retry lineage, and the one-active-run-per-workspace invariant.
- [x] 1.3 Define repository and executor protocols with no dependency on SQLite, a concrete provider, FastAPI, the gateway runtime, or reference repositories.
- [x] 1.4 Add administrator-owned cloud settings for feature enablement, data root, database path, worker identity, concurrency, lease timing, allowed profiles, images, resources, and network policy.

## 2. Durable Repository (complete)

- [x] 2.1 Implement an idempotent versioned SQLite schema for workspaces, mounts, runs, events, run files, and idempotency keys.
- [x] 2.2 Configure WAL, busy timeout, foreign keys, short transactions, and safe timestamp serialization with explicit UTC offsets.
- [x] 2.3 Implement workspace create, list, get, compare-and-set transition, and release persistence operations.
- [x] 2.4 Implement run submit, list, get, atomic claim, heartbeat, transition, cancellation request, retry creation, and expired-lease query operations.
- [x] 2.5 Implement monotonic per-run event append/read operations and run-file manifest operations.
- [x] 2.6 Add concurrency, idempotency, pagination, restart persistence, and database-lock regression tests.

## 3. Lifecycle Service and Worker (complete)

- [x] 3.1 Implement workspace creation, inspection, listing, and release use cases with profile resolution and path validation at the service boundary.
- [x] 3.2 Implement run submission, inspection, listing, cancellation, and retry use cases with idempotency and state validation.
- [x] 3.3 Implement a fake executor that supports deterministic start, progress, completion, failure, cancellation, and reattachment outcomes for tests.
- [x] 3.4 Implement a durable worker loop that claims runs up to configured capacity, heartbeats leases, updates workspace state, persists executor identity, and records lifecycle events.
- [x] 3.5 Implement startup reconciliation for queued runs, expired leases, reattachable executors, and non-reattachable interrupted runs without automatic replay.
- [x] 3.6 Add service-and-worker integration tests covering success, start failure, execution failure, cancellation, retry, restart recovery, and worker contention.

## 4. HTTP API and Event Streaming (complete)

- [x] 4.1 Add Pydantic request and response contracts for workspace, run, event, file, pagination, idempotency, and stable errors.
- [x] 4.2 Add `/api/v1/cloud/workspaces` create, list, get, and release routes behind an opt-in feature flag.
- [x] 4.3 Add `/api/v1/cloud/runs` submit, list, get, cancel, and retry routes.
- [x] 4.4 Add paginated event retrieval and reconnectable Server-Sent Events using per-run sequence and `Last-Event-ID`.
- [x] 4.5 Add file listing and secure byte-range file retrieval restricted to manifest-listed run files.
- [x] 4.6 Map domain errors to stable HTTP status and error-code responses, and verify timestamps include a UTC offset.
- [x] 4.7 Add API integration tests using the fake executor and temporary SQLite, including API-process restart.

## 5. Query/Benchmark and Trajectory Contract Slice

- [x] 5.1 Rebaseline proposal, design, requirements, and task phases to the private-cloud architecture.
- [x] 5.2 Add versioned requests, typed `query`/`benchmark` modes, structured benchmark metadata and terminal outcomes, query-mode rejection, and retry preservation.
- [x] 5.3 Add a provider-neutral executor name and optional benchmark adapter protocol without importing a concrete provider or harness.
- [x] 5.4 Add typed canonical/raw trajectory manifest semantics and expose canonical ATIF identity through run and file responses.
- [x] 5.5 Require exactly one executor-produced canonical ATIF file for success and make the fake executor produce its own deterministic fixture.
- [x] 5.6 Add additive SQLite schema v2 migration and persistence for run mode, request version, benchmark data, and trajectory metadata, including v1 roll-forward coverage.
- [x] 5.7 Add domain, repository, worker, protocol, and HTTP compatibility tests for omitted-mode defaults, benchmark validation/results, and trajectory retrieval.
- [x] 5.8 Run scoped tests, practical lint/type checks, strict OpenSpec validation, and review the diff for unrelated changes.

## 6. Private-Cloud Storage and Scheduling

- [ ] 6.1 Add tenant-aware repository fields and protocol operations with additive rolling migrations.
- [ ] 6.2 Implement and conformance-test a PostgreSQL or MySQL repository with transactional claims and leases.
- [ ] 6.3 Add a Redis-backed scheduling/wakeup implementation while the durable repository remains lifecycle truth.
- [ ] 6.4 Add an artifact-store protocol and OSS/MinIO implementations with immutable checksums, bounded reads, and signed access.
- [ ] 6.5 Add retention, reconciliation, backup/restore, and mixed-version deployment tests.

## 7. Private-Cloud Identity and Policy

- [ ] 7.1 Add authenticated principal and tenant context to API, service, repository, event, file, and idempotency boundaries.
- [ ] 7.2 Enforce tenant ownership and non-disclosure for workspace, run, event, and file operations.
- [ ] 7.3 Add secret-reference resolution, least-privilege worker delivery, redaction, rotation, and audit records.
- [ ] 7.4 Enforce administrator-owned provider, mount, image, resource, ingress, and egress policies.
- [ ] 7.5 Add cross-tenant, secret leakage, signed URL, and network-policy security tests.

## 8. OpenSandbox and Optional Executors

- [ ] 8.1 Implement OpenSandbox as the primary remote `ExecutorProvider` without changing lifecycle contracts.
- [ ] 8.2 Add OpenSandbox start, event, result, cancellation, inspection, reattachment, and ATIF conformance tests.
- [ ] 8.3 Add optional aworld-env and/or local Docker providers behind the same protocol where deployments require them.
- [ ] 8.4 Add provider capability discovery, administrator selection, health checks, and fail-closed credential handling.

## 9. Server CLI User Path

- [ ] 9.1 Add a typed HTTP client with endpoint configuration, authentication, timeouts, error decoding, and SSE reconnection.
- [ ] 9.2 Add workspace create/list/show/release commands with machine-readable output.
- [ ] 9.3 Add run submit/list/show/watch/cancel/retry commands supporting both modes and versioned benchmark metadata.
- [ ] 9.4 Add file/log/artifact/trajectory listing and download commands, preferring canonical ATIF.
- [ ] 9.5 Add CLI contract and end-to-end tests against the Server API and fake executor.

## 10. Optional Benchmark Adapters

- [ ] 10.1 Define adapter registration, configuration, capability, and failure mapping contracts.
- [ ] 10.2 Add a generic benchmark adapter conformance suite independent of any harness.
- [ ] 10.3 If required by a deployment, implement Harbor as a separately packaged optional adapter with no Cloud-core import.
- [ ] 10.4 Verify query mode and Cloud startup operate with no benchmark adapter installed.

## 11. Deployment and E2E Verification

- [ ] 11.1 Document private-cloud topology, configuration, identity, secrets, network policy, storage, retention, recovery, and upgrades.
- [ ] 11.2 Verify query and benchmark runs through CLI, Server API, worker, OpenSandbox, events/files, and canonical ATIF retrieval.
- [ ] 11.3 Verify cancellation, retry, restart, provider reattachment, and missing-trajectory failure behavior.
- [ ] 11.4 Verify horizontal repository/scheduler behavior, tenant isolation, object-storage access, and rolling upgrades.
- [ ] 11.5 Review final changes for unrelated edits and record environment-specific limitations.
