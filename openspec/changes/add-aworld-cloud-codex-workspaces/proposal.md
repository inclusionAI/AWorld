## Why

AWorld needs a private-cloud execution service where users and CI can submit ordinary agent queries or benchmark tasks through one durable API, observe execution without provider access, and retrieve a final interoperable trajectory for every successful run. The existing durable control-plane foundation proves the lifecycle locally, but its proposal still describes a Codex/Docker workspace product rather than the intended provider-neutral AWorld Cloud service.

## What Changes

- Define one Server API and CLI contract for `query` and `benchmark` runs using the same durable lifecycle, event stream, files, cancellation, and retry semantics. Benchmark identity is immutable request data; reward and verifier result are terminal output.
- Make the final canonical trajectory an executor-produced, manifest-listed ATIF file; provider-native trajectories may also be retained as raw trajectory files.
- Keep execution behind a provider-neutral protocol. OpenSandbox is the primary remote provider; aworld-env and local Docker are optional provider implementations.
- Keep benchmark preparation and verification behind an optional adapter protocol. Harbor may be implemented by an optional adapter, but is never a Cloud-core or query-mode dependency.
- Retain SQLite and the fake executor for development while defining repository, scheduler, and artifact-store seams suitable for PostgreSQL/MySQL, Redis, and OSS/MinIO in private deployments.
- Require tenant-scoped authorization, secret references, server-owned network policy, and auditability at every external boundary.
- Evolve persisted and HTTP contracts additively so older clients default to `query` and existing SQLite databases roll forward without rewriting history.

## Capabilities

### New Capabilities

- `cloud-workspace-lifecycle`: Durable creation, inspection, reuse, and release of isolated AWorld execution workspaces.
- `cloud-run-lifecycle`: Versioned submission, scheduling, cancellation, retry, recovery, and terminal handling shared by query and benchmark runs.
- `cloud-run-observability`: Structured events and file retrieval, including canonical ATIF and optional raw provider trajectories.
- `cloud-workspace-security`: Tenant authorization, server-owned execution policy, secret isolation, and private-network controls.

### Modified Capabilities

None.

## Impact

- Rebaselines `aworld.cloud` and `/api/v1/cloud` as the private-cloud control plane rather than a Docker-specific Codex service.
- Adds additive run-mode, benchmark identity/outcome, and trajectory manifest fields to domain, SQLite, and HTTP contracts.
- Preserves the existing fake executor and SQLite repository for development and tests.
- Requires no benchmark harness package in Cloud core. A future Harbor adapter is optional and separately packaged.
- Adds no dependency on external benchmark suites or unrelated deployment stacks.
