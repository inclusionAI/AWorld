# AWorld Cloud CLI

`aworld-cli cloud` is an HTTP-only client for the AWorld Cloud Server API. It never opens the Cloud database or calls an Executor Provider directly.

Set the server URL with `--endpoint` or `AWORLD_CLOUD_ENDPOINT`. Set a bearer token with `--token` or `AWORLD_CLOUD_TOKEN` when authentication is enabled. Every command emits one JSON object to standard output; errors use the stable Cloud error envelope on standard error.

## Workspaces

```bash
aworld-cli cloud --endpoint http://cloud.example.test workspace create \
  --name review --profile standard

aworld-cli cloud workspace list
aworld-cli cloud workspace show workspace-123
aworld-cli cloud workspace release workspace-123
```

Create and release accept `--idempotency-key`. When omitted, the CLI generates a UUID for that invocation.

## Query and benchmark runs

Query is the default mode:

```bash
aworld-cli cloud run submit \
  --workspace-id workspace-123 \
  --task "Inspect and fix the failing test"
```

Benchmark mode uses the same run lifecycle and requires dataset and task identity:

```bash
aworld-cli cloud run submit \
  --workspace-id workspace-123 \
  --mode benchmark \
  --dataset suite-name \
  --task-id case-42 \
  --task "Resolve the repository issue"
```

Run inspection and control commands are:

```bash
aworld-cli cloud run list --workspace-id workspace-123
aworld-cli cloud run show run-123
aworld-cli cloud run events run-123
aworld-cli cloud run files run-123
aworld-cli cloud run cancel run-123
aworld-cli cloud run retry run-123
```

Download the canonical ATIF trajectory selected by the run response:

```bash
aworld-cli cloud run trajectory run-123 --output trajectory.atif.json
```

Live SSE watch/reconnection and arbitrary file download commands are not implemented yet. The listed `events` command retrieves a bounded event page.
