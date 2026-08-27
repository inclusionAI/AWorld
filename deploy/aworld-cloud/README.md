# AWorld Cloud Private Cloud Quickstart

This guide brings up the current AWorld Cloud Docker Compose MVP on one trusted host.
It is a single-machine private-cloud deployment with no Kubernetes dependency.

Run every command in this guide from the repository root unless stated otherwise.

## Architecture

The Compose deployment contains these components:

- `aworld-cloud-server` serves the FastAPI Cloud API, the operations Dashboard, and interactive API documentation.
- `aworld-cloud-worker` claims durable runs and invokes the Local Docker executor.
- `aworld-cloud-cli` is an on-demand, HTTP-only client of the Server API.
- SQLite stores Workspace, Batch, Run, event, and file-manifest state in `cloud.sqlite3`.
- Local Docker executes query runs, while Harbor uses the same host Docker daemon to execute the supported Terminal-Bench task and its verifier.

Server and Worker share one absolute host data directory at the same absolute path inside both containers.
Worker also mounts the Docker Unix socket and therefore has host-root-equivalent access.
Use this deployment only on a trusted host and trusted network.

For the underlying contracts, see [Compose MVP](../../docs/AWorld%20Cloud/Compose%20MVP.md), [Cloud CLI](../../docs/AWorld%20Cloud/CLI.md), and [Run Protocol](../../docs/AWorld%20Cloud/Run%20Protocol.md).

## Prerequisites

- A Linux or macOS host with Docker Engine or Docker Desktop and the Docker Compose v2 plugin.
- A Docker daemon reachable from a container through a daemon-visible Unix socket.
- Bash and `curl` for the examples below.
- Network access to GitHub and the public Harbor and Terminal-Bench image and dataset sources.
- Enough free disk space for the Cloud image, benchmark image, Harbor cache, and run output.
- At least the configured per-run memory allowance, which defaults to 4 GiB.

Confirm the Docker client, daemon, and Compose plugin before initialization:

```bash
docker version
docker compose version
```

## Initialize `.env`

The initialization script creates `deploy/aworld-cloud/.env` from `.env.example` when it is absent, validates the Compose configuration, creates persistent directories, and discovers a Docker socket that a container can actually use.

```bash
./scripts/aworld-cloud-init.sh
```

Review the generated file before starting:

```bash
sed -n '1,200p' deploy/aworld-cloud/.env
```

`AWORLD_CLOUD_DATA_DIR` must be a non-root absolute host path.
Linux keeps the default `/var/tmp/aworld-cloud`, while macOS initialization replaces that legacy default with the repository-local `.aworld-cloud` directory so Docker Desktop or Colima can share it with containers.
`AWORLD_CLOUD_PORT` defaults to `8000`.
Run initialization again after changing Docker contexts or when a persisted socket stops working.

Set a known daemon-visible socket explicitly when automatic discovery cannot select the correct one:

```bash
AWORLD_CLOUD_DOCKER_SOCKET=/var/run/docker.sock \
  ./scripts/aworld-cloud-init.sh
```

## Start and stop

After the one-time initialization, start Server and Worker with one Compose command:

```bash
docker compose \
  --env-file deploy/aworld-cloud/.env \
  -f deploy/aworld-cloud/docker-compose.yml \
  up --build --detach --wait aworld-cloud-server aworld-cloud-worker
```

Check both services and the API health endpoint:

```bash
docker compose \
  --env-file deploy/aworld-cloud/.env \
  -f deploy/aworld-cloud/docker-compose.yml \
  ps
curl --fail http://127.0.0.1:8000/healthz
```

Open [http://127.0.0.1:8000/](http://127.0.0.1:8000/) for the Dashboard.
The Dashboard shows Server and Worker health, recent Batches and Runs, Batch progress, cancellation controls, Run events, and safe previews or downloads of registered files.
Interactive FastAPI documentation is available at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).
Use the configured `AWORLD_CLOUD_PORT` instead of `8000` in host URLs if you changed the default.

Stop the stack without deleting persistent data:

```bash
docker compose \
  --env-file deploy/aworld-cloud/.env \
  -f deploy/aworld-cloud/docker-compose.yml \
  down
```

## Workspace, Batch, and Run

