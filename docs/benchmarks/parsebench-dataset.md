# ParseBench + FileX benchmark runbook

AWorld converts the pinned
[`llamaindex/ParseBench`](https://huggingface.co/datasets/llamaindex/ParseBench)
release into a deterministic `yolo-dataset-package/v2`, runs each public task
with FileX, and reduces the verifier reward vectors into one versioned report.
The supported remote execution path is:

```text
aworld-cli -> mcpgateway -> Harbor scheduler -> Arca + Runtime
           -> AWorld harness -> FileX -> isolated ParseBench verifier
```

Package authoring never downloads the dataset, calls FileX, or calls a model.
It consumes an already-present, revision-proven source checkout. Running a task
is a separate operation.

## Pinned release

The implementation accepts exactly this contract:

- Dataset revision: `2805a1d940f95a203e0ae4b88be9934f7765b3fc`
- Official scorer revision: `34b73455032797754f6ed62e14c27a8b5423d11e`
- Full selection-manifest digest:
  `sha256:66fc68ad1f912ab3b5b03239ae76f4330ebcb801a1578a53913ff3231ab18f62`
- Source files: `chart.jsonl`, `layout.jsonl`, `table.jsonl`,
  `text_content.jsonl`, and `text_formatting.jsonl`
- Expected source cardinality: 169,011 rules and 2,078 unique source-file
  parse executions

Rules are grouped by normalized source path and one-indexed `page`. Most source
documents are already single-page excerpts and use an all-document execution
key; an explicitly paged source remains a separate execution for each page.

The source directory must be either:

1. a clean Git checkout at the pinned dataset revision, or
2. the real Hugging Face cache snapshot directory for that revision.

For Git, all five JSONL files and every referenced document must be tracked and
unchanged. Staged, unstaged, untracked, or ignored source material is rejected.
A plain `.parsebench-revision` marker is intentionally not revision evidence.

For a Hugging Face cache snapshot, every required material must resolve into
the same repository cache's bounded `blobs/` directory. A 40-character blob
name is verified as a Git blob SHA-1 and a 64-character blob name as a raw
SHA-256. Renaming a directory to the revision, copying loose files into it, or
changing a blob without changing its content address fails closed. Git LFS/Xet
objects must already be materialized. Links escaping the checkout or bounded
blob directory are rejected.

## Recommended smoke workflow

The CLI deliberately defaults to a deterministic smoke package with one
selection per scoring dimension. This is the right mode for local integration
and CI because it exercises package import, scheduling, FileX, verification,
and reduction without launching all 2,078 executions.

Set non-secret paths locally. For an image intended for shared or production
use, use an immutable digest:

```bash
export PARSEBENCH_SOURCE=/path/to/huggingface/cache/snapshots/2805a1d940f95a203e0ae4b88be9934f7765b3fc
export PARSEBENCH_PACKAGE=/tmp/parsebench-smoke.zip
export PARSEBENCH_RUNTIME_IMAGE='registry.example/aworld-filex-parsebench@sha256:<64-hex-digest>'

aworld-cli --no-banner benchmark parsebench prepare \
  --source "$PARSEBENCH_SOURCE" \
  --output "$PARSEBENCH_PACKAGE" \
  --runtime-image "$PARSEBENCH_RUNTIME_IMAGE"
```

`--smoke-per-dimension 1` and `--smoke-seed parsebench-smoke-v1` are the
defaults. Pass them explicitly when a recorded command should show the
selection policy. The converter validates the complete pinned checkout before
selecting the smoke subset, so a partial or silently revised corpus cannot be
presented as this release.

For local contract development only, a mutable image may be used explicitly:

```bash
aworld-cli --no-banner benchmark parsebench prepare \
  --source "$PARSEBENCH_SOURCE" \
  --output /tmp/parsebench-smoke-local.zip \
  --runtime-image aworld-filex-parsebench:local \
  --allow-mutable-local-image
```

That package and every report derived from it are non-publishable. The command
prints canonical JSON containing the package checksum, selection digest, task
and rule counts, dimensions, and `publishable` decision.

### Configure mcpgateway and Arca

The ParseBench submission uses Harbor's `agent_only` execution environment,
the `aworld` harness, and the logical VLM profile
`default__gemini-3.1-pro-preview`. The mcpgateway Harbor configuration routes
that profile to:

- target profile `arca-default`;
- Theta runtime profile `theta-parsebench-gemini-31-pro-v1`;
- concrete model `gemini-3.1-pro-preview`;
- the OpenAI-compatible MatrixLLM endpoint configured by the deployment.

This model choice is aligned with the evaluator configuration in
`config/biz_config/default/asap_evaluator.yaml`. The model credential is not a
YAML value: the mcpgateway service process must receive it through
`MATRIXLLM_API_KEY`. A missing credential fails route deployment closed.

The CLI reads its control-plane connection values from environment variables:

```bash
export MCPGATEWAY_URL=http://127.0.0.1:8100
export MCPGATEWAY_JWT_TOKEN='<set in the shell or secret manager>'
export MCPGATEWAY_STAFF_ID='<set when the deployment requires it>'
```

`MCPGATEWAY_JWT_TOKEN` and `MCPGATEWAY_STAFF_ID` may be omitted only when the
target deployment does not require them. Alternate variable names can be
selected with `--token-env` and `--staff-id-env`; those options accept variable
names, never secret values. When either credential is present, the CLI requires
HTTPS except for an explicit loopback URL (`localhost`, `127.0.0.1`, or `::1`).
`MATRIXLLM_API_KEY` belongs in the mcpgateway/Arca
deployment environment, not in the Dataset package or the CLI command line.

Do not put any credential in a package, task parameter, model profile, runtime
image reference, command argument, run manifest, report, or checked-in config.
Runtime owns the model route and exposes only the protected proxy configuration
needed by the AWorld/FileX process. FileX receives the credential through its
process environment and removes it before constructing its structured config.

PaddleOCR-VL also requires the local `PP-DocLayoutV3` detector. The benchmark
never lets PaddleX fetch this model while a task is running. The reviewed
Runtime materializes the official `PP-DocLayoutV3_infer.tar` archive with
SHA-256
`98b9bac88c80f6bc0fda7e0bfc2cae180020371c0b2edbb1eb498a70ace751b1`,
verifies the three extracted model files, and mounts them read-only at
`/opt/skillsbench-agent-frameworks/paddlex-models/PP-DocLayoutV3`. AWorld
revalidates those files before every FileX invocation and records model
`PP-DocLayoutV3` plus manifest digest
`sha256:effeb59959c7da305dd1d0e74382e82dfa82f10b8ffb9be25b20a2b21d5bad6f`
in the version-2 result provenance. A missing or changed model fails before
FileX starts; it never falls back to a registry download.

The protected execution profile keeps layout detection and chart recognition
enabled. Chart regions are routed to the same approved remote VLM so the
official ParseBench chart dimension is scored from structured chart content;
orientation, document unwarping, seal recognition, and all runtime model
downloads remain disabled.

For a local diagnostic outside the Runtime image, set the non-secret
`AWORLD_PARSEBENCH_LAYOUT_MODEL_DIR` to an absolute, non-symlinked directory
containing that exact extracted model. This variable selects only local model
files and must not contain a URL.

### Submit one or two tasks

Use `--limit` or repeat `--task-id` to validate only a few tasks. The command
first validates the complete package and reserves the output path, then imports
the package and binds the exact Dataset publication receipt. Before batch
submission it asks mcpgateway to build only the selected Task images, waits for
READY images from that same publication generation and task-set, and rejects a
READY projection that has no immutable image digest. It then submits the
ordered selection with a durable client request ID and writes a canonical,
credential-free run manifest. Existing outputs are never overwritten:

```bash
aworld-cli --no-banner benchmark parsebench submit \
  --package "$PARSEBENCH_PACKAGE" \
  --limit 2 \
  --run-manifest /tmp/parsebench-run.json
```

To select known tasks instead of the first catalog entries:

```bash
export PARSEBENCH_TASK_ID_1=pb-...
export PARSEBENCH_TASK_ID_2=pb-...

aworld-cli --no-banner benchmark parsebench submit \
  --package "$PARSEBENCH_PACKAGE" \
  --task-id "$PARSEBENCH_TASK_ID_1" \
  --task-id "$PARSEBENCH_TASK_ID_2" \
  --run-manifest /tmp/parsebench-run.json
```

The default model profile is already the ParseBench VLM profile. Override it
only with another deployment-approved logical profile. `--task-timeout` is in
seconds and accepts 60 through 14,400; the default is 3,600.
`--image-build-timeout` independently bounds image preparation and defaults to
3,600 seconds; it accepts 60 through 14,400. A partial selection uses the
per-Task build API, so `--limit 2` does not construct every image in a full
2,078-execution package.

If the process or response is interrupted, the output remains a canonical
`submitting`, `imported`, or `images_ready` intent. Resume only the exact same
package, selection, model, timeout, and gateway URL:

```bash
aworld-cli --no-banner benchmark parsebench submit \
  --package "$PARSEBENCH_PACKAGE" \
  --limit 2 \
  --run-manifest /tmp/parsebench-run.json \
  --resume
```

mcpgateway scopes the saved request ID by tenant and environment. The package
import binds that ID to the uploaded ZIP digest, and the batch submission binds
it to the complete canonical request. After an accepted response is lost,
explicit `--resume` recovers both the original Dataset publication receipt and
the original ordered batch/run receipt. Image recovery first reads the
current-generation status and triggers only images that are absent or in a
terminal failed state; an in-flight image build is polled rather than blindly
restarted. Reusing the key with a different ZIP or different frozen submission
semantics fails with a conflict. The CLI never retries mutating requests
implicitly.

### Check status

```bash
aworld-cli --no-banner benchmark parsebench status \
  --run-manifest /tmp/parsebench-run.json
```

Status performs a lightweight `limit=0` metadata query and does not download
run results. It prints the batch ID, status, total, completed, failed, and
terminal flag. Keep the run manifest unchanged: it binds the package digest,
publication receipt, dataset/scorer revisions, Runtime image, gateway URL,
current-generation image-build receipt, client request ID, model profile, task
IDs, ordered run IDs, and acceptance checksum.

### Write the report

Run this after status is terminal:

```bash
aworld-cli --no-banner benchmark parsebench report \
  --run-manifest /tmp/parsebench-run.json \
  --output /tmp/parsebench-report.json
```

`report` rejects non-terminal or identity-inconsistent gateway results. It
downloads bounded pages using the gateway's reward-only projection, validates
one stable terminal snapshot plus each task's sample/run mapping and versioned reward vector, applies
the official five-dimension reduction, and atomically writes a deterministic
`aworld.parsebench.gateway-report/v2` document. Smoke and partial reports are
always marked `publishable: false`, even when every selected task succeeds.

## Full release safety gates

Full package creation is explicit and is still only a packaging operation:

```bash
aworld-cli --no-banner benchmark parsebench prepare \
  --source "$PARSEBENCH_SOURCE" \
  --output /path/to/parsebench-full.zip \
  --runtime-image "$PARSEBENCH_RUNTIME_IMAGE" \
  --full
```

A publishable full package must contain all 2,078 executions, match the pinned
full selection digest, use the exact dataset/scorer revisions, and reference
the exact independently audited Runtime image digest released in
`PINNED_PARSEBENCH_RUNTIME_IMAGE`. An arbitrary immutable `@sha256` image is not
sufficient. This release intentionally leaves that pin unset until the
verifier-capable image is built, audited, and pushed, so all current full
packages and reports fail closed as non-publishable. They remain suitable for
bounded integration runs. A package made with `--allow-mutable-local-image` is
also permanently non-publishable.

Submitting every task in the full package has a second, independent opt-in:

```bash
aworld-cli --no-banner benchmark parsebench submit \
  --package /path/to/parsebench-full.zip \
  --run-manifest /path/to/parsebench-full-run.json \
  --full
```

Without `submit --full`, the CLI rejects an attempt to launch the complete
release. It is safe to use `--limit 1` or `--limit 2` on a full package for a
non-publishable integration check; that does not require the full-run opt-in.
Do not run the complete benchmark merely to validate wiring.

## Local validation checklist

A proportionate local check is:

1. Run the focused ParseBench unit and CLI tests.
2. Create the default smoke package from the complete pinned source snapshot.
3. Import it into a local mcpgateway deployment and submit one or two tasks to
   Arca with `--limit 1` or `--limit 2`.
4. Poll `status`, then generate `report` after the batch reaches `done`.
5. Confirm `/logs/artifacts/document.md`, `/logs/artifacts/layout.json`,
   `/logs/artifacts/result.json`, and `/logs/agent/trajectory.json` exist for a
   successful task, and that the report remains non-publishable.

For a direct task-container diagnostic, the AWorld harness exposes:

```bash
aworld-cli --no-banner benchmark parsebench run-task \
  --task-spec /workspace/parsebench-task.json \
  --workspace-root /workspace \
  --artifacts-root /logs/artifacts \
  --trajectory-output /logs/agent/trajectory.json
```

This command is intended to run inside the authored task environment, where
the document is mounted below `/workspace/input` and Runtime supplies protected
`LLM_BASE_URL`, `LLM_MODEL_NAME`, and `LLM_API_KEY` values. Do not copy those
values into an extracted task directory. Runtime also provides the immutable
layout detector described above; a local run must set
`AWORLD_PARSEBENCH_LAYOUT_MODEL_DIR` explicitly. The verifier runs separately
through the task's `tests/test.sh`; it owns the ground truth and pinned scorer.

Useful repository-level checks include:

```bash
pytest -q tests/benchmarks/parsebench
pytest -q tests/benchmarks/parsebench/test_gateway_cli.py \
  tests/benchmarks/parsebench/test_benchmark_commands.py
```

These tests validate the adapter, scorer boundary, package contracts, CLI
lifecycle, pagination, full-run gates, and credential-free manifests without
paying for a full benchmark run.

## Task and verifier boundary

Each generated task archive has this shape:

```text
<task-id>/
├── task.toml
├── instruction.md
├── environment/
│   ├── Dockerfile
│   ├── parsebench-task.json
│   ├── parsebench-scope.json
│   └── input/document.<ext>
└── tests/
    ├── Dockerfile
    ├── test.sh
    ├── ground_truth.json
    └── parsebench-scope.json
```

Only the source document and versioned public execution contracts enter
`environment/`. `parsebench-task.json` contains exactly its schema version,
task ID, pinned dataset/scorer revisions, and source runtime path, size,
checksum, and optional page. It contains no dimensions, rule counts, rules,
tags, original category-bearing source path, or expected Markdown. The public
`dataset.jsonl` follows the same fairness boundary.

Rule payloads, rule IDs, tags, expected Markdown, dimensions, and their JSONL
line provenance remain under verifier-owned `tests/`. `task.toml` selects
`verifier.environment_mode = "separate"` and gives the verifier its own
no-network build environment. The AWorld harness writes `document.md`,
`layout.json`, and the `aworld-parsebench-filex-result/v2` `result.json` below
`/logs/artifacts`; Harbor stops the agent
environment before starting the verifier and restores only those artifacts.
The private `tests/` tree is never uploaded into the agent environment.

The authored top-level environment deliberately has neither `image` nor
`docker_image`: `environment/Dockerfile` must be built so the per-task source
and public contract enter the task image. In the mcpgateway + Arca path,
Runtime freezes that built image and injects its immutable `docker_image` into
an execution copy of `task.toml`. A direct Harbor run must likewise build the
authored Dockerfile instead of selecting the common runtime base as a prebuilt
task image.

`manifest.json` records the checksum and size of the canonical catalog and
every task archive. Its provenance records checksums and row counts for all
five source JSONL files, revisions, source and selected cardinalities,
converter version, runtime image reference, deterministic selection policy,
revision-evidence kind, and the public task-contract version. Package import,
submission, result collection, and score reduction all revalidate these
identities rather than trusting mutable control-plane metadata.
