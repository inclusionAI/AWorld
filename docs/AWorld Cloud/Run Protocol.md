# AWorld Cloud Run Protocol v1

This document is the transport contract for `/api/v1/cloud` run requests, responses, and trajectory files. It supplements the generated OpenAPI schema with lifecycle rules that clients and Executor Providers must preserve.

## Architecture boundary

The private-cloud AWorld Cloud Server calls an Executor Provider directly through the provider-neutral start/wait/inspect/cancel contract. OpenSandbox is the primary remote provider. `aworld-env` is an optional compatibility provider, and Local Docker is for development/debugging only.

Provider-internal orchestration is opaque. No container-orchestrator object or field is part of this protocol.

A Benchmark Adapter is optional and applies only to benchmark preparation or result normalization. Query requests and the Cloud core do not depend on Harbor or another benchmark product.

## Submit request

`POST /api/v1/cloud/workspaces/{workspace_id}/runs` accepts:

```json
{
  "idempotency_key": "client-generated-key",
  "request_schema_version": "aworld.cloud.run-request.v1",
  "mode": "query",
  "task": "Inspect and fix the failing test",
  "model": null,
  "benchmark": null
}
```

`request_schema_version` may be omitted and currently defaults to `aworld.cloud.run-request.v1`. Unknown versions are rejected.

`mode` may be omitted and defaults to `query`:

- `query`: `benchmark` MUST be absent or null.
- `benchmark`: `benchmark.dataset` and `benchmark.task_id` are required non-empty identities; `benchmark.harness` and `benchmark.verifier` are optional non-empty identities.

Reward and verifier result are not accepted as request fields. They are trusted only as terminal output from the Executor Provider or configured Benchmark Adapter.

Example benchmark request:

```json
{
  "idempotency_key": "swe-bench-case-1-attempt-1",
  "mode": "benchmark",
  "task": "Resolve the repository issue",
  "benchmark": {
    "dataset": "swe-bench",
    "task_id": "case-1",
    "harness": "harness-v1",
    "verifier": "patch-verifier-v2"
  }
}
```

Retries preserve request schema, mode, benchmark identity, task, and model while creating a new run ID and attempt.

## Run response additions

Run responses include the following additive v1 fields:

```json
{
  "request_schema_version": "aworld.cloud.run-request.v1",
  "mode": "benchmark",
  "benchmark": {
    "dataset": "swe-bench",
    "task_id": "case-1",
    "harness": "harness-v1",
    "verifier": "patch-verifier-v2"
  },
  "benchmark_outcome": {
    "reward": 0.75,
    "result": {
      "passed": true
    }
  },
  "canonical_trajectory_file_id": "file-run-123-trajectory"
}
```

`benchmark_outcome` is null before terminalization and for query runs. When present, `reward` is null or finite and `result` is a JSON object.

`canonical_trajectory_file_id` is null until a canonical trajectory has been registered. A `SUCCEEDED` run always has exactly one canonical trajectory file ID.

## Trajectory file contract

Trajectory content is produced by the Executor Provider. The control plane registers and serves it but never derives it from events, stdout, or logs.

A trajectory item returned by `GET /api/v1/cloud/runs/{run_id}/files` has `kind=trajectory` and:

```json
{
  "trajectory": {
    "format": "atif",
    "schema_version": "ATIF-v1.7",
    "role": "canonical"
  }
}
```

Rules:

- Exactly one provider-supplied canonical trajectory is required for a successful query or benchmark.
- The canonical trajectory uses `format=atif` and an explicit schema version.
- An otherwise successful provider result without exactly one canonical ATIF trajectory becomes `FAILED` with `error_code=trajectory_missing`.
- The control plane does not inspect or synthesize ATIF content in this slice.
- Provider-native trajectories may also be registered with `format=provider_native` and `role=provider_raw`.
- Canonical and provider-raw files keep distinct file IDs, paths, sizes, and SHA-256 checksums.
- Failed or cancelled runs may expose partial/raw trajectories without changing their original terminal reason.

## Compatibility

Existing v1 callers that submit only `idempotency_key`, `task`, and optional `model` continue to create query runs. Existing SQLite v1 rows migrate with query defaults and null benchmark/trajectory metadata. Existing run-file kinds and endpoints are unchanged; the trajectory kind and manifest fields are additive.