A Workspace is the durable execution boundary selected from an administrator-owned profile.
It owns one writable repository directory and one managed Codex home, and its Runs execute one at a time in this MVP.

A Run is one query or benchmark execution with its own lifecycle, events, logs, result, artifacts, and canonical ATIF trajectory on success.
A Run can be submitted by itself or belong to one Batch.

A Batch atomically creates a named group of new Runs in one Workspace.
Its state, progress, terminal counts, and average reward are derived from those Runs, and cancelling a Batch requests cancellation of its queued or active work.

## Create a two-query Batch with `curl`

The current Compose MVP has no authentication layer, so these local requests need no token.
The commands below use the Server container's Python interpreter only to extract generated IDs from JSON responses.

Define the endpoint and the exact Compose invocation once in the current Bash session:

```bash
export AWORLD_CLOUD_ENDPOINT=http://127.0.0.1:8000
compose=(
  docker compose
  --env-file deploy/aworld-cloud/.env
  -f deploy/aworld-cloud/docker-compose.yml
)
```

Create a Workspace with the profile configured by this deployment:

```bash
WORKSPACE_JSON="$(
  curl --fail-with-body --silent --show-error \
    --request POST \
    --header 'content-type: application/json' \
    --data '{
      "idempotency_key": "curl-workspace-quickstart-v1",
      "name": "curl-quickstart",
      "profile_name": "terminal-bench"
    }' \
    "${AWORLD_CLOUD_ENDPOINT}/api/v1/cloud/workspaces"
)"
printf '%s\n' "${WORKSPACE_JSON}"
WORKSPACE_ID="$(
  printf '%s' "${WORKSPACE_JSON}" |
    "${compose[@]}" exec -T aworld-cloud-server \
      python -c 'import json, sys; print(json.load(sys.stdin)["id"])'
)"
```

Create one Batch containing exactly two query Runs:

```bash
BATCH_JSON="$(
  curl --fail-with-body --silent --show-error \
    --request POST \
    --header 'content-type: application/json' \
    --data '{
      "idempotency_key": "curl-two-query-batch-v1",
      "name": "curl-two-queries",
      "runs": [
        {
          "mode": "query",
          "task": "printf '\''%s\\n'\'' '\''first query'\''",
          "model": null
        },
        {
          "mode": "query",
          "task": "printf '\''%s\\n'\'' '\''second query'\''",
          "model": null
        }
      ]
    }' \
    "${AWORLD_CLOUD_ENDPOINT}/api/v1/cloud/workspaces/${WORKSPACE_ID}/batches"
)"
printf '%s\n' "${BATCH_JSON}"
BATCH_ID="$(
  printf '%s' "${BATCH_JSON}" |
    "${compose[@]}" exec -T aworld-cloud-server \
      python -c 'import json, sys; print(json.load(sys.stdin)["id"])'
)"
```

Query the Batch aggregate and its Runs:

```bash
curl --fail-with-body --silent --show-error \
  "${AWORLD_CLOUD_ENDPOINT}/api/v1/cloud/batches/${BATCH_ID}"
curl --fail-with-body --silent --show-error \
  "${AWORLD_CLOUD_ENDPOINT}/api/v1/cloud/batches/${BATCH_ID}/runs"
```

Cancel queued or active work in the Batch:

```bash
curl --fail-with-body --silent --show-error \
  --request POST \
  --header 'content-type: application/json' \
  --data '{"idempotency_key":"curl-cancel-two-query-batch-v1"}' \
  "${AWORLD_CLOUD_ENDPOINT}/api/v1/cloud/batches/${BATCH_ID}/cancel"
```

Cancellation is idempotent for the supplied key, and Runs that already reached a terminal state remain terminal.

## Create a two-query Batch with the Cloud CLI

The `aworld-cloud-cli` Compose service runs the same `aworld-cli cloud` HTTP client without installing it on the host.
The CLI emits one JSON object per command.

Reuse the `compose` Bash array from the previous section, or define it again before running these commands.
Create another Workspace and capture its generated ID:

