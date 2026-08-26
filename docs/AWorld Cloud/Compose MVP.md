# AWorld Cloud Docker Compose MVP

This deployment runs one real Terminal-Bench task end to end through the AWorld
Cloud API. It is a local/private-cloud MVP, not the final multi-tenant production
topology.

## What runs

The single Compose file at `deploy/aworld-cloud/docker-compose.yml` defines:

- `aworld-cloud-server`: FastAPI and the durable Cloud API;
- `aworld-cloud-worker`: durable worker plus the Local Docker provider;
- `aworld-cloud-cli`: an on-demand HTTP-only CLI service.

Server and worker share SQLite and run artifacts through one absolute bind mount.
The worker mounts `/var/run/docker.sock` and invokes Harbor 0.6.6 at pinned commit
`ccf3df5ef50141b004322d4008b84b64797b76b9`. Harbor then creates the real task
container, executes the selected agent, and runs the task verifier. There is no
Kubernetes layer.

The default smoke agent is Harbor's `oracle`: it runs the benchmark's official
solution and then the real verifier. This proves the dataset download, Docker
environment, harness, verifier, result parsing, lifecycle, and artifact path
without requiring model credentials. It is a harness smoke test, not a model
capability score and not a fake executor.

## Start and verify

Prerequisites are Docker Engine with the Compose v2 plugin, access to the public
Harbor and Terminal-Bench registries, and enough disk for the task image.

```bash
./scripts/aworld-cloud-init.sh
docker compose \
  --env-file deploy/aworld-cloud/.env \
  -f deploy/aworld-cloud/docker-compose.yml \
  up --build --detach --wait aworld-cloud-server aworld-cloud-worker
./scripts/verify-aworld-cloud-terminal-bench.sh
```

The verification script creates a workspace, submits exactly
`terminal-bench@2.0/fix-git`, polls it to a terminal state, and asserts reward
`1.0`. It stores the API result, events, file manifest, provider stdout/stderr,
Compose logs, and the one canonical ATIF trajectory below
`$AWORLD_CLOUD_DATA_DIR/verification`.

For individual CLI calls:

```bash
docker compose \
  --env-file deploy/aworld-cloud/.env \
  -f deploy/aworld-cloud/docker-compose.yml \
  run --rm aworld-cloud-cli cloud run list
```

## Trust and storage boundary

The worker runs as root and has the host Docker socket. This is equivalent to
host-root access and is acceptable only for this explicitly local MVP. Keep the
data directory absolute: Harbor passes its paths to the host Docker daemon, so
the Compose bind source and container target intentionally have the same path.

SQLite, the artifact directory, and Harbor's cache are persistent under
`AWORLD_CLOUD_DATA_DIR`. Back up that directory only while the services are
stopped or with a SQLite-aware backup procedure. To stop without deleting data:

```bash
docker compose \
  --env-file deploy/aworld-cloud/.env \
  -f deploy/aworld-cloud/docker-compose.yml down
```

PostgreSQL, Redis, MinIO, identity/tenant policy, and the primary OpenSandbox
provider remain behind their existing interfaces and are intentionally deferred.
They are not represented by placeholder services in this MVP Compose file.
