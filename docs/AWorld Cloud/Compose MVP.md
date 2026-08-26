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
The worker mounts the active host Docker Unix socket at the container path
`/var/run/docker.sock` and invokes Harbor 0.6.6 at pinned commit
`ccf3df5ef50141b004322d4008b84b64797b76b9`. Harbor then creates the real task
container, executes the selected agent, and runs the task verifier. There is no
Kubernetes layer.

The Cloud image uses the pinned full `python:3.12.11-bookworm` base so Harbor's
TaskClient has runtime Git for fetching benchmark tasks. Harbor itself is
installed from that commit's SHA-256-verified GitHub source archive, so the
image build does not run `apt-get`, `git clone`, or require SSH.

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

The init and verification scripts first honor an explicit
`AWORLD_CLOUD_DOCKER_SOCKET`, then test daemon-side and active-context
candidates. A short `docker:27.5.1-cli` container mounts each candidate and
connects to the Docker API; only a working bind source is selected. This matters
for Colima and similar VM-backed contexts: the host CLI may connect through
`~/.colima/.../docker.sock`, while containers must mount the daemon-side
`/var/run/docker.sock`. The init script persists the resolved path in
`deploy/aworld-cloud/.env`. If an automatically persisted value stops working,
running init again replaces it with a newly validated value.

On macOS, init also replaces the old generated
`AWORLD_CLOUD_DATA_DIR=/var/tmp/aworld-cloud` value with the absolute
repo-local `.aworld-cloud` directory. Paths below `/var/tmp` are inside the
Colima VM when evaluated by the daemon and are not the host's `/var/tmp`.
Repo-local storage is shared by both sides, so downloaded results and ATIF files
are visible to the host verification process. A non-default value is preserved;
to deliberately keep `/var/tmp/aworld-cloud` on macOS, pass it explicitly when
running init. Linux/private-cloud deployments retain the `/var/tmp` default and
may continue to set any non-root absolute `AWORLD_CLOUD_DATA_DIR`.

To select it explicitly:

```bash
AWORLD_CLOUD_DOCKER_SOCKET=/var/run/docker.sock \
  ./scripts/aworld-cloud-init.sh
```

The verification script creates a workspace, submits exactly
`terminal-bench@2.0/fix-git`, polls it to a terminal state, and asserts reward
`1.0`. It stores the API result, events, file manifest, provider stdout/stderr,
Compose logs, and the one canonical ATIF trajectory below
`$AWORLD_CLOUD_DATA_DIR/verification`.
Its final JSON and ATIF assertions consume the files through host-side input
redirection, ensuring the reported artifacts are actually host-visible.

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
The deployment does not read or mount the host's `.codex` directory; workspace
Codex state, when needed, lives below the managed Cloud data directory.

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