```bash
CLI_WORKSPACE_JSON="$(
  "${compose[@]}" run --rm -T aworld-cloud-cli \
    cloud workspace create \
    --name cli-quickstart \
    --profile terminal-bench \
    --idempotency-key cli-workspace-quickstart-v1
)"
printf '%s\n' "${CLI_WORKSPACE_JSON}"
CLI_WORKSPACE_ID="$(
  printf '%s' "${CLI_WORKSPACE_JSON}" |
    "${compose[@]}" exec -T aworld-cloud-server \
      python -c 'import json, sys; print(json.load(sys.stdin)["id"])'
)"
```

Place the two Run requests in the shared data directory so the CLI container can read them:

```bash
CLOUD_DATA_DIR="$(
  sed -n 's/^AWORLD_CLOUD_DATA_DIR=//p' deploy/aworld-cloud/.env |
    tail -n 1
)"
RUNS_FILE="${CLOUD_DATA_DIR}/quickstart-two-queries.json"
cat > "${RUNS_FILE}" <<'JSON'
[
  {
    "task": "printf '%s\\n' 'first query'",
    "mode": "query",
    "model": null
  },
  {
    "task": "printf '%s\\n' 'second query'",
    "mode": "query",
    "model": null
  }
]
JSON
```

Create the Batch and capture its ID:

```bash
CLI_BATCH_JSON="$(
  "${compose[@]}" run --rm -T aworld-cloud-cli \
    cloud batch create \
    --workspace-id "${CLI_WORKSPACE_ID}" \
    --name cli-two-queries \
    --runs-file "${RUNS_FILE}" \
    --idempotency-key cli-two-query-batch-v1
)"
printf '%s\n' "${CLI_BATCH_JSON}"
CLI_BATCH_ID="$(
  printf '%s' "${CLI_BATCH_JSON}" |
    "${compose[@]}" exec -T aworld-cloud-server \
      python -c 'import json, sys; print(json.load(sys.stdin)["id"])'
)"
```

Query or cancel it through the CLI:

```bash
"${compose[@]}" run --rm -T aworld-cloud-cli \
  cloud batch get "${CLI_BATCH_ID}"
"${compose[@]}" run --rm -T aworld-cloud-cli \
  cloud run list --workspace-id "${CLI_WORKSPACE_ID}"
"${compose[@]}" run --rm -T aworld-cloud-cli \
  cloud batch cancel "${CLI_BATCH_ID}" \
  --idempotency-key cli-cancel-two-query-batch-v1
```

## Run Terminal-Bench `fix-git`

The repository verification script creates a Workspace, submits `terminal-bench@2.0/fix-git` in benchmark mode through Harbor, waits for completion, and asserts reward `1.0` plus one canonical ATIF trajectory.

```bash
./scripts/verify-aworld-cloud-terminal-bench.sh
```

The configured Harbor agent is `oracle`, which runs the benchmark's official solution and real verifier.
This is an end-to-end harness smoke test rather than a model capability score.
The first run can take time while Docker and Harbor download benchmark inputs and images.

## Trajectories, logs, and artifacts

Every Run has a service-owned directory at `${AWORLD_CLOUD_DATA_DIR}/runs/<run-id>/`.
A successful query or benchmark Run registers one canonical `${AWORLD_CLOUD_DATA_DIR}/runs/<run-id>/trajectory.atif.json` file.
Provider stdout, stderr, and normalized result are stored as `stdout.log`, `stderr.log`, and `result.json` in the same Run directory.
Benchmark output is stored below `${AWORLD_CLOUD_DATA_DIR}/runs/<run-id>/harbor/<job-name>/`, and that job's `result.json` is registered as an artifact when Harbor produces it.

List registered files and download the standard logs and canonical trajectory through the CLI:

```bash
"${compose[@]}" run --rm -T aworld-cloud-cli cloud run files RUN_ID
"${compose[@]}" run --rm -T aworld-cloud-cli \
  cloud run logs RUN_ID --output-dir "${CLOUD_DATA_DIR}/downloads/RUN_ID"
"${compose[@]}" run --rm -T aworld-cloud-cli \
  cloud run trajectory RUN_ID \
  --output "${CLOUD_DATA_DIR}/downloads/RUN_ID-trajectory.atif.json"
```

Replace `RUN_ID` with an actual Run ID from `cloud run list`, a Batch Run listing, or the Dashboard.
Output paths under `CLOUD_DATA_DIR` are visible at the same absolute path to the host and CLI container.
The `logs` command downloads manifest entries of kind `stdout`, `stderr`, and `result`, while `files` and the Dashboard also expose registered trajectory and artifact entries.

