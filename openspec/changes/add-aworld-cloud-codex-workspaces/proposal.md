## Why

AWorld needs a private-cloud execution service where users and CI can submit ordinary agent queries or benchmark tasks through one durable API, observe execution without provider access, and retrieve a final interoperable trajectory for every successful run. The existing durable control-plane foundation proves the lifecycle locally, but its proposal still describes a Codex/Docker workspace product rather than the intended provider-neutral AWorld Cloud service.

## What Changes

- Define one Server API and CLI contract for `query` and `benchmark` runs using the same durable lifecycle, event stream, files, cancellation, and retry semantics. Benchmark identity is immutable request data; reward and verifier result are terminal output.
- Make the final canonical trajectory an executor-produced, manifest-listed ATIF file; provider-native trajectories may also be retained as raw trajectory files.
- Keep execution behind a provider-neutral protocol. The MVP selects Local Docker;
  OpenSandbox remains the future production provider and `aworld-env` remains an
  optional compatibility path.
- Run the first real benchmark through Harbor's Docker harness without importing
  Harbor's Python internals into Cloud core.
- Use SQLite and a local artifact volume for the MVP while retaining repository,
  scheduler, and artifact-store seams for later PostgreSQL, Redis, and
  S3-compatible storage implementations.
- Require tenant-scoped authorization, secret references, server-owned network policy, and auditability at every external boundary.
- Evolve persisted and HTTP contracts additively so older clients default to `query` and existing SQLite databases roll forward without rewriting history.
- Deliver one runnable Docker Compose entrypoint containing the Cloud server,
  worker, and on-demand CLI, backed by SQLite, a local artifact volume, the host
  Docker socket, and the Local Docker/Harbor provider.

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
- Preserves the fake executor for deterministic tests while the shipped MVP uses
  a real Local Docker/Harbor execution path.
- Requires no Harbor Python import in Cloud core; Harbor is pinned in the runtime
  image and invoked through its documented CLI.
- Adds no dependency on external benchmark suites or unrelated deployment stacks.
- Defines no Kubernetes layer or Kubernetes API contract; any provider-internal implementation remains outside AWorld Cloud.
- Makes Docker Compose the single MVP deployment entrypoint for this change.
