# AWorld Cloud upstream protocol contracts

This document records additive contracts consumed by Cloud CLI and dashboard
clients. The stable API prefix is `/api/v1/cloud`; Batch does not add an
orchestration platform or change the Executor Provider boundary.

## Workspace → Batch → Runs

A Batch is a durable named group of one or more runs in one workspace. A Run
may have a nullable `batch_id`, so existing standalone runs and migrated SQLite
rows remain valid.

Create a Batch atomically with:

```http
POST /api/v1/cloud/workspaces/{workspace_id}/batches
```

```json
{
  "name": "nightly",
  "idempotency_key": "client-stable-key",
  "runs": [
    {"task": "first query", "mode": "query", "model": null},
    {
      "task": "benchmark case",
      "mode": "benchmark",
      "benchmark": {"dataset": "suite", "task_id": "case-1"}
    }
  ]
}
```

Each run item follows the versioned Run request contract, without its own
idempotency key. Batch identity, all runs, and their initial `run.queued` events
commit in one transaction. Reusing the create key with the same payload returns
the original Batch; a different payload returns `idempotency_conflict`.

The read/control endpoints are:

- `GET /batches` with `limit`, `page_token`, and optional `workspace_id`;
- `GET /batches/{batch_id}`;
- `GET /batches/{batch_id}/runs` with `limit` and `page_token`;
- `POST /batches/{batch_id}/cancel` with `idempotency_key`.

Cancel atomically moves queued runs to `cancelled` and active runs to
`cancelling`. Terminal runs are preserved. Repeating the same request is
idempotent.

## Derived state and statistics

Batch state and statistics are derived from durable Runs on every read rather
than cached in a worker process. This makes state converge after worker or
server restarts.

- `queued`: every run is queued;
- `running`: at least one run is active, or queued work follows a terminal run;
- `cancelling`: at least one run is cancelling;
- `succeeded`: every run succeeded;
- `cancelled`: every run was cancelled;
- `partially_succeeded`: at least one run succeeded and another failed or was cancelled;
- `failed`: all work is terminal, no run succeeded, and at least one failed.

Counts expose `total`, `queued`, `running`, `succeeded`, `failed`, and
`cancelled`; `running` includes starting, running, and cancelling Runs.
`progress` is terminal runs divided by total runs. `started_at` is the earliest
Run start and `finished_at` is the latest Run finish only after every Run is
terminal.

`average_reward` is the arithmetic mean over Runs with a non-null benchmark
reward. `reward_count` states the denominator; the average is null when that
count is zero.

## Compatibility and architecture

The SQLite schema adds a `batches` table and nullable indexed `runs.batch_id`.
Migration does not rewrite existing Runs. The Server and Worker continue to use
the same repository and direct Executor Provider interfaces. There is no
Kubernetes product layer, and Batch does not introduce SkillsBench or ASAP.