The Terminal-Bench verification script additionally writes these audit files below `${AWORLD_CLOUD_DATA_DIR}/verification/`:

- `<run-id>-result.json` contains the terminal Run response.
- `<run-id>-events.json` contains the bounded event page.
- `<run-id>-files.json` contains the file manifest.
- `<run-id>-logs/` contains downloaded stdout, stderr, and result files.
- `<run-id>-trajectory.atif.json` contains the downloaded canonical trajectory.
- `<run-id>-compose.log` contains Server and Worker Compose logs captured after the run.

## Persistent data

The absolute `AWORLD_CLOUD_DATA_DIR` bind mount survives `docker compose down` and image rebuilds.
Its important paths are:

- `cloud.sqlite3` for durable control-plane state.
- `runs/<run-id>/` for logs, results, trajectories, and provider output.
- `workspace-repos/<workspace-id>/` for each Workspace's writable repository.
- `workspaces/<workspace-id>/codex-home/` for managed Workspace Codex state.
- `cache/` and `home/` for Harbor and runtime caches.
- `verification/` for the Terminal-Bench verification bundle.

Back up the entire directory only while Server and Worker are stopped, or use a SQLite-aware backup procedure while they are running.
Releasing a Workspace removes its writable repository after active work finishes, so do not use Workspace release as a backup mechanism.

## Troubleshooting

### Initialization cannot find a Docker socket

Confirm that `docker version` reaches the daemon, rerun initialization after changing Docker contexts, and set `AWORLD_CLOUD_DOCKER_SOCKET` explicitly when needed.
The path must be absolute, mountable by Docker, and usable from a short validation container.

### Server or Worker is unhealthy

Inspect service state and recent logs:

```bash
"${compose[@]}" ps
"${compose[@]}" logs --tail=200 aworld-cloud-server aworld-cloud-worker
```

Worker startup verifies both `harbor --help` and access to the Docker daemon.
A stale socket setting, stopped daemon, or failed image build will keep it unhealthy.

### A Run remains queued

Check Worker health and logs first.
The MVP defaults to one Worker with concurrency `1`, and Runs in one Workspace execute serially, so a long benchmark can keep later Runs queued.

### Terminal-Bench fails or times out

Check free disk space, registry and GitHub connectivity, Docker daemon logs, and `${AWORLD_CLOUD_DATA_DIR}/runs/<run-id>/stderr.log`.
The default wall-clock limit is `AWORLD_CLOUD_RUN_TIMEOUT_SECONDS=1800`, and the first run may spend substantial time downloading inputs.
Use the Run response and `${AWORLD_CLOUD_DATA_DIR}/verification/<run-id>-compose.log` together when diagnosing verifier failures.

### The Dashboard is unreachable

Check `"${compose[@]}" ps`, call `/healthz`, and confirm that the host URL uses the port configured by `AWORLD_CLOUD_PORT`.
The default Compose port mapping listens on host port `8000`.

### The host cannot see generated files on macOS

Run initialization again and keep `AWORLD_CLOUD_DATA_DIR` on a path shared with Docker Desktop or Colima.
The script's repository-local `.aworld-cloud` default is designed for this case.

## Current MVP boundaries

- The deployment is single-host and has no Kubernetes, high availability, or horizontal scaling.
- SQLite and a local bind-mounted filesystem replace production database, object storage, and distributed queue services.
- PostgreSQL, Redis, MinIO, OpenSandbox, identity, tenant isolation, quotas, and policy enforcement are deferred.
- The current Compose stack has no authentication, so it must not be exposed to an untrusted network.
- Worker has the host Docker socket and runs as root, which is equivalent to host-root access.
- Query mode executes the supplied task as a shell command in the administrator-configured runtime image.
- The Local Docker benchmark allowlist currently contains only `terminal-bench@2.0/fix-git` and uses Harbor's `oracle` agent by default.
- The CLI polls for terminal state and retrieves bounded event pages, but it does not yet provide live SSE watch/reconnection or arbitrary file-download commands.
- The Dashboard is a dependency-free operations view over the existing API, not a production administration or security plane.
